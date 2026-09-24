"""AI-01 · 服务配置隔离。

「配置隔离」如果不写成检查，就只是文档里的一句话（本项目的立身之本：
能用代码检查的，绝不靠文档要求）。本文件把它固化成三条可执行约束：

    ① **AI 服务包不得直接读写 `os.environ`** —— 所有配置一律经
       `app.core.config` 的访问器读取。这样「哪个服务读了哪些键」是可审计的，
       而不是散落在一堆 `os.getenv("...")` 里（f309978 那类半途改名正源于此）。
    ② **服务不得读别的服务的私有环境变量**（`aids-ai` 不该读 `MOCK_*`，
       `aids-mock` 不该读 `ARK_*`）—— 跨服务耦合最难排查：它能跑通，
       只是把两个服务的配置绑死在一起。
    ③ **AI 服务的启动断言面 == 它真实依赖**（只有 Ark Key）。
       此前 AI 与主业务共用 `run_startup_assertions()`，于是「AI 能不能起」
       取决于 `DATABASE_URL` / `SECRET_KEY` / `FIELD_*` —— 隔离在文档成立、
       在启动路径上不成立。

扫描方式：AST（不是文本匹配）—— 文本匹配会把注释/字符串里的 `os.getenv` 当真。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.config import (
    DEFAULT_ARK_BASE_URL,
    DEFAULT_ARK_EMBEDDING_DIM,
    DEFAULT_ARK_EMBEDDING_MODEL,
    DEFAULT_ARK_MODEL,
    StartupAssertionError,
    assert_required_keys_present,
    get_ark_api_key,
    get_ark_base_url,
    get_ark_embedding_dim,
    get_ark_embedding_model,
    get_ark_model,
    run_ai_startup_assertions,
)

pytestmark = [pytest.mark.contract, pytest.mark.task("AI-01")]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SERVICE_PACKAGES: dict[str, Path] = {
    "aids-backend": PROJECT_ROOT / "aids-backend" / "aids_backend",
    "aids-ai": PROJECT_ROOT / "aids-ai" / "aids_ai",
    "aids-mock": PROJECT_ROOT / "aids-mock" / "aids_mock",
}

# 各服务**专属**的环境变量前缀（别的服务读到它即为跨服务耦合）
EXCLUSIVE_PREFIXES: dict[str, str] = {
    "aids-ai": "ARK_",
    "aids-mock": "MOCK_",
}

# AI 服务只允许依赖共享契约层的 `app.core.*`（不得碰 app 的业务/ORM 层）
_AI_ALLOWED_APP_PREFIX = "app.core"


# =====================================================================
# 扫描器（AST）
# =====================================================================


class _EnvReadVisitor(ast.NodeVisitor):
    """收集 `os.getenv(...)` / `os.environ[...]` / `os.environ.get(...)` 的键字面量。"""

    def __init__(self, os_aliases: set[str], direct_names: set[str]) -> None:
        self._os_aliases = os_aliases
        self._direct = direct_names
        self.reads: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        key = self._key_from_call(node)
        if key is not None:
            self.reads.append((node.lineno, key))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_environ(node.value):
            self.reads.append((node.lineno, _const_str(node.slice)))
        self.generic_visit(node)

    # ---- 判定辅助 ----

    def _is_os_module(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self._os_aliases

    def _is_environ(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            return self._is_os_module(node.value)
        return isinstance(node, ast.Name) and node.id == "environ" and "environ" in self._direct

    def _key_from_call(self, node: ast.Call) -> str | None:
        func = node.func
        # os.getenv("X") / os.environ.get("X")
        if (
            isinstance(func, ast.Attribute)
            and self._is_os_module(func.value)
            and (func.attr == "getenv" or (func.attr == "get" and self._is_environ(func.value)))
        ):
            return _first_arg(node)
        # from os import getenv -> getenv("X") / environ.get("X")
        if isinstance(func, ast.Name) and func.id == "getenv" and "getenv" in self._direct:
            return _first_arg(node)
        if isinstance(func, ast.Attribute) and func.attr == "get" and self._is_environ(func.value):
            return _first_arg(node)
        return None


def _const_str(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "<dynamic>"


def _first_arg(node: ast.Call) -> str:
    if node.args:
        return _const_str(node.args[0])
    return "<dynamic>"


def _os_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """返回 (`import os as x` 的别名集合, `from os import ...` 的直接名字集合)。"""
    aliases: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    aliases.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in {"getenv", "environ"}:
                    direct.add(alias.name)
    return aliases, direct


def scan_env_reads(path: Path) -> list[tuple[int, str]]:
    """扫描单文件的直接环境变量读取，返回 [(行号, 键或 <dynamic>), ...]。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    aliases, direct = _os_names(tree)
    if not aliases and not direct:
        return []
    visitor = _EnvReadVisitor(aliases, direct)
    visitor.visit(tree)
    return sorted(visitor.reads)


