"""统一分页（对齐 `docs/API.md` §1.1）。

约定（SSOT 在 `app/core/response.py`，本模块不复刻）：
    请求 `{ "pageNum": 1, "pageSize": 20 }`   // pageNum 从 1 开始，pageSize 上限 100
    响应 `{ "total": 128, "list": [ ] }`

本模块只做一件 `PageQuery` → SQL `LIMIT/OFFSET` 的换算与执行，
**不另立一套分页模型**：那样前端就要适配两种分页结构，
而这正是 `PAGE_SIZE_MAX` 留在 `app/core/response.py` 的原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import PAGE_NUM_MIN, PAGE_SIZE_MAX


class InvalidPageError(ValueError):
    """分页参数越界。

    为什么不静默 clamp：调用方传了 `pageSize=1000` 却只拿到 100 条，
    会误以为"数据就这么点"。越界应显式失败 —— 与项目"不静默降级"的取向一致。
    """


@dataclass(frozen=True, slots=True)
class Page:
    """一页数据。`total` / `list` 即 API 契约里的字段名。"""

    total: int
    list: list[Any] = field(default_factory=list)
    page_size: int = 20

    @property
    def page_count(self) -> int:
        """总页数（向上取整）；无数据时为 0，前端据此显示"暂无数据"。"""
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


def _validate(page_num: int, page_size: int) -> None:
    if page_num < PAGE_NUM_MIN:
        raise InvalidPageError(f"pageNum 从 {PAGE_NUM_MIN} 开始，收到 {page_num}")
    if not 1 <= page_size <= PAGE_SIZE_MAX:
        raise InvalidPageError(f"pageSize 必须在 1 ~ {PAGE_SIZE_MAX} 之间，收到 {page_size}")


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    page_num: int = PAGE_NUM_MIN,
    page_size: int = 20,
) -> Page:
    """执行分页查询，返回 `Page(total, list, page_size)`。

    实现要点：
    - `total` 用 `SELECT count(*) FROM (<原查询>) AS anon`：带 join / where 的复杂
      查询也能得到正确总数，不必让调用方把条件再写一遍（写两遍必然漂移）。
    - 计数前先 `order_by(None)`：排序对计数毫无意义，却会让 MySQL 多做一次排序。
    - `LIMIT/OFFSET` 在超大偏移时性能衰减（`OFFSET 100000` 要扫描并丢弃前 10 万行）。
      本项目数据量下可接受；将来需要时改用游标分页
      （`WHERE id < last_id ORDER BY id DESC LIMIT n`）—— 届时只改本函数。
    """
    _validate(page_num, page_size)

    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = await session.scalars(
        stmt.limit(page_size).offset((page_num - PAGE_NUM_MIN) * page_size)
    )

    return Page(total=int(total or 0), list=list(rows.all()), page_size=page_size)
