"""部署产物一致性契约测试 —— 防「配置指向不存在的目录」。

为什么需要（f309978 的真实事故）：
    该提交把 Dockerfile 的 COPY 源从 `aids-backend/aids_backend` 改成了
    `aids-backend/app`，却**并未真正重命名目录** —— 配置指向了不存在的路径，
    `docker build` 必然失败。而三道防线全都绕过了它：

        1) `deploy/docker-compose.yml` 的 9 个服务全是中间件，不含 backend/ai/mock；
        2) CI 的 smoke job 只跑 `docker compose config`，不执行 build；
        3) 文档一致性门禁只校验 Dockerfile 文本里的 EXPOSE 端口，不看文件系统。

    本质是「配置 ↔ 真实文件系统」这一维度没有任何测试覆盖。本文件补上它，
    让这类问题在 CI 里当场可见，而不是等人肉 `docker build` 才发现。

另含扫描面的覆盖率断言（HANDOFF §9）：
    C2/C4 两个扫描器曾各自写死 `[PROJECT_ROOT / "app"]`，导致 BE-01 新增的
    `aids-backend/aids_backend/` 完全不受门禁约束，且**门禁全绿**。
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DEPLOY_DIR = PROJECT_ROOT / "deploy" / "app"

# Dockerfile 文件名 → 服务目录名（与 deploy/ 的命名约定一致）
# 注意：frontend.Dockerfile 用 ${APP_DIR} 变量引用源，无法静态判定，故不纳入。
_DOCKERFILE_SERVICE: dict[str, str] = {
    "backend.Dockerfile": "aids-backend",
    "ai.Dockerfile": "aids-ai",
    "mock.Dockerfile": "aids-mock",
}

# 尚未开工的服务镜像：其 COPY 源允许缺失。
# 开工（补齐服务包）后**必须**从本清单移除 —— test_copy_sources_exist 里的
# 反向断言会强制提醒，避免这份清单腐烂成永久豁免。
_PENDING_SERVICES: dict[str, str] = {
    "aids-ai": "AI 服务未开工（HANDOFF §4：aids-ai/ 仅 requirements.txt）",
    "aids-mock": "Mock 服务未开工（HANDOFF §4：aids-mock/ 仅 requirements.txt）",
}

_GUNICORN_CMD = re.compile(r'"gunicorn",\s*"(?P<mod>[a-z_][a-z0-9_]*)\.main:app"')


def _service_dockerfiles() -> list[Path]:
    """只返回能静态判定 COPY 源的服务 Dockerfile。"""
    return sorted(p for p in APP_DEPLOY_DIR.glob("*.Dockerfile") if p.name in _DOCKERFILE_SERVICE)


def _copy_sources(text: str) -> list[str]:
    """取 Dockerfile 中所有 `COPY` 的**源**路径。

    跳过两类无法静态判定的：
        - `--from=builder` 多阶段复制（源在上一阶段，不在构建上下文）
        - 含构建变量（如 `${APP_DIR}`）的路径
    """
    sources: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        if "--from=" in line:
            continue
        operands = [p for p in line.split()[1:] if not p.startswith("--")]
        if len(operands) < 2:
            continue
        sources.extend(src for src in operands[:-1] if "$" not in src)
    return sources


@pytest.mark.parametrize("dockerfile", _service_dockerfiles(), ids=lambda p: p.name)
def test_copy_sources_exist(dockerfile: Path) -> None:
    """Dockerfile 的每个 COPY 源都必须在构建上下文（仓库根）里真实存在。"""
    service = _DOCKERFILE_SERVICE[dockerfile.name]
    text = dockerfile.read_text(encoding="utf-8")
    missing = [src for src in _copy_sources(text) if not (PROJECT_ROOT / src).exists()]

    if service in _PENDING_SERVICES:
        # 反向断言：登记为「未开工」就必须真的缺东西。
        # 没有它，豁免清单会永远留着，把断言悄悄变成空操作。
        assert missing, (
            f"{service} 登记在 _PENDING_SERVICES（{'未开工'}），但其 COPY 源已全部存在 —— "
            f"服务已开工，请从 tests/contract/test_deploy_artifacts.py 的 "
            f"_PENDING_SERVICES 移除该项，让本断言真正生效。"
        )
        return

    assert not missing, (
        f"{dockerfile.name} 的 COPY 源不存在：{missing}\n"
        f"这类「配置指向不存在的目录」不会在 `docker compose config` 或文档门禁里暴露，"
        f"只有真正 `docker build` 时才炸（f309978 事故），故在此固化。"
    )


@pytest.mark.parametrize("dockerfile", _service_dockerfiles(), ids=lambda p: p.name)
def test_cmd_module_matches_service_package(dockerfile: Path) -> None:
    """CMD 的 gunicorn 模块名必须解析到真实存在的服务包。"""
    service = _DOCKERFILE_SERVICE[dockerfile.name]
    if service in _PENDING_SERVICES:
        return

    match = _GUNICORN_CMD.search(dockerfile.read_text(encoding="utf-8"))
    assert match, f'{dockerfile.name} 的 CMD 未按约定写 gunicorn "<pkg>.main:app"，测试无法定位入口'

    pkg = PROJECT_ROOT / service / match.group("mod")
    assert (pkg / "main.py").is_file(), (
        f"{dockerfile.name} 的 CMD 指向 {match.group('mod')}.main:app，"
        f"但 {pkg.as_posix()}/main.py 不存在 —— 容器起来即 ModuleNotFoundError"
    )


class TestScannerCoverage:
    """扫描门禁必须覆盖全部服务包（HANDOFF §9 的缺口）。"""

    def test_scan_targets_covers_all_service_packages(self) -> None:
        """存在即纳入 —— 新增服务后不需要人记得登记扫描面。"""
        from tests.contract._targets import PROJECT_ROOT as ROOT
        from tests.contract._targets import scan_targets

        declared = set(scan_targets())
        existing = {p for p in ROOT.glob("aids-*/aids_*") if p.is_dir()}
        uncovered = sorted(p.as_posix() for p in existing - declared)
        assert not uncovered, (
            f"以下服务包存在但不在扫描面内，其代码不受 C2/C4 门禁约束：{uncovered}\n"
            f"修复：检查 tests/contract/_targets.py 的 SERVICE_PACKAGE_GLOB。"
        )

    @pytest.mark.parametrize("module", ["scan_error_codes", "scan_enum_magic_numbers"])
    def test_scanner_uses_shared_targets(self, module: str) -> None:
        """两个扫描器不得各写一份默认扫描面 —— 那正是「扫得到一半」的成因。"""
        from tests.contract._targets import scan_targets

        scanner = importlib.import_module(f"tests.contract.{module}")
        default_targets = getattr(scanner, "_default_targets")  # noqa: B009 - 动态模块
        assert default_targets() == scan_targets(), (
            f"{module}._default_targets() 与 _targets.scan_targets() 不一致；"
            f"新增服务时会出现「C4 扫得到、C2 扫不到」的静默缺口。"
        )


class TestBuildContextHygiene:
    """构建上下文卫生。

    构建上下文是整个仓库根（deploy 下各 Dockerfile 的 context 均为 `..`）。
    没有 .dockerignore 时，约 200MB 的 .venv/ 与 .git/ 会被逐次打包传给 daemon：
    构建无谓变慢，且 .env / 私钥存在被 COPY 进镜像的风险。
    """

    _REQUIRED_PATTERNS = (".venv/", ".git/", ".env", "*.pem", "*.key")

    def test_dockerignore_excludes_heavy_and_secret_paths(self) -> None:
        ignore = PROJECT_ROOT / ".dockerignore"
        assert ignore.is_file(), (
            "缺少 .dockerignore：构建上下文是整个仓库根，"
            ".venv/（约 200MB）与 .env 会被逐次打包传给 docker daemon"
        )
        patterns = {
            line.strip()
            for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        missing = [p for p in self._REQUIRED_PATTERNS if p not in patterns]
        assert not missing, (
            f".dockerignore 未排除 {missing} —— 它们会进构建上下文"
            f"（.env / 私钥被 COPY 进镜像即密钥泄露）"
        )

    def test_does_not_ignore_paths_needed_by_dockerfiles(self) -> None:
        """反向保护：Dockerfile 真正需要的源不能被排除，否则构建直接失败。"""
        ignore = PROJECT_ROOT / ".dockerignore"
        if not ignore.is_file():
            return
        text = ignore.read_text(encoding="utf-8")

        # deploy/mysql/Dockerfile 依赖 docs/sql/；约束文件与依赖清单在仓库根
        for pattern, why in (
            (r"^docs/?$", "deploy/mysql/Dockerfile 需要 COPY docs/sql/"),
            (r"^constraints\.txt$", "deploy/app/*.Dockerfile 需要 COPY constraints.txt"),
            (r"^aids-[a-z]+/?$", "deploy/app/*.Dockerfile 需要 COPY aids-*/ 下的源码与清单"),
        ):
            assert not re.search(pattern, text, re.M), (
                f".dockerignore 排除了 {pattern!r}，但 {why} —— 镜像会因源不存在而构建失败"
            )
