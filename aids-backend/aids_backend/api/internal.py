"""内部服务接口（API.md §四，BE-06）—— 仅供 AI 服务内网调用的查询接口。

信任边界（务必分清，两套正交的鉴权）：
    - **服务间**：本文件的两个依赖 —— `require_internal`（静态 Token + 签名 +
      nonce 防重放）与 `verify_internal_network`（内网网段限制），见
      `app/core/service_auth.py`；
    - **用户**：AI 服务对它的用户验 JWT 后，把 userId 作为查询参数传入
      （PRD §9.3 / API.md §四 安全要求：「userId 由 FastAPI 从用户 JWT 解析后
      传入，禁止从对话内容中提取」）。userId 在这里是**业务入参**而非凭据。

脱敏（验收硬要求）：
    本接口面向 AI 大模型的上下文组装，返回内容会进入提示词 ——
    `receiver_phone`（AES 密文）**永远不出现在响应里**；`receiver_addr` 只回
    省市区前缀 + 掩码（详细地址须脱敏，PRD §4.3）。

`POST /internal/kb/notify`（知识库变更通知）的调用方是主业务服务、接收方是
AI 服务，属 **AI 服务侧**的路由（T5 随知识库机制落地）；异步广播走 Kafka
`kb.index.rebuild`（API.md §六），不在本文件。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.response import ApiResponse, ok
from app.core.service_auth import require_internal, verify_internal_network
from app.domain.enums import OrderStatus
from app.models.biz import (
    BizDelivery,
    BizDeliveryTrace,
    BizOrder,
    BizOrderItem,
    BizRefund,
    BizSpu,
)
from app.orm.session import get_db

router = APIRouter(
    prefix="/internal",
    tags=["内部服务"],
    dependencies=[Depends(require_internal), Depends(verify_internal_network)],
)


def _status_name(status: int) -> str:
    """状态数字 → 枚举名（AI 与前端展示都用名字；未知值原样返回不抛错）。"""
    try:
        return OrderStatus(status).name
    except ValueError:
        return str(status)


def _mask_address(addr: str) -> str:
    """详细地址脱敏：保留前 9 字符（省市区）+ 掩码（PRD §4.3 详细地址须脱敏）。"""
    if len(addr) <= 9:
        return "****"
    return f"{addr[:9]}****"


def _order_payload(order: BizOrder, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "orderNo": order.order_no,
        "status": order.status,
        "statusName": _status_name(order.status),
        "payAmount": float(order.pay_amount),
        "createTime": order.create_time.isoformat() if order.create_time else None,
        "items": items,
    }


@router.get("/order/list", summary="按用户查订单列表")
async def order_list(
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Query(alias="userId", gt=0, description="AI 服务从其用户的 JWT 解出")],
    status: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(alias="pageNum", ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> ApiResponse:
    """按用户查订单列表（AI 客服"我的订单"类问答的数据源）。"""
    base = select(BizOrder).where(BizOrder.user_id == user_id)
    if status is not None:
        base = base.where(BizOrder.status == status)

    total = int(
        (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    )
    orders = (
        (
            await session.execute(
                base.order_by(BizOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    items_by_order: dict[str, list[dict[str, Any]]] = {}
    if orders:
        item_rows = (
            (
                await session.execute(
                    select(BizOrderItem).where(
                        BizOrderItem.order_no.in_([o.order_no for o in orders])
                    )
                )
            )
            .scalars()
            .all()
        )
        for item in item_rows:
            items_by_order.setdefault(item.order_no, []).append(
                {"spuName": item.spu_name, "quantity": item.quantity}
            )

    return ok(
        {
            "total": total,
            "list": [
                _order_payload(order, items_by_order.get(order.order_no, [])) for order in orders
            ],
        }
    )


@router.get("/order/{orderNo}", summary="订单详情")
async def order_detail(
    orderNo: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    order = (
        (await session.execute(select(BizOrder).where(BizOrder.order_no == orderNo)))
        .scalars()
        .one_or_none()
    )
    if order is None:
        raise BusinessError.not_found("订单不存在")

    item_rows = (
        (await session.execute(select(BizOrderItem).where(BizOrderItem.order_no == orderNo)))
        .scalars()
        .all()
    )
    payload = _order_payload(
        order,
        [{"spuName": i.spu_name, "quantity": i.quantity} for i in item_rows],
    )
    # 脱敏（验收硬要求）：收货人姓名可回；电话密文与详细地址**不回原文**
    payload["receiver"] = order.receiver
    payload["receiverAddr"] = _mask_address(order.receiver_addr)
    payload["deliveryCompany"] = order.delivery_company
    payload["deliveryNo"] = order.delivery_no
    return ok(payload)


@router.get("/order/{orderNo}/trace", summary="物流轨迹")
async def order_trace(
    orderNo: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    """未发货返回空轨迹 —— 对 AI 场景"还没发货"是**正常答案**，不是 404 错误。"""
    delivery = (
        (await session.execute(select(BizDelivery).where(BizDelivery.order_no == orderNo)))
        .scalars()
        .one_or_none()
    )
    if delivery is None:
        return ok({"deliveryNo": None, "company": None, "traces": []})

    traces = (
        (
            await session.execute(
                select(BizDeliveryTrace)
                .where(BizDeliveryTrace.delivery_id == delivery.id)
                .order_by(BizDeliveryTrace.trace_time)
            )
        )
        .scalars()
        .all()
    )
    return ok(
        {
            "deliveryNo": delivery.delivery_no,
            "company": delivery.company_name,
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


@router.get("/refund/{refundNo}", summary="售后进度")
async def refund_detail(
    refundNo: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    refund = (
        (await session.execute(select(BizRefund).where(BizRefund.refund_no == refundNo)))
        .scalars()
        .one_or_none()
    )
    if refund is None:
        raise BusinessError.not_found("售后单不存在")
    return ok(
        {
            "refundNo": refund.refund_no,
            "orderNo": refund.order_no,
            "type": refund.type,
            "amount": float(refund.amount),
            "freightAmount": float(refund.freight_amount),
            "status": refund.status,
            "reason": refund.reason,
            "auditOpinion": refund.audit_opinion,
            "createTime": refund.create_time.isoformat() if refund.create_time else None,
        }
    )


@router.get("/product/{spuId}", summary="商品详情（供知识库同步）")
async def product_detail(
    spuId: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    spu = (await session.execute(select(BizSpu).where(BizSpu.id == spuId))).scalars().one_or_none()
    if spu is None:
        raise BusinessError.not_found("商品不存在")
    return ok(
        {
            "spuId": spu.id,
            "name": spu.name,
            "subtitle": spu.subtitle,
            "mainPic": spu.main_pic,
            "detail": spu.detail,
        }
    )
