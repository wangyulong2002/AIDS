"""AIDS ORM 模型 —— `sys_*` 表（**生成物，请勿手改**）。

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


class SysAuditLog(Base, PKMixin, CreateTimeMixin):
    """操作审计日志表（`sys_audit_log`）。"""

    __tablename__ = "sys_audit_log"

    operator_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    operator_name: Mapped[str] = mapped_column(VARCHAR(32), comment='冗余操作人姓名')
    module: Mapped[str] = mapped_column(VARCHAR(32), comment='模块, 如 order/product')
    action: Mapped[str] = mapped_column(VARCHAR(32), comment='动作, 如 ship/audit/refund')
    method: Mapped[str | None] = mapped_column(VARCHAR(8), nullable=True, server_default=text('NULL'), comment='HTTP方法')
    path: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'), comment='请求路径')
    params: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='请求参数(脱敏后)')
    result_code: Mapped[int | None] = mapped_column(INTEGER, nullable=True, server_default=text('NULL'), comment='响应code')
    ip: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='操作IP')
    user_agent: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'))
    cost_ms: Mapped[int | None] = mapped_column(INTEGER, nullable=True, server_default=text('NULL'), comment='耗时')
    trace_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='全链路追踪ID')

    __table_args__ = (
        Index("idx_operator_time", "operator_id", "create_time"),
        Index("idx_module_action", "module", "action"),
        Index("idx_trace_id", "trace_id"),
    )


class SysConfig(Base, PKMixin, TimestampMixin):
    """系统动态配置表（`sys_config`）。"""

    __tablename__ = "sys_config"

    config_key: Mapped[str] = mapped_column(VARCHAR(128), comment='配置键, 如 ai.retrieval_score_threshold')
    config_value: Mapped[str] = mapped_column(VARCHAR(512), comment='配置值(字符串, 按 value_type 解析)')
    value_type: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='值类型: 0 string 1 int 2 float 3 bool 4 json')
    description: Mapped[str] = mapped_column(VARCHAR(255), server_default=text(''), comment='配置说明')
    updated_by: Mapped[int] = mapped_column(BIGINT(unsigned=True), server_default=text('0'), comment='最后修改人 sys_user.id')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("config_key", name="uk_config_key"),
    )


class SysDeadLetter(Base, PKMixin, CreateTimeMixin):
    """死信表（`sys_dead_letter`）。"""

    __tablename__ = "sys_dead_letter"

    source: Mapped[str] = mapped_column(VARCHAR(32), comment='来源: KAFKA/DELAY_TASK/LOCAL_MSG')
    ref_key: Mapped[str] = mapped_column(VARCHAR(128), comment='业务唯一键(如 order_no)')
    topic: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True, server_default=text('NULL'), comment='来源topic')
    payload: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='原始消息体')
    error_msg: Mapped[str | None] = mapped_column(VARCHAR(1000), nullable=True, server_default=text('NULL'))
    retry_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'))
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待处理 1已重放 2已忽略')
    handle_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    handler_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True, server_default=text('NULL'), comment='处理人')

    __table_args__ = (
        Index("idx_source_status", "source", "status"),
        Index("idx_ref_key", "ref_key"),
    )


class SysLocalMessage(Base, PKMixin, TimestampMixin):
    """本地消息表（`sys_local_message`）。"""

    __tablename__ = "sys_local_message"

    biz_type: Mapped[str] = mapped_column(VARCHAR(32), comment='业务类型: ORDER/PAYMENT/STOCK/PRODUCT...')
    biz_no: Mapped[str] = mapped_column(VARCHAR(64), comment='业务单号(幂等键)')
    topic: Mapped[str] = mapped_column(VARCHAR(64), comment='目标Kafka topic')
    payload: Mapped[Any] = mapped_column(JSON, comment='消息体')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('0'), comment='状态: 0待投递 1已投递 2投递失败(超重试上限)')
    retry_count: Mapped[int] = mapped_column(INTEGER, server_default=text('0'))
    next_retry_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"), comment='下次重试时间(指数退避)')
    error_msg: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("biz_type", "biz_no", "topic", name="uk_biz_topic"),  # 同一业务事件的幂等键
        Index("idx_status_retry", "status", "next_retry_time"),  # 投递任务扫描
    )


class SysPermission(Base, PKMixin, TimestampMixin):
    """权限表（`sys_permission`）。"""

    __tablename__ = "sys_permission"

    parent_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), server_default=text('0'), comment='父权限, 0为根')
    name: Mapped[str] = mapped_column(VARCHAR(32), comment='权限名称')
    type: Mapped[int] = mapped_column(TINYINT, comment='类型: 1菜单 2按钮 3接口')
    perm_code: Mapped[str] = mapped_column(VARCHAR(64), comment='权限标识, 如 order:ship')
    path: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True, server_default=text('NULL'), comment='前端路由/后端接口路径')
    sort: Mapped[int] = mapped_column(INTEGER, server_default=text('0'))
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("perm_code", name="uk_perm_code"),
        Index("idx_parent_id", "parent_id"),
    )


class SysRole(Base, PKMixin, TimestampMixin):
    """角色表（`sys_role`）。"""

    __tablename__ = "sys_role"

    name: Mapped[str] = mapped_column(VARCHAR(32), comment='角色名称')
    code: Mapped[str] = mapped_column(VARCHAR(32), comment='角色编码, 如 admin/agent/ops')
    data_scope: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='数据权限范围: 1仅本人 2本部门 3全量(PRD §2.2)')
    remark: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True, server_default=text('NULL'))
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1启用 0禁用')
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("code", name="uk_code"),
    )


class SysRolePermission(Base, PKMixin):
    """角色权限关联表（`sys_role_permission`）。"""

    __tablename__ = "sys_role_permission"

    role_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    permission_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uk_role_perm"),
        Index("idx_permission_id", "permission_id"),
    )


class SysUser(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """后台用户表（`sys_user`）。"""

    __tablename__ = "sys_user"

    username: Mapped[str] = mapped_column(VARCHAR(32), comment='登录名')
    password: Mapped[str] = mapped_column(CHAR(60), comment='密码(BCrypt)')
    real_name: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True, server_default=text('NULL'), comment='姓名')
    mobile: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True, server_default=text('NULL'), comment='手机号密文(AES-256-GCM)')
    mobile_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, server_default=text('NULL'), comment='手机号HMAC-SHA256')
    status: Mapped[int] = mapped_column(TINYINT, server_default=text('1'), comment='状态: 1正常 0禁用')
    last_login_time: Mapped[dt.datetime | None] = mapped_column(DATETIME, nullable=True, server_default=text('NULL'))
    create_time: Mapped[dt.datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        Index("idx_mobile_hash", "mobile_hash"),
    )


class SysUserRole(Base, PKMixin):
    """用户角色关联表（`sys_user_role`）。"""

    __tablename__ = "sys_user_role"

    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    role_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uk_user_role"),
        Index("idx_role_id", "role_id"),
    )
