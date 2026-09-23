"""SQLAlchemy DeclarativeBase 与全局命名约定。

=====================================================================
为什么必须显式声明 naming_convention（不是形式主义）：
    不指定时，约束/索引名由数据库自动生成（`biz_user_ibfk_1` 这类随机名），
    而 **Alembic autogenerate 是靠"名字"比对约束的** —— 名字随机化会让
    每次 autogenerate 都以为约束变了，迁移脚本里塞满无意义的 drop/create，
    最后没人敢信任它（这正是很多项目把 Alembic 用成"手写 SQL 记录器"的原因）。

    本项目的索引名在 DDL 里是**显式命名**的（`uk_mobile_hash` / `idx_user_id`），
    模型侧同样显式命名，两边可逐字比对（由
    `tests/contract/test_orm_matches_schema.py` 强制）。
    下面的模板只是**兜底**：漏写 `name=` 时也不至于产生随机名字。
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 兜底命名模板（确定性、可复现）
NAMING_CONVENTION: dict[str, str] = {
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uk_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。

    只承担两件事：给 metadata 装上命名约定，以及充当"所有模型都注册在这里"
    这一事实 —— Alembic autogenerate 正是靠 `Base.metadata.tables` 发现全表。
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
