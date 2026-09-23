"""AIDS ORM 模型 —— `biz_*` 表（**生成物，请勿手改**）。

单一来源：`docs/sql/schema.sql`
生成器：  `python3 scripts/gen_orm_models.py --write`
校验：    `python3 scripts/gen_orm_models.py --check`（CI / pre-commit）

要加业务逻辑（relationship / 领域方法），写到 `app/models/relations.py`
或各 service 层 —— 重新生成会覆盖本文件。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    DECIMAL,
    INTEGER,
    JSON,
    LONGTEXT,
    TEXT,
    TINYINT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.orm.base import Base
from app.orm.mixins import (
    CreateTimeMixin,
    OptimisticLockMixin,
    PKMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class BizAddress(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """收货地址表（`biz_address`）。"""

    __tablename__ = "biz_address"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='用户ID')
    receiver: Mapped[str] = mapped_column(VARCHAR(32), comment='收货人')
    phone: Mapped[str] = mapped_column(VARCHAR(128), comment='联系电话密文(AES-256-GCM)')
    phone_hash: Mapped[str] = mapped_column(CHAR(64), comment='联系电话HMAC-SHA256(仅查询, 不唯一)')
    province: Mapped[str] = mapped_column(VARCHAR(32), comment='省')
    city: Mapped[str] = mapped_column(VARCHAR(32), comment='市')
    district: Mapped[str] = mapped_column(VARCHAR(32), comment='区/县')
    detail: Mapped[str] = mapped_column(VARCHAR(255), comment='详细地址')
    is_default: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='默认地址: 0否 1是')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_user_id", "user_id"),
    )


class BizBrand(Base, PKMixin, TimestampMixin):
    """品牌表（`biz_brand`）。"""

    __tablename__ = "biz_brand"

    name: Mapped[str] = mapped_column(VARCHAR(64), comment='品牌名称')
    logo: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='品牌LOGO')
    description: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True, server_default=text('NULL'), comment='品牌描述')
    sort: Mapped[int] = mapped_column(INTEGER, server_default=text('0'))
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("name", name="uk_name"),
    )


class BizCart(Base, PKMixin, TimestampMixin):
    """购物车表（`biz_cart`）。"""

    __tablename__ = "biz_cart"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    quantity: Mapped[int] = mapped_column(INTEGER, server_default=text('1'), comment='数量(>0)')
    checked: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='是否勾选: 0否 1是')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("user_id", "sku_id", name="uk_user_sku"),
    )


class BizCategory(Base, PKMixin, TimestampMixin):
    """商品分类表（`biz_category`）。"""

    __tablename__ = "biz_category"

    parent_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), server_default=text('0'), comment='父类目ID, 0为根')
    name: Mapped[str] = mapped_column(VARCHAR(32), comment='类目名称')
    level: Mapped[int] = mapped_column(TINYINT, comment='层级: 1/2/3')
    icon: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='图标URL')
    sort: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='排序, 越小越靠前')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_parent_id", "parent_id"),
    )


class BizCouponTemplate(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """优惠券模板表（`biz_coupon_template`）。"""

    __tablename__ = "biz_coupon_template"

    name: Mapped[str] = mapped_column(VARCHAR(64), comment='券名称')
    type: Mapped[int] = mapped_column(TINYINT, comment='类型: 1满减 2折扣 3无门槛')
    threshold_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='使用门槛(满X元), 0为无门槛')
    discount_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='优惠金额(满减/无门槛)')
    discount_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(3,2), nullable=True, server_default=text('NULL'), comment='折扣率(折扣券, 如0.90)')
    max_discount: Mapped[Decimal | None] = mapped_column(DECIMAL(12,2), nullable=True, server_default=text('NULL'), comment='最大优惠(折扣券封顶)')
    total_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='发放总量')
    remain_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='剩余数量(原子扣减: UPDATE ... WHERE remain_count >= 1)')
    per_limit: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='每人限领张数(应用层校验已领数)')
    start_time: Mapped[dt.datetime] = mapped_column(DATETIME, comment='可领取开始时间')
    end_time: Mapped[dt.datetime] = mapped_column(DATETIME, comment='可领取结束时间')
    valid_days: Mapped[int] = mapped_column(INTEGER, server_default=text('7'), comment='领取后有效天数')
    scope_type: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='适用范围: 1全场 2指定类目 3指定商品')
    scope_ids: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='指定类目/商品ID数组')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0草稿 1发放中 2已结束 3已下架')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_status_time", "status", "start_time", "end_time"),
    )


class BizDelivery(Base, PKMixin, TimestampMixin):
    """运单表（`biz_delivery`）。"""

    __tablename__ = "biz_delivery"

    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str] = mapped_column(VARCHAR(32))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    company_code: Mapped[str] = mapped_column(VARCHAR(32), comment='快递公司编码(如 SF/YTO)')
    company_name: Mapped[str] = mapped_column(VARCHAR(64), comment='快递公司名称')
    delivery_no: Mapped[str] = mapped_column(VARCHAR(64), comment='运单号')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待揽收 1运输中 2派送中 3已签收 4异常')
    deliver_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='发货时间')
    sign_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='签收时间')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("delivery_no", name="uk_delivery_no"),
        Index("idx_order_no", "order_no"),
    )


class BizDeliveryTrace(Base, PKMixin, CreateTimeMixin):
    """物流轨迹表（`biz_delivery_trace`）。"""

    __tablename__ = "biz_delivery_trace"

    delivery_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    delivery_no: Mapped[str] = mapped_column(VARCHAR(64), comment='冗余运单号')
    trace_time: Mapped[dt.datetime] = mapped_column(DATETIME, comment='轨迹发生时间')
    status_desc: Mapped[str] = mapped_column(VARCHAR(64), comment='轨迹描述(如"已签收")')
    location: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True, server_default=text('NULL'), comment='所在地点')

    __table_args__ = (
        UniqueConstraint("delivery_no", "trace_time", "status_desc", name="uk_delivery_time_desc"),  # 拉取幂等
        Index("idx_delivery_id", "delivery_id"),
    )


class BizFreightTemplate(Base, PKMixin, TimestampMixin):
    """运费模板表（`biz_freight_template`）。"""

    __tablename__ = "biz_freight_template"

    name: Mapped[str] = mapped_column(VARCHAR(64), comment='模板名称')
    free_threshold: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='包邮门槛(商品实付金额), 0为不包邮')
    base_freight: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='基础运费')
    remote_extra: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='偏远地区附加费')
    remote_regions: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='偏远地区省份数组, 如 ["新疆","西藏"]')
    is_default: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='是否默认模板: 0否 1是(全局唯一)')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))


class BizMessage(Base, PKMixin, CreateTimeMixin):
    """站内信表（`biz_message`）。"""

    __tablename__ = "biz_message"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    type: Mapped[int] = mapped_column(TINYINT, comment='类型: 1订单 2售后 3系统 4营销')
    title: Mapped[str] = mapped_column(VARCHAR(64))
    content: Mapped[str] = mapped_column(VARCHAR(500))
    biz_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='关联业务ID(订单/售后单)')
    is_read: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='已读: 0否 1是')
    read_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))

    __table_args__ = (
        Index("idx_user_read_time", "user_id", "is_read", "create_time"),  # 未读列表 + 倒序
    )


class BizOrder(Base, PKMixin, TimestampMixin, OptimisticLockMixin):
    """订单主表（`biz_order`）。"""

    __tablename__ = "biz_order"

    order_no: Mapped[str] = mapped_column(VARCHAR(32), comment='业务订单号(对外唯一)')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='下单用户(预留分片键)')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('10'), comment='状态: 10待付款 20待发货 30待收货 40待评价 50已完成 60已取消 70售后中 80已关闭')
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='商品总额 = Σ order_item.total_amount')
    discount_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='优惠金额 = Σ order_item.discount_amount')
    freight_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='运费')
    pay_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='实付金额 = total - discount + freight')
    refund_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='累计已退金额(含运费), 约束 <= pay_amount')
    coupon_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='使用的用户优惠券ID(biz_user_coupon.id)')
    receiver: Mapped[str] = mapped_column(VARCHAR(32), comment='收货人快照')
    receiver_phone: Mapped[str] = mapped_column(VARCHAR(128), comment='电话快照密文(AES-256-GCM)')
    receiver_addr: Mapped[str] = mapped_column(VARCHAR(500), comment='完整地址快照(省市区拼接)')
    pay_type: Mapped[int | None] = mapped_column(TINYINT, nullable=True, server_default=text('NULL'), comment='支付方式: 1支付宝 2微信')
    pay_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    delivery_company: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='快递公司(冗余自 biz_delivery, 列表展示用)')
    delivery_no: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='主运单号(冗余自 biz_delivery)')
    deliver_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='发货时间')
    finish_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='完成时间')
    cancel_reason: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='取消原因')
    cancel_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("order_no", name="uk_order_no"),
        Index("idx_user_status", "user_id", "status"),
        Index("idx_status_create", "status", "create_time"),  # 超时关单/自动收货兜底扫描(PRD §6.7)
        Index("idx_create_time", "create_time"),
    )


class BizOrderItem(Base, PKMixin, CreateTimeMixin):
    """订单明细表（`biz_order_item`）。"""

    __tablename__ = "biz_order_item"

    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str] = mapped_column(VARCHAR(32), comment='冗余订单号')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='冗余, 售后查询用')
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    spu_name: Mapped[str] = mapped_column(VARCHAR(128), comment='商品名称快照')
    sku_pic: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='SKU图片快照')
    sku_specs: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='规格快照')
    price: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='成交单价快照')
    quantity: Mapped[int] = mapped_column(INTEGER, comment='购买数量')
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='小计 = price * quantity')
    discount_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='优惠券分摊金额(按小计比例分摊, 最后一件补差)')
    pay_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='实付小计 = total_amount - discount_amount')
    refund_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='累计已退金额, 约束 <= pay_amount')
    refund_status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='售后状态: 0无 1申请中 2退款中 3已退款 4已拒绝')

    __table_args__ = (
        Index("idx_order_id", "order_id"),
        Index("idx_order_no", "order_no"),
        Index("idx_user_id", "user_id"),
        Index("idx_spu_id", "spu_id"),
    )


class BizOrderLog(Base, PKMixin, CreateTimeMixin):
    """订单状态流转日志表（`biz_order_log`）。"""

    __tablename__ = "biz_order_log"

    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str] = mapped_column(VARCHAR(32))
    from_status: Mapped[int | None] = mapped_column(TINYINT, nullable=True, server_default=text('NULL'), comment='变更前状态, NULL 表示创建')
    to_status: Mapped[int] = mapped_column(TINYINT, comment='变更后状态')
    event: Mapped[str] = mapped_column(VARCHAR(32), comment='触发事件: SUBMIT/PAY/TIMEOUT_CANCEL/DELIVER/CONFIRM/AFTER_SALE...')
    operator_type: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='操作者: 1用户 2商家 3系统 4客服')
    operator_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'))
    remark: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'))

    __table_args__ = (
        Index("idx_order_id", "order_id", "create_time"),
    )


class BizPayment(Base, PKMixin, TimestampMixin):
    """支付流水表（`biz_payment`）。"""

    __tablename__ = "biz_payment"

    payment_no: Mapped[str] = mapped_column(VARCHAR(32), comment='支付单号(商户订单号 out_trade_no)')
    order_no: Mapped[str] = mapped_column(VARCHAR(32), comment='订单号')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    pay_type: Mapped[int] = mapped_column(TINYINT, comment='支付方式: 1支付宝 2微信')
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='支付金额')
    refunded_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='累计已退金额, 约束 <= amount')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待支付 1已发起待回调 2成功 3失败 4已关闭')
    channel_trade_no: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='渠道交易号')
    channel_resp: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='渠道回调原始报文(验签后)')
    callback_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='成功回调时间')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("payment_no", name="uk_payment_no"),
        UniqueConstraint("channel_trade_no", name="uk_channel_trade_no"),  # 渠道回调幂等(MySQL 允许多个 NULL)
        Index("idx_order_no", "order_no"),
        Index("idx_status_update", "status", "update_time"),  # 主动查询补偿扫描
    )


class BizProductReview(Base, PKMixin, TimestampMixin):
    """商品评价表（`biz_product_review`）。"""

    __tablename__ = "biz_product_review"

    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_item_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    score: Mapped[int] = mapped_column(TINYINT, comment='评分: 1-5星')
    content: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True, server_default=text('NULL'), comment='评价内容')
    images: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='评价图片URL数组')
    is_anonymous: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='匿名评价: 0否 1是')
    reply: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True, server_default=text('NULL'), comment='商家回复')
    reply_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1显示 0隐藏(违规)')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("order_item_id", name="uk_order_item_id"),  # 一个订单项一条评价
        Index("idx_spu_id_score", "spu_id", "score"),
        Index("idx_spu_time", "spu_id", "create_time"),  # 商品评价列表按时间倒序
    )


class BizRefund(Base, PKMixin, TimestampMixin):
    """售后单表（`biz_refund`）。"""

    __tablename__ = "biz_refund"

    refund_no: Mapped[str] = mapped_column(VARCHAR(32), comment='售后单号')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str] = mapped_column(VARCHAR(32), comment='冗余订单号')
    order_item_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    type: Mapped[int] = mapped_column(TINYINT, comment='类型: 1仅退款 2退货退款')
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='退款金额(商品分摊金额, 运费单独退)')
    freight_amount: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='退还运费(PRD §6.6: 运费全额退还)')
    reason: Mapped[str] = mapped_column(VARCHAR(255), comment='申请原因')
    evidence: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='凭证图片URL数组')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待审核 1已拒绝 2待退货 3待收货 4退款中 5已完成 6退款失败 7已撤销')
    audit_opinion: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='审核意见')
    audit_user_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='审核人')
    audit_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    channel_refund_no: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='渠道退款单号')
    finish_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("refund_no", name="uk_refund_no"),
        Index("idx_order_no", "order_no"),
        Index("idx_user_id", "user_id"),
        Index("idx_order_item_id", "order_item_id"),
        Index("idx_status", "status"),
    )


class BizSku(Base, PKMixin, TimestampMixin):
    """SKU表（`biz_sku`）。"""

    __tablename__ = "biz_sku"

    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    sku_code: Mapped[str] = mapped_column(VARCHAR(64), comment='SKU编码(唯一)')
    specs: Mapped[Any] = mapped_column(JSON, comment='销售规格, 如 [{"k":"颜色","v":"红"},{"k":"尺寸","v":"XL"}]')
    price: Mapped[Decimal] = mapped_column(DECIMAL(12,2), comment='售价')
    original_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12,2), nullable=True, server_default=text('NULL'), comment='划线价')
    pic: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='SKU图片')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("sku_code", name="uk_sku_code"),
        Index("idx_spu_id", "spu_id"),
    )


class BizSkuStock(Base, PKMixin, TimestampMixin, OptimisticLockMixin):
    """SKU库存表（`biz_sku_stock`）。"""

    __tablename__ = "biz_sku_stock"

    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    total_stock: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='总库存')
    available_stock: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='可售库存')
    locked_stock: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='锁定库存(下单未支付预扣)')
    sold_stock: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='已售出库存')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("sku_id", name="uk_sku_id"),
    )


class BizSpu(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """SPU商品表（`biz_spu`）。"""

    __tablename__ = "biz_spu"

    category_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='三级类目ID')
    brand_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='品牌ID')
    freight_template_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='运费模板ID(PRD §6.6), NULL 用默认模板')
    name: Mapped[str] = mapped_column(VARCHAR(128), comment='商品名称')
    subtitle: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='副标题')
    main_pic: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='主图URL')
    detail: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment='商品富文本详情')
    min_price: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='SKU最低价(冗余, 列表排序用)')
    max_price: Mapped[Decimal] = mapped_column(DECIMAL(12,2), server_default=text('0.00'), comment='SKU最高价(冗余)')
    total_stock: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='总库存(冗余, 各SKU total_stock 之和)')
    sales: Mapped[int] = mapped_column(INTEGER(unsigned=True), server_default=text('0'), comment='销量(冗余)')
    view_count: Mapped[int] = mapped_column(BIGINT(unsigned=True), server_default=text('0'), comment='浏览量(异步落库)')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0草稿 1待审核 2上架 3下架')
    audit_opinion: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='审核意见')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_category_status", "category_id", "status"),
        Index("idx_brand_id", "brand_id"),
    )


class BizSpuImage(Base, PKMixin, CreateTimeMixin):
    """SPU图集表（`biz_spu_image`）。"""

    __tablename__ = "biz_spu_image"

    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    url: Mapped[str] = mapped_column(VARCHAR(255), comment='图片URL')
    sort: Mapped[int] = mapped_column(INTEGER, server_default=text('0'))

    __table_args__ = (
        Index("idx_spu_id", "spu_id"),
    )


class BizStockLog(Base, PKMixin, CreateTimeMixin):
    """库存变更流水表（`biz_stock_log`）。"""

    __tablename__ = "biz_stock_log"

    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'), comment='关联订单号(手动调整为NULL)')
    refund_no: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'), comment='关联售后单号(退货/退款回补时)')
    change_type: Mapped[int] = mapped_column(TINYINT, comment='类型: 1下单锁定 2支付扣减 3取消释放 4手动调整 5退货入库 6退款回补')
    change_num: Mapped[int] = mapped_column(INTEGER, comment='变更数量(正入负出)')
    before_num: Mapped[int] = mapped_column(INTEGER, comment='变更前可用库存')
    after_num: Mapped[int] = mapped_column(INTEGER, comment='变更后可用库存')
    operator_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='操作人(手动调整时)')
    remark: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'))
    idempotent_key: Mapped[str | None] = mapped_column(VARCHAR(96), nullable=True, server_default=text('NULL'), comment='幂等键(PRD §6.8), 构造规则见上')

    __table_args__ = (
        UniqueConstraint("idempotent_key", name="uk_idempotent_key"),
        Index("idx_sku_id", "sku_id"),
        Index("idx_order_no", "order_no"),
        Index("idx_refund_no", "refund_no"),
    )


class BizUser(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """用户表（`biz_user`）。"""

    __tablename__ = "biz_user"

    mobile: Mapped[str] = mapped_column(VARCHAR(128), comment='手机号密文(AES-256-GCM, Base64)')
    mobile_hash: Mapped[str] = mapped_column(CHAR(64), comment='手机号HMAC-SHA256(登录查询/唯一约束)')
    password: Mapped[str | None] = mapped_column(CHAR(60), nullable=True, server_default=text('NULL'), comment='密码(BCrypt), 验证码注册可为空')
    nickname: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'), comment='昵称')
    avatar: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='头像URL')
    gender: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='性别: 0未知 1男 2女')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1正常 0禁用')
    register_channel: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='注册渠道: 1手机验证码 2密码 3微信 4支付宝')
    last_login_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='最后登录时间')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("mobile_hash", name="uk_mobile_hash"),
    )


class BizUserCoupon(Base, PKMixin, TimestampMixin):
    """用户优惠券表（`biz_user_coupon`）。"""

    __tablename__ = "biz_user_coupon"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    coupon_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='biz_coupon_template.id')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1未使用 2锁定中 3已使用 4已过期 5冻结中(退款)')
    order_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='锁定/使用的订单ID')
    receive_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"), comment='领取时间')
    expire_time: Mapped[dt.datetime] = mapped_column(DATETIME, comment='过期时间')
    use_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_user_status", "user_id", "status"),
        Index("idx_coupon_id", "coupon_id"),
        Index("idx_status_expire", "status", "expire_time"),  # 过期扫描
    )
