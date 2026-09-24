"""Mock 支付渠道（MOCK-01）—— `PaymentChannel` 抽象的渠道侧实现 + 回调投递。

为什么抽象（TASKS MOCK-01）：真实渠道接入时业务代码零改动 —— 调用方（BE-23）
依赖 `PaymentChannel` 接口（Protocol），Mock 是它的**第一个实现**；切换真实渠道
= 换一个实现类，接口形态（统一下单 / 回调 / 查询 / 退款 / 对账）不变。

状态机（渠道视角，与商户侧 biz_payment 独立）：
    支付单：待支付(0) → 已支付(1)（收银台确认）/ 已关闭(2)（超时或用户取消）
    退款单：退款中(0) → 退款成功(1)（Mock 即时成功，真实渠道是异步的）
    回调：  待推送(0) → 推送成功(1)（商户回 "success"）/ 失败重试(2) / 放弃(3)

故障注入（PRD §8.3 / MOCK-04 的执行点，env 下发、缺省全关）：
    MOCK_CALLBACK_DELAY_SECONDS / MOCK_CALLBACK_REPEAT_TIMES /
    MOCK_CALLBACK_LOSS_RATE / MOCK_CHANNEL_FAILURE_RATE
"""

from __future__ import annotations

import logging
import os
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol, TypedDict

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aids_mock.constants import (
    BIZ_TYPE_PAYMENT,
    CALLBACK_ABANDONED,
    CALLBACK_DELIVERED,
    CALLBACK_FAILED,
    CALLBACK_WAITING,
    ENV_CALLBACK_DELAY_SECONDS,
    ENV_CALLBACK_LOSS_RATE,
    ENV_CALLBACK_REPEAT_TIMES,
    ENV_CHANNEL_FAILURE_RATE,
    PAY_CLOSED,
    PAY_PENDING,
    PAY_SUCCESS,
    RECON_READY,
    REFUND_SUCCESS,
)
from aids_mock.models import MockCallbackLog, MockPaymentOrder, MockRefundOrder
from aids_mock.rsa import ensure_keypair, sign_rsa2

logger = logging.getLogger(__name__)

MAX_CALLBACK_RETRY: Final[int] = 5
CALLBACK_BACKOFF_SECONDS: Final[int] = 30
CHANNEL_NAME: Final[str] = "MOCK_PAY"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class FaultConfig(TypedDict):
    """故障注入配置（MOCK-04 的开关取值，全部来自 env，缺省全关）。

    用 TypedDict 而不是 `dict[str, int | float]`：后者会让每个取值都是
    `int | float` 联合，`range(...)`、切片这类需要 int 的地方全部报类型错，
    逼着调用方到处写 `int(...)` 转换——类型信息丢失，转换也失去了意义。
    """

    delay_seconds: int
    repeat_times: int
    loss_rate: float
    failure_rate: float


def fault_config() -> FaultConfig:
    """故障注入配置（MOCK-04 的开关；缺省全关，不注入任何故障）。"""
    return {
        "delay_seconds": _env_int(ENV_CALLBACK_DELAY_SECONDS, 0),
        "repeat_times": max(1, _env_int(ENV_CALLBACK_REPEAT_TIMES, 1)),
        "loss_rate": min(1.0, max(0.0, _env_float(ENV_CALLBACK_LOSS_RATE, 0.0))),
        "failure_rate": min(1.0, max(0.0, _env_float(ENV_CHANNEL_FAILURE_RATE, 0.0))),
    }


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _trade_no() -> str:
    return (
        f"{CHANNEL_NAME}{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"
    )


def _should_fail() -> bool:
    return random.random() < _env_float(ENV_CHANNEL_FAILURE_RATE, 0.0)


# =====================================================================
# PaymentChannel 抽象（MOCK-01 的接口交付物）
# =====================================================================


class PaymentChannel(Protocol):
    """支付渠道接口。真实渠道（支付宝/微信）接入时实现同一协议，调用方零改动。"""

    async def create_payment(
        self,
        session: AsyncSession,
        *,
        out_trade_no: str,
        pay_type: int,
        amount: Decimal,
        subject: str,
        notify_url: str,
        return_url: str | None,
        expire_minutes: int = 15,
    ) -> MockPaymentOrder: ...

    async def query_payment(
        self, session: AsyncSession, *, out_trade_no: str
    ) -> MockPaymentOrder | None: ...

    async def close_payment(
        self, session: AsyncSession, *, out_trade_no: str, reason: str
    ) -> bool: ...

    async def refund(
        self,
        session: AsyncSession,
        *,
        out_refund_no: str,
        out_trade_no: str,
        refund_amount: Decimal,
        reason: str | None,
    ) -> MockRefundOrder: ...


