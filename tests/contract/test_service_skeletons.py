"""三服务骨架契约测试 —— 每个服务都必须「能被探活、能返回统一响应体、被登记进工具链」。

为什么需要（B2 前置条件）：
    `deploy/app/{backend,ai,mock}.Dockerfile` 三个镜像在此前**从未端到端构建过**：
    构建只在校验 compose 配置的 CI job 里"被跳过"，而 compose 的 9 个服务全是中间件，
    不含应用服务。于是「改名做了一半」「COPY 源不存在」这类问题可以长期潜伏。
    本文件把「三个服务都能装配、都能探活、响应体一致」固化成断言，
    真正的 `docker build` + 容器探活由 CI 的 images job 承担（见 .github/workflows/ci.yml）。

为什么把「工具链登记」也放进契约测试：
    HANDOFF §9 记录的静默缺口是——新增服务后，C2/C4 扫描器、ruff、
    pyright、pytest 的 pythonpath 各自有份"服务清单"，漏登记任何一处都表现为
    「门禁全绿但该服务从未被检查」。故这里对 pyproject.toml 做反向断言：
    磁盘上存在 `aids-<name>/aids_<name>/` 就必须被登记。
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from app.core.errors import SUCCESS, CommonError

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_APP = PROJECT_ROOT / "deploy" / "app"


class _Service(NamedTuple):
    name: str
    factory: str  # 装配模块（无导入副作用，可自由 import）
    main_module: str  # gunicorn 入口（有导入副作用，只做静态校验）
    dockerfile: str
    port: int


SERVICES: tuple[_Service, ...] = (
    _Service(
        "aids-backend", "aids_backend.app_factory", "aids_backend.main", "backend.Dockerfile", 8080
    ),
    _Service("aids-ai", "aids_ai.app_factory", "aids_ai.main", "ai.Dockerfile", 8000),
    _Service("aids-mock", "aids_mock.app_factory", "aids_mock.main", "mock.Dockerfile", 8081),
)

_IDS = [s.name for s in SERVICES]

_HEALTHCHECK = re.compile(
    r"curl\s+-sf\s+http://localhost:(?P<port>\d+)(?P<path>/[\w\-/]*)",
)

# 探针 data 的字段集合：三服务一致，且只允许这两个键（PRD §10 安全基线）。
_PROBE_DATA_KEYS = {"status", "env"}
_ENVELOPE_KEYS = {"code", "message", "data"}


def _client(service: _Service) -> TestClient:
    module = importlib.import_module(service.factory)
    return TestClient(module.create_app(), raise_server_exceptions=False)


# =====================================================================
# 一、镜像 ↔ 应用：探针与端口（跨产物校验，纯静态 + 发请求）
# =====================================================================


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_dockerfile_healthcheck_is_reachable(service: _Service) -> None:
    """容器 HEALTHCHECK 的路径必须与 app 里真实注册的路径一致。

    不一致不会有任何编译/类型错误，只表现为容器永久 unhealthy、被反复重启。
    """
    text = (DEPLOY_APP / service.dockerfile).read_text(encoding="utf-8")
    match = _HEALTHCHECK.search(text)
    assert match, (
        f"{service.dockerfile} 的 HEALTHCHECK 未按约定写 curl -sf http://localhost:PORT/path"
    )

    response = _client(service).get(match.group("path"))
    assert response.status_code == 200, (
        f"{service.name} 的探针 {match.group('path')} 返回 {response.status_code}——"
        f"容器会被判定为 unhealthy"
    )


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_dockerfile_exposes_documented_port(service: _Service) -> None:
    """端口是跨文档常量（VERSIONS.md §二 #11）：8080 / 8000 / 8081。"""
    text = (DEPLOY_APP / service.dockerfile).read_text(encoding="utf-8")
    assert re.search(rf"EXPOSE\s+{service.port}\b", text), (
        f"{service.dockerfile} 应 EXPOSE {service.port}；改端口须同步 compose/Nginx/前端三处"
    )
    declared = _HEALTHCHECK.search(text)
    assert declared and int(declared.group("port")) == service.port, (
        f"{service.dockerfile} 的 HEALTHCHECK 端口与 EXPOSE 不一致"
    )


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_cmd_entrypoint_module_is_importable_by_name(service: _Service) -> None:
    """CMD 的 gunicorn 目标必须是本服务真实存在的模块名（`<pkg>.main:app`）。"""
    text = (DEPLOY_APP / service.dockerfile).read_text(encoding="utf-8")
    pkg, module = service.main_module.split(".", 1)
    assert f'"{service.main_module}:app"' in text, (
        f'{service.dockerfile} 的 CMD 应写 gunicorn "{service.main_module}:app"'
    )
    assert (PROJECT_ROOT / service.name / pkg / f"{module}.py").is_file()


