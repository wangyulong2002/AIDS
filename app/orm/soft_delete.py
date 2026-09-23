"""逻辑删除：显式过滤器与删除/恢复辅助。

=====================================================================
为什么**不做**全局隐式过滤（本模块最重要的设计决定）：
    MyBatis-Plus 那类框架习惯用全局插件，自动给所有查询补 `deleted = 0`。
    本项目刻意不这么做，两个原因：

    1. **后台需要看已删除的数据**（回收站 / 审计追溯 / 误删恢复）。全局过滤下
       这个功能无处安放 —— 除非每个查询都手写"关闭过滤器"，而一旦有人忘了，
       就变成"为什么这个接口看不到数据"的玄学问题。
    2. **隐式 SQL 变换极难排查**：ORM 生成的 SQL 里凭空多一个条件，
       日志与 `EXPLAIN` 和代码对不上。

    所以改成**显式**：默认不过滤，需要的地方调用 `alive(stmt, Model)`。
    命名刻意避开 `not_deleted`（双重否定），`alive` 读起来就是"活着的记录"。

一条硬约束：本模块**拒绝给没有 deleted 列的表使用** —— 传错模型直接抛
`TypeError`，而不是生成一条引用不存在列的 SQL（那会变成请求链深处的
`Unknown column 'deleted'`，排查成本高得多）。
"""

from __future__ import annotations

from typing import Any, Final, cast

from sqlalchemy import CursorResult, Select, Table, update
from sqlalchemy.ext.asyncio import AsyncSession

# DDL 约定 5：`deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除: 0否 1是'`
ALIVE: Final[int] = 0
DELETED: Final[int] = 1

_DELETED: Final[str] = "deleted"
_ID: Final[str] = "id"


def _table_of(model: type[Any]) -> Table:
    """取模型的 Table，并确认它确实有 deleted 列。"""
    table = cast(Table, model.__table__)
    if _DELETED not in table.c:
        raise TypeError(
            f"{table.name} 没有 `{_DELETED}` 列 —— 逻辑删除仅用于主数据"
            f"（用户 / 地址 / 商品 / 优惠券模板 / 后台用户）。"
            f"订单、支付这类事实数据不得逻辑删除，只能靠状态机流转。"
        )
    return table


def alive(stmt: Select[Any], model: type[Any]) -> Select[Any]:
    """给 SELECT 补上 `deleted = 0`（只查未删除的行）。"""
    return stmt.where(_table_of(model).c[_DELETED] == ALIVE)


def include_deleted(stmt: Select[Any]) -> Select[Any]:
    """显式声明"本次要包含已删除的行"。

    为什么不只是"不调用 `alive()` 就算"：回收站 / 审计这类接口应当**显式**表达
    意图，让 code review 一眼看出"这里是故意要看已删除的"，
    而不是看起来像漏写了过滤条件。

    本函数是恒等变换 —— 它的价值在语义，不在行为。
    """
    return stmt


async def soft_delete_by_id(session: AsyncSession, model: type[Any], pk: int) -> int:
    """按主键逻辑删除。返回受影响行数。

    **天然幂等**：条件里带了 `deleted = 0`，所以对已删除的行重复调用
    受影响行数为 0，不会报错、也不会刷新时间戳。
    """
    table = _table_of(model)
    stmt = (
        update(table)
        .where(table.c[_ID] == pk, table.c[_DELETED] == ALIVE)
        .values({_DELETED: DELETED})
    )
    result = await session.execute(stmt)
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def restore_by_id(session: AsyncSession, model: type[Any], pk: int) -> int:
    """按主键恢复（回收站功能）。返回受影响行数；0 表示它本来就没被删。"""
    table = _table_of(model)
    stmt = (
        update(table)
        .where(table.c[_ID] == pk, table.c[_DELETED] == DELETED)
        .values({_DELETED: ALIVE})
    )
    result = await session.execute(stmt)
    return int(cast(CursorResult[Any], result).rowcount or 0)
