"""Mock 支付网关路由（MOCK-01）—— 渠道视角的 HTTP 面。

报文契约（v0-draft，依据 TASKS 的 T1 定稿指引：先按「RSA2 + 统一响应体」出
最小可用版本，T3 期间只允许向后兼容地加字段）：

    商户请求头：X-Timestamp / X-Nonce / X-Sign（RSA2，验签用商户公钥；
                未配置公钥时显式降级跳过并告警 —— v0-draft，见 rsa/payment_channel）
    回调：POST notify_url，body 为统一响应体包着的报文，头带 X-Channel-Sign；
          商户返回字面量 "success" 视为投递成功。

收银台（`/cashier/{out_trade_no}`）：模拟"用户"的三个操作 —— 确认支付 / 取消 /
超时关单。它是**演示与故障注入的入口**（PRD §8.3），不是给前端集成的页面。
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import PAY_CLOSED, PAY_PENDING, PAY_SUCCESS
from aids_mock.db import get_db
from aids_mock.models import MockPaymentOrder, MockReconFile
from aids_mock.payment_channel import (
    MockPaymentChannel,
    _utcnow,
    cancel_pay,
    confirm_pay,
    dispatch_due_callbacks,
    self_private_pem,
)
from aids_mock.rsa import verify_rsa2
from app.core.exceptions import BusinessError
from app.core.response import ApiResponse, ok

router = APIRouter(tags=["Mock 支付网关"])


def _channel() -> MockPaymentChannel:
    return MockPaymentChannel(self_private_pem())


def _require_merchant_signed(request: Request, body: bytes) -> None:
    """验商户请求签名（v0-draft：未配置商户公钥时显式跳过并告警）。"""
    from aids_mock.payment_channel import merchant_public_pem

    public_pem = merchant_public_pem()
    if public_pem is None:
        return
    timestamp = request.headers.get("X-Timestamp", "")
    nonce = request.headers.get("X-Nonce", "")
    sign = request.headers.get("X-Sign", "")
    if not verify_rsa2(public_pem, body, timestamp, nonce, sign):
        raise BusinessError.forbidden("Mock 渠道：商户请求验签失败")


def _decimal(value: str, field: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise BusinessError.not_found(f"{field} 金额不合法") from exc
    if amount <= 0:
        raise BusinessError.not_found(f"{field} 金额必须大于 0")
    return amount


# =====================================================================
# 渠道 API（商户 → 渠道）
# =====================================================================


@router.post("/payment/uniorder", summary="统一下单")
async def uniorder(
    request: Request, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    body = await request.body()
    _require_merchant_signed(request, body)
    payload = json.loads(body or b"{}")

    try:
        order = await _channel().create_payment(
            session,
            out_trade_no=str(payload["outTradeNo"]),
            pay_type=int(payload.get("payType", 1)),
            amount=_decimal(str(payload["amount"]), "amount"),
            subject=str(payload.get("subject", "Mock 订单")),
            notify_url=str(payload["notifyUrl"]),
            return_url=payload.get("returnUrl"),
            expire_minutes=int(payload.get("expireMinutes", 15)),
        )
    except KeyError as exc:
        raise BusinessError.not_found(f"缺少必填字段：{exc}") from exc

    return ok(
        {
            "outTradeNo": order.out_trade_no,
            "tradeNo": order.trade_no,
            "payUrl": f"/cashier/{order.out_trade_no}",
            "status": order.status,
            "amount": float(order.amount),
        }
    )


@router.get("/payment/query", summary="主动查询订单状态")
async def query_payment(
    session: Annotated[AsyncSession, Depends(get_db)],
    out_trade_no: Annotated[str, Query(alias="outTradeNo")],
) -> ApiResponse:
    order = await _channel().query_payment(session, out_trade_no=out_trade_no)
    if order is None:
        raise BusinessError.not_found("订单不存在")
    return ok(
        {
            "outTradeNo": order.out_trade_no,
            "tradeNo": order.trade_no,
            "status": order.status,
            "paid": order.status == PAY_SUCCESS,
            "amount": float(order.amount),
            "refundedAmount": float(order.refunded_amount),
            "payTime": order.pay_time.isoformat() if order.pay_time else None,
        }
    )


@router.post("/payment/refund", summary="渠道退款")
async def refund(
    request: Request, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    body = await request.body()
    _require_merchant_signed(request, body)
    payload = json.loads(body or b"{}")

    try:
        refund = await _channel().refund(
            session,
            out_refund_no=str(payload["outRefundNo"]),
            out_trade_no=str(payload["outTradeNo"]),
            refund_amount=_decimal(str(payload["refundAmount"]), "refundAmount"),
            reason=payload.get("reason"),
        )
    except KeyError as exc:
        raise BusinessError.not_found(f"缺少必填字段：{exc}") from exc
    except ValueError as exc:
        raise BusinessError.not_found(str(exc)) from exc

    return ok(
        {
            "outRefundNo": refund.out_refund_no,
            "refundNo": refund.refund_no,
            "status": refund.status,
            "refundAmount": float(refund.refund_amount),
        }
    )


@router.get("/recon/daily", summary="T+1 对账单（CSV）")
async def daily_recon(
    session: Annotated[AsyncSession, Depends(get_db)],
    bill_date: Annotated[str, Query()],
    pay_type: Annotated[int, Query()] = 1,
) -> ApiResponse:
    try:
        recon = await _channel().build_daily_recon(session, bill_date=bill_date, pay_type=pay_type)
    except ValueError as exc:
        # 非法账单日宁可报错，也不返回一张"看起来无差异"的空对账单
        raise BusinessError.not_found(f"账单日期不合法：{bill_date}") from exc
    session.add(MockReconFile(**recon))
    return ok(recon)


@router.post("/callbacks/dispatch", summary="推送到期回调（演示/联调手动触发；生产由循环驱动）")
async def dispatch_callbacks(session: Annotated[AsyncSession, Depends(get_db)]) -> ApiResponse:
    outcome = await dispatch_due_callbacks(session)
    return ok(outcome)


# =====================================================================
# 沙箱收银台（模拟"用户"操作；演示与故障注入入口）
# =====================================================================


_CASHIER_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>Mock 沙箱收银台</title>
<style>body{{font-family:system-ui;max-width:420px;margin:60px auto;text-align:center}}
.box{{border:1px solid #ddd;border-radius:12px;padding:32px}}
.amount{{font-size:40px;margin:16px 0}} button{{margin:8px;padding:12px 28px;font-size:16px;
border:none;border-radius:8px;cursor:pointer}} .pay{{background:#1677ff;color:#fff}}
.cancel{{background:#eee}}</style></head>
<body><div class="box"><h2>Mock 沙箱收银台</h2>
<p>商户订单号：{out_trade_no}</p><p>{subject}</p>
<div class="amount">¥ {amount}</div>
<form method="post" action="/cashier/{out_trade_no}/confirm">
<button class="pay" type="submit">确认支付</button></form>
<form method="post" action="/cashier/{out_trade_no}/cancel">
<button class="cancel" type="submit">取消支付</button></form>
<p style="color:#999">仅演示用 —— 模拟第三方渠道的支付动作与故障注入</p>
</div></body></html>"""


