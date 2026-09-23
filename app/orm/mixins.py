"""按需组合的列组（Mixins）。

=====================================================================
为什么是"组合"而不是"一个包含全部公共列的大基类"：
    实测 `docs/sql/schema.sql` 的 38 张表，公共列**并不是全集**：

        id          38/38   全表都有（DDL 约定 2：雪花 ID，应用层生成）
        create_time 36/38   两张纯关联表（sys_user_role / sys_role_permission）没有
        update_time 25/38   日志/明细类表刻意没有 —— 有它就等于允许改写历史
        deleted      5/38   仅主数据（用户 / 地址 / 商品 / 优惠券模板 / 后台用户）
        version      3/38   仅并发写热点（sku_stock / order / kb_document）

    若做一个"含全部公共列"的单基类，会同时制造三类错误：
        a) 给日志表附上 update_time —— 语义上允许改写历史；
        b) 给实际没有该列的表写 ORM 属性 —— 运行时报 `Unknown column`，
           且 Alembic autogenerate 会尝试 `ALTER TABLE` 把它加上；
        c) 漏掉某表确实有的列 —— 运行时报错，方向相反但同样致命。

    故拆成列组，让每个模型**显式声明自己要哪些**；
    这份声明本身就是"这张表为什么没有 X"的可读说明。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, func, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.orm.snowflake import next_id


def utcnow() -> dt.datetime:
    """当前 UTC 时间（**naive**）。

    为什么必须 naive：DDL 用的是 `DATETIME`（不带时区），项目约定"时间统一
    UTC 存储"。若返回带 tzinfo 的 datetime，驱动会做一次隐式转换，结果随宿主机
    时区漂移 —— 这类 bug 只在跨时区部署时暴露，本地永远复现不了。
    """
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class PKMixin:
    """雪花 ID 主键（38/38 表）。

    DDL：`id BIGINT UNSIGNED NOT NULL COMMENT '雪花ID'`。
    值由**应用层**生成（DDL 约定 2），故用 Python 侧 `default=next_id`
    而不是 `server_default` —— 数据库并不知道本项目雪花 ID 的纪元与 workerId。
    """

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        default=next_id,
        comment="雪花ID",
    )


class CreateTimeMixin:
    """创建时间（36/38 表）。

    DDL：`create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`。
    应用侧 `default=utcnow` 与 DDL 的 `DEFAULT CURRENT_TIMESTAMP` 并存，各管一段：
    - 前者保证"应用写入的一定是 UTC"，不依赖数据库会话时区；
    - 后者保证裸 SQL（运维手工插入 / seed）也有值。
    """

    create_time: Mapped[dt.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        server_default=func.current_timestamp(),
    )


class TimestampMixin(CreateTimeMixin):
    """`create_time` + `update_time`（25/38 表）。

    `onupdate=utcnow` 就是 TASKS BE-02 要求的那套"自动填充"：任何经 ORM 的
    UPDATE 都会带上新时间，业务代码不必手写（手写必然有人忘）。
    DDL 侧的 `ON UPDATE CURRENT_TIMESTAMP` 是给裸 SQL 更新兜底的。
    """

    update_time: Mapped[dt.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.current_timestamp(),
    )


class SoftDeleteMixin:
    """逻辑删除（5/38 表）。

    DDL：`deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除: 0否 1是'`。
    仅用于主数据 —— 订单 / 支付这类**事实数据不得逻辑删除**，只能靠状态机流转
    （删掉一笔订单的可见性，等于毁掉对账依据）。

    加了本 Mixin 只是**有了这一列**；"查询自动过滤已删除"要靠
    `app/orm/soft_delete.py` 的显式过滤器。为什么不做全局隐式过滤，
    见该模块注释（会连后台"查看已删除"的入口一起过滤掉）。
    """

    deleted: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="逻辑删除: 0否 1是",
    )


class OptimisticLockMixin:
    """乐观锁版本号（3/38 表）。

    DDL：`version INT NOT NULL DEFAULT 0 COMMENT '乐观锁版本号'`。
    仅用于并发写热点：`biz_sku_stock`（库存扣减）、`biz_order`（状态流转）、
    `ai_kb_document`（知识库重建）—— 给冷表加 version 只是徒增 UPDATE 开销。

    配合 `app/orm/optimistic.py::update_with_version` 使用：
    `UPDATE ... WHERE id=? AND version=?`，受影响行数为 0 即表示被并发改过。
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="乐观锁版本号",
    )
