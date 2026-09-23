"""BE-05 · Kafka 生产者封装的单元测试（不连 Broker）。

连不上 Broker 也能测的部分恰恰是**不能出错的部分**：
    - `acks="all"` 与 `enable_idempotence` 必须在构造参数里 —— 丢了它们，
      消息丢失/重复只在事故现场暴露；
    - key=业务单号、value=JSON、headers 带 trace_id —— 消费方契约。
    连接行为由真实链路（T3 起的投递循环）覆盖。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core import kafka_producer as kp
from app.core.kafka_producer import KafkaNotConfiguredError
from app.core.trace import reset_trace_id, set_trace_id

pytestmark = [pytest.mark.unit, pytest.mark.task("BE-05")]


@pytest.fixture(autouse=True)
def _isolate() -> Any:
    kp.reset_producer()
    yield
    kp.reset_producer()


class _RecordingProducer:
    """记录构造参数与 send 调用的替身。"""

    instances: list[_RecordingProducer] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[dict[str, Any]] = []
        type(self).instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(
        self, topic: str, *, value: bytes, key: bytes, headers: list[tuple[str, bytes]]
    ) -> None:
        self.sent.append({"topic": topic, "value": value, "key": key, "headers": headers})


@pytest.fixture(autouse=True)
def _patch_producer_cls(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingProducer]:
    _RecordingProducer.instances = []
    monkeypatch.setattr(kp, "AIOKafkaProducer", _RecordingProducer)
    return _RecordingProducer.instances


def test_bootstrap_servers_env_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    with pytest.raises(KafkaNotConfiguredError):
        kp.get_producer()


def test_producer_is_created_with_idempotent_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    producer = kp.get_producer()
    assert isinstance(producer, _RecordingProducer)
    assert producer.kwargs["bootstrap_servers"] == "127.0.0.1:29092"
    assert producer.kwargs["acks"] == "all", "必须等全部 ISR 副本落盘"
    assert producer.kwargs["enable_idempotence"] is True, "生产端幂等不可关"


def test_singleton_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    assert kp.get_producer() is kp.get_producer()
    assert len(_RecordingProducer.instances) == 1


def test_send_json_serializes_and_carries_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    token = set_trace_id("a" * 32)
    try:
        asyncio.run(
            kp.send_json("order.created", key="ORD123", payload={"orderNo": "ORD123", "total": 1})
        )
    finally:
        reset_trace_id(token)

    sent = _RecordingProducer.instances[0].sent[0]
    assert sent["topic"] == "order.created"
    assert sent["key"] == b"ORD123"
    assert json.loads(sent["value"]) == {"orderNo": "ORD123", "total": 1}
    assert ("trace_id", b"a" * 32) in sent["headers"], "消息头必须带 trace_id"


def test_send_without_trace_context_has_no_trace_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    asyncio.run(kp.send_json("order.created", key="K", payload={}))
    sent = _RecordingProducer.instances[0].sent[0]
    assert sent["headers"] == []


def test_stop_is_safe_when_never_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    asyncio.run(kp.stop())  # 未启动过也必须能安全调用
