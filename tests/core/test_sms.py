"""BE-05 · 短信抽象的单元测试。

当前唯一实现是 `LoggingSmsSender`（开发环境：内容进日志、零外部调用）。
Mock 通道由 MOCK-02 落地后按环境切换，调用方（BE-07）不感知。
"""

from __future__ import annotations

import logging

import pytest

from app.core.sms import (
    LoggingSmsSender,
    SmsSendError,
    get_sms_sender,
    mask_mobile,
    reset_sms_sender,
)

pytestmark = pytest.mark.unit


async def test_send_records_message() -> None:
    sender = LoggingSmsSender()
    await sender.send(mobile="13800138000", scene="LOGIN", params={"code": "123456"})
    assert sender.sent[0]["mobile"] == "13800138000"


async def test_send_returns_message_id() -> None:
    sender = LoggingSmsSender()
    message_id = await sender.send(mobile="13800138000", scene="LOGIN", params={"code": "123456"})
    assert message_id.startswith("log-")
    assert sender.sent[0]["scene"] == "LOGIN"
    assert sender.sent[0]["params"]["code"] == "123456"


async def test_unknown_scene_is_rejected() -> None:
    """短信是成本与滥用面：场景白名单外的调用必须失败，而不是打出去。"""
    sender = LoggingSmsSender()
    with pytest.raises(SmsSendError, match="未知短信场景"):
        await sender.send(mobile="13800138000", scene="MARKETING", params={})


async def test_log_never_contains_full_mobile(caplog: pytest.LogCaptureFixture) -> None:
    """PRD §4.3：手机号进日志必须脱敏 —— 明文是合规事故，也是撞库素材。"""
    sender = LoggingSmsSender()
    with caplog.at_level(logging.INFO, logger="app.core.sms"):
        await sender.send(mobile="13800138000", scene="LOGIN", params={"code": "123456"})
    assert "13800138000" not in caplog.text
    assert "138****8000" in caplog.text


def test_mask_mobile() -> None:
    assert mask_mobile("13800138000") == "138****8000"
    assert mask_mobile("12345") == "***"


def test_sender_is_process_singleton() -> None:
    reset_sms_sender()
    assert get_sms_sender() is get_sms_sender()
    reset_sms_sender()
