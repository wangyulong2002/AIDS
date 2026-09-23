"""乐观锁的条件更新（DDL 约定 7）。

=====================================================================
原理（PRD §7 库存防线的第二层）：

    UPDATE biz_sku_stock
       SET available_stock = available_stock - :n, version = version + 1
     WHERE id = :id AND version = :v AND available_stock >= :n

    受影响行数为 0 有两种可能：**版本不匹配**（期间被别人改过）
    或**业务条件不满足**（如库存不足）。调用方据此重试或失败，
    而不是盲目覆盖 —— 这就是"乐观"二字的含义：不加行锁，
    靠版本号做冲突检测，只在**真冲突**时才付重试成本。

为什么不用 `SELECT ... FOR UPDATE` 取代它：
    悲观锁在秒杀场景下会把同一行库存变成串行瓶颈（所有请求排队等同一把锁）。
    二者不是替代关系，本项目按 PRD §7 分层协同：
    Redis Lua 预扣（挡掉绝大部分流量）→ DB 乐观锁（最后一道不超卖的保证）。

硬约束：本模块**拒绝给没有 version 列的表使用**，传错模型直接抛错，
不生成引用不存在列的 SQL。
"""

from __future__ import annotations

from typing import Any, Final, TypeVar, cast

from sqlalchemy import CursorResult, Table, update
from sqlalchemy.ext.asyncio import AsyncSession

_VERSION: Final[str] = "version"
_ID: Final[str] = "id"

T = TypeVar("T")


class MissingVersionColumnError(TypeError):
    """模型没有 `version` 列，不能用于乐观锁更新。"""


class OptimisticLockError(RuntimeError):
    """乐观锁冲突：目标行的 version 已被其他事务修改。

    调用方可以据此重试（PRD §7 的"失败重试"），或向用户报"操作太频繁，请重试"。
    """


def _table_of(model: type[Any]) -> Table:
    table = cast(Table, model.__table__)
    if _VERSION not in table.c:
        raise MissingVersionColumnError(
            f"{table.name} 没有 `{_VERSION}` 列 —— 乐观锁仅用于并发写热点"
            f"（sku_stock / order / kb_document）。给冷表加 version 只是徒增 UPDATE 开销。"
        )
    return table


async def update_with_version(
    session: AsyncSession,
    model: type[Any],
    pk: int,
    expected_version: int,
    values: dict[str, Any],
) -> int:
    """按 `id + version` 条件更新，并把 version 自增 1。**返回受影响行数**。

    `0` 表示冲突（被并发改过，或该行不存在）——
    不要把它当成"更新成功但内容没变"，调用方必须显式处理。
    """
    table = _table_of(model)
    if _VERSION in values:
        raise ValueError(f"values 里不要自带 {_VERSION} —— 本函数负责自增，传入会双重递增")

    payload = {**values, _VERSION: expected_version + 1}
    stmt = (
        update(table)
        .where(table.c[_ID] == pk, table.c[_VERSION] == expected_version)
        .values(**payload)
    )
    result = await session.execute(stmt)
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def update_or_raise(
    session: AsyncSession,
    model: type[Any],
    pk: int,
    expected_version: int,
    values: dict[str, Any],
) -> None:
    """同 `update_with_version`，但冲突时直接抛 `OptimisticLockError`。

    适合"冲突即失败、由上层给用户提示"的场景；需要自己重试的用前者。
    """
    affected = await update_with_version(session, model, pk, expected_version, values)
    if affected == 0:
        table = _table_of(model)
        raise OptimisticLockError(
            f"{table.name}.id={pk} 的 version 已不是 {expected_version}（被并发修改），"
            f"本次更新未生效"
        )
