"""S4 结构性防御测试：数据权限（IDOR 防护）。

权威文档：
    PRD §2.2 数据权限模型 / §12.4 数据权限（安全）
    TASKS BE-04 验收标准：
        「用 A 的 Token 访问 B 的订单/地址/优惠券/会话全部 403，
          遍历式 IDOR 扫描（批量请求递增 ID）无一条越权数据泄露」

为什么这类测试必须自动化：
    IDOR 是电商最高频安全漏洞（PRD §13 风险表列为"高"）。
    bysj 的教训是"文档里写了要防，但没写测试 → 某个接口漏了没人发现"。
    遍历式扫描天然适合自动化——人手不可能试 1000 个 ID。

本文件分两部分：
    ① 静态扫描：禁止从请求参数读 userId 做权限判断（BE-04 的硬约束）
    ② 契约测试：越权访问必须返回 403 + 错误码 10005（待接口实现后启用）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.errors import CommonError

pytestmark = pytest.mark.invariant

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"


# =====================================================================
# ① 静态扫描：禁止从请求参数读 userId 做权限判断
# =====================================================================


class TestNoUserIdFromRequest:
    """BE-04 硬约束：业务代码禁止从请求参数读 userId 做权限判断。

    原文：「业务代码禁止从请求参数读 userId 做权限判断；
          资源访问一律 WHERE id=? AND user_id=?，查不到返回 403」

    为什么这条能自动化：
        "从请求参数取 userId" 在代码里有明确的形态——
        参数名里含 user_id 且来源是 request/query/body。
        这是可静态识别的模式。

    豁免：
        - app/core/security.py（鉴权模块，userId 的合法来源）
        - tests/
        - 内部服务接口（BE-06，走服务间鉴权而非用户 JWT）
    """

    # 请求来源的关键字（命中即认为是从请求取）
    _REQUEST_SOURCES = ("request", "req", "query", "params", "body", "payload")

    def _iter_py_files(self) -> list[Path]:
        if not APP_DIR.exists():
            return []
        return [p for p in APP_DIR.rglob("*.py") if p.name != "security.py"]

    def test_no_user_id_parameter_extraction(self) -> None:
        """扫描形如 `request.args.get("user_id")` / `query.user_id` 的用法。"""
        pattern = re.compile(
            r"(?:request|req|query|params|body|payload)\s*[\.\[]\s*['\"]?user_?id['\"]?",
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in self._iter_py_files():
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(source.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                if pattern.search(line):
                    violations.append(f"{path}:{i}: {line.strip()}")

        assert not violations, (
            "发现从请求参数读取 userId（违反 BE-04 数据权限约束）：\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n  修复：userId 必须来自 JWT（app/core/security.py 提供），"
            "资源访问用 WHERE id=? AND user_id=?"
        )

    def test_no_bare_get_user_id_function(self) -> None:
        """禁止定义 `get_user_id(request)` 这类从请求取 userId 的辅助函数。"""
        bad_defs: list[str] = []
        for path in self._iter_py_files():
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                    "get_user_id",
                    "parse_user_id",
                    "extract_user_id",
                }:
                    bad_defs.append(f"{path}:{node.lineno}: def {node.name}(...)")

        assert not bad_defs, "发现疑似从请求取 userId 的函数定义：\n" + "\n".join(
            f"  {d}" for d in bad_defs
        )


# =====================================================================
# ② 越权返回码契约
# =====================================================================


class TestIdorResponseContract:
    """越权访问的错误码与 HTTP 状态码必须符合 API.md。"""

    def test_idor_error_code_is_10005(self) -> None:
        """API.md §1.3：10005 = 数据越权（IDOR 拦截）。"""
        assert int(CommonError.DATA_FORBIDDEN) == 10005

    def test_idor_does_not_leak_existence(self) -> None:
        """关键安全语义：查不到与无权访问必须返回同一结果，不暴露资源是否存在。

        API.md §1.3 明确：「不暴露资源是否存在」。
        如果无权访问返回 403 而资源不存在返回 404，攻击者就能枚举出哪些 ID 有效。
        """
        from tests.contract._doc_parser import API_DOC

        text = API_DOC.read_text(encoding="utf-8")
        assert "不暴露资源是否存在" in text, "API.md 缺少『不暴露资源是否存在』的安全语义声明"

    def test_doc_declares_idor_scan_requirement(self) -> None:
        """PRD §12.4 要求遍历式 IDOR 扫描。"""
        from tests.contract._doc_parser import DOCS

        prd = (DOCS / "PRD.md").read_text(encoding="utf-8")
        assert "遍历式 IDOR 扫描" in prd
        assert "无一条越权数据泄露" in prd

    def test_doc_lists_all_protected_resources(self) -> None:
        """PRD §12.4 列举的受保护资源：订单/地址/优惠券/会话。"""
        from tests.contract._doc_parser import DOCS

        prd = (DOCS / "PRD.md").read_text(encoding="utf-8")
        for resource in ("订单", "地址", "优惠券", "会话"):
            assert resource in prd, f"PRD §12.4 未提及受保护资源: {resource}"


# =====================================================================
# ③ 待接口实现后启用的端到端脚手架
# =====================================================================


@pytest.mark.skip(reason="待 T1 BE-04 实现数据权限拦截器后启用（当前 0 业务代码）")
class TestIdorEndToEnd:
    """越权遍历扫描（BE-04 验收标准的直接代码化）。

    实现后应：
        1. 用 A 的 Token 遍历递增 ID，访问 B 的资源
        2. 断言全部返回 403 + code=10005
        3. 断言响应体不包含任何 B 的数据字段
    """

    PROTECTED_RESOURCES = (
        "/api/order/{id}",
        "/api/address/{id}",
        "/api/coupon/{id}",
        "/api/ai/conversation/{id}",
    )

    def test_batch_ascending_id_scan_no_leak(self) -> None:
        """遍历式 IDOR 扫描：批量递增 ID 无一条越权数据泄露。"""
        raise NotImplementedError("待 BE-04 实现后填充")
