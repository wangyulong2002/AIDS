"""BE-04 机制级测试：OwnedRepository 生成的 SQL 必须真正带上行级条件。

为什么用「编译 SQL」而不是连真库：
    数据权限失效的最危险形态是「查询里**没有** user_id 条件」——它不报错，
    只是安静地查出不该查的数据。这类问题看编译产物就能抓住：断言生成的 SQL
    里有 owner 条件（有 `deleted` 列的表还有 `deleted = 0`），比搭一套数据库
    快得多、也确定得多。跨库的执行语义由 `requires_mysql` 的反射/CRUD 测试覆盖。

三种形态都要锁（对应 schema.sql 的现实）：
    - 主数据表（`biz_address`：user_id + deleted）→ user_id **和** deleted = 0
    - 事实表（`biz_order`：user_id，**无 deleted**，靠状态机流转）→ 只有 user_id；
      若这里也强行加 deleted，订单这类核心表会直接 TypeError 不可用
    - 公共资源表（`biz_brand`：无 user_id）→ **构造即 TypeError**：
      没有行级条件的"仓库"是配置错误，必须当场炸，而不是悄悄查出全表
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.models.biz import BizAddress, BizBrand, BizOrder
from app.orm.repository import OwnedRepository

pytestmark = [pytest.mark.invariant, pytest.mark.task("BE-04")]

USER_A = 8001


def asyncio_run(awaitable: Any) -> Any:
    """微小辅助：用例保持同步函数，局部需要 await 的地方用它包一层。"""
    return asyncio.run(awaitable)


class _AddressRepo(OwnedRepository[BizAddress]):
    model = BizAddress


class _OrderRepo(OwnedRepository[BizOrder]):
    model = BizOrder


class _BrandRepo(OwnedRepository[BizBrand]):
    model = BizBrand


def _repo(repo_cls: type[OwnedRepository[Any]], user_id: int = USER_A) -> OwnedRepository[Any]:
    """scoped()/select_all() 不触库，session 传 None 即可（类型上 cast 掉）。"""
    return repo_cls(cast(AsyncSession, None), user_id)


def _sql(stmt: Select[Any]) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# =====================================================================
# 一、条件注入（编译产物）
# =====================================================================


def test_master_data_table_gets_owner_and_alive() -> None:
    sql = _sql(_repo(_AddressRepo).select_all())
    assert f"biz_address.user_id = {USER_A}" in sql, f"缺少行级 owner 条件：{sql}"
    assert "biz_address.deleted = 0" in sql, f"缺少逻辑删除过滤：{sql}"


def test_fact_table_gets_owner_only() -> None:
    """biz_order 无 deleted 列（事实数据靠状态机流转）——只注入 owner 条件。"""
    sql = _sql(_repo(_OrderRepo).select_all())
    assert f"biz_order.user_id = {USER_A}" in sql, f"缺少行级 owner 条件：{sql}"
    assert "deleted" not in sql, f"事实表不应出现逻辑删除条件：{sql}"


def test_model_without_owner_column_is_rejected() -> None:
    with pytest.raises(TypeError, match="不能使用 OwnedRepository"):
        _repo(_BrandRepo)


def test_owner_condition_follows_the_user_not_the_table() -> None:
    """换一个 user_id，条件跟着变 —— 防止实现里把 user_id 写成常量。"""
    sql = _sql(_repo(_AddressRepo, 9999).select_all())
    assert "biz_address.user_id = 9999" in sql


# =====================================================================
# 二、get / require：一条查询、一种结果
# =====================================================================


class _CaptureSession:
    """记录被执行的语句并返回"查无此行"——require 的失败路径。"""

    def __init__(self) -> None:
        self.statements: list[Select[Any]] = []

    async def execute(self, stmt: Select[Any]) -> Any:
        self.statements.append(stmt)

        class _Result:
            def scalar_one_or_none(self) -> None:
                return None

        return _Result()


def test_require_raises_10005_on_miss() -> None:
    session = _CaptureSession()
    repo = _AddressRepo(cast(AsyncSession, session), USER_A)
    with pytest.raises(BusinessError) as exc:
        asyncio_run(repo.require(123))
    assert exc.value.code == int(CommonError.DATA_FORBIDDEN)
    assert exc.value.resolved_http_status == 403


def test_get_issues_single_query_with_both_conditions() -> None:
    """关键：owner 条件与 id 条件必须在**同一条**查询里。

    若实现成"先查 id 再比对 user_id"，会多一次查询：多一次日志/慢查询暴露、
    多一次竞态窗口，而且"查到但不是你的"会短暂进入业务代码。
    """
    session = _CaptureSession()
    repo = _AddressRepo(cast(AsyncSession, session), USER_A)

    asyncio_run(repo.get(123))

    assert len(session.statements) == 1, "一次 get() 不应产生多条查询"
    sql = _sql(session.statements[0])
    assert "biz_address.id = 123" in sql
    assert f"biz_address.user_id = {USER_A}" in sql
