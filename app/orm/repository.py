"""行级数据权限（IDOR 防护）的统一入口 —— Repository 基类。

权威文档：PRD §2.2（功能权限 + 数据权限，缺一不可）/ §12.4；TASKS BE-04。

规则（S4，静态扫描 + 契约测试强制）：
    1. 业务代码**禁止**从请求参数读 userId 做权限判断 —— userId 只能来自 JWT，
       由 `app/core/security.py::get_current_user` 提供；
    2. 自有资源的访问一律 `WHERE id = ? AND user_id = ?`，查不到返回 403（10005）；
    3. 「查不到」与「不是你的」必须**不可区分** —— 否则攻击者可以用 404/403 的差异
       枚举出哪些 ID 真实存在（API.md §1.3：不暴露资源是否存在）。

为什么收口成一个基类：
    「记得在每个查询里加 user_id 条件」是纪律，而纪律必然失效 —— 只要有一个
    接口忘了写，就是一条可枚举的越权，且**没有任何报错**。把条件注入收口到
    唯一入口后，业务代码只说"我要这条资源"，条件由这里统一补：漏写的可能性
    从「每个接口」缩小到「这一处」，而这一处有 27+ 条测试盯着。

设计要点：
    - `scoped()` 是唯一的条件注入点：`owner = user_id`，外加 `alive()`
      （仅当模型有 `deleted` 列）。列表/分页也必须从这里出发。
    - 事实表（订单/支付）**没有 `deleted` 列**（schema.sql 约定：事实数据靠状态机
      流转，不逻辑删除），但它们同样是用户自有资源 —— 故 `alive()` 按列存在性
      分流，而不是让订单这类核心表直接不可用。
    - 模型没有 owner 列 → **构造即 TypeError**：这是配置错误，必须当场炸，
      而不是悄悄生成一条不带行级条件的 SQL（那等于没防）。
"""

from __future__ import annotations

from typing import Any, ClassVar, Final, Generic, TypeVar, cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.orm.soft_delete import alive

OWNER_COLUMN: Final[str] = "user_id"
PK_COLUMN: Final[str] = "id"

ModelT = TypeVar("ModelT")


class OwnedRepository(Generic[ModelT]):
    """按 `user_id` 收窄的资源仓库基类。

    子类只需声明 `model`（owner 列名默认 `user_id`，与 schema.sql 全部自有资源一致）：

        class AddressRepo(OwnedRepository[BizAddress]):
            model = BizAddress

    用法：
        repo = AddressRepo(session, current_user.user_id)
        address = await repo.require(address_id)   # 查不到 / 不是你的 → 403(10005)
        stmt = repo.scoped(select(BizAddress))     # 列表/分页也必须从这里出发
    """

    model: ClassVar[type[Any]]
    owner_column: ClassVar[str] = OWNER_COLUMN

    def __init__(self, session: AsyncSession, user_id: int) -> None:
        table = self.model.__table__
        if self.owner_column not in table.c:
            raise TypeError(
                f"{table.name} 没有 `{self.owner_column}` 列，不能使用 OwnedRepository —— "
                f"行级数据权限只用于用户自有资源；公共资源（商品/类目/品牌）不需要"
            )
        self._table = table
        self._has_deleted = "deleted" in table.c
        self.session = session
        self.user_id = user_id

    def scoped(self, stmt: Select[Any]) -> Select[Any]:
        """唯一的条件注入点：`owner = user_id`（+ `deleted = 0`，若该表支持逻辑删除）。"""
        stmt = stmt.where(self._table.c[self.owner_column] == self.user_id)
        if self._has_deleted:
            stmt = alive(stmt, self.model)
        return stmt

    def select_all(self) -> Select[Any]:
        """已收窄的查询起点（列表/分页从这里开始，再叠加业务条件）。"""
        return self.scoped(select(self.model))

    async def get(self, pk: int) -> ModelT | None:
        """`WHERE id = ? AND user_id = ?`。返回 None 表示「不存在**或**不是你的」。"""
        stmt = self.scoped(select(self.model)).where(self._table.c[PK_COLUMN] == pk)
        result = await self.session.execute(stmt)
        return cast(ModelT | None, result.scalar_one_or_none())

    async def require(self, pk: int) -> ModelT:
        """取不到就 403（10005），**不区分**「不存在」与「无权」。

        为什么不用 `get()` + 手动判断：把「403 语义」放业务代码里，早晚会有人
        写成 404（那样就能枚举 ID 了）。这里只提供一种结果。
        """
        found = await self.get(pk)
        if found is None:
            raise BusinessError.data_forbidden()
        return found
