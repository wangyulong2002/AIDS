"""Mock 库 ORM ↔ `docs/sql/mock_schema.sql` 一致性（与 C9 同思路的另一半）。

为什么 Mock 库也要有这一层：
    Mock 模拟的是「第三方渠道」——它与主业务是**不同主体、不同数据库**
    （mock_schema.sql 头部）。主库的 `test_orm_matches_schema.py` 只盯
    `app/models/` 与 `schema.sql`，Mock 的独立 `MockBase` 完全不在其扫描面内。
    于是「Mock 模型与渠道 DDL 漂移」的症状是**渠道行为与契约不符**：
    例如漏掉 `uk_out_trade_no` 会让"重复下单返回原单"在真库上变成插入失败，
    而所有 Mock 单测（用替身会话，不建表）依然全绿。

    本文件用**独立解析器**（`tests/contract/_doc_parser.py`）直接读 mock_schema.sql，
    与 `aids_mock.models` 的 metadata 逐表比对，防的正是上面这类静默漂移。
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint

from aids_mock.models import MockBase  # 导入即注册全部 Mock 模型
from tests.contract._doc_parser import (
    parse_mock_schema_tables,
    parse_mock_schema_unique_index_columns,
)

pytestmark = pytest.mark.contract

DDL_TABLES: dict[str, list[str]] = parse_mock_schema_tables()
ORM_TABLES = dict(MockBase.metadata.tables.items())

# mock_schema.sql：支付 4 + 短信 1 + 物流 2 = 7 表
EXPECTED_TABLES = 7


class TestTableCoverage:
    def test_table_count_matches_ddl(self) -> None:
        assert len(DDL_TABLES) == EXPECTED_TABLES, "mock_schema.sql 表数变化，请同步本锚点"
        assert len(ORM_TABLES) == EXPECTED_TABLES

    def test_no_missing_or_extra_table(self) -> None:
        missing = sorted(set(DDL_TABLES) - set(ORM_TABLES))
        extra = sorted(set(ORM_TABLES) - set(DDL_TABLES))
        assert not missing and not extra, f"缺表={missing} 多表={extra}"

    def test_mock_base_is_isolated_from_main_base(self) -> None:
        """Mock 的 Base 必须与主业务库的 Base 分离（否则迁移/DDL 会互相污染）。"""
        from app.orm.base import Base

        assert MockBase is not Base
        assert not (set(ORM_TABLES) & set(Base.metadata.tables)), "Mock 表混进了主库 metadata"


@pytest.mark.parametrize("table_name", sorted(DDL_TABLES))
class TestTableShape:
    def test_columns_match_ddl(self, table_name: str) -> None:
        """字段集合必须与 DDL 完全一致（不比顺序，见 C9 的同类说明）。"""
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
        """Mock 库沿用同一主键约定：`id BIGINT UNSIGNED` + 应用层雪花 ID。"""
        table = ORM_TABLES[table_name]
        assert [c.name for c in table.primary_key] == ["id"]
        assert table.c.id.type.unsigned is True  # type: ignore[attr-defined]
        assert table.c.id.default is not None, "主键必须由应用生成，不能依赖数据库自增"


class TestUniqueIndexes:
    """Mock 的幂等语义全靠 UNIQUE 索引（重复下单返回原单 / 轨迹按时间幂等）。"""

    def test_unique_indexes_match_ddl(self) -> None:
        ddl_indexes = parse_mock_schema_unique_index_columns()
        problems: list[str] = []
        for table_name, indexes in ddl_indexes.items():
            if not indexes:
                continue
            actual = {
                c.name: tuple(col.name for col in c.columns)
                for c in ORM_TABLES[table_name].constraints
                if isinstance(c, UniqueConstraint)
            }
            for name, cols in indexes.items():
                if name not in actual:
                    problems.append(f"{table_name}: 缺 UNIQUE 约束 {name}")
                elif actual[name] != cols:
                    problems.append(f"{table_name}.{name}: 列不符 DDL={cols} 模型={actual[name]}")
        assert not problems, "\n".join(problems)

    def test_probe_known_idempotency_indexes_exist(self) -> None:
        """反向自检：确认比对不是空转 —— 三条幂等键必须在位。"""
        expected = {
            "mock_payment_order": {"uk_out_trade_no", "uk_trade_no"},
            "mock_logistics_trace": {"uk_delivery_time"},
            "mock_recon_file": {"uk_date_type"},
        }
        for table_name, names in expected.items():
            actual = {c.name for c in ORM_TABLES[table_name].constraints}
            assert names <= actual, f"{table_name} 缺幂等键：{sorted(names - actual)}"


class TestNullability:
    def test_missing_column_is_a_hard_fail(self) -> None:
        """自检：本测试本身要能发现"少一列"。"""
        assert "out_trade_no" in ORM_TABLES["mock_payment_order"].columns

    def test_not_null_columns_declared_correctly(self) -> None:
        """抽查核心 NOT NULL 列，防"可空性未被真正映射"。"""
        cases = {
            "mock_payment_order": ["out_trade_no", "trade_no", "amount", "status", "expire_time"],
            "mock_callback_log": ["biz_type", "payload", "sign", "next_retry_time", "retry_count"],
            "mock_logistics_order": ["delivery_no", "company_code", "company_name", "status"],
        }
        for table_name, columns in cases.items():
            table = ORM_TABLES[table_name]
            for col in columns:
                assert not table.c[col].nullable, f"{table_name}.{col} 不应可空"
