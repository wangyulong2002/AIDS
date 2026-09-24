"""Mock 物流服务路由（MOCK-03）。

核心语义（TASKS MOCK-03）：「轨迹按时间自动推进生成」——
    查询轨迹时，按"自运单创建起经过的时间"把应发生的节点**补齐**落库
    （`uk(delivery_no, trace_time)` 天然幂等），模拟真实快递的节点流：
        待揽收(0min) → 已揽收(2min) → 运输中(10min) → 派送中(30min) → 已签收(60min)
    「下单后立刻查」与「一小时后查」得到合理不同的轨迹长度；
    每次查询把运单状态推进到已到达的最后一个节点。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import (
    LOGISTICS_DELIVERING,
    LOGISTICS_IN_TRANSIT,
    LOGISTICS_PICKUP_PENDING,
    LOGISTICS_SIGNED,
)
from aids_mock.db import get_db
from aids_mock.models import MockLogisticsOrder, MockLogisticsTrace
from app.core.exceptions import BusinessError
from app.core.response import ApiResponse, ok

router = APIRouter(prefix="/logistics", tags=["Mock 物流服务"])

COMPANY_CODES: dict[str, str] = {
    "SF": "顺丰速运",
    "YTO": "圆通速递",
    "ZTO": "中通快递",
    "JD": "京东物流",
}

# 轨迹节点（自运单创建起的分钟偏移, 到达后的运单状态, 描述文案）—— 按偏移升序
_TRACE_NODES: tuple[tuple[int, int, str], ...] = (
    (0, LOGISTICS_PICKUP_PENDING, "商家已下单，等待揽收"),
    (2, LOGISTICS_IN_TRANSIT, "快递员已揽收"),
    (10, LOGISTICS_IN_TRANSIT, "包裹已到达转运中心"),
    (30, LOGISTICS_DELIVERING, "包裹正在派送中"),
    (60, LOGISTICS_SIGNED, "包裹已签收"),
)


class WaybillCreateRequest(BaseModel):
    companyCode: str = Field(min_length=2, max_length=8)
    senderCity: str | None = None
    receiverCity: str | None = None
    deliveryNo: str | None = Field(default=None, max_length=64, description="不传则由 Mock 生成")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/waybill", summary="创建运单")
async def create_waybill(
    payload: WaybillCreateRequest, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    if payload.companyCode not in COMPANY_CODES:
        raise BusinessError.not_found(f"不支持的快递公司：{payload.companyCode}")

    delivery_no = payload.deliveryNo or f"MOCK{uuid.uuid4().hex[:20].upper()}"
    exists = (
        (
            await session.execute(
                select(MockLogisticsOrder).where(MockLogisticsOrder.delivery_no == delivery_no)
            )
        )
        .scalars()
        .one_or_none()
    )
    if exists is not None:
        raise BusinessError.not_found("运单号已存在")

    waybill = MockLogisticsOrder(
        delivery_no=delivery_no,
        company_code=payload.companyCode,
        company_name=COMPANY_CODES[payload.companyCode],
        sender_city=payload.senderCity,
        receiver_city=payload.receiverCity,
        status=LOGISTICS_PICKUP_PENDING,
    )
    session.add(waybill)
    await session.flush()

    # 揽收节点立即落库（后续节点由查询时的"时间推进"补齐）
    session.add(
        MockLogisticsTrace(
            delivery_no=delivery_no,
            trace_time=_utcnow(),
            status_desc=_TRACE_NODES[0][2],
            location=payload.senderCity,
        )
    )
    return ok({"deliveryNo": delivery_no, "companyName": waybill.company_name})


async def _load_waybill(session: AsyncSession, delivery_no: str) -> MockLogisticsOrder | None:
    return (
        (
            await session.execute(
                select(MockLogisticsOrder).where(MockLogisticsOrder.delivery_no == delivery_no)
            )
        )
        .scalars()
        .one_or_none()
    )


async def _advance_traces(
    session: AsyncSession, waybill: MockLogisticsOrder
) -> list[MockLogisticsTrace]:
    """把"当前时刻应发生"的节点补齐落库，返回全部轨迹（按时间升序）。"""
    existing = {
        t.trace_time: t
        for t in (
            (
                await session.execute(
                    select(MockLogisticsTrace).where(
                        MockLogisticsTrace.delivery_no == waybill.delivery_no
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    now = _utcnow()
    created_at = waybill.create_time or now

    for offset_minutes, status, desc in _TRACE_NODES:
        trace_time = created_at + timedelta(minutes=offset_minutes)
        if trace_time > now or trace_time in existing:
            continue
        trace = MockLogisticsTrace(
            delivery_no=waybill.delivery_no,
            trace_time=trace_time,
            status_desc=desc,
            location=waybill.receiver_city,
        )
        session.add(trace)
        existing[trace_time] = trace
        waybill.status = status  # 节点按偏移升序遍历，最后一个到达的节点即当前状态

    return sorted(existing.values(), key=lambda t: t.trace_time)


@router.get("/trace/{delivery_no}", summary="查询运单轨迹（自动推进）")
async def query_trace(
    delivery_no: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    waybill = await _load_waybill(session, delivery_no)
    if waybill is None:
        raise BusinessError.not_found("运单不存在")

    traces = await _advance_traces(session, waybill)
    return ok(
        {
            "deliveryNo": waybill.delivery_no,
            "companyName": waybill.company_name,
            "status": waybill.status,
            "traces": [
                {
                    "time": t.trace_time.isoformat() if t.trace_time else None,
                    "desc": t.status_desc,
                    "location": t.location,
                }
                for t in traces
            ],
        }
    )