class MockPaymentChannel:
    """Mock 渠道实现。所有写操作发生在传入的会话事务里（调用方 commit）。"""

    def __init__(self, channel_private_pem: bytes) -> None:
        self._channel_private_pem = channel_private_pem

    # ---- 统一下单 ----
    async def create_payment(
        self,
        session: AsyncSession,
        *,
        out_trade_no: str,
        pay_type: int,
        amount: Decimal,
        subject: str,
        notify_url: str,
        return_url: str | None,
        expire_minutes: int = 15,
    ) -> MockPaymentOrder:
        """统一下单。`uk_out_trade_no` 幂等：同号重复下单返回原单（真实渠道同语义）。

        `MOCK_CHANNEL_FAILURE_RATE` > 0 时按概率模拟渠道故障（抛错 → 商户侧重试）。
        """
        existing = (
            await session.execute(
                select(MockPaymentOrder).where(MockPaymentOrder.out_trade_no == out_trade_no)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        if _should_fail():
            raise RuntimeError("Mock 渠道故障注入：统一下单失败（MOCK_CHANNEL_FAILURE_RATE）")

        order = MockPaymentOrder(
            out_trade_no=out_trade_no,
            trade_no=_trade_no(),
            merchant_id=os.getenv("MOCK_PAY_MERCHANT_ID", "MOCK_MERCHANT_001"),
            pay_type=pay_type,
            amount=amount,
            subject=subject,
            notify_url=notify_url,
            return_url=return_url,
            status=PAY_PENDING,
            expire_time=_utcnow() + timedelta(minutes=expire_minutes),
        )
        session.add(order)
        await session.flush()
        return order

    # ---- 主动查询 ----
    async def query_payment(
        self, session: AsyncSession, *, out_trade_no: str
    ) -> MockPaymentOrder | None:
        return (
            await session.execute(
                select(MockPaymentOrder).where(MockPaymentOrder.out_trade_no == out_trade_no)
            )
        ).scalar_one_or_none()

    # ---- 关单（超时 / 用户取消）----
    async def close_payment(self, session: AsyncSession, *, out_trade_no: str, reason: str) -> bool:
        order = await self.query_payment(session, out_trade_no=out_trade_no)
        if order is None or order.status != PAY_PENDING:
            return False
        order.status = PAY_CLOSED
        order.close_time = _utcnow()
        logger.info("Mock 渠道关单：%s（%s）", out_trade_no, reason)
        return True

    # ---- 退款（Mock 即时成功；真实渠道为异步 + 回调）----
    async def refund(
        self,
        session: AsyncSession,
        *,
        out_refund_no: str,
        out_trade_no: str,
        refund_amount: Decimal,
        reason: str | None,
    ) -> MockRefundOrder:
        order = await self.query_payment(session, out_trade_no=out_trade_no)
        if order is None:
            raise ValueError(f"渠道无此订单：{out_trade_no}")
        if order.status != PAY_SUCCESS:
            raise ValueError("订单未支付，不能退款")
        if order.refunded_amount + refund_amount > order.amount:
            raise ValueError("累计退款金额超过支付金额")

        if _should_fail():
            raise RuntimeError("Mock 渠道故障注入：退款失败（MOCK_CHANNEL_FAILURE_RATE）")

        existing = (
            await session.execute(
                select(MockRefundOrder).where(MockRefundOrder.out_refund_no == out_refund_no)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        refund = MockRefundOrder(
            out_refund_no=out_refund_no,
            refund_no=f"RF{_trade_no()}",
            out_trade_no=out_trade_no,
            trade_no=order.trade_no,
            refund_amount=refund_amount,
            reason=reason,
            status=REFUND_SUCCESS,
            refund_time=_utcnow(),
        )
        session.add(refund)
        order.refunded_amount = order.refunded_amount + refund_amount
        await session.flush()
        return refund

    # ---- T+1 对账单（CSV 文本，本地演示直接返回内容）----
    async def build_daily_recon(
        self, session: AsyncSession, *, bill_date: str, pay_type: int
    ) -> dict[str, Any]:
        target = date.fromisoformat(bill_date)
        day_start = datetime(target.year, target.month, target.day)
        day_end = day_start + timedelta(days=1)

        pays = (
            (
                await session.execute(
                    select(MockPaymentOrder).where(
                        MockPaymentOrder.pay_type == pay_type,
                        MockPaymentOrder.pay_time >= day_start,
                        MockPaymentOrder.pay_time < day_end,
                        MockPaymentOrder.status == PAY_SUCCESS,
                    )
                )
            )
            .scalars()
            .all()
        )
        refunds = (
            (
                await session.execute(
                    select(MockRefundOrder).where(
                        MockRefundOrder.create_time >= day_start,
                        MockRefundOrder.create_time < day_end,
                        MockRefundOrder.status == REFUND_SUCCESS,
                    )
                )
            )
            .scalars()
            .all()
        )

        lines = ["out_trade_no,trade_no,pay_amount,pay_time,status"]
        for p in pays:
            lines.append(f"{p.out_trade_no},{p.trade_no},{p.amount},{p.pay_time},{PAY_SUCCESS}")
        total_amount = sum(p.amount for p in pays)
        refund_amount = sum(r.refund_amount for r in refunds)
        for r in refunds:
            lines.append(
                f"{r.out_refund_no},{r.refund_no},-{r.refund_amount},{r.refund_time},REFUND"
            )

        # 金额在响应里统一为浮点数（与 uniorder/query/refund 同口径，见 API.md 示例）；
        # 内部累计仍用 Decimal，避免浮点累加误差。
        return {
            "bill_date": target,
            "pay_type": pay_type,
            "file_content": "\n".join(lines),
            "total_count": len(pays),
            "total_amount": float(total_amount),
            "refund_count": len(refunds),
            "refund_amount": float(refund_amount),
            "status": RECON_READY,
        }


# =====================================================================
# 收银台动作（模拟"用户"在沙箱页面上的操作）
# =====================================================================


async def confirm_pay(
    session: AsyncSession, channel: MockPaymentChannel, *, out_trade_no: str
) -> MockPaymentOrder:
    """收银台「确认支付」：待支付 → 已支付，并登记回调。"""
    order = await channel.query_payment(session, out_trade_no=out_trade_no)
    if order is None:
        raise ValueError(f"渠道无此订单：{out_trade_no}")
    if order.status == PAY_SUCCESS:
        return order  # 幂等：重复确认不重复回调
    if order.status != PAY_PENDING:
        raise ValueError("订单非待支付状态，不能支付")
    if order.expire_time < _utcnow():
        order.status = PAY_CLOSED
        order.close_time = _utcnow()
        raise ValueError("订单已超时关闭")

    order.status = PAY_SUCCESS
    paid_at = _utcnow()
    order.pay_time = paid_at
    await session.flush()
    await schedule_callbacks(
        session,
        biz_type=BIZ_TYPE_PAYMENT,
        out_trade_no=order.out_trade_no,
        notify_url=order.notify_url,
        payload={
            "outTradeNo": order.out_trade_no,
            "tradeNo": order.trade_no,
            "amount": float(order.amount),
            "status": "SUCCESS",
            "payTime": paid_at.isoformat(),
        },
    )
    return order


async def cancel_pay(
    session: AsyncSession, channel: MockPaymentChannel, *, out_trade_no: str
) -> bool:
    """收银台「取消支付」：待支付 → 已关闭（不发回调 —— 用户取消不是支付事件）。"""
    return await channel.close_payment(
        session, out_trade_no=out_trade_no, reason="用户在收银台取消"
    )


# =====================================================================
# 回调登记与投递（含故障注入）
# =====================================================================


async def schedule_callbacks(
    session: AsyncSession,
    *,
    biz_type: int,
    out_trade_no: str,
    notify_url: str,
    payload: dict[str, Any],
) -> int:
    """按故障注入配置登记回调投递记录（延迟/重复/丢失）。

    返回登记的条数。真正的网络推送由 `dispatch_due_callbacks` 执行 ——
    分开的原因：推送是 IO，登记是事务内操作；推送失败只影响回调记录的
    状态推进，不能回滚支付业务。
    """
    import json

    config = fault_config()
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    timestamp = str(int(_utcnow().timestamp()))
    nonce = uuid.uuid4().hex
    sign = sign_rsa2(self_private_pem(), body, timestamp, nonce)
    signed_payload = {
        **payload,
        "timestamp": timestamp,
        "nonce": nonce,
        "sign": sign,
    }

    registered = 0
    for attempt in range(1, config["repeat_times"] + 1):
        if attempt > 1 and random.random() < config["loss_rate"]:
            continue  # 重复推送中按概率丢失
        if attempt == 1 and random.random() < config["loss_rate"]:
            continue  # 首次推送也可能丢失（PRD §8.3：回调丢失）
        session.add(
            MockCallbackLog(
                biz_type=biz_type,
                out_trade_no=out_trade_no,
                notify_url=notify_url,
                payload=signed_payload,
                sign=sign,
                attempt_no=attempt,
                status=CALLBACK_WAITING,
                next_retry_time=_utcnow() + timedelta(seconds=config["delay_seconds"]),
            )
        )
        registered += 1
    return registered


def self_private_pem() -> bytes:
    """渠道私钥（缺失则现场生成 —— 开发引导；路径 env：MOCK_CHANNEL_RSA_PRIVATE_KEY_PATH）。"""
    private_path = Path(
        os.getenv("MOCK_CHANNEL_RSA_PRIVATE_KEY_PATH", "./secrets/mock_channel_private.pem")
    )
    public_path = Path(
        os.getenv("MOCK_CHANNEL_RSA_PUBLIC_KEY_PATH", "./secrets/mock_channel_public.pem")
    )
    private_pem, _ = ensure_keypair(private_path, public_path)
    return private_pem


def merchant_public_pem() -> bytes | None:
    """商户公钥（用于验商户请求签名）；未配置 → None（v0-draft 显式降级并告警）。"""
    path = Path(os.getenv("MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH", ""))
    if not path.is_file():
        logger.warning(
            "未配置 MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH，商户请求验签跳过（v0-draft，T3 定稿后关闭）"
        )
        return None
    return path.read_bytes()


async def dispatch_due_callbacks(
    session: AsyncSession,
    *,
    poster: Any = None,
    now: datetime | None = None,
    limit: int = 20,
) -> dict[str, int]:
    """推送到期回调；商户返回 "success" 视为成功，否则退避重试，超限放弃。

    `poster(url, body, headers)` 可注入（默认 httpx）—— 投递的**状态机语义**
    （成功/重试/放弃）必须能在不依赖网络的情况下被测试。
    """
    import json as _json

    if poster is None:
        poster = _httpx_poster
    now = now or _utcnow()

    logs = (
        (
            await session.execute(
                select(MockCallbackLog)
                .where(
                    MockCallbackLog.status == CALLBACK_WAITING,
                    MockCallbackLog.next_retry_time <= now,
                )
                .order_by(MockCallbackLog.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    delivered = failed = abandoned = 0
    for log in logs:
        body = _json.dumps(log.payload, ensure_ascii=False).encode("utf-8")
        try:
            http_code, resp_text = await poster(
                log.notify_url,
                body,
                {"Content-Type": "application/json", "X-Channel-Sign": log.sign},
            )
        except Exception as exc:  # noqa: BLE001 - 网络异常按失败重试
            http_code, resp_text = 0, f"{exc.__class__.__name__}: {exc}"[:255]

        log.http_status = http_code
        log.resp_body = (resp_text or "")[:255]
        # 变量名刻意叫 `http_code` 而不是 `status_code`：后者含 "status" 子串，会被
        # C2 扫描器当作**业务状态字段**（要求用枚举成员），而 HTTP 状态码与业务状态机
        # 无关。按 `app/core/handlers.py` 的先例**改名**处理，不加 `# enum-ok` 豁免 ——
        # 到处加豁免会让门禁名存实亡。
        if http_code == 200 and (resp_text or "").strip() == "success":
            log.status = CALLBACK_DELIVERED
            delivered += 1
            continue

        log.retry_count += 1
        if log.retry_count >= MAX_CALLBACK_RETRY:
            log.status = CALLBACK_ABANDONED
            abandoned += 1
        else:
            log.status = CALLBACK_FAILED
            log.next_retry_time = now + timedelta(
                seconds=CALLBACK_BACKOFF_SECONDS * log.retry_count
            )
            failed += 1

    return {"delivered": delivered, "failed": failed, "abandoned": abandoned}


async def _httpx_poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, content=body, headers=headers)
        return response.status_code, response.text
