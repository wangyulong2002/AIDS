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

本文件分三部分：
    ① 静态扫描：禁止从请求参数读 userId 做权限判断（BE-04 的硬约束）
    ② 契约测试：越权访问必须返回 403 + 错误码 10005
    ③ 端到端：A 的 Token 批量递增 ID 扫描，无一条越权数据泄露（BE-04 验收原文）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from aids_backend.deps import get_current_user
from app.core.errors import CommonError
from app.core.jwt import ACCESS_TYP, JwtKeys, encode_token
from app.core.refresh_store import InMemoryRefreshStore
from app.core.response import ApiResponse, ok
from app.core.security import CurrentUser
from app.models.biz import BizAddress
from app.orm.repository import OwnedRepository
from app.orm.session import get_db
from tests.contract._targets import scan_targets

pytestmark = [pytest.mark.invariant, pytest.mark.task("BE-04")]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"

# 鉴权模块是 userId 的**合法**来源（从 JWT 解出），唯一豁免。
# 按**路径**豁免，不按文件名——历史写法 `p.name != "security.py"` 会让
# 任何 app/**/security.py 都自动免疫。
_ALLOWED_RELATIVE = "app/core/security.py"

# 请求来源关键字：命中即认为"从请求取 userId"
_REQUEST_SOURCES = ("request", "req", "query", "query_params", "params", "body", "payload")

