"""状态枚举唯一可信源（SSOT）。

=====================================================================
本文件是全项目状态枚举的**唯一权威定义**。
=====================================================================

为什么必须单列：
    电商项目里同一个状态会在四处出现——
      DDL 存数字 / PRD 写英文状态机 / 后端常量 / 前端映射。
    三处以上并存必然漂移（"10 到底是待付款还是待发货"）。

本文件与 docs/DATA-DICTIONARY.md §一 逐字对应，由
tests/contract/test_enum_mapping.py 自动校验，**任何一侧漂移都会让 CI 变红**。

修改规则：
    1. 只改本文件 + docs/DATA-DICTIONARY.md，两处同步。
    2. 枚举成员名必须与文档「PRD 状态名（代码常量）」列逐字一致。
    3. 枚举值必须与文档「值」列一致。
    4. 禁止在业务代码里出现裸数字（如 `if order.status == 20`）。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

# =====================================================================
# 交易域
# =====================================================================


class OrderStatus(IntEnum):
    """biz_order.status — 文档 §一 / PRD §6.1"""

    PENDING_PAY = 10
    PAID = 20
    SHIPPED = 30
    PENDING_REVIEW = 40
    COMPLETED = 50
    CANCELLED = 60
    AFTER_SALE = 70
    CLOSED = 80


class PaymentStatus(IntEnum):
    """biz_payment.status — 文档 §一 / PRD §6.4"""

    INIT = 0
    PAYING = 1
    SUCCESS = 2
    FAILED = 3
    CLOSED = 4


class RefundStatus(IntEnum):
    """biz_refund.status — 文档 §一 / PRD §6.5"""

    APPLIED = 0
    REJECTED = 1
    WAIT_RETURN = 2
    WAIT_RECEIVE = 3
    REFUNDING = 4
    REFUND_SUCCESS = 5
    REFUND_FAILED = 6
    CANCELLED = 7


class UserCouponStatus(IntEnum):
    """biz_user_coupon.status — 文档 §一 / PRD §6.3"""

    UNUSED = 1
    LOCKED = 2
    USED = 3
    EXPIRED = 4
    FROZEN = 5


class StockChangeType(IntEnum):
    """biz_stock_log.change_type — 文档 §一 / PRD §6.2

    库存四段记账（total = available + locked + sold）的变动类型。
    每种类型的账目影响见文档「说明」列，由 test_stock_invariant.py 校验。
    """

    LOCK = 1  # available-1, locked+1
    PAY = 2  # locked-1, sold+1
    RELEASE = 3  # locked-1, available+1
    ADJUST = 4  # total+n, available+n（必填原因）
    RETURN = 5  # sold-1, available+1（退货收货后）
    REFUND = 6  # sold-1, available+1（仅退款）


class OrderOperatorType(IntEnum):
    """biz_order_log.operator_type — 文档 §一 / PRD §6.1"""

    USER = 1
    MERCHANT = 2
    SYSTEM = 3
    AGENT = 4


# =====================================================================
# AI 客服域
# =====================================================================


class ConversationStatus(IntEnum):
    """ai_conversation.status — 文档 §一 / PRD §9.8"""

    AI_SERVING = 1
    HANDOFF_REQUESTED = 2
    HUMAN_SERVING = 3
    CLOSED = 4


class HandoffReason(IntEnum):
    """ai_handoff_record.reason — 文档 §一 / PRD §9.4（转人工 5 类触发）"""

    LOW_CONFIDENCE = 1  # S1/S2/S3 任一低置信
    USER_REQUEST = 2  # 用户显式要求转人工
    ORDER_DISPUTE = 3  # 涉及金额争议/投诉/法律
    NEGATIVE_SENTIMENT = 4  # 连续两次表达不满
    UNRESOLVED = 5  # 同一问题连续 3 轮未解决


class HandoffQueueStatus(IntEnum):
    """ai_handoff_record.queue_status — 文档 §一 / PRD §9.5"""

    QUEUED = 1
    SERVING = 2
    FINISHED = 3
    ABANDONED = 4  # 超 60s 无坐席接入


class MessageRole(IntEnum):
    """ai_message.role — 文档 §一 / PRD §9.1"""

    USER = 1
    ASSISTANT = 2
    SYSTEM = 3
    TOOL = 4  # 订单查询等工具调用结果


class KbDocumentStatus(IntEnum):
    """ai_kb_document.status — 文档 §一 / PRD §9.2"""

    PENDING = 0
    PROCESSING = 1
    ACTIVE = 2
    FAILED = 3  # 原因见 error_msg


# =====================================================================
# 枚举 → 文档表头映射（契约测试的锚点）
# =====================================================================
#
# key   = docs/DATA-DICTIONARY.md §一 里的三级标题中的表名
# value = (枚举类, 字段名)
#
# test_enum_mapping.py 用这张表定位文档中的 markdown 表格进行逐行比对。
# 新增枚举时必须同步登记于此。

ENUM_REGISTRY: Final[dict[str, tuple[type[IntEnum], str]]] = {
    "biz_order.status": (OrderStatus, "status"),
    "biz_payment.status": (PaymentStatus, "status"),
    "biz_refund.status": (RefundStatus, "status"),
    "biz_user_coupon.status": (UserCouponStatus, "status"),
    "biz_stock_log.change_type": (StockChangeType, "change_type"),
    "biz_order_log.operator_type": (OrderOperatorType, "operator_type"),
    "ai_conversation.status": (ConversationStatus, "status"),
    "ai_handoff_record.reason": (HandoffReason, "reason"),
    "ai_handoff_record.queue_status": (HandoffQueueStatus, "queue_status"),
    "ai_message.role": (MessageRole, "role"),
    "ai_kb_document.status": (KbDocumentStatus, "status"),
}
