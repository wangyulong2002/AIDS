"""AIDS ORM 模型 —— `ai_*` 表（**生成物，请勿手改**）。

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


class AiAgent(Base, PKMixin, TimestampMixin):
    """客服坐席表（`ai_agent`）。"""

    __tablename__ = "ai_agent"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='关联 sys_user.id')
    agent_no: Mapped[str] = mapped_column(VARCHAR(32), comment='坐席工号')
    nickname: Mapped[str] = mapped_column(VARCHAR(32), comment='对外展示名')
    max_concurrent: Mapped[int] = mapped_column(TINYINT, server_default=text('5'), comment='最大同时接待会话数')
    skill_tags: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True, server_default=text('NULL'), comment='技能标签(逗号分隔, 用于路由)')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='启用状态: 0停用 1启用(实时在线态见Redis)')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("agent_no", name="uk_agent_no"),
        UniqueConstraint("user_id", name="uk_user_id"),
    )


class AiConversation(Base, PKMixin, TimestampMixin):
    """AI会话表（`ai_conversation`）。"""

    __tablename__ = "ai_conversation"

    conversation_no: Mapped[str] = mapped_column(VARCHAR(32), comment='会话编号')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='游客使用匿名会话ID(负数或独立号段)')
    channel: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='来源: 1商品页 2订单页 3独立对话页')
    spu_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='咨询商品(商品页来源时)')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1 AI接待中 2待人工接入 3人工接待中 4已结束')
    resolved: Mapped[int | None] = mapped_column(TINYINT, nullable=True, server_default=text('NULL'), comment='是否解决: 1是 0否 (结束时按 PRD §9.6 判定)')
    close_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("conversation_no", name="uk_conversation_no"),
        Index("idx_user_id", "user_id", "status"),
        Index("idx_status_update", "status", "update_time"),  # 会话超时结束扫描
    )


class AiFeedback(Base, PKMixin, CreateTimeMixin):
    """AI会话评价表（`ai_feedback`）。"""

    __tablename__ = "ai_feedback"

    conversation_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    message_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), comment='针对的助手消息')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    rating: Mapped[int] = mapped_column(TINYINT, comment='评价: 1赞 -1踩')
    comment: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='补充说明')

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uk_message_user"),  # 同一用户对同一消息仅一次评价
        Index("idx_conversation_id", "conversation_id"),
    )


class AiHandoffRecord(Base, PKMixin, TimestampMixin):
    """转人工记录表（`ai_handoff_record`）。"""

    __tablename__ = "ai_handoff_record"

    conversation_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    order_no: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'), comment='关联订单号(争议场景)')
    reason: Mapped[int] = mapped_column(TINYINT, comment='转接原因: 1低置信度 2用户要求 3订单争议 4多次不满 5连续未解决')
    queue_status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1排队中 2接入中 3已完成 4用户放弃')
    agent_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='客服人员(ai_agent.id)')
    agent_name: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'))
    accept_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'), comment='接入时间')
    close_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_conversation_id", "conversation_id"),
        Index("idx_agent_status", "agent_id", "queue_status"),
        Index("idx_queue_status_time", "queue_status", "create_time"),  # 排队超时扫描
    )


class AiKbChunk(Base, PKMixin, CreateTimeMixin):
    """AI知识库切片表（`ai_kb_chunk`）。"""

    __tablename__ = "ai_kb_chunk"

    document_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    domain: Mapped[str] = mapped_column(VARCHAR(16), comment='冗余知识域, 检索路由用')
    content: Mapped[str] = mapped_column(TEXT, comment='切片原文')
    token_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='切片token数')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1生效 0失效(文档重建后旧切片置0)')

    __table_args__ = (
        Index("idx_document_id", "document_id"),
        Index("idx_domain_status", "domain", "status"),
    )


class AiKbDocument(Base, PKMixin, TimestampMixin, OptimisticLockMixin):
    """AI知识库文档表（`ai_kb_document`）。"""

    __tablename__ = "ai_kb_document"

    title: Mapped[str] = mapped_column(VARCHAR(128), comment='文档标题')
    domain: Mapped[str] = mapped_column(VARCHAR(16), comment='知识域: product/policy/faq')
    file_type: Mapped[str] = mapped_column(VARCHAR(16), server_default=text('md'), comment='类型: pdf/docx/md/text')
    file_url: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='原始文件URL(手工录入为NULL)')
    content: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment='纯文本内容')
    chunk_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'), comment='切片数量')
    enabled: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='是否生效: 0否 1是')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待处理 1处理中 2已生效 3处理失败')
    error_msg: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True, server_default=text('NULL'), comment='处理失败原因')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_domain_enabled", "domain", "enabled"),
    )


class AiMessage(Base, PKMixin, CreateTimeMixin):
    """AI消息表（`ai_message`）。"""

    __tablename__ = "ai_message"

    conversation_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    role: Mapped[int] = mapped_column(TINYINT, comment='角色: 1用户 2助手 3系统 4工具返回')
    content: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True, comment='消息内容')
    intent: Mapped[str | None] = mapped_column(VARCHAR(16), nullable=True, server_default=text('NULL'), comment='识别意图: presale/order/aftersale/chat/handoff')
    confidence: Mapped[Decimal | None] = mapped_column(DECIMAL(4,3), nullable=True, server_default=text('NULL'), comment='意图置信度 0-1')
    retrieved_chunk_ids: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='本次检索命中的切片ID数组(溯源)')
    model: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='使用的Ark模型')
    prompt_tokens: Mapped[int | None] = mapped_column(INTEGER, nullable=True, server_default=text('NULL'))
    completion_tokens: Mapped[int | None] = mapped_column(INTEGER, nullable=True, server_default=text('NULL'))
    latency_ms: Mapped[int | None] = mapped_column(INTEGER, nullable=True, server_default=text('NULL'), comment='首字延迟ms')
    filtered: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='是否被敏感词拦截: 0否 1是')

    __table_args__ = (
        Index("idx_conversation_id", "conversation_id", "create_time"),
    )
