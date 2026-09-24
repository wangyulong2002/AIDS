"""Mock 库（`aids_mock`）的 ORM 模型 —— 与 docs/sql/mock_schema.sql 逐表对应。

为什么 Mock 服务有独立的 Base 与独立模型（不复用 app/models）：
    Mock 模拟的是「第三方渠道」，与商户系统是**不同主体、不同数据库**（mock_schema.sql
    头部）。物理隔离才能真实模拟渠道故障；模型也随之独立 —— 渠道的字段与状态机
    与商户侧 `biz_payment` 刻意不同步（渠道有自己的生命周期）。

与 DDL 的一致性：由 `tests/contract/test_mock_schema.py` 对照 mock_schema.sql 校验
（列名 / 唯一键），防止手写模型与 DDL 漂移 —— 与 app/models 的 C1/C9 同思路。

数值默认值全部走 `server_default=text(...)`（与 app/models 同款）：`default=0` 这类
Python 侧默认会让 C2 扫描器把「status 字段 + 字面量」当魔法数字报出来。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DATE,
    DATETIME,
    DECIMAL,
    INTEGER,
    JSON,
    SMALLINT,
    VARCHAR,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.orm.mixins import PKMixin  # 复用雪花 ID 主键约定（与主业务同源）


class MockBase(DeclarativeBase):
    """Mock 库独立 Base —— 与主业务库的 Base 严格分离。"""


class MockPaymentOrder(PKMixin, MockBase):
    """渠道支付单表（`mock_payment_order`）。"""

    __tablename__ = "mock_payment_order"

    out_trade_no: Mapped[str] = mapped_column(VARCHAR(32), comment="商户订单号(商户传入)")
    trade_no: Mapped[str] = mapped_column(VARCHAR(64), comment="渠道交易号(渠道生成)")
    merchant_id: Mapped[str] = mapped_column(VARCHAR(32), comment="商户号")
    pay_type: Mapped[int] = mapped_column(TINYINT, comment="支付方式: 1支付宝 2微信")
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), comment="订单金额")
    subject: Mapped[str] = mapped_column(VARCHAR(128), comment="订单标题")
    notify_url: Mapped[str] = mapped_column(VARCHAR(255), comment="异步通知地址")
    return_url: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    status: Mapped[int] = mapped_column(
        TINYINT, server_default=text("0"), comment="状态: 0待支付 1已支付 2已关闭 3支付失败"
    )
    refunded_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(12, 2), server_default=text("0.00"), comment="累计已退金额"
    )
    pay_time: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    close_time: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    expire_time: Mapped[datetime] = mapped_column(DATETIME, comment="支付超时时间(超过则渠道关单)")
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")
    update_time: Mapped[datetime] = mapped_column(
        DATETIME, server_default="CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )

    __table_args__ = (
        UniqueConstraint("out_trade_no", name="uk_out_trade_no"),
        UniqueConstraint("trade_no", name="uk_trade_no"),
        Index("idx_status_expire", "status", "expire_time"),
    )


class MockRefundOrder(PKMixin, MockBase):
    """渠道退款单表（`mock_refund_order`）。"""

    __tablename__ = "mock_refund_order"

    out_refund_no: Mapped[str] = mapped_column(VARCHAR(32), comment="商户退款单号")
    refund_no: Mapped[str] = mapped_column(VARCHAR(64), comment="渠道退款单号")
    out_trade_no: Mapped[str] = mapped_column(VARCHAR(32), comment="关联商户订单号")
    trade_no: Mapped[str] = mapped_column(VARCHAR(64), comment="关联渠道交易号")
    refund_amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), comment="退款金额")
    reason: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    status: Mapped[int] = mapped_column(
        TINYINT, server_default=text("0"), comment="状态: 0退款中 1退款成功 2退款失败"
    )
    refund_time: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")
    update_time: Mapped[datetime] = mapped_column(
        DATETIME, server_default="CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )

    __table_args__ = (
        UniqueConstraint("out_refund_no", name="uk_out_refund_no"),
        UniqueConstraint("refund_no", name="uk_refund_no"),
        Index("idx_out_trade_no", "out_trade_no"),
    )


class MockCallbackLog(PKMixin, MockBase):
    """回调投递记录表（`mock_callback_log`）—— 故障注入 + 幂等验证的核心。"""

    __tablename__ = "mock_callback_log"

    biz_type: Mapped[int] = mapped_column(SMALLINT, comment="类型: 1支付回调 2退款回调")
    out_trade_no: Mapped[str] = mapped_column(VARCHAR(32), comment="关联商户订单号")
    notify_url: Mapped[str] = mapped_column(VARCHAR(255))
    payload: Mapped[Any] = mapped_column(JSON, comment="回调报文(含签名)")
    sign: Mapped[str] = mapped_column(VARCHAR(512), comment="RSA2签名")
    attempt_no: Mapped[int] = mapped_column(INTEGER, server_default=text("1"), comment="第几次推送")
    status: Mapped[int] = mapped_column(
        SMALLINT, server_default=text("0"), comment="状态: 0待推送 1推送成功 2推送失败 3已放弃"
    )
    http_status: Mapped[int | None] = mapped_column(INTEGER, nullable=True)
    resp_body: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    next_retry_time: Mapped[datetime] = mapped_column(
        DATETIME, server_default="CURRENT_TIMESTAMP", comment="下次推送时间(退避)"
    )
    retry_count: Mapped[int] = mapped_column(INTEGER, server_default=text("0"))
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")
    update_time: Mapped[datetime] = mapped_column(
        DATETIME, server_default="CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )

    __table_args__ = (
        Index("idx_status_retry", "status", "next_retry_time"),
        Index("idx_out_trade_no", "out_trade_no"),
    )


class MockReconFile(PKMixin, MockBase):
    """渠道对账单（`mock_recon_file`）—— T+1 生成。"""

    __tablename__ = "mock_recon_file"

    bill_date: Mapped[Any] = mapped_column(DATE, comment="账单日期(T-1)")
    pay_type: Mapped[int] = mapped_column(TINYINT, comment="支付方式: 1支付宝 2微信")
    file_url: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    file_content: Mapped[str | None] = mapped_column(
        MEDIUMTEXT, nullable=True, comment="对账明细(CSV文本, 本地演示直接返回)"
    )
    total_count: Mapped[int] = mapped_column(INTEGER, server_default=text("0"))
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), server_default=text("0.00"))
    refund_count: Mapped[int] = mapped_column(INTEGER, server_default=text("0"))
    refund_amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), server_default=text("0.00"))
    status: Mapped[int] = mapped_column(
        SMALLINT, server_default=text("0"), comment="状态: 0生成中 1已就绪"
    )
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (UniqueConstraint("bill_date", "pay_type", name="uk_date_type"),)


class MockSmsRecord(PKMixin, MockBase):
    """Mock 短信记录表（`mock_sms_record`）。"""

    __tablename__ = "mock_sms_record"

    mobile: Mapped[str] = mapped_column(VARCHAR(20), comment="接收手机号(Mock库仅演示用)")
    template_code: Mapped[str] = mapped_column(
        VARCHAR(32), comment="模板编码: LOGIN_CODE/ORDER_SHIP/NOTIFY..."
    )
    content: Mapped[str] = mapped_column(VARCHAR(500), comment="短信内容")
    params: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment="模板参数")
    biz_no: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, comment="关联业务号")
    status: Mapped[int] = mapped_column(
        SMALLINT, server_default=text("0"), comment="状态: 0待发送 1发送成功 2发送失败"
    )
    error_msg: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    send_time: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        Index("idx_mobile_time", "mobile", "create_time"),
        Index("idx_biz_no", "biz_no"),
    )


class MockLogisticsOrder(PKMixin, MockBase):
    """Mock 运单表（`mock_logistics_order`）。"""

    __tablename__ = "mock_logistics_order"

    delivery_no: Mapped[str] = mapped_column(VARCHAR(64), comment="运单号")
    company_code: Mapped[str] = mapped_column(VARCHAR(32), comment="快递公司编码")
    company_name: Mapped[str] = mapped_column(VARCHAR(64))
    sender_city: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    receiver_city: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    status: Mapped[int] = mapped_column(
        SMALLINT, server_default=text("0"), comment="状态: 0待揽收 1运输中 2派送中 3已签收 4异常"
    )
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")
    update_time: Mapped[datetime] = mapped_column(
        DATETIME, server_default="CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )

    __table_args__ = (UniqueConstraint("delivery_no", name="uk_delivery_no"),)


class MockLogisticsTrace(PKMixin, MockBase):
    """Mock 物流轨迹表（`mock_logistics_trace`）—— 轨迹由服务按时间自动推进。"""

    __tablename__ = "mock_logistics_trace"

    delivery_no: Mapped[str] = mapped_column(VARCHAR(64))
    trace_time: Mapped[datetime] = mapped_column(DATETIME, comment="轨迹发生时间")
    status_desc: Mapped[str] = mapped_column(VARCHAR(64), comment="轨迹描述")
    location: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DATETIME, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        UniqueConstraint("delivery_no", "trace_time", name="uk_delivery_time"),
        Index("idx_delivery_no", "delivery_no"),
    )
