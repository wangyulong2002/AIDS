"""BE-01 契约测试：路由分组、健康探针、启动防御。

三件事，共同点是「两边约定不一致时不会有任何编译/类型错误，只能靠测试发现」：

    ① 模块 → 前缀映射与 docs/API.md 一致 —— 防「代码挂 /marketing、文档写 /coupon」
    ② /health 与 Dockerfile 的 HEALTHCHECK 一致 —— 防容器永久 unhealthy
    ③ main.py 在错误配置下拒绝启动 —— S1 结构性防御的端到端验证
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aids_backend.api import MODULE_ROUTERS
from aids_backend.app_factory import create_app
from tests.contract._doc_parser import API_DOC

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "aids-backend"
BACKEND_DOCKERFILE = PROJECT_ROOT / "deploy" / "app" / "backend.Dockerfile"

# 模块 → 前缀。必须与 aids_backend/api/__init__.py 的映射表、
# docs/API.md 的真实路径三方一致。
EXPECTED_PREFIXES: dict[str, str] = {
    "user": "/user",
    "product": "/product",
    "order": "/order",
    "pay": "/payment",
    "marketing": "/coupon",
    "admin": "/admin",
}


# =====================================================================
# 一、模块前缀（代码 ↔ 文档 双向校验）
# =====================================================================


class TestModulePrefixes:
    def test_prefixes_match_expected_mapping(self) -> None:
        actual = {name: router.prefix for name, router in MODULE_ROUTERS.items()}
        assert actual == EXPECTED_PREFIXES, (
            "模块前缀漂移。若为有意变更，须同步三处："
            "docs/API.md、aids_backend/api/__init__.py 的映射表、本断言。"
        )

    @pytest.mark.parametrize(("module", "prefix"), sorted(EXPECTED_PREFIXES.items()))
    def test_prefix_actually_exists_in_api_doc(self, module: str, prefix: str) -> None:
        """反向校验：每个前缀都必须能在 API.md 里找到真实路径。

        只断「两边前缀字符串相等」不够——两边同时写错（都改成 /marketing）
        仍会通过。要求 API.md 中确实存在以该前缀开头的路径，才能锁住语义。
        """
        text = API_DOC.read_text(encoding="utf-8")
        pattern = rf"`{re.escape(prefix)}/"
        assert re.search(pattern, text), (
            f"模块 {module} 的前缀 {prefix} 在 docs/API.md 中找不到任何真实路径"
        )


# =====================================================================
# 二、健康探针（代码 ↔ Dockerfile 跨产物校验）
# =====================================================================


class TestHealthEndpointContract:
    def test_dockerfile_healthcheck_path_matches_app(self) -> None:
        """容器 HEALTHCHECK 的路径必须与 app 里真实注册的路径一致。

        不一致不会有任何编译/类型错误，只会表现为容器永久 unhealthy，
        被编排系统反复重启——排查成本极高，故在此固化。
        """
        dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        match = re.search(
            r"curl\s+-sf\s+http://localhost:(?P<port>\d+)(?P<path>/[\w\-/]*)",
            dockerfile,
        )
        assert match, (
            "backend.Dockerfile 的 HEALTHCHECK 未按约定写 "
            "curl -sf http://localhost:PORT/path —— 测试无法定位探针路径"
        )

        # 用「发请求」判断探针是否真的可达，而不是遍历 app.routes：
        # fastapi >= 0.14x 的 include_router 结果被包成惰性 _IncludedRouter
        # （没有 .path 属性），遍历 routes 会漏掉全部子路由，得到假阴性。
        probe_path = match.group("path")
        response = TestClient(create_app(), raise_server_exceptions=False).get(probe_path)
        assert response.status_code == 200, (
            f"Dockerfile 探针路径 {probe_path} 未在应用中注册（返回 {response.status_code}）"
            f"—— 容器会被判定为 unhealthy 并反复重启"
        )

    def test_dockerfile_exposes_documented_port(self) -> None:
        dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        assert re.search(r"EXPOSE\s+8080\b", dockerfile), (
            "主业务服务端口应为 8080（TASKS 服务清单）；改端口须同步 compose/Nginx/前端"
        )


# =====================================================================
# 三、启动防御（S1：错误配置必须拒绝启动）
# =====================================================================


class TestStartupGuard:
    """用子进程而非直接 import —— main.py 的副作用正是「退出进程」，
    在测试进程内触发会把整个 pytest 干掉。"""

    @staticmethod
    def _import_main(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # 先清掉可能污染断言的继承变量，再按用例注入
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"APP_ENV", "DATABASE_URL", "SECRET_KEY"}
        }
        env.update(env_overrides)
        # 子进程需同时解析 app.*（共享层）与 aids_backend.*（服务层）
        env["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(BACKEND_DIR), str(PROJECT_ROOT), env.get("PYTHONPATH", ""))
            if part
        )
        # 不传 cwd：PROJECT_ROOT 在 WSL 下是 UNC 路径，Windows 的 CreateProcess
        # 不接受 UNC 作为工作目录；PYTHONPATH 已显式给出，无需依赖 cwd。
        return subprocess.run(
            [sys.executable, "-c", "import aids_backend.main"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_refuses_test_db_in_production(self) -> None:
        """S1-a：生产环境连测试库 → 拒绝启动。"""
        result = self._import_main(
            {"APP_ENV": "production", "DATABASE_URL": "mysql://u:p@h/aids_shop_test"}
        )
        assert result.returncode != 0, "生产环境连测试库必须拒绝启动"
        assert "启动断言失败" in (result.stdout + result.stderr)

    def test_refuses_default_secret_in_production(self) -> None:
        """S1-b：生产环境用默认弱密钥 → 拒绝启动。"""
        result = self._import_main(
            {
                "APP_ENV": "production",
                "DATABASE_URL": "mysql://u:p@h/aids_shop",
                "SECRET_KEY": "changeme",
            }
        )
        assert result.returncode != 0, "生产环境用默认密钥必须拒绝启动"
        assert "启动断言失败" in (result.stdout + result.stderr)

    def test_refuses_production_db_in_test_env(self) -> None:
        """S1-d：bysj 的历史顽疾——测试跑了生产库。这里必须被代码拦住。"""
        result = self._import_main({"APP_ENV": "test", "DATABASE_URL": "mysql://u:p@h/aids_shop"})
        assert result.returncode != 0, "测试环境连生产库必须拒绝启动"
        assert "启动断言失败" in (result.stdout + result.stderr)

    def test_starts_fine_in_development(self) -> None:
        """正向对照。

        缺了它，上面三条「必须失败」的断言会变得毫无意义——
        一个永远报错的实现也能全部通过。
        """
        result = self._import_main({"APP_ENV": "development"})
        assert result.returncode == 0, (
            f"开发环境应能正常启动（含导入期无异常）。"
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
