"""BE-02 验收：ORM 基建在**真实 MySQL** 上可用。

TASKS.md BE-02 的验收原文是「DDL 全表可生成基础 CRUD」。这里把它拆成三层：

    L1  全表可达      —— 38 张表逐表 `SELECT count(*)`（模型映射错会直接 Unknown column）
    L2  映射正确      —— 反射真实库的列清单，与 ORM 模型逐表比对
    L3  完整 CRUD     —— 在一张代表性表上跑 insert → select → update → delete

为什么不真的对 38 张表逐表做 INSERT：
    每张表的必填字段不同，且部分列带 UNIQUE / CHECK 约束，构造"对每张表都合法"
    的样本数据要写 38 份夹具 —— 那是业务模块（BE-07 起）的测试该干的事，
    在这里只会变成一堆脆弱的硬编码。**映射正确性**（L2）才是基建要证明的：
    列名对不上时 L1 就已经炸了，而 L3 足以证明"增删改查这条链路是通的"。

依赖真实 MySQL：`DATABASE_URL` 必须指向**库名含 test** 的隔离库，
否则启动断言 S1-d 会拒绝启动（这是 Bysj 历史顽疾的代码化根治）。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, inspect, select

from app.models.sys import SysConfig
from app.orm.base import Base
from app.orm.session import get_engine, session_scope

# 直接建 engine（不经过 conftest 的 db_conn fixture），所以必须自己声明跳过条件：
# 未配 DATABASE_URL 时应当 skip 而不是 error —— 否则本地无库就无法跑全量测试，
# 而"跑不了全量"会让开发者干脆不跑。
pytestmark = [
    pytest.mark.contract,
    pytest.mark.requires_mysql,
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL"),
        reason="未配置 DATABASE_URL，跳过需要真实 MySQL 的 ORM 验收测试",
    ),
]


class TestEveryTableIsReachable:
    """L1：38 张表都能被 ORM 查询到（列名/表名映射错误的直接暴露点）。"""

    async def test_count_every_table(self) -> None:
        async with session_scope() as session:
            for name, table in Base.metadata.tables.items():
                total = await session.scalar(select(func.count()).select_from(table))
                assert total == 0, f"{name} 期望空表，实际 {total} 行（测试库不干净？）"


class TestModelMatchesRealDatabase:
    """L2：反射真实库，逐表比对列清单。

    这一层比"模型 ↔ DDL 文本"更强 —— 它验证的是**数据库实际建出来的样子**，
    能抓住 `schema.sql` 与真实库漂移（例如 CI 的 sed 替换出错、迁移未执行）。
    """

    async def test_columns_match_information_schema(self) -> None:
        engine = get_engine()
        async with engine.connect() as conn:
            actual_tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))

            expected_tables = set(Base.metadata.tables)
            missing = sorted(expected_tables - actual_tables)
            assert not missing, f"模型有、库里没有的表：{missing}"

            problems: list[str] = []
            for name, table in Base.metadata.tables.items():
                reflected = await conn.run_sync(lambda c, n=name: inspect(c).get_columns(n))
                db_cols = {col["name"] for col in reflected}
                model_cols = set(table.columns.keys())
                if db_cols != model_cols:
                    problems.append(
                        f"{name}: 模型多出={sorted(model_cols - db_cols)} "
                        f"模型缺少={sorted(db_cols - model_cols)}"
                    )
            assert not problems, "\n".join(problems)


class TestCrudRoundTrip:
    """L3：完整 CRUD 链路（选 sys_config —— 字段简单、无软删除与乐观锁干扰）。"""

    async def test_insert_select_update_delete(self) -> None:
        key = f"smoke_{uuid.uuid4().hex[:8]}"

        # ---- Create ----
        async with session_scope() as session:
            # sys_config 不带 SoftDeleteMixin（它不是主数据），故无 deleted 参数
            row = SysConfig(config_key=key, config_value="v1", value_type=0, description="smoke")
            session.add(row)
            await session.flush()
            pk = row.id
            assert pk > 0, "主键必须由应用层（雪花 ID）生成"

        # ---- Read ----
        async with session_scope() as session:
            found = await session.scalar(select(SysConfig).where(SysConfig.id == pk))
            assert found is not None
            assert found.config_key == key
            assert found.config_value == "v1"
            assert found.create_time is not None, "create_time 必须被自动填充"

        # ---- Update（走 ORM 的 onupdate 自动刷新时间）----
        async with session_scope() as session:
            row = await session.scalar(select(SysConfig).where(SysConfig.id == pk))
            assert row is not None and row.update_time is not None
            before = row.update_time
            row.config_value = "v2"
            await session.flush()

        async with session_scope() as session:
            row = await session.scalar(select(SysConfig).where(SysConfig.id == pk))
            assert row is not None
            assert row.config_value == "v2"
            assert row.update_time is not None and row.update_time >= before

        # ---- Delete ----
        async with session_scope() as session:
            row = await session.scalar(select(SysConfig).where(SysConfig.id == pk))
            assert row is not None
            await session.delete(row)

        async with session_scope() as session:
            assert await session.scalar(select(SysConfig).where(SysConfig.id == pk)) is None
