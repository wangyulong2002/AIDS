"""C9 契约测试：ORM 模型 ↔ `docs/sql/schema.sql` 一致性。

为什么它与 `scripts/gen_orm_models.py --check` 并存（看似重复，实为两层）：
    `--check` 断言的是「`app/models/` 的文件内容 == 生成器的输出」——
    它能抓住"有人手改了生成物"，但**抓不住"生成器本身解析错了"**：
    生成器漏掉一列时，文件与它自己的输出依然自洽，`--check` 照样绿。

    这不是假想 —— 写生成器时真踩过：列正则用了 `[^,]*`，于是 10 个
    COMMENT 里带逗号的列（`'手机号密文(AES-256-GCM, Base64)'`）整体消失，
    38 表 419 列变成 409 列，而 `--check` 全程绿灯。

    所以本文件用**独立的解析器**（`tests/contract/_doc_parser.py`，它同时是 C1 的
    锚点）直接读 DDL 与 ORM metadata 比对，让 DDL / 数据字典 / ORM 三方互相咬合。
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint

from app.orm.base import Base
from tests.contract._doc_parser import parse_schema_tables, parse_schema_unique_index_columns

pytestmark = pytest.mark.contract

# 导入即注册全部模型（`app/models/__init__.py` 负责）——
# 少导入一个模块，对比就会"少表"，而症状是迁移缺表。
import app.models  # noqa: E402,F401

DDL_TABLES: dict[str, list[str]] = parse_schema_tables()
ORM_TABLES = dict(Base.metadata.tables.items())

# C1 的同一个锚：主库 38 表 / 419 字段
EXPECTED_TABLES = 38
EXPECTED_COLUMNS = 419


class TestTableCoverage:
    def test_table_count_matches_ddl(self) -> None:
        assert len(ORM_TABLES) == len(DDL_TABLES) == EXPECTED_TABLES

    def test_no_missing_or_extra_table(self) -> None:
        missing = sorted(set(DDL_TABLES) - set(ORM_TABLES))
        extra = sorted(set(ORM_TABLES) - set(DDL_TABLES))
        assert not missing and not extra, f"缺表={missing} 多表={extra}"

    def test_total_column_count(self) -> None:
        total = sum(len(t.columns) for t in ORM_TABLES.values())
        assert total == EXPECTED_COLUMNS, "字段总数与 C1 的锚（419）不一致"


@pytest.mark.parametrize("table_name", sorted(DDL_TABLES))
class TestTableShape:
    def test_columns_match_ddl(self, table_name: str) -> None:
        """字段**集合**必须与 DDL 完全一致。

        不比顺序：SQLAlchemy 会把 Mixin 提供的列排在子类声明的列之后，
        与 DDL 的书写顺序天然不同；列顺序在 SQL 层没有语义（本项目一律显式列名）。
        """
        expected = set(DDL_TABLES[table_name])
        actual = set(ORM_TABLES[table_name].columns.keys())
        assert actual == expected, (
            f"{table_name} 字段不一致：\n"
            f"  模型多出={sorted(actual - expected)}\n"
            f"  模型缺少={sorted(expected - actual)}"
        )

    def test_column_count_matches_ddl(self, table_name: str) -> None:
        assert len(ORM_TABLES[table_name].columns) == len(DDL_TABLES[table_name])

    def test_primary_key_is_id_snowflake(self, table_name: str) -> None:
        """DDL 约定 2：主键是 `id BIGINT UNSIGNED`，且由应用层生成。"""
        table = ORM_TABLES[table_name]
        assert [c.name for c in table.primary_key] == ["id"]
        assert table.c.id.type.unsigned is True  # type: ignore[attr-defined]
        assert table.c.id.default is not None, "主键必须由应用生成，不能依赖数据库自增"


class TestNullability:
    """可空性不一致的后果是**运行期插入失败**，且只在特定字段上触发。"""

    def test_missing_column_is_a_hard_fail(self) -> None:
        """自检：本测试本身要能发现"少一列"（防止写成永远通过的空断言）。"""
        table = ORM_TABLES["biz_user"]
        assert "mobile" in table.columns, "biz_user.mobile 缺失 —— 契约测试未真正生效"

    def test_not_null_columns_declared_correctly(self) -> None:
        """DDL 里显式 NOT NULL 的列，模型侧不得标成可空。

        只抽查核心表（全量 419 列逐列解析 DDL 的可空性成本高，
        而可空性已在生成器里按 DDL 逐列映射，此处做**抽样反证**）。
        """
        cases = {
            "biz_user": ["mobile", "mobile_hash", "gender", "status", "deleted"],
            "biz_order": ["order_no", "user_id", "status", "version"],
            "biz_sku_stock": ["sku_id", "total_stock", "version"],
        }
        for table_name, columns in cases.items():
            table = ORM_TABLES[table_name]
            for col in columns:
                assert not table.c[col].nullable, f"{table_name}.{col} 不应可空"


class TestUniqueIndexes:
    """DDL 侧的 UNIQUE 索引必须原样出现在模型里（名字 + 列清单）。"""

    def test_unique_indexes_match_ddl(self) -> None:
        ddl_indexes = parse_schema_unique_index_columns()
        problems: list[str] = []
        for table_name, indexes in ddl_indexes.items():
            if not indexes:
                continue
            model_table = ORM_TABLES[table_name]
            actual = {
                c.name: tuple(col.name for col in c.columns)
                for c in model_table.constraints
                if isinstance(c, UniqueConstraint)
            }
            for name, cols in indexes.items():
                if name not in actual:
                    problems.append(f"{table_name}: 缺 UNIQUE 约束 {name}")
                elif actual[name] != cols:
                    problems.append(f"{table_name}.{name}: 列不符 DDL={cols} 模型={actual[name]}")
        assert not problems, "\n".join(problems)

    def test_probe_known_idempotency_index_exists(self) -> None:
        """反向自检：确认上面的比对不是空转（`biz_stock_log` 的幂等键是 C8 的重点）。"""
        names = {c.name for c in ORM_TABLES["biz_stock_log"].constraints}
        assert "uk_idempotent_key" in names
