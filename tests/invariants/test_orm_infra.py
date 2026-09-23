"""ORM 基建（Mixin / 软删除 / 乐观锁 / 分页）的不变量测试。

为什么**全部不需要数据库**：
    - 表结构类断言直接读 SQLAlchemy 的 metadata（`Table.c`）；
    - SQL 类断言把语句 `compile()` 成字符串检查；
    - 需要 session 的地方用极简 Fake 捕获语句。
    这样本地没起 MySQL 也能跑（与 `tests/contract` 既有的
    "无 DB 也要能收集"约定一致），而真正的数据往返留给 `requires_mysql` 层。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import pytest
from sqlalchemy import MetaData, Select, String, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.orm.base import NAMING_CONVENTION
from app.orm.mixins import (
    CreateTimeMixin,
    OptimisticLockMixin,
    PKMixin,
    SoftDeleteMixin,
    TimestampMixin,
    utcnow,
)
from app.orm.optimistic import (
    MissingVersionColumnError,
    OptimisticLockError,
    update_or_raise,
    update_with_version,
)
from app.orm.pagination import InvalidPageError, Page, paginate
from app.orm.soft_delete import ALIVE, DELETED, alive, include_deleted

pytestmark = [pytest.mark.invariant, pytest.mark.task("BE-02")]


# ---------------------------------------------------------------------
# 探针模型：用**独立 metadata**，避免污染 Base.metadata（那会进 Alembic autogenerate）
# ---------------------------------------------------------------------
class ProbeBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ProbeFull(ProbeBase, PKMixin, TimestampMixin, SoftDeleteMixin, OptimisticLockMixin):
    """四件套齐全的探针表。"""

    __tablename__ = "probe_full"
    name: Mapped[str] = mapped_column(String(32))


class ProbeCreateOnly(ProbeBase, PKMixin, CreateTimeMixin):
    """只有 create_time（对应日志/明细类表，如 biz_order_item）。"""

    __tablename__ = "probe_create_only"
    name: Mapped[str] = mapped_column(String(32))


class ProbeBare(ProbeBase, PKMixin):
    """只有主键（对应纯关联表，如 sys_user_role）。"""

    __tablename__ = "probe_bare"
    name: Mapped[str] = mapped_column(String(32))


def _sql(stmt: Any) -> str:
    """把语句编译成 MySQL SQL 文本（不连库）。"""
    return str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": False}))


def _sql_literal(stmt: Any) -> str:
    """编译并**内联字面量**（用于断言具体的 LIMIT/OFFSET 数值）。"""
    return str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


def _compact(stmt: Any) -> str:
    """编译后把连续空白压成一个空格，便于做子串断言。"""
    return " ".join(_sql(stmt).split())


def _default_name(holder: Any) -> str:
    """取列默认值（`default` / `onupdate`）所指函数的名字。

    不直接 `holder.arg is utcnow`：SQLAlchemy 会把可调用默认值包一层
    `CallableColumnDefault`，而函数对象的 `is` 比较在测试进程里并不可靠
    （模块可能被以不同路径重复导入）。比 `__name__` 既够用又稳定。
    """
    if holder is None:
        return ""
    return getattr(getattr(holder, "arg", None), "__name__", "")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """只记录语句、返回预设结果。"""

    def __init__(self, rowcount: int = 1, total: int = 0, rows: list[Any] | None = None) -> None:
        self._rowcount = rowcount
        self._total = total
        self._rows = rows or []
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(self._rowcount)

    async def scalar(self, stmt: Any) -> int:
        self.statements.append(stmt)
        return self._total

    async def scalars(self, stmt: Any) -> _FakeScalarResult:
        self.statements.append(stmt)
        return _FakeScalarResult(self._rows)


def _session(**kwargs: Any) -> AsyncSession:
    return cast(AsyncSession, _FakeSession(**kwargs))


# =====================================================================
# 一、Mixin 产生的列（对齐 schema.sql 的列定义）
# =====================================================================


class TestMixinColumns:
    def test_pk_is_app_generated_snowflake(self) -> None:
        col = ProbeBare.__table__.c.id
        assert col.primary_key
        assert col.type.__class__.__name__.upper() == "BIGINT"
        assert col.type.unsigned is True  # type: ignore[attr-defined]
        # 主键必须由应用生成（DDL 约定 2），不是数据库自增
        assert col.autoincrement is False or col.default is not None
        assert _default_name(col.default) == "next_id"

    def test_create_time_is_utc_and_auto(self) -> None:
        col = ProbeCreateOnly.__table__.c.create_time
        assert not col.nullable
        assert _default_name(col.default) == "utcnow"
        assert col.server_default is not None, "DDL 侧应有 DEFAULT CURRENT_TIMESTAMP 兜底"

    def test_bare_mixin_has_no_extra_columns(self) -> None:
        """纯关联表不得被塞进时间戳列 —— 那会变成对不存在的列做 ALTER TABLE。"""
        assert set(ProbeBare.__table__.c.keys()) == {"id", "name"}

    def test_create_only_has_no_update_time(self) -> None:
        """日志/明细表刻意没有 update_time（有它就等于允许改写历史）。"""
        assert "update_time" not in ProbeCreateOnly.__table__.c

    def test_timestamp_mixin_fills_update_on_change(self) -> None:
        col = ProbeFull.__table__.c.update_time
        assert not col.nullable
        assert _default_name(col.onupdate) == "utcnow", "UPDATE 时必须自动刷新 update_time"

    def test_soft_delete_column_semantics(self) -> None:
        col = ProbeFull.__table__.c.deleted
        assert not col.nullable
        assert col.default is not None and col.default.arg == ALIVE
        assert ALIVE == 0 and DELETED == 1

    def test_version_column_semantics(self) -> None:
        col = ProbeFull.__table__.c.version
        assert not col.nullable
        assert col.default is not None and col.default.arg == 0


class TestUtcnow:
    def test_returns_naive_utc(self) -> None:
        """必须是 naive：DDL 用 DATETIME（无时区），带 tzinfo 会引发隐式转换。"""
        now = utcnow()
        assert now.tzinfo is None
        assert abs((now - dt.datetime.now(dt.UTC).replace(tzinfo=None)).total_seconds()) < 5


# =====================================================================
# 二、逻辑删除
# =====================================================================


class TestSoftDelete:
    def test_alive_appends_deleted_filter(self) -> None:
        sql = _sql(alive(select(ProbeFull), ProbeFull))
        # 注意 SELECT 列表里本来就有 probe_full.deleted 列，
        # 所以不能只数 "deleted" 出现的次数 —— 要断言 WHERE 条件本身。
        assert "WHERE probe_full.deleted = %s" in sql

    def test_include_deleted_is_identity(self) -> None:
        """显式"要看已删除"是恒等变换 —— 它的价值在语义而非行为。"""
        base: Select[Any] = select(ProbeFull)
        assert _sql(include_deleted(base)) == _sql(base)

    def test_rejects_model_without_deleted_column(self) -> None:
        with pytest.raises(TypeError, match="没有 `deleted` 列"):
            alive(select(ProbeBare), ProbeBare)


# =====================================================================
# 三、乐观锁
# =====================================================================


class TestOptimisticLock:
    async def test_update_uses_version_condition_and_bumps(self) -> None:
        session = _session(rowcount=1)
        affected = await update_with_version(
            session, ProbeFull, pk=1, expected_version=3, values={"name": "x"}
        )

        assert affected == 1
        sql = _compact(cast(_FakeSession, session).statements[0])
        assert "UPDATE probe_full" in sql
        assert "SET name=%s" in sql
        # WHERE 必须同时锁 id 与 version —— 只锁 id 就退化成盲写（覆盖别人的修改）
        assert "WHERE probe_full.id = %s AND probe_full.version = %s" in sql

    async def test_zero_rowcount_means_conflict(self) -> None:
        session = _session(rowcount=0)
        assert await update_with_version(session, ProbeFull, 1, 3, {"name": "x"}) == 0

    async def test_update_or_raise_raises_on_conflict(self) -> None:
        session = _session(rowcount=0)
        with pytest.raises(OptimisticLockError, match="version 已不是"):
            await update_or_raise(session, ProbeFull, 1, 3, {"name": "x"})

    async def test_caller_must_not_pass_version(self) -> None:
        """自带 version 会与本函数的自增叠加，导致版本号跳号。"""
        with pytest.raises(ValueError, match="不要自带 version"):
            await update_with_version(_session(), ProbeFull, 1, 3, {"version": 99})

    async def test_rejects_model_without_version_column(self) -> None:
        with pytest.raises(MissingVersionColumnError, match="没有 `version` 列"):
            await update_with_version(_session(), ProbeBare, 1, 0, {"name": "x"})


# =====================================================================
# 四、分页
# =====================================================================


class TestPagination:
    @pytest.mark.parametrize(
        ("page_num", "page_size"),
        [(0, 20), (-1, 20), (1, 0), (1, 101), (1, 1000)],
    )
    async def test_invalid_params_raise(self, page_num: int, page_size: int) -> None:
        """越界必须显式失败，不静默 clamp —— 否则调用方以为"数据就这么点"。"""
        with pytest.raises(InvalidPageError):
            await paginate(_session(), select(ProbeFull), page_num, page_size)

    async def test_returns_total_and_rows(self) -> None:
        page = await paginate(_session(total=128, rows=["a", "b"]), select(ProbeFull), 1, 20)
        assert page.total == 128
        assert page.list == ["a", "b"]

    async def test_count_query_drops_order_by(self) -> None:
        """计数带排序毫无意义，却会让 MySQL 多做一次排序。"""
        session = _FakeSession(total=5)
        await paginate(cast(AsyncSession, session), select(ProbeFull).order_by(ProbeFull.id), 1, 20)
        count_sql = _sql(session.statements[0])
        assert "ORDER BY" not in count_sql.upper()

    async def test_offset_from_first_page(self) -> None:
        session = _FakeSession(total=50)
        await paginate(cast(AsyncSession, session), select(ProbeFull), 3, 10)
        # MySQL 用 `LIMIT offset, count`；第 3 页、每页 10 条 → offset = 20
        assert "LIMIT 20, 10" in _sql_literal(session.statements[1])

    @pytest.mark.parametrize(
        ("total", "page_size", "expected"),
        [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (128, 20, 7)],
    )
    def test_page_count(self, total: int, page_size: int, expected: int) -> None:
        assert Page(total=total, page_size=page_size).page_count == expected