async def _load_order(session: AsyncSession, out_trade_no: str) -> MockPaymentOrder:
    order = (
        (
            await session.execute(
                select(MockPaymentOrder).where(MockPaymentOrder.out_trade_no == out_trade_no)
            )
        )
        .scalars()
        .one_or_none()
    )
    if order is None:
        raise BusinessError.not_found("订单不存在")
    return order


@router.get("/cashier/{out_trade_no}", response_class=HTMLResponse, summary="沙箱收银台页面")
async def cashier_page(
    out_trade_no: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> HTMLResponse:
    order = await _load_order(session, out_trade_no)
    if order.status == PAY_CLOSED:
        return HTMLResponse("<h2>订单已关闭</h2>")
    if order.status == PAY_SUCCESS:
        return HTMLResponse("<h2>该订单已支付</h2>")
    return HTMLResponse(
        _CASHIER_PAGE.format(
            out_trade_no=order.out_trade_no, subject=order.subject, amount=order.amount
        )
    )


@router.post("/cashier/{out_trade_no}/confirm", summary="收银台：确认支付")
async def cashier_confirm(
    out_trade_no: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    order = await confirm_pay(session, _channel(), out_trade_no=out_trade_no)
    return ok({"outTradeNo": order.out_trade_no, "status": order.status})


@router.post("/cashier/{out_trade_no}/cancel", summary="收银台：取消支付")
async def cashier_cancel(
    out_trade_no: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    closed = await cancel_pay(session, _channel(), out_trade_no=out_trade_no)
    return ok({"outTradeNo": out_trade_no, "closed": closed})


@router.post("/payment/{out_trade_no}/timeout-close", summary="渠道侧超时关单（延迟任务扫描用）")
async def timeout_close(
    out_trade_no: str, session: Annotated[AsyncSession, Depends(get_db)]
) -> ApiResponse:
    """只有「待支付且已超时」才会关单；其余状态原样返回（幂等）。"""
    order = await _load_order(session, out_trade_no)
    if (
        order.status == PAY_PENDING
        and order.expire_time is not None
        and order.expire_time < _utcnow()
    ):
        order.status = PAY_CLOSED
        order.close_time = _utcnow()
    return ok({"outTradeNo": out_trade_no, "status": order.status})
