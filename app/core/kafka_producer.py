"""Kafka 生产者封装（BE-05）—— 本地消息表的投递出口。

两个不可妥协的配置（PRD §5.3 / §12.6「异步消息丢失」风险应对）：
    - `acks="all"`：等全部 ISR 副本落盘才算成功。配合本地消息表的
      「先落库、后投递、失败重试」，消除"Broker 收了但生产者以为没收到"的重发歧义。
    - `enable_idempotence=True`：生产者幂等（PID + 序列号），Broker 侧去重，
      生产端重试不再产生重复消息。**消费端仍需幂等**（本地消息表的
      `uk(biz_type, biz_no, topic)` + 消费方业务键）——幂等不传染，这是分工。

约定：
    - 消息 key = 业务单号（`biz_no`）：同一单号落同一分区，保住该单的事件顺序；
    - 消息头带 `trace_id`（若当前上下文有）：跨服务排障时能对上一条业务请求；
    - `value` 为 UTF-8 JSON，消费方按各 topic 的消息体契约解析（API.md §六）。

Kafka 未配置时的语义：抛 `KafkaNotConfiguredError`，由调用方决定降级 ——
本地消息表的投递循环会捕获它并让消息**保持待投递**（下轮重试），这正 PRD §12.6
「Kafka 停机：生产端堆积，恢复后自动补发，无消息丢失」的实现方式。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from aiokafka import AIOKafkaProducer

from app.core.trace import current_trace_id

logger = logging.getLogger(__name__)

KAFKA_ENV_KEY: Final[str] = "KAFKA_BOOTSTRAP_SERVERS"


class KafkaNotConfiguredError(RuntimeError):
    """未配置 KAFKA_BOOTSTRAP_SERVERS —— 投递循环捕获后让消息保持待投递。"""


_producer: AIOKafkaProducer | None = None
_started: bool = False


def _bootstrap_servers() -> str:
    import os

    return os.getenv(KAFKA_ENV_KEY, "").strip()


def get_producer() -> AIOKafkaProducer:
    """进程级生产者单例（惰性构造；连接在 `ensure_started()` 时才发生）。"""
    global _producer  # noqa: PLW0603 - 进程级单例
    if _producer is None:
        servers = _bootstrap_servers()
        if not servers:
            raise KafkaNotConfiguredError(
                f"未配置 {KAFKA_ENV_KEY}；Kafka 投递不可用（调用方应降级为本地消息表重试）"
            )
        _producer = AIOKafkaProducer(
            bootstrap_servers=servers,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,  # 攒 5ms 批量发送：吞吐换极小延迟，交易链路无感
            request_timeout_ms=10000,
        )
    return _producer


def reset_producer() -> None:
    """重置单例（测试 / 配置变更后）。调用方应先 stop 旧实例。"""
    global _producer, _started  # noqa: PLW0603
    _producer = None
    _started = False


async def ensure_started() -> AIOKafkaProducer:
    """确保生产者已连接（幂等）。"""
    global _started  # noqa: PLW0603
    producer = get_producer()
    if not _started:
        await producer.start()
        _started = True
        logger.info("Kafka 生产者已连接")
    return producer


async def stop() -> None:
    """flush 并断开（应用 shutdown 时调用；未启动过则跳过）。"""
    global _started  # noqa: PLW0603
    if _producer is not None and _started:
        await _producer.stop()
        logger.info("Kafka 生产者已断开")
    _started = False


async def send_json(topic: str, *, key: str, payload: dict[str, Any]) -> None:
    """发送一条 JSON 消息。key = 业务单号；headers 自动带 trace_id（若存在）。

    失败抛出（aiokafka 异常 / KafkaNotConfiguredError），由调用方决定降级 ——
    本地消息表的投递循环是预期调用方，它会把失败转成「保持待投递 + 退避重试」。
    """
    producer = await ensure_started()
    headers: list[tuple[str, bytes]] = []
    trace_id = current_trace_id()
    if trace_id:
        headers.append(("trace_id", trace_id.encode("utf-8")))
    await producer.send_and_wait(
        topic,
        value=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        key=key.encode("utf-8"),
        headers=headers,
    )
