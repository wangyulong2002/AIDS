"""Mock 渠道的常量与状态机取值（全部命名常量 —— C2 禁止 status 字段配魔法数字）。"""

from __future__ import annotations

from typing import Final

# ---- 渠道支付单状态（mock_payment_order.status）----
PAY_PENDING: Final[int] = 0
PAY_SUCCESS: Final[int] = 1
PAY_CLOSED: Final[int] = 2
PAY_FAILED: Final[int] = 3

# ---- 渠道退款单状态（mock_refund_order.status）----
REFUND_PROCESSING: Final[int] = 0
REFUND_SUCCESS: Final[int] = 1
REFUND_FAILED: Final[int] = 2

# ---- 回调投递状态（mock_callback_log.status）----
CALLBACK_WAITING: Final[int] = 0
CALLBACK_DELIVERED: Final[int] = 1
CALLBACK_FAILED: Final[int] = 2
CALLBACK_ABANDONED: Final[int] = 3

# ---- 回调类型（mock_callback_log.biz_type）----
BIZ_TYPE_PAYMENT: Final[int] = 1
BIZ_TYPE_REFUND: Final[int] = 2

# ---- 对账单状态（mock_recon_file.status）----
RECON_GENERATING: Final[int] = 0
RECON_READY: Final[int] = 1

# ---- 短信记录状态（mock_sms_record.status）----
SMS_WAITING: Final[int] = 0
SMS_SENT: Final[int] = 1
SMS_FAILED: Final[int] = 2

# ---- 物流状态（mock_logistics_order.status）----
LOGISTICS_PICKUP_PENDING: Final[int] = 0
LOGISTICS_IN_TRANSIT: Final[int] = 1
LOGISTICS_DELIVERING: Final[int] = 2
LOGISTICS_SIGNED: Final[int] = 3
LOGISTICS_EXCEPTION: Final[int] = 4

# ---- 故障注入（PRD §8.3 / MOCK-04 的开关，env 下发；缺省全部关闭）----
ENV_CALLBACK_DELAY_SECONDS = "MOCK_CALLBACK_DELAY_SECONDS"
ENV_CALLBACK_REPEAT_TIMES = "MOCK_CALLBACK_REPEAT_TIMES"
ENV_CALLBACK_LOSS_RATE = "MOCK_CALLBACK_LOSS_RATE"
ENV_CHANNEL_FAILURE_RATE = "MOCK_CHANNEL_FAILURE_RATE"
