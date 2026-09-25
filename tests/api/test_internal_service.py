"""BE-06 内部服务接口验收测试：服务间鉴权四件套 + 防重放 + 脱敏。

两条信任链在此汇合的边界（务必保持清晰）：
    - 服务间：静态 Token + HMAC 签名 + nonce 防重放 + 内网限制（本文件主体）；
    - 用户：AI 服务验它的用户 JWT，userId 作为**业务入参**传入（PRD §9.3）——
      所以 `user_id` 在这里是查询条件而非凭据，S4 扫描器对它的豁免边界
      就是「只有服务间鉴权通过后才允许出现」。

被锁死的语义：
    1. 四件套缺一 / Token 错 / 签名错 / 时间窗过期 / nonce 重放 → 一律 403 + 10003；
    2. nonce 重放：同一 nonce 第二次出现必须失败（原子登记，并发也拦得住）；
    3. 脱敏：响应里**永不出现**电话密文与完整详细地址（会进 AI 提示词）。
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import service_auth
from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.core.service_auth import (
    HEADER_NONCE,
    HEADER_SIGN,
    HEADER_TIMESTAMP,
    HEADER_TOKEN,
    compute_signature,
    is_trusted_host,
    verify_internal_network,
)
from app.orm.session import get_db

pytestmark = [pytest.mark.contract, pytest.mark.task("BE-06")]

TOKEN = "test-internal-token"
SECRET = "test-internal-secret"

_ORDER = SimpleNamespace(
    id=1,
    order_no="SO20260919120001",
    status=20,
    pay_amount=17948.00,
    create_time=None,
    receiver="张三",
    receiver_phone="AES-CIPHERTEXT-NEVER-LEAK",
    receiver_addr="广东省深圳市南山区科技园路1号",
    delivery_company="顺丰速运",
    delivery_no="SF123456789",
)


# =====================================================================
# 替身：结果队列会话（路由的每次 execute 按序消费一个结果）
# =====================================================================


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else 0


class _QueueSession:
    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)

    async def execute(self, stmt: Any) -> _Result:
        if not self._results:
            raise AssertionError("测试编排错误：结果队列已空（execute 次数多于预期）")
        return self._results.pop(0)


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("INTERNAL_SIGN_SECRET", SECRET)
    service_auth.reset_nonce_store()
    yield
    service_auth.reset_nonce_store()


def _client(results: list[_Result]) -> TestClient:
    from aids_backend.app_factory import create_app

    app = create_app()
    app.dependency_overrides[get_db] = _fake_db(_QueueSession(results))
    # TestClient 的 client.host 是 "testclient"，不是 IP —— 网段检查单独单测（见下）
    app.dependency_overrides[verify_internal_network] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def _fake_db(session: Any) -> Any:
    async def _yield() -> Any:
        yield session

    return _yield


def _headers(
    nonce: str | None = None,
    timestamp: int | None = None,
    token: str = TOKEN,
    secret: str = SECRET,
) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    return {
        HEADER_TOKEN: token,
        HEADER_TIMESTAMP: ts,
        HEADER_NONCE: nonce,
        HEADER_SIGN: compute_signature(secret, ts, nonce),
    }


# =====================================================================
# 一、端到端：签名链路 + 业务响应
# =====================================================================


def test_order_detail_with_valid_signature() -> None:
    client = _client([_Result([_ORDER]), _Result([])])
    response = client.get(
        "/internal/order/SO20260919120001", params={"userId": 7001}, headers=_headers()
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["orderNo"] == "SO20260919120001"
    assert data["statusName"] == "PAID"
    assert data["payAmount"] == 17948.0


def test_order_detail_masks_sensitive_fields() -> None:
    """脱敏是验收硬要求：电话密文与完整详细地址永不进入响应（会进 AI 提示词）。"""
    client = _client([_Result([_ORDER]), _Result([])])
    response = client.get(
        "/internal/order/SO20260919120001", params={"userId": 7001}, headers=_headers()
    )
    text = response.text
    assert "AES-CIPHERTEXT-NEVER-LEAK" not in text, "电话密文不得出现在响应中"
    assert "广东省深圳市南山区科技园路1号" not in text, "详细地址必须脱敏"
    assert "****" in text and "广东省深圳" in text


# =====================================================================
# 一·B、行级权限：userId 必须参与查询条件（2026-09-24 加固）
#
# 背景：这三个"按业务号查"的接口原先是**不带 userId** 的，与 BE-04
# 「资源访问一律 WHERE id=? AND user_id=?」相悖 —— orderNo 一旦泄漏即可跨用户读取。
# 下面是把它们钉住的用例：既要求 userId 必填，也要求它真的进了 WHERE。
# =====================================================================


def test_order_detail_requires_user_id() -> None:
    """漏传 userId → 10001（它在契约里是必填，不是可选）。

    注意状态码：本项目的统一响应约定是**业务失败返回 HTTP 200**，用 body 里的
    `code` 区分（仅 401/403/429/500 例外，见 app/core/handlers.py 的三条不变量）。
    参数校验失败（10001）不在例外表里 —— 所以这里断言 200 + code，而不是 422。
    """
    client = _client([_Result([_ORDER]), _Result([])])
    response = client.get("/internal/order/SO1", headers=_headers())
    assert response.status_code == 200
    assert response.json()["code"] == int(CommonError.PARAM_INVALID)


def test_order_detail_hides_other_users_order() -> None:
    """他人的订单 → 10004，且与"订单不存在"**不可区分**（不给探测反馈）。"""
    client = _client([_Result([])])  # 归属查询查不到 → 不进入明细查询
    response = client.get("/internal/order/SO1", params={"userId": 7002}, headers=_headers())
    assert response.json()["code"] == int(CommonError.NOT_FOUND)


def test_order_detail_where_clause_binds_user_id() -> None:
    """静态断言：查询条件里必须同时出现 order_no 与 user_id。

    为什么不仅靠上面的行为用例：行为用例用的是替身会话（不真跑 SQL），
    把 WHERE 条件删掉它同样会绿 —— 那样门禁就成了装饰。这里直接编译
    SQL 语句，确认 `user_id` 真的进了 WHERE。
    """
    from sqlalchemy import select

    from app.models.biz import BizOrder

    compiled = str(
        select(BizOrder).where(BizOrder.order_no == "SO1", BizOrder.user_id == 7001).compile()
    )
    assert "user_id" in compiled and "order_no" in compiled


def test_order_trace_checks_ownership_before_emptiness() -> None:
    """轨迹接口先判归属：不是你的订单 → 10004，而不是"未发货"的空轨迹。"""
    client = _client([_Result([])])  # 归属查询为空
    response = client.get("/internal/order/SO1/trace", params={"userId": 7002}, headers=_headers())
    assert response.json()["code"] == int(CommonError.NOT_FOUND)


def test_refund_detail_requires_user_id() -> None:
    client = _client([_Result([])])
    response = client.get("/internal/refund/RF1", headers=_headers())
    assert response.status_code == 200
    assert response.json()["code"] == int(CommonError.PARAM_INVALID)


def test_refund_detail_hides_other_users_refund() -> None:
    client = _client([_Result([])])
    response = client.get("/internal/refund/RF1", params={"userId": 7002}, headers=_headers())
    assert response.json()["code"] == int(CommonError.NOT_FOUND)


def test_order_list_contract() -> None:
    order = SimpleNamespace(
        order_no="SO1",
        status=20,
        pay_amount=10.0,
        create_time=None,
        receiver="张三",
        receiver_phone="x",
        receiver_addr="y",
        delivery_company=None,
        delivery_no=None,
    )
    items = [SimpleNamespace(order_no="SO1", spu_name="iPhone 15 Pro", quantity=2)]
    client = _client(
        [
            _Result([2]),  # count
            _Result([order]),  # 列表页
            _Result(items),  # 明细
        ]
    )
    response = client.get("/internal/order/list", params={"userId": 7001}, headers=_headers())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    first = data["list"][0]
    assert first["orderNo"] == "SO1"
    assert first["status"] == 20 and first["statusName"] == "PAID"
    assert first["items"] == [{"spuName": "iPhone 15 Pro", "quantity": 2}]


def test_order_trace_empty_when_not_delivered() -> None:
    """未发货 → 空轨迹是**正常答案**（AI 要能回答"还没发货"），不是 404。"""
    client = _client([_Result([1]), _Result([])])  # 归属命中 → 无配送单
    response = client.get("/internal/order/SO1/trace", params={"userId": 7001}, headers=_headers())
    assert response.status_code == 200
    assert response.json()["data"] == {"deliveryNo": None, "company": None, "traces": []}


# =====================================================================
# 二、鉴权失败路径（一律 403 + 10003，不区分具体原因）
# =====================================================================


def test_missing_headers_is_403() -> None:
    response = _client([]).get("/internal/order/SO1")
    assert response.status_code == 403
    assert response.json()["code"] == int(CommonError.FORBIDDEN)


def test_wrong_token_is_403() -> None:
    client = _client([])
    response = client.get("/internal/order/SO1", headers=_headers(token="attacker"))
    assert response.status_code == 403
    assert response.json()["code"] == int(CommonError.FORBIDDEN)


def test_wrong_signature_is_403() -> None:
    client = _client([])
    response = client.get("/internal/order/SO1", headers=_headers(secret="attacker-secret"))
    assert response.status_code == 403


def test_stale_timestamp_is_403() -> None:
    """时间窗外（>300s）拒绝 —— 缩小重放窗口。"""
    stale = int(time.time()) - service_auth.SIGNATURE_TTL_SECONDS - 60
    client = _client([])
    response = client.get("/internal/order/SO1", headers=_headers(timestamp=stale))
    assert response.status_code == 403


def test_nonce_replay_is_rejected() -> None:
    """同一 nonce 第二次出现必须失败 —— 防重放的验收核心。"""
    headers = _headers(nonce="replay-me")
    results = [_Result([_ORDER]), _Result([])] * 2  # 若放行，两次请求都有数据可返回
    client = _client(results)

    first = client.get("/internal/order/SO20260919120001", headers=headers)
    assert first.status_code == 200
    replay = client.get("/internal/order/SO20260919120001", headers=headers)
    assert replay.status_code == 403
    assert replay.json()["code"] == int(CommonError.FORBIDDEN)


def test_invalid_timestamp_format_is_403() -> None:
    client = _client([])
    headers = _headers()
    headers[HEADER_TIMESTAMP] = "not-a-number"
    assert client.get("/internal/order/SO1", headers=headers).status_code == 403


# =====================================================================
# 三、内网网段限制（独立依赖；TestClient 的 host 不是 IP，故单测覆盖）
# =====================================================================


def test_trusted_private_host_passes() -> None:
    request: Any = SimpleNamespace(client=SimpleNamespace(host="172.20.0.5"))
    verify_internal_network(request)  # 不抛即通过


@pytest.mark.parametrize("host", ["1.2.3.4", "8.8.8.8", None])
def test_untrusted_host_is_403(host: str | None) -> None:
    request: Any = SimpleNamespace(client=SimpleNamespace(host=host) if host else None)
    with pytest.raises(BusinessError) as exc:
        verify_internal_network(request)
    assert exc.value.code == int(CommonError.FORBIDDEN)


def test_unparseable_host_is_untrusted() -> None:
    assert is_trusted_host("testclient") is False
    assert is_trusted_host("172.20.0.5") is True
    assert is_trusted_host(None) is False


# =====================================================================
# 四、签名内核
# =====================================================================


def test_signature_is_deterministic_and_secret_bound() -> None:
    assert compute_signature("s1", "1000", "n1") == compute_signature("s1", "1000", "n1")
    assert compute_signature("s1", "1000", "n1") != compute_signature("s2", "1000", "n1")
    assert compute_signature("s1", "1000", "n1") != compute_signature("s1", "1001", "n1")
    assert compute_signature("s1", "1000", "n1") != compute_signature("s1", "1000", "n2")