def _package_reads(package: Path) -> dict[Path, list[tuple[int, str]]]:
    return {py: reads for py in sorted(package.rglob("*.py")) if (reads := scan_env_reads(py))}


# =====================================================================
# 一、扫描器自检（防「门禁自己在空转」）
# =====================================================================


class TestScannerHealth:
    """扫描器必须真的能发现问题 —— 否则下面三条约束全是摆设。"""

    def test_detects_all_common_forms(self) -> None:
        source = (
            "import os\n"
            "from os import getenv, environ\n"
            'a = os.getenv("A")\n'
            'b = os.environ.get("B")\n'
            'c = os.environ["C"]\n'
            'd = getenv("D")\n'
            'e = environ.get("E")\n'
            "f = os.getenv(SOME_VAR)\n"
        )
        tree = ast.parse(source)
        aliases, direct = _os_names(tree)
        visitor = _EnvReadVisitor(aliases, direct)
        visitor.visit(tree)
        keys = [key for _, key in visitor.reads]
        assert keys == ["A", "B", "C", "D", "E", "<dynamic>"]

    def test_ignores_mentions_in_strings_and_comments(self) -> None:
        """文本匹配会把文档字符串里的例子当真 —— AST 不会。"""
        source = 'DOC = """os.getenv(\'NOT_A_REAL_READ\')"""\n# os.getenv("ALSO_NOT")\n'
        tree = ast.parse(source)
        aliases, direct = _os_names(tree)
        visitor = _EnvReadVisitor(aliases, direct)
        visitor.visit(tree)
        assert visitor.reads == []

    def test_scanner_sees_mock_direct_reads(self) -> None:
        """反向：Mock 服务确实存在直接读取（证明扫描面不是空的）。"""
        reads = _package_reads(SERVICE_PACKAGES["aids-mock"])
        found = {key for entries in reads.values() for _, key in entries}
        assert "MOCK_PAY_MERCHANT_ID" in found


# =====================================================================
# 二、三条隔离约束
# =====================================================================


def test_ai_package_reads_env_only_through_app_core_config() -> None:
    """① AI 服务包不得直接读写 `os.environ`（配置一律经 `app.core.config`）。"""
    reads = _package_reads(SERVICE_PACKAGES["aids-ai"])
    detail = "\n".join(
        f"  {py.relative_to(PROJECT_ROOT)}:{lineno}: {key}"
        for py, entries in reads.items()
        for lineno, key in entries
    )
    assert not reads, (
        "aids-ai 服务包出现直接的环境变量读取：\n"
        f"{detail}\n"
        "修法：在 app/core/config.py 增加访问器，服务侧只调用访问器 "
        "（这样「哪个服务读了哪些键」才是可审计的）。"
    )


def test_no_service_reads_another_services_env_keys() -> None:
    """② 服务不得读别的服务的私有键（`aids-ai` 读 `MOCK_*` = 跨服务耦合）。"""
    problems: list[str] = []
    for service, package in SERVICE_PACKAGES.items():
        foreign = {
            other: prefix for other, prefix in EXCLUSIVE_PREFIXES.items() if other != service
        }
        for py, entries in _package_reads(package).items():
            for lineno, key in entries:
                if key == "<dynamic>":
                    continue
                for other, prefix in foreign.items():
                    if key.startswith(prefix):
                        rel = py.relative_to(PROJECT_ROOT)
                        problems.append(f"{rel}:{lineno}: {service} 读了 {other} 的键 {key}")
    assert not problems, "跨服务环境变量耦合：\n" + "\n".join(problems)


