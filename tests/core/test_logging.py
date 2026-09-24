"""AI-01 · 结构化日志（JSON）+ traceId 注入的单元测试。

被锁死的语义（DEP-10 的前置）：
    1. 输出是**单行** JSON（多行会把一条日志拆成多条，采集器无法按行聚合）；
    2. 字段口径固定：ts/level/logger/service/msg，业务字段经 `extra={"fields": ...}` 合并；
    3. traceId 来自 ContextVar —— 有链路时写入，无链路时**不输出该键**；
    4. `configure_structured_logging` 幂等，且不误伤别的库/测试框架装的 handler。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import (
    FIELDS_EXTRA_KEY,
    JsonFormatter,
    configure_structured_logging,
    log_fields,
)
from app.core.trace import reset_trace_id, set_trace_id

pytestmark = [pytest.mark.unit, pytest.mark.task("AI-01")]

_SERVICE = "aids-ai"


def _record(
    msg: str = "hello",
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
) -> logging.LogRecord:
    try:
        if exc_info:
            raise ValueError("boom")
    except ValueError:
        import sys

        return logging.LogRecord("aids_ai.demo", level, __file__, 10, msg, (), sys.exc_info())
    return logging.LogRecord("aids_ai.demo", level, __file__, 10, msg, (), None)


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    """快照并还原根 logger —— configure_* 会改它，不能污染同进程的其它测试。"""
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
    for handler in before:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(level)


class TestJsonFormatter:
    def test_emits_required_fields(self) -> None:
        payload = json.loads(JsonFormatter(_SERVICE).format(_record()))
        assert payload["service"] == _SERVICE
        assert payload["level"] == "INFO"
        assert payload["logger"] == "aids_ai.demo"
        assert payload["msg"] == "hello"
        assert "+00:00" in payload["ts"], "ts 必须是带 UTC 偏移的 ISO8601"

    def test_output_is_single_line(self) -> None:
        """多行消息必须被转义 —— 否则一条日志在采集器里会变成多条。"""
        payload = JsonFormatter(_SERVICE).format(_record("line1\nline2"))
        assert "\n" not in payload
        assert json.loads(payload)["msg"] == "line1\nline2"

    def test_trace_id_injected_from_contextvar(self) -> None:
        trace_id = "abcdef0123456789abcdef0123456789"
        token = set_trace_id(trace_id)
        try:
            payload = json.loads(JsonFormatter(_SERVICE).format(_record()))
        finally:
            reset_trace_id(token)
        assert payload["traceId"] == trace_id

    def test_trace_id_key_absent_without_context(self) -> None:
        """无链路上下文时不得写出 `traceId: null`（否则与"有链路"在聚合时混淆）。"""
        payload = json.loads(JsonFormatter(_SERVICE).format(_record()))
        assert "traceId" not in payload

    def test_extra_fields_are_merged(self) -> None:
        record = _record("订单已支付")
        record.fields = {"orderNo": "ORD1", "amount": 199.0}
        payload = json.loads(JsonFormatter(_SERVICE).format(record))
        assert payload["orderNo"] == "ORD1"
        assert payload["amount"] == 199.0
        assert payload["msg"] == "订单已支付"

    def test_exception_is_serialized_not_raised(self) -> None:
        """带异常栈的记录必须能序列化 —— 打日志本身抛异常等于丢掉现场。"""
        payload = json.loads(JsonFormatter(_SERVICE).format(_record(exc_info=True)))
        assert "ValueError: boom" in payload["exc"]

    def test_non_serializable_field_does_not_break_logging(self) -> None:
        record = _record()
        record.fields = {"obj": object()}
        payload = json.loads(JsonFormatter(_SERVICE).format(record))
        assert isinstance(payload["obj"], str), "不可序列化的值应退化为字符串，而不是抛错"


class TestConfigure:
    def test_installs_marked_handler(self, restore_root_logger: None) -> None:
        handler = configure_structured_logging(_SERVICE)
        assert isinstance(handler.formatter, JsonFormatter)

    def test_is_idempotent(self, restore_root_logger: None) -> None:
        """重复调用不得叠加 handler —— 叠加会让同一行日志被打两遍。"""
        first = configure_structured_logging(_SERVICE)
        second = configure_structured_logging(_SERVICE)
        root = logging.getLogger()
        installed = [h for h in root.handlers if getattr(h, "_aids_structured_handler", False)]
        assert len(installed) == 1
        assert first is not second  # 旧 handler 应被摘除
        assert first not in root.handlers

    def test_keeps_foreign_handlers(self, restore_root_logger: None) -> None:
        """不得清空 root 的全部 handler（会连 pytest 的 caplog 一起端掉）。"""
        foreign = logging.NullHandler()
        logging.getLogger().addHandler(foreign)
        configure_structured_logging(_SERVICE)
        assert foreign in logging.getLogger().handlers

    def test_level_from_env(
        self, restore_root_logger: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "warning")
        configure_structured_logging(_SERVICE)
        assert logging.getLogger().level == logging.WARNING


def test_log_fields_helper_shape() -> None:
    assert log_fields(orderNo="ORD1") == {FIELDS_EXTRA_KEY: {"orderNo": "ORD1"}}
