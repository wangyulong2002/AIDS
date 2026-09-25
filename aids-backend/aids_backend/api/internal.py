"""内部服务接口（API.md §四，BE-06）—— 仅供 AI 服务内网调用的查询接口。

信任边界（务必分清，两套正交的鉴权）：
    - **服务间**：本文件的两个依赖 —— `require_internal`（静态 Token + 签名 +
      nonce 防重放）与 `verify_internal_network`（内网网段限制），见
      `app/core/service_auth.py`；
    - **用户**：AI 服务对它的用户验 JWT 后，把 userId 作为查询参数传入
      （PRD §9.3 / API.md §四 安全要求：「userId 由 FastAPI 从用户 JWT 解析后
      传入，禁止从对话内容中提取」）。userId 在这里是**业务入参**而非凭据。

**但"业务入参"不等于"可以不校验"**（2026-09-24 加固）：本文件**每个**接口都把
userId 写进 WHERE 条件（`order_no = ? AND user_id = ?`），查不到一律 10004 且
不区分"不存在"与"不是你的"。原先只有 `order_list` 这么做，
`order/{orderNo}` / `{orderNo}/trace` / `refund/{refundNo}` 是只按业务号查的 ——
那与 BE-04「资源访问一律 `WHERE id=? AND user_id=?`」的硬约束相悖：
只要 orderNo 泄漏或被猜到，就能跨用户读取。AI-11 的验收
（「仅限当前用户本人订单」）必须落在**服务端**，不能只靠调用方自觉。

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
    orderNo: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Query(alias="userId", gt=0, description="AI 服务从其用户的 JWT 解出")],
) -> ApiResponse:
    """订单详情（**必须带 userId**，越权查不到就是查不到）。

    为什么这三个"按业务号查"的接口也要带 userId（2026-09-24 加固）：
        `order_list` 一直是按 userId 过滤的，但 `{orderNo}` / `{orderNo}/trace` /
        `refund/{refundNo}` 原先只按业务号查 —— 而 BE-04 的硬约束是
        「资源访问一律 `WHERE id=? AND user_id=?`」。少了这一条，只要 orderNo
        被猜到或从别处泄漏，就能跨用户读到他人订单（受服务间签名 + 内网限制兜底，
        但那是"网络边界"而不是"数据权限"）。
        AI-11 的验收写的是「仅限当前用户本人订单」，把这条约束放在**服务端**才算数。

    语义与 BE-04 一致：**「不存在」与「不是你的」不可区分** —— 两者都返回**同一个**
    业务码 10004（HTTP 状态按项目统一响应约定仍为 200），不给探测者任何反馈。
    """
    order = (
        (
            await session.execute(
                select(BizOrder).where(BizOrder.order_no == orderNo, BizOrder.user_id == user_id)
            )
        )
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
    orderNo: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Query(alias="userId", gt=0)],
) -> ApiResponse:
    """未发货返回空轨迹 —— 对 AI 场景"还没发货"是**正常答案**，不是 404 错误。

    但「订单不是这个用户的」是**另一个**答案，必须先判归属（返回 10004），
    否则"未发货"会变成探测他人订单是否存在的旁路。
    """
    owned = (
        await session.execute(
            select(BizOrder.id).where(BizOrder.order_no == orderNo, BizOrder.user_id == user_id)
        )
    ).scalar_one_or_none()
    if owned is None:
        raise BusinessError.not_found("订单不存在")

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
    refundNo: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Query(alias="userId", gt=0)],
) -> ApiResponse:
    """售后进度（同样必须带 userId，见 `order_detail` 的说明）。"""
    refund = (
        (
            await session.execute(
                select(BizRefund).where(
                    BizRefund.refund_no == refundNo, BizRefund.user_id == user_id
                )
            )
        )
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
