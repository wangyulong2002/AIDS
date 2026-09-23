"""BE-05 · 本地消息表（事务性发件箱）的单元测试（不连库、不连 Kafka）。

被锁死的状态机（比"能发出去"更值得测）：
    待投递(0) --发送成功--> 已投递(1)
    待投递(0) --发送失败--> 待投递(0) + retry_count+1 + 指数退避
    待投递(0) --重试超限--> 投递失败(2) + 写死信表（可人工重放/忽略）
另有入队幂等（uk 去重）与扫描语句的形状（status=0 + 到期 + 稳定排序 + 限量）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox import (
    BACKOFF_MAX_SECONDS,
    MAX_RETRY,
    DispatchOutcome,
    OutboxRelay,
    dispatch_once,
    enqueue,
    next_retry_delay,
    pending_statement,
)
from app.models.sys import SysDeadLetter, SysLocalMessage

pytestmark = [pytest.mark.unit, pytest.mark.task("BE-05")]

_NOW = datetime(2026, 9, 23, 12, 0, 0)


def _sess(fake: Any) -> AsyncSession:
    """替身会话按 AsyncSession 传递（cast：仅用到 execute/add，形状等价）。"""
    return cast(AsyncSession, fake)


def _message(**overrides: Any) -> SysLocalMessage:
    fields: dict[str, Any] = {
        "biz_type": "ORDER",
        "biz_no": "ORD123",
        "topic": "order.created",
        "payload": {"orderNo": "ORD123"},
        "status": 0,
        "retry_count": 0,
    }
    fields.update(overrides)
    return SysLocalMessage(**fields)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.added: list[Any] = []
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        return _FakeResult(list(self.rows))

    def add(self, obj: Any) -> None:
        self.added.append(obj)


class _FakeProducer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def send(self, *, topic: str, key: str, payload: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("kafka down")
        self.calls.append({"topic": topic, "key": key, "payload": payload})


# =====================================================================
# 一、退避与扫描语句
# =====================================================================


def test_backoff_is_exponential_and_capped() -> None:
    assert next_retry_delay(0) == 10
    assert next_retry_delay(1) == 20
    assert next_retry_delay(2) == 40
    assert next_retry_delay(6) == BACKOFF_MAX_SECONDS  # 640 → 封顶 600
    assert next_retry_delay(100) == BACKOFF_MAX_SECONDS


def test_pending_statement_shape() -> None:
    sql = str(pending_statement(limit=7).compile(compile_kwargs={"literal_binds": True}))
    assert "sys_local_message.status = 0" in sql
    assert "next_retry_time <=" in sql
    assert "ORDER BY" in sql and "sys_local_message.id" in sql
    assert "LIMIT 7" in sql


# =====================================================================
# 二、入队幂等
# =====================================================================


async def test_enqueue_inserts_when_new() -> None:
    session = _FakeSession()
    assert (
        await enqueue(
            _sess(session), biz_type="ORDER", biz_no="ORD1", topic="order.created", payload={}
        )
        is True
    )
    assert len(session.added) == 1
    added = session.added[0]
    assert isinstance(added, SysLocalMessage)
    assert added.status == 0 and added.retry_count == 0


async def test_enqueue_is_idempotent() -> None:
    """同一业务事件重复入队 → False，不产生第二行（uk 兜底并发）。"""
    session = _FakeSession(rows=[123])  # 已存在同键消息的主键
    assert (
        await enqueue(
            _sess(session), biz_type="ORDER", biz_no="ORD123", topic="order.created", payload={}
        )
        is False
    )
    assert session.added == []


# =====================================================================
# 三、投递状态机
# =====================================================================


async def test_success_marks_delivered() -> None:
    message = _message()
    session = _FakeSession(rows=[message])
    producer = _FakeProducer()

    outcome = await asyncio.wait_for(_dispatch(session, producer), timeout=2)

    assert outcome == DispatchOutcome(sent=1, failed=0, dead=0)
    assert message.status == 1 and message.error_msg is None
    assert producer.calls[0] == {
        "topic": "order.created",
        "key": "ORD123",
        "payload": {"orderNo": "ORD123"},
    }


async def test_failure_schedules_backoff() -> None:
    message = _message()
    session = _FakeSession(rows=[message])
    outcome = await _dispatch(session, _FakeProducer(fail=True), now=_NOW)

    assert outcome.failed == 1 and outcome.sent == 0
    assert message.status == 0 and message.retry_count == 1
    assert message.next_retry_time == _NOW + timedelta(seconds=10)
    assert message.error_msg == "kafka down"


async def test_exhausted_retries_goes_to_dead_letter() -> None:
    """重试超限：状态转 2 + 落死信表（不丢弃，转人工重放/忽略）。"""
    message = _message(retry_count=MAX_RETRY - 1)
    session = _FakeSession(rows=[message])
    outcome = await _dispatch(session, _FakeProducer(fail=True), now=_NOW)

    assert outcome.dead == 1
    assert message.status == 2
    assert len(session.added) == 1
    letter = session.added[0]
    assert isinstance(letter, SysDeadLetter)
    assert letter.source == "LOCAL_MSG"
    assert letter.ref_key == "ORD123" and letter.topic == "order.created"


async def test_mixed_batch_counts() -> None:
    rows = [_message(biz_no=f"ORD{i}") for i in range(4)]
    session = _FakeSession(rows=rows)

    class _Flaky(_FakeProducer):
        async def send(self, *, topic: str, key: str, payload: dict[str, Any]) -> None:
            self.calls.append({})
            if key == "ORD2":
                raise RuntimeError("boom")

    outcome = await _dispatch(session, _Flaky(), now=_NOW)
    assert (outcome.sent, outcome.failed, outcome.dead) == (3, 1, 0)


async def _dispatch(
    session: _FakeSession, producer: _FakeProducer, now: datetime | None = None
) -> DispatchOutcome:
    return await asyncio.wait_for(
        dispatch_once(_sess(session), now=now, send=producer.send), timeout=2
    )


# =====================================================================
# 四、投递循环
# =====================================================================


async def test_relay_run_once_drives_dispatch() -> None:
    message = _message()
    session = _FakeSession(rows=[message])
    producer = _FakeProducer()

    class _Factory:
        def __call__(self):  # noqa: ANN204 - 异步上下文管理器
            return self

        async def __aenter__(self) -> AsyncSession:
            return _sess(session)

        async def __aexit__(self, *args: Any) -> None:
            return None

    relay = OutboxRelay(_Factory(), send=producer.send)
    outcome = await asyncio.wait_for(relay.run_once(), timeout=2)
    assert isinstance(outcome, DispatchOutcome)
    assert outcome.sent == 1


def test_outcome_quiet_helper() -> None:
    assert DispatchOutcome(sent=0, failed=0, dead=0).quiet is True
    assert DispatchOutcome(sent=1, failed=0, dead=0).quiet is False
