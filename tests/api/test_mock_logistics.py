"""MOCK-03 物流服务验收测试。

核心语义：「轨迹按时间自动推进」—— 查询时按"自运单创建起经过的时间"补齐节点，
运单状态推进到最后一个已到达的节点；`uk(delivery_no, trace_time)` 保证补齐幂等。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import LOGISTICS_SIGNED
from aids_mock.db import get_db
from aids_mock.models import MockLogisticsOrder, MockLogisticsTrace

pytestmark = [pytest.mark.contract, pytest.mark.task("MOCK-03")]

_NOW = datetime(2026, 9, 24, 12, 0, 0)


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


class _SmartSession:
    """按 select 的实体分发：MockLogisticsOrder → 运单；MockLogisticsTrace → 已有轨迹。"""

    def __init__(self, waybill: SimpleNamespace | None = None) -> None:
        self.waybill = waybill
        self.traces: list[MockLogisticsTrace] = []
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        entity = None
        if stmt.column_descriptions:
            entity = stmt.column_descriptions[0]["entity"]
        if entity is MockLogisticsOrder:
            return _Result([self.waybill] if self.waybill else [])
        if entity is MockLogisticsTrace:
            return _Result(list(self.traces))
        return _Result([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, MockLogisticsTrace):
            self.traces.append(obj)

    async def flush(self) -> None:
        pass


def _client(waybill: SimpleNamespace | None = None) -> tuple[TestClient, _SmartSession]:
    from aids_mock.app_factory import create_app

    session = _SmartSession(waybill)
    app = create_app()
    app.dependency_overrides[get_db] = _fake_db(session)
    return TestClient(app, raise_server_exceptions=False), session


def _fake_db(session: _SmartSession) -> Any:
    async def _yield() -> Any:
        yield cast(AsyncSession, session)

    return _yield


def _waybill(created_minutes_ago: int = 0, **overrides: Any) -> SimpleNamespace:
    """构造运单替身。`overrides` 用于单个用例指定运单号等字段（如重复单号用例）。"""
    fields: dict[str, Any] = {
        "delivery_no": "MOCKTEST0001",
        "company_code": "SF",
        "company_name": "顺丰速运",
        "sender_city": "深圳",
        "receiver_city": "杭州",
        "status": 0,
        "create_time": datetime.now(UTC).replace(tzinfo=None)
        - timedelta(minutes=created_minutes_ago),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_create_waybill() -> None:
    client, session = _client()
    response = client.post(
        "/logistics/waybill",
        json={"companyCode": "SF", "senderCity": "深圳", "receiverCity": "杭州"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["deliveryNo"].startswith("MOCK")
    assert len(session.added) == 2  # 运单 + 揽收轨迹节点


def test_create_waybill_rejects_unknown_company() -> None:
    client, _ = _client()
    response = client.post("/logistics/waybill", json={"companyCode": "XX"})
    assert response.json()["code"] != 0


def test_create_waybill_rejects_duplicate_no() -> None:
    client, _ = _client(_waybill(delivery_no="MOCKEXIST"))
    response = client.post(
        "/logistics/waybill", json={"companyCode": "SF", "deliveryNo": "MOCKEXIST"}
    )
    assert response.json()["code"] != 0


def test_trace_advances_with_elapsed_time() -> None:
    """创建于 61 分钟前 → 5 个节点全部补齐，运单状态推进到"已签收"。"""
    client, session = _client(_waybill(created_minutes_ago=61))
    response = client.get("/logistics/trace/MOCKTEST0001")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["traces"]) == 5
    assert data["status"] == LOGISTICS_SIGNED
    assert data["traces"][-1]["desc"] == "包裹已签收"


def test_trace_advancement_is_idempotent() -> None:
    """已有轨迹的时间点不再重复落库（uk 幂等）。"""
    client, session = _client(_waybill(created_minutes_ago=61))
    client.get("/logistics/trace/MOCKTEST0001")
    added_first = len(session.added)

    client.get("/logistics/trace/MOCKTEST0001")  # 再次查询：无新节点可补
    assert len(session.added) == added_first


def test_trace_partial_advancement() -> None:
    """创建于 5 分钟前 → 只到 0/2 分钟两个节点，10 分钟节点尚未到达。"""
    client, _ = _client(_waybill(created_minutes_ago=5))
    response = client.get("/logistics/trace/MOCKTEST0001")
    data = response.json()["data"]
    assert len(data["traces"]) == 2  # 0 / 2 分钟节点（10 分钟节点在将来，不补齐）
    assert data["status"] != LOGISTICS_SIGNED


def test_trace_unknown_waybill_is_404_style_error() -> None:
    client, _ = _client()
    response = client.get("/logistics/trace/NOPE")
    assert response.json()["code"] != 0
