"""ORM 基建（SQLAlchemy 2.0 async）。

TASKS.md BE-02。本包提供**跨服务共享**的数据访问底座——为什么放 `app/`（共享层）
而不是某个服务下：主库（`aids_shop`）同时含 `biz_`/`ai_`/`sys_` 三类表，
三个服务连的是**同一个 schema**，Alembic 也只有一条迁移链；
把 Base / Mixin / 会话工厂放服务里，第二个服务接入时必然复制一份（SSOT 副本）。

模块职责：
    snowflake.py   雪花 ID 生成（主键由应用层生成，见 schema.sql 约定 2）
    base.py        DeclarativeBase + 索引/约束命名约定（Alembic autogenerate 依赖）
    mixins.py      按需组合的列组：PK / CreateTime / Timestamp / SoftDelete / Version
    session.py     async engine + sessionmaker（唯一入口，禁止各模块自建 engine）
    soft_delete.py 逻辑删除的查询过滤器与删除辅助
    pagination.py  统一分页（对齐 API.md §1.1 的 {pageNum,pageSize} / {total,list}）
    optimistic.py  乐观锁的条件更新（对齐 DDL 的 `version` 列）
"""

from __future__ import annotations