def test_ai_package_couples_only_to_shared_core_layer() -> None:
    """②' AI 服务只能依赖共享契约层 `app.core.*`，不得碰 app 的业务/ORM 层。"""
    offenders: list[str] = []
    for py in sorted(SERVICE_PACKAGES["aids-ai"].rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith("app.") and not module.startswith(_AI_ALLOWED_APP_PREFIX):
                    offenders.append(f"{py.name}: import {module}")
                if module.startswith(("aids_backend", "aids_mock")):
                    offenders.append(f"{py.name}: import {module}")
    assert not offenders, "AI 服务出现跨层/跨服务依赖：\n" + "\n".join(offenders)


# =====================================================================
# 三、启动断言面收窄（AI 只依赖 Ark Key）
# =====================================================================


class TestAiStartupScope:
    def test_ai_requires_only_ark_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """主业务的 FIELD_*/SECRET_KEY 缺席时，AI 服务仍应能启动。"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        for key in ("FIELD_ENCRYPT_KEY", "FIELD_HMAC_KEY", "SECRET_KEY", "DATABASE_URL"):
            monkeypatch.delenv(key, raising=False)
        run_ai_startup_assertions()  # 不抛异常即通过

    def test_ai_rejects_missing_ark_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with pytest.raises(StartupAssertionError, match="ARK_API_KEY"):
            run_ai_startup_assertions()

    def test_default_assertion_still_covers_field_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """回归护栏：收窄只对 AI 生效，缺省（主业务）口径不得被削弱。"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        monkeypatch.delenv("FIELD_ENCRYPT_KEY", raising=False)
        monkeypatch.delenv("FIELD_HMAC_KEY", raising=False)
        with pytest.raises(StartupAssertionError, match="FIELD_ENCRYPT_KEY"):
            assert_required_keys_present(env="production")

    def test_dev_env_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        run_ai_startup_assertions()


# =====================================================================
# 四、Ark 访问器 + main.py 接线
# =====================================================================


class TestArkAccessors:
    def test_defaults_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("ARK_API_KEY", "ARK_BASE_URL", "ARK_MODEL", "ARK_EMBEDDING_MODEL"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("ARK_EMBEDDING_DIM", raising=False)
        assert get_ark_api_key() == ""
        assert get_ark_base_url() == DEFAULT_ARK_BASE_URL
        assert get_ark_model() == DEFAULT_ARK_MODEL
        assert get_ark_embedding_model() == DEFAULT_ARK_EMBEDDING_MODEL
        assert get_ark_embedding_dim() == DEFAULT_ARK_EMBEDDING_DIM

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARK_API_KEY", "  ark-abc  ")
        monkeypatch.setenv("ARK_BASE_URL", "https://example.invalid/api/v3")
        monkeypatch.setenv("ARK_EMBEDDING_DIM", "1024")
        assert get_ark_api_key() == "ark-abc", "Key 必须 strip，避免复制粘贴带空白"
        assert get_ark_base_url() == "https://example.invalid/api/v3"
        assert get_ark_embedding_dim() == 1024

    def test_invalid_dim_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非法维度必须报错，不能静默回退 —— 维度错会让向量检索静默失准。"""
        monkeypatch.setenv("ARK_EMBEDDING_DIM", "big")
        with pytest.raises(StartupAssertionError, match="必须是整数"):
            get_ark_embedding_dim()


def _called_names(tree: ast.AST) -> set[str]:
    """收集被调用的函数名（`f()` 与 `x.f()` 都取最后一段）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_ai_main_wires_logging_and_scoped_assertions() -> None:
    """main.py 必须装结构化日志并用**收窄后**的断言（否则隔离只在 config 层成立）。

    用 AST 取"实际被调用的函数名"，而不是在源码里找子串 ——
    main.py 的 docstring 正好解释了"为什么不用 run_startup_assertions()"，
    文本匹配会把这句说明误判成违规。
    """
    source_path = SERVICE_PACKAGES["aids-ai"] / "main.py"
    called = _called_names(ast.parse(source_path.read_text(encoding="utf-8")))
    assert "configure_structured_logging" in called, "AI 服务启动必须装结构化日志"
    assert "run_ai_startup_assertions" in called, "AI 服务必须用收窄后的启动断言"
    assert "run_startup_assertions" not in called, (
        "AI 服务不得再调用全量断言 —— 那会把 AI 的启动条件与主业务配置绑死"
    )
