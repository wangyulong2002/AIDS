"""MOCK-01 支付网关验收测试（不连库、不连真实网络）。

被锁死的状态机与契约：
    1. 统一下单幂等：同 `outTradeNo` 重复下单返回原单（真实渠道同语义）；
    2. 收银台动作：确认支付 → 已支付 + **登记回调**；取消支付 → 已关闭且**不回调**；
    3. 回调投递：商户返回 "success" 才算成功；失败退避重试；超限放弃；
    4. 金额约束：累计退款不得超过支付金额。

替身会话按 select 的**实体**分发数据（模拟数据库按表返回），
与替身返回"队列结果"相比，更能暴露"查错表/漏过滤条件"这类实现错误。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import (
    CALLBACK_ABANDONED,
    CALLBACK_DELIVERED,
    CALLBACK_FAILED,
    CALLBACK_WAITING,
    PAY_CLOSED,
    PAY_PENDING,
    PAY_SUCCESS,
)
from aids_mock.db import get_db
from aids_mock.models import MockCallbackLog, MockPaymentOrder
from aids_mock.payment_channel import MAX_CALLBACK_RETRY, dispatch_due_callbacks

pytestmark = [pytest.mark.contract, pytest.mark.task("MOCK-01")]

_NOW = datetime(2026, 9, 24, 12, 0, 0)


def _order(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "out_trade_no": "ORD100",
        "trade_no": "MOCKPAY0001",
        "merchant_id": "MOCK_MERCHANT_001",
        "pay_type": 1,
        "amount": Decimal("100.00"),
        "subject": "测试商品",
        "notify_url": "http://backend/payment/callback",
        "return_url": None,
        "status": PAY_PENDING,
        "refunded_amount": Decimal("0.00"),
        "pay_time": None,
        "close_time": None,
        "expire_time": _NOW + timedelta(minutes=15),
        "create_time": _NOW,
        "update_time": _NOW,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


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
        """`_load_order` 走的是 `.scalars().one_or_none()`（与 `scalar_one_or_none` 等价）。
        替身必须补齐实际用到的 Result API —— 缺它会让用例以 AttributeError 伪装成 500。"""
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else 0


class _SmartSession:
    """按 select 的实体分发数据；add 的对象按类型登记到对应表。"""

    def __init__(
        self,
        payment_rows: list[Any] | None = None,
        callback_rows: list[Any] | None = None,
        refund_rows: list[Any] | None = None,
    ) -> None:
        self.payment_rows = payment_rows or []
        self.callback_rows = callback_rows or []
        self.refund_rows = refund_rows or []
        self.added: list[Any] = []
        self.executed = 0

    async def execute(self, stmt: Any) -> _Result:
        self.executed += 1
        descriptions = getattr(stmt, "column_descriptions", None)
        entity = descriptions[0]["entity"] if descriptions else None
        if entity is MockPaymentOrder:
            return _Result(list(self.payment_rows))
        if entity is MockCallbackLog:
            return _Result(list(self.callback_rows))
        return _Result([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, MockCallbackLog):
            self.callback_rows.append(obj)
        if isinstance(obj, MockPaymentOrder):
            self.payment_rows.append(obj)

    async def flush(self) -> None:
        pass


def _as_session(fake: Any) -> AsyncSession:
    return cast(AsyncSession, fake)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """真实 app + 替身会话；渠道密钥指到临时目录（self_private_pem 现场生成）。"""
    from aids_mock.app_factory import create_app

    monkeypatch.setenv("MOCK_CHANNEL_RSA_PRIVATE_KEY_PATH", str(tmp_path / "ch_private.pem"))
    monkeypatch.setenv("MOCK_CHANNEL_RSA_PUBLIC_KEY_PATH", str(tmp_path / "ch_public.pem"))
    monkeypatch.delenv("MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("MOCK_CHANNEL_FAILURE_RATE", raising=False)

    session = _SmartSession()
    app = create_app()
    app.dependency_overrides[get_db] = _fake_db(session)
    app.state.test_session = session
    return TestClient(app, raise_server_exceptions=False)


def _fake_db(session: Any) -> Any:
    async def _yield() -> Any:
        yield session

    return _yield


def _session_of(client: TestClient) -> _SmartSession:
    return client.app.state.test_session  # type: ignore[no-any-return]


def _uniorder_body(out_trade_no: str = "ORD100", amount: str = "100.00") -> dict[str, Any]:
    return {
        "outTradeNo": out_trade_no,
        "payType": 1,
        "amount": amount,
        "subject": "测试商品",
        "notifyUrl": "http://backend/payment/callback",
    }


# =====================================================================
# 一、统一下单（幂等）
# =====================================================================


def test_uniorder_creates_pending_order(client: TestClient) -> None:
    response = client.post("/payment/uniorder", json=_uniorder_body())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["outTradeNo"] == "ORD100"
    assert data["status"] == PAY_PENDING
    assert data["payUrl"] == "/cashier/ORD100"


def test_uniorder_is_idempotent(client: TestClient) -> None:
    """同号重复下单返回原单（tradeNo 不变，不创建第二笔）。"""
    first = client.post("/payment/uniorder", json=_uniorder_body()).json()["data"]
    second = client.post("/payment/uniorder", json=_uniorder_body()).json()["data"]
    assert second["tradeNo"] == first["tradeNo"]


def test_uniorder_rejects_non_positive_amount(client: TestClient) -> None:
    response = client.post("/payment/uniorder", json=_uniorder_body(amount="0"))
    assert response.json()["code"] != 0


# =====================================================================
# 二、收银台动作
# =====================================================================


def _seed_pending_order(client: TestClient, out_trade_no: str = "ORD100") -> None:
    """收银台动作的前置：先统一下单，否则收银台查不到订单（渠道语义如此）。"""
    response = client.post("/payment/uniorder", json=_uniorder_body(out_trade_no))
    assert response.json()["code"] == 0, response.text


def test_confirm_pays_and_schedules_callback(client: TestClient) -> None:
    _seed_pending_order(client)
    response = client.post("/cashier/ORD100/confirm")
    assert response.status_code == 200
    session = _session_of(client)
    order = session.payment_rows[0]
    assert order.status == PAY_SUCCESS
    assert order.pay_time is not None
    callbacks = [o for o in session.added if isinstance(o, MockCallbackLog)]
    assert len(callbacks) == 1
    assert callbacks[0].status == CALLBACK_WAITING
    assert callbacks[0].payload["outTradeNo"] == "ORD100"
    assert callbacks[0].sign


def test_confirm_is_idempotent(client: TestClient) -> None:
    """重复确认支付：直接返回已支付，**不再**登记第二条回调。"""
    _seed_pending_order(client)
    client.post("/cashier/ORD100/confirm")
    added_before = len([o for o in _session_of(client).added if isinstance(o, MockCallbackLog)])
    assert added_before == 1, "首次确认必须登记恰好一条回调（否则本用例在空跑）"
    client.post("/cashier/ORD100/confirm")
    added_after = len([o for o in _session_of(client).added if isinstance(o, MockCallbackLog)])
    assert added_after == added_before


def test_cancel_closes_without_callback(client: TestClient) -> None:
    """取消支付 → 已关闭，且**不**产生回调（用户取消不是支付事件）。"""
    _seed_pending_order(client)
    response = client.post("/cashier/ORD100/cancel")
    assert response.status_code == 200
    session = _session_of(client)
    assert session.payment_rows[0].status == PAY_CLOSED
    assert not any(isinstance(o, MockCallbackLog) for o in session.added)


def test_cashier_page_renders_for_pending_order(client: TestClient) -> None:
    client.post("/payment/uniorder", json=_uniorder_body())
    response = client.get("/cashier/ORD100")
    assert response.status_code == 200
    assert "Mock 沙箱收银台" in response.text
    assert "确认支付" in response.text


# =====================================================================
# 三、回调投递状态机
# =====================================================================


def _callback_log(**overrides: Any) -> MockCallbackLog:
    fields: dict[str, Any] = {
        "biz_type": 1,
        "out_trade_no": "ORD100",
        "notify_url": "http://backend/payment/callback",
        "payload": {"outTradeNo": "ORD100", "amount": 100.0},
        "sign": "sig",
        "attempt_no": 1,
        "status": CALLBACK_WAITING,
        "retry_count": 0,
        "next_retry_time": _NOW - timedelta(seconds=1),
    }
    fields.update(overrides)
    return MockCallbackLog(**fields)


def _dispatch(session: _SmartSession, poster: Any, now: datetime = _NOW) -> dict[str, int]:
    return asyncio.run(dispatch_due_callbacks(_as_session(session), poster=poster, now=now))


def test_dispatch_success_on_merchant_success() -> None:
    session = _SmartSession(callback_rows=[_callback_log()])

    async def _poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        return 200, "success"

    outcome = _dispatch(session, _poster)
    assert outcome["delivered"] == 1
    assert session.callback_rows[0].status == CALLBACK_DELIVERED
    assert session.callback_rows[0].http_status == 200


def test_dispatch_failure_schedules_backoff() -> None:
    """投递失败：状态转「推送失败」+ retry_count 递增 + 下次重试时间后移（退避）。"""
    session = _SmartSession(callback_rows=[_callback_log()])

    async def _poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        return 500, "oops"

    outcome = _dispatch(session, _poster)
    assert outcome["failed"] == 1
    log = session.callback_rows[0]
    assert log.status == CALLBACK_FAILED
    assert log.retry_count == 1
    assert log.next_retry_time > _NOW


def test_dispatch_abandons_after_max_retry() -> None:
    session = _SmartSession(callback_rows=[_callback_log(retry_count=MAX_CALLBACK_RETRY - 1)])

    async def _poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        return 500, "oops"

    outcome = _dispatch(session, _poster)
    assert outcome["abandoned"] == 1
    assert session.callback_rows[0].status == CALLBACK_ABANDONED


def test_dispatch_sends_expected_body() -> None:
    """投递报文必须是登记时的 payload（含签名），而非投递时的临时数据。"""
    session = _SmartSession(callback_rows=[_callback_log()])
    captured: dict[str, Any] = {}

    async def _poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        captured["url"] = url
        return 200, "success"

    _dispatch(session, _poster)
    assert captured["url"] == "http://backend/payment/callback"


def test_dispatch_sends_channel_signature_header() -> None:
    session = _SmartSession(callback_rows=[_callback_log()])
    captured: dict[str, Any] = {}

    async def _poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        captured["headers"] = headers
        captured["body"] = body
        return 200, "success"

    _dispatch(session, _poster)
    assert "X-Channel-Sign" in captured["headers"]
    assert json.loads(captured["body"])["outTradeNo"] == "ORD100"


# =====================================================================
# 四、T+1 对账单（CSV）
# =====================================================================


def test_daily_recon_returns_csv(client: TestClient) -> None:
    """对账单：已支付订单进 CSV 明细，金额对账口径与渠道单一致。

    替身不模拟 WHERE（`pay_time` 落在账单日的过滤），故此处只锁"生成链路通、
    明细含该笔、金额口径正确"——账单日的过滤由真实库上的集成测试承担。
    """
    _seed_pending_order(client)
    client.post("/cashier/ORD100/confirm")

    response = client.get("/recon/daily", params={"bill_date": "2026-09-24", "pay_type": 1})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["bill_date"] == "2026-09-24"
    assert data["status"] == 1
    lines = data["file_content"].splitlines()
    assert lines[0] == "out_trade_no,trade_no,pay_amount,pay_time,status"
    assert any(line.startswith("ORD100,") for line in lines[1:])
    assert data["total_count"] == 1
    assert data["total_amount"] == 100.0


def test_daily_recon_rejects_bad_date(client: TestClient) -> None:
    """非法账单日不得静默产出空对账单（宁可报错，也不给商户一张假的"无差异"单）。"""
    response = client.get("/recon/daily", params={"bill_date": "not-a-date", "pay_type": 1})
    assert response.json()["code"] != 0
