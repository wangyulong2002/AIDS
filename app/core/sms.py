"""短信服务抽象接口（BE-05）。

为什么先只做抽象、不做实现：
    短信的唯一通道是 **Mock 服务**（MOCK-02，独立服务独立端口），真实渠道因资质
    不可接入（PRD 边界声明）。BE-05 的交付物是**接口形状**：验证码/通知的调用方
    （BE-07 注册登录）只依赖 `SmsSender`，Mock 通道就绪后换实现、调用方零改动
    —— 与支付渠道的 `PaymentChannel` 抽象同一思路。

开发环境行为：
    `LoggingSmsSender` 把内容打进日志（API.md §2.1：Mock 验证码回显由 MOCK-02
    决定，seed 数据下固定 `123456`）。**手机号在日志中必须脱敏**（PRD §4.3）——
    明文手机号进日志是合规事故，也是撞库素材。

`mobile` 参数刻意收 `str` 且由调用方保证已加密存储侧的分离：
    这里拿到的是**发送时的明文**（运营商接口只认明文），但调用方（BE-07）落库
    必须走 `biz_user.mobile`（AES-GCM 密文）+ `mobile_hash`（HMAC）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

# 合法场景：调用方传别的值直接拒 —— 短信是成本与滥用面，不能放开
VALID_SCENES: Final[tuple[str, ...]] = ("LOGIN", "REGISTER")


def mask_mobile(mobile: str) -> str:
    """日志脱敏：`13800138000` → `138****8000`（API.md §2.1 的展示口径）。"""
    if len(mobile) != 11:
        return "***"
    return f"{mobile[:3]}****{mobile[-4:]}"


class SmsSendError(RuntimeError):
    """短信发送失败（频率限制 / 渠道不可用）。调用方转成对应业务错误码。

    刻意不继承 `BusinessError`：本模块是**接口层**，选错误码是调用方
    （BE-07，对应 2xxxx 段）的职责——接口层不该替业务层决定对外错误码。
    """


class SmsSender(Protocol):
    """短信发送接口。实现方：LoggingSmsSender（开发）→ Mock 通道（MOCK-02）。"""

    async def send(self, *, mobile: str, scene: str, params: dict[str, str]) -> str:
        """发送一条短信，返回渠道侧消息 ID。失败抛 `SmsSendError`。"""
        ...


class LoggingSmsSender:
    """开发环境实现：内容进日志（已脱敏），不产生任何外部调用与成本。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []  # 供测试/联调断言（进程内可见）

    async def send(self, *, mobile: str, scene: str, params: dict[str, str]) -> str:
        if scene not in VALID_SCENES:
            raise SmsSendError(f"未知短信场景：{scene}")
        message_id = f"log-{uuid.uuid4().hex[:12]}"
        logger.info(
            "发送短信 scene=%s mobile=%s params=%s message_id=%s",
            scene,
            mask_mobile(mobile),
            params,
            message_id,
        )
        self.sent.append({"mobile": mobile, "scene": scene, "params": params, "id": message_id})
        return message_id


_sender: SmsSender | None = None


def get_sms_sender() -> SmsSender:
    """进程级单例。真实通道就绪后在此按环境切换（MOCK-02 接入点）。"""
    global _sender  # noqa: PLW0603 - 进程级单例
    if _sender is None:
        _sender = LoggingSmsSender()
    return _sender


def reset_sms_sender() -> None:
    global _sender  # noqa: PLW0603
    _sender = None