# 覆盖三种取用形态（原实现只覆盖 `body.user_id` 一种）：
#   body.user_id              → 属性访问
#   payload["user_id"]        → 下标访问
#   body.get("userId")        → 字典 get（FastAPI 里最常见）
_USER_ID_FROM_REQUEST = re.compile(
    r"\b(?:"
    + "|".join(_REQUEST_SOURCES)
    + r")\b(?:\s*\.\s*\w+)*(?:\.\s*get\s*\(\s*|\.\s*\[|\s*\[|\.\s*)['\"]?user_?id['\"]?",
    re.IGNORECASE,
)


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

    def _iter_py_files(self) -> list[Path]:
        """扫描面 = 共享层 + 全部服务包（复用 `_targets.scan_targets()`，与 C2/C4 同口径）。

        曾经只扫仓库根 `app/`：BE-01 起服务代码在 `aids-*/aids_*/` 下，
        **完全不在扫描范围且门禁全绿** —— 与 HANDOFF §9 记录的静默缺口同型。
        新增服务后无需登记：`scan_targets()` 自动覆盖。
        """
        allowed = (APP_DIR / "core" / "security.py").resolve()
        return [
            p
            for target in scan_targets()
            for p in sorted(target.rglob("*.py"))
            if p.resolve() != allowed
        ]

    def test_scan_covers_every_service_package(self) -> None:
        """反向：服务包必须真的在扫描面内（否则本类的扫描全是空转）。"""
        covered = {p.resolve() for p in self._iter_py_files()}
        for pkg in scan_targets():
            if pkg.name.startswith("aids_"):
                assert any(
                    p.is_relative_to(pkg.resolve()) for p in covered
                ), f"服务包 {pkg} 不在 IDOR 扫描面内 —— 服务层的越权代码将无人检查"

    def test_no_user_id_parameter_extraction(self) -> None:
        """扫描 `request.args.get("user_id")` / `body.get("userId")` / `payload["user_id"]`。"""
        violations: list[str] = []
        for path in self._iter_py_files():
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(source.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                if _USER_ID_FROM_REQUEST.search(line):
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
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in {
                    "get_user_id",
                    "parse_user_id",
                    "extract_user_id",
                }:
                    bad_defs.append(f"{path}:{node.lineno}: def {node.name}(...)")

        assert not bad_defs, "发现疑似从请求取 userId 的函数定义：\n" + "\n".join(
            f"  {d}" for d in bad_defs
        )


class TestIdorScannerPattern:
    """回归：正则必须覆盖 FastAPI 的常见取用形态，且不误伤"按 user_id 过滤资源"。

    为什么要单测正则：扫描器的漏检是**静默的**——它不报错，只是抓不到。
    原实现只认 `body.user_id` 一种形态，`body.get("userId")`（FastAPI 里最常见）
    和 `payload["user_id"]` 都能大摇大摆走过去，而这正类漏洞是 PRD §13 列为"高"的风险。
    """

    _MUST_MATCH = [
        'request.args.get("user_id")',
        'body.get("userId")',
        'payload["user_id"]',
        "query.userId",
        'query_params.get("user_id")',
        "req.query.user_id",
        "body.user_id",
    ]

    _MUST_NOT_MATCH = [
        "biz_order.user_id == uid",  # 资源过滤（正确写法）
        "filter(BizOrder.user_id == current_user_id)",
        "Where(id=order_id, user_id=current_user_id)",
        "def get_current_user_id(token: str) -> int:",  # 从 Token 解析，合法
    ]

    @pytest.mark.parametrize("line", _MUST_MATCH)
    def test_pattern_matches(self, line: str) -> None:
        assert _USER_ID_FROM_REQUEST.search(line), f"IDOR 扫描器漏检：{line}"

    @pytest.mark.parametrize("line", _MUST_NOT_MATCH)
    def test_pattern_does_not_match(self, line: str) -> None:
        assert not _USER_ID_FROM_REQUEST.search(line), f"IDOR 扫描器误报：{line}"


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
# ③ 端到端：越权遍历扫描（BE-04 验收原文的直接代码化）
# =====================================================================


class _FakeResult:
    def __init__(self, item: object) -> None:
        self._item = item

    def scalar_one_or_none(self) -> Any:
        return self._item


class _FakeSession:
    """不连库的会话替身：按 SQL 参数模拟数据库的行级过滤。

    OwnedRepository 生成的语句带 `user_id = :user_id_1` 参数 —— 这里检查编译后的
    参数中**user_id 列**的值是否等于"行主人"：匹配才返回行，否则 None。
    于是「A 扫 B 的资源」与「B 读自己的资源」走**同一段被测代码**，
    差别只在参数 —— 这正是数据库的真实行为，而不是替身另写一套逻辑。
    """

    def __init__(self, row: SimpleNamespace) -> None:
        self._row = row

    async def execute(self, stmt: Any) -> _FakeResult:
        params = stmt.compile().params
        hit = any(
            str(key).startswith("user_id") and value == self._row.user_id
            for key, value in params.items()
        )
        return _FakeResult(self._row if hit else None)


# 数据库里"存在"的那条资源：属于用户 B（含只有主人该看到的字段）
# 列名对齐 biz_address（receiver / detail）；刻意不含 phone（密文）——探针也不回传敏感字段
_B_ADDRESS = SimpleNamespace(
    id=5001,
    user_id=8002,
    receiver="用户B的收货人",
    detail="B 的收货地址（泄露即事故）",
    deleted=0,
)
USER_A = 8001  # 扫描发起者（不是资源主人）
USER_B = 8002  # 资源主人


class _AddressRepo(OwnedRepository[BizAddress]):
    model = BizAddress


def _probe_router() -> APIRouter:
    """探针路由：一个"用户自有资源"的标准实现，与未来的订单/地址/优惠券接口同构。

    响应体刻意带 `receiver_name` —— 验收要求"响应体不包含 B 的数据字段"，
    得先有可泄露的字段，断言才有意义。
    """
    router = APIRouter()

    @router.get("/probe/owned/{pk}")
    async def _get_owned(
        pk: int,
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> ApiResponse:
        repo = _AddressRepo(session, current_user.user_id)
        address = await repo.require(pk)
        return ok(
            {
                "id": address.id,
                "user_id": address.user_id,
                "receiver": address.receiver,
            }
        )

    return router


@pytest.fixture
def keys() -> JwtKeys:
    return JwtKeys.generate()


@pytest.fixture
def store() -> InMemoryRefreshStore:
    return InMemoryRefreshStore()


@pytest.fixture
def idor_client(keys: JwtKeys, store: InMemoryRefreshStore) -> TestClient:
    """真实 app + 探针路由；密钥 / 吊销存储 / 数据库会话三处依赖替换为可控替身。"""
    from aids_backend.app_factory import create_app
    from aids_backend.deps import get_jwt_keys, get_refresh_store
    from app.orm.session import get_db

    app = create_app()
    app.include_router(_probe_router())
    app.dependency_overrides[get_jwt_keys] = lambda: keys
    app.dependency_overrides[get_refresh_store] = lambda: store

    session = _FakeSession(_B_ADDRESS)

    async def _fake_db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app, raise_server_exceptions=False)


def _token(keys: JwtKeys, user_id: int) -> str:
    return encode_token(
        keys, typ=ACCESS_TYP, user_id=user_id, roles=("user",), ttl=600, jti="jti", sid="sid"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestIdorEndToEnd:
    """验收原文：「用 A 的 Token 访问 B 的订单/地址/优惠券/会话全部 403，
    批量递增 ID 扫描无一条越权数据泄露」。

    订单/地址/优惠券/会话的真实业务接口属 BE-08/BE-19 等任务；本类用
    **同一套 Repository 基类** + 探针路由先行固化机制，后续每个自有资源接口
    必须复用这条路径（S4 扫描器 + code review 保证，不靠自觉）。
    """

    def test_owner_can_read_own_resource(self, idor_client: TestClient, keys: JwtKeys) -> None:
        """正向对照：B 读自己的资源必须 200 —— 缺了它，"必须失败"的断言没有意义。"""
        response = idor_client.get(
            f"/probe/owned/{_B_ADDRESS.id}", headers=_auth(_token(keys, USER_B))
        )
        assert response.status_code == 200
        assert response.json()["code"] == 0
        assert response.json()["data"]["receiver"] == _B_ADDRESS.receiver

    def test_foreign_resource_is_403_with_10005(
        self, idor_client: TestClient, keys: JwtKeys
    ) -> None:
        response = idor_client.get(
            f"/probe/owned/{_B_ADDRESS.id}", headers=_auth(_token(keys, USER_A))
        )
        assert response.status_code == 403
        assert response.json()["code"] == int(CommonError.DATA_FORBIDDEN)

    def test_missing_and_foreign_are_indistinguishable(
        self, idor_client: TestClient, keys: JwtKeys
    ) -> None:
        """「不存在」与「无权」必须完全一致 —— 不一致就能枚举有效 ID。"""
        headers = _auth(_token(keys, USER_A))
        foreign = idor_client.get(f"/probe/owned/{_B_ADDRESS.id}", headers=headers)
        missing = idor_client.get("/probe/owned/999999", headers=headers)
        assert foreign.status_code == missing.status_code == 403
        assert foreign.json() == missing.json(), "两种失败响应不一致 → 可枚举资源 ID"

    def test_batch_ascending_id_scan_no_leak(self, idor_client: TestClient, keys: JwtKeys) -> None:
        """遍历式扫描（PRD §12.4）：批量递增 ID，无一条越权数据泄露。"""
        headers = _auth(_token(keys, USER_A))
        leaked: list[int] = []
        for pk in range(1, 51):
            response = idor_client.get(f"/probe/owned/{pk}", headers=headers)
            if response.status_code != 403 or response.json()["code"] != int(
                CommonError.DATA_FORBIDDEN
            ):
                leaked.append(pk)
            else:
                # 响应体只能是统一响应体，且 data 必须为空 —— B 的任何字段不得出现
                body = response.json()
                assert set(body) == {"code", "message", "data"}
                assert body["data"] is None
        assert not leaked, f"以下 ID 未被拦截（越权泄露）：{leaked}"

    def test_missing_token_is_401_not_403(self, idor_client: TestClient) -> None:
        """未登录是 401（10002），不要混进"越权"（403）——前端对两者的处理不同。"""
        response = idor_client.get("/probe/owned/1")
        assert response.status_code == 401
        assert response.json()["code"] == int(CommonError.UNAUTHORIZED)