# =====================================================================
# 二、三服务对外契约一致（统一响应体 / 探针不泄漏）
# =====================================================================


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_probe_returns_unified_envelope(service: _Service) -> None:
    body = _client(service).get("/health").json()
    assert set(body) == _ENVELOPE_KEYS, f"{service.name} 探针响应体字段漂移：{sorted(body)}"
    assert body["code"] == SUCCESS
    assert body["data"]["status"] == "up"


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_probe_does_not_leak_internals(service: _Service) -> None:
    """探针是未鉴权接口，data 只允许 {status, env}。"""
    data = _client(service).get("/health").json()["data"]
    assert set(data) == _PROBE_DATA_KEYS, f"{service.name} 探针泄漏了额外字段：{sorted(data)}"


@pytest.mark.parametrize("service", SERVICES, ids=_IDS)
def test_unknown_path_keeps_unified_envelope(service: _Service) -> None:
    """路由未命中：HTTP 保留 404，但 body 仍是统一响应体（前端拦截器按 code 分流）。"""
    response = _client(service).get("/definitely-not-a-route")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["code"] == int(CommonError.NOT_FOUND)


# =====================================================================
# 三、新增服务必须被登记进工具链（防「门禁全绿但从未检查」）
# =====================================================================


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _existing_service_packages() -> list[Path]:
    return sorted(p for p in PROJECT_ROOT.glob("aids-*/aids_*") if p.is_dir())


def test_every_service_package_exists_on_disk() -> None:
    """反向：声明的三个服务包必须都在磁盘上（本测试自身不能空跑）。"""
    found = {p.name for p in _existing_service_packages()}
    assert found == {"aids_backend", "aids_ai", "aids_mock"}, sorted(found)


def test_every_service_package_is_on_pytest_pythonpath() -> None:
    """漏登记 → 该服务在测试里 import 不到，只能靠人发现。"""
    declared = set(_pyproject()["tool"]["pytest"]["ini_options"]["pythonpath"])
    missing = sorted(
        p.parent.name for p in _existing_service_packages() if p.parent.name not in declared
    )
    assert not missing, f"以下服务目录未登记到 pyproject 的 pytest pythonpath：{missing}"


def test_every_service_package_is_type_checked_by_pyright() -> None:
    """漏登记 → 该服务的类型错误不会被 CI 发现。"""
    pyright = _pyproject()["tool"]["pyright"]
    include = set(pyright["include"])
    extra_paths = set(pyright["extraPaths"])
    missing_include, missing_path = [], []
    for pkg in _existing_service_packages():
        rel = f"{pkg.parent.name}/{pkg.name}"
        if rel not in include:
            missing_include.append(rel)
        if pkg.parent.name not in extra_paths:
            missing_path.append(pkg.parent.name)
    assert not missing_include, f"以下服务包不在 pyright.include：{missing_include}"
    assert not missing_path, f"以下服务目录不在 pyright.extraPaths：{missing_path}"
