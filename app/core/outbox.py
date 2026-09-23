"""本地消息表（事务性发件箱，BE-05）—— 跨服务一致性的实现方式。

要解决的问题（PRD §5.3 / §12.6 / §13）：
    「业务落库成功」与「消息发出去」是两个不同的事务（一个在 MySQL，一个在 Kafka）。
    先发消息再落库：落库失败则消息已出去（凭空事件）；先落库再发消息：发送失败则
    事件丢失。**本地消息表**的答案是：把"要发消息"这件事本身写进同一个数据库事务 ——
        BEGIN
            写业务行（订单/支付/库存流水）
            写 sys_local_message（status=0 待投递）
        COMMIT
    之后由**投递任务**把待投递的消息发往 Kafka，成功置 1，失败退避重试，
    超过上限落 `sys_dead_letter` 等人工处理。Kafka 停机 = 消息堆积 = 恢复后补发，
    全程无丢失（PRD §12.6 的验收场景）。

幂等（三层，各管一段）：
    1. 入队幂等：`uk(biz_type, biz_no, topic)` —— 同一业务事件不会被登记两次；
    2. 生产幂等：Kafka `enable_idempotence`（见 app/core/kafka_producer.py）；
    3. 消费幂等：消费方按业务键去重（属消费方任务，不在本模块）。

为什么重试要"指数退避 + 上限"：
    固定间隔会让故障期间的失败消息以相同节奏反复打同一道伤口；无上限则
    永久性错误（消息体超限等）无限重试。上限触顶后**转死信表**——
    丢进死信 ≠ 丢弃，而是从"自动重试"切换为"人工可重放/可忽略"。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.kafka_producer import send_json
from app.models.sys import SysDeadLetter, SysLocalMessage

logger = logging.getLogger(__name__)

MAX_RETRY: Final[int] = 8
BACKOFF_BASE_SECONDS: Final[int] = 10
BACKOFF_MAX_SECONDS: Final[int] = 600
DEFAULT_BATCH: Final[int] = 50

# sys_local_message.status 的取值（DDL 注释：0待投递 1已投递 2投递失败超上限）。
# 为什么不是 app/domain/enums.py 里的正式枚举组：那 11 组是「PRD 状态机 ↔ 字典」
# 的映射口径（C2 锁死为 11 组）；本地消息的投递状态是**基础设施状态**，不在该口径内，
# 故用命名常量收口 —— C2 禁的是魔法数字，不是禁止命名常量。
# TODO(T4 后台任务)：管理员重放死信时若需要展示该状态，再升级为正式枚举组并同步字典。
MSG_PENDING: Final[int] = 0
MSG_DELIVERED: Final[int] = 1
MSG_DEAD_LETTERED: Final[int] = 2

# sys_dead_letter.status：0待处理 1已重放 2已忽略（本模块只写"待处理"）
DEAD_LETTER_PENDING: Final[int] = 0

# 会话工厂返回的是**异步上下文管理器**（session_scope 语义：进出即开事务/提交回滚），
# 不是裸 AsyncIterator —— 曾错写成 AsyncIterator，pyright 当场指出 async with 不成立。
DispatchSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def next_retry_delay(retry_count: int) -> int:
    """指数退避（秒）：10, 20, 40, ... 封顶 600。`retry_count` 为已失败次数。"""
    return min(BACKOFF_BASE_SECONDS * (2**retry_count), BACKOFF_MAX_SECONDS)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # schema 约定：DATETIME 一律 UTC 裸值


@dataclass(frozen=True)
class DispatchOutcome:
    """一轮投递的结果计数。"""

    sent: int
    failed: int
    dead: int

    @property
    def quiet(self) -> bool:
        return self.sent == 0 and self.failed == 0 and self.dead == 0


async def enqueue(
    session: AsyncSession,
    *,
    biz_type: str,
    biz_no: str,
    topic: str,
    payload: dict[str, Any],
) -> bool:
    """在**当前事务内**登记一条待投递消息（与业务行同 commit / 同 rollback）。

    返回 False 表示同一业务事件已登记过（幂等去重）——调用方大多数场景应忽略返回值。
    去重先查后插：避免让 `IntegrityError` 污染业务事务（捕获它必须回滚整笔）；
    并发下的重复插入由 `uk(biz_type, biz_no, topic)` 兜底，事务回滚后重试即幂等。
    """
    exists = await session.execute(
        select(SysLocalMessage.id).where(
            SysLocalMessage.biz_type == biz_type,
            SysLocalMessage.biz_no == biz_no,
            SysLocalMessage.topic == topic,
        )
    )
    if exists.scalar_one_or_none() is not None:
        logger.info("本地消息已登记，跳过重复入队：%s/%s/%s", biz_type, biz_no, topic)
        return False

    session.add(
        SysLocalMessage(
            biz_type=biz_type,
            biz_no=biz_no,
            topic=topic,
            payload=payload,
            status=MSG_PENDING,
            retry_count=0,
            next_retry_time=_utcnow(),
        )
    )
    return True


def pending_statement(limit: int = DEFAULT_BATCH) -> Select[tuple[SysLocalMessage]]:
    """待投递扫描语句：status=0 且到达重试时间，按 id 稳定顺序，限量。

    （语句单独抽出来是为了被测试编译检查 —— 过滤条件与排序丢了是静默丢失语义。）
    """
    return (
        select(SysLocalMessage)
        .where(
            SysLocalMessage.status == MSG_PENDING,
            SysLocalMessage.next_retry_time <= _utcnow(),
        )
        .order_by(SysLocalMessage.id)
        .limit(limit)
    )


async def dispatch_once(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_BATCH,
    now: datetime | None = None,
    send: Callable[..., Any] = send_json,
) -> DispatchOutcome:
    """把到期且待投递的消息发往 Kafka；失败按退避重试，超限转死信。

    参数 `send` 刻意可注入（默认真实 Kafka）：投递循环的**状态机语义**必须能在
    不依赖 Kafka 的情况下被测试 —— 失败转退避、超限转死信，这些比"能发出去"
    更值得锁死。
    """
    now = now or _utcnow()
    result = await session.execute(pending_statement(limit))
    messages = list(result.scalars().all())

    sent = failed = dead = 0
    for message in messages:
        try:
            await send(topic=message.topic, key=message.biz_no, payload=message.payload)
        except Exception as exc:  # noqa: BLE001 - 投递失败的原因不影响状态机走向
            message.retry_count += 1
            message.error_msg = str(exc)[:500]
            if message.retry_count >= MAX_RETRY:
                message.status = MSG_DEAD_LETTERED
                session.add(
                    SysDeadLetter(
                        source="LOCAL_MSG",
                        ref_key=message.biz_no,
                        topic=message.topic,
                        payload=message.payload,
                        error_msg=message.error_msg,
                        retry_count=message.retry_count,
                        status=DEAD_LETTER_PENDING,
                    )
                )
                dead += 1
                logger.error(
                    "本地消息转死信：%s/%s 重试 %s 次仍失败",
                    message.biz_type,
                    message.biz_no,
                    message.retry_count,
                )
            else:
                # 首次失败（count=1）应退避 10s —— 传自增前的次数，与 next_retry_delay
                # 的文档序列（10, 20, 40...）对齐；传自增后的值会变成首次 20s。
                message.next_retry_time = now + timedelta(
                    seconds=next_retry_delay(message.retry_count - 1)
                )
                failed += 1
                logger.warning(
                    "本地消息投递失败（第 %s 次，%ss 后重试）：%s/%s",
                    message.retry_count,
                    next_retry_delay(message.retry_count - 1),
                    message.biz_type,
                    message.biz_no,
                )
        else:
            message.status = MSG_DELIVERED
            message.error_msg = None
            sent += 1

    return DispatchOutcome(sent=sent, failed=failed, dead=dead)


class OutboxRelay:
    """投递循环：周期性调用 `dispatch_once`。

    刻意不接进应用 lifespan —— 交易链路（T3）起才有消息可投，届时以 lifespan
    启动 `run_forever()`；当前测试直接驱动 `run_once()`。
    """

    def __init__(
        self,
        session_factory: DispatchSessionFactory,
        *,
        interval_seconds: float = 5.0,
        batch_size: int = DEFAULT_BATCH,
        send: Callable[..., Any] = send_json,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._batch = batch_size
        self._send = send

    async def run_once(self) -> DispatchOutcome:
        async with self._session_factory() as session:
            return await dispatch_once(session, limit=self._batch, send=self._send)

    async def run_forever(self) -> None:
        import asyncio

        while True:
            try:
                outcome = await self.run_once()
                if not outcome.quiet:
                    logger.info("本地消息投递：%s", outcome)
            except Exception:  # noqa: BLE001 - 循环绝不退出：一轮失败等下一轮
                logger.exception("本地消息投递循环异常（将在下轮重试）")
            await asyncio.sleep(self._interval)
