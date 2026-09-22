"""C1 契约测试：DDL ↔ 数据字典 一致性。

校验链：
    docs/sql/schema.sql  ←→  docs/DATA-DICTIONARY.md §二 表清单 / §三 字段明细

两层校验：
    ① 静态层（始终运行）：解析 schema.sql 文本 vs 解析 markdown 表格
    ② 反射层（需真实 MySQL，marker=requires_mysql）：连库反射 information_schema
       静态层能查"文档之间是否矛盾"，反射层才能查"实际建出来的库是否正确"。
       两者都需要——静态层跑得快，反射层查得真。

基线（v1.2 schema）：
    38 张表 / 419 字段 / 前缀 biz_ · ai_ · sys_
"""

from __future__ import annotations

import pytest

from tests.contract._doc_parser import (
    parse_field_details,
    parse_schema_tables,
    parse_table_declaration,
)

pytestmark = pytest.mark.contract

DOC_TABLE_DECL: dict[str, int] = parse_table_declaration()
DOC_FIELD_DETAIL: dict[str, list[str]] = parse_field_details()
SCHEMA_TABLES: dict[str, list[str]] = parse_schema_tables()

ALLOWED_PREFIXES = ("biz_", "ai_", "sys_")

# 基线常量：改动文档时应同步更新，让"变更"显式化而非静默漂移
EXPECTED_TABLE_COUNT = 38
EXPECTED_FIELD_TOTAL = 419


# =====================================================================
# 文档内部一致性（表清单 ↔ 字段明细）
# =====================================================================


class TestDocInternalConsistency:
    """文档自身两处（§二 表清单 / §三 字段明细）必须互相自洽。"""

    def test_table_set_identical(self) -> None:
        declared = set(DOC_TABLE_DECL)
        detailed = set(DOC_FIELD_DETAIL)
        assert declared == detailed, (
            f"字典 §二 表清单与 §三 字段明细的表集合不一致：\n"
            f"  仅 §二: {sorted(declared - detailed)}\n"
            f"  仅 §三: {sorted(detailed - declared)}"
        )

    def test_declared_field_count_matches_detail(self) -> None:
        """§二 声明的字段数必须等于 §三 明细里的实际行数。"""
        mismatches: list[str] = []
        for table, declared_count in DOC_TABLE_DECL.items():
            actual = len(DOC_FIELD_DETAIL.get(table, []))
            if declared_count != actual:
                mismatches.append(f"  {table}: §二声明 {declared_count}, §三实际 {actual}")
        assert not mismatches, "字段数不一致：\n" + "\n".join(mismatches)

    def test_expected_totals(self) -> None:
        """基线：38 表 / 419 字段。"""
        assert len(DOC_TABLE_DECL) == EXPECTED_TABLE_COUNT
        assert sum(DOC_TABLE_DECL.values()) == EXPECTED_FIELD_TOTAL


# =====================================================================
# DDL ↔ 字典 一致性
# =====================================================================


class TestSchemaMatchesDictionary:
    """schema.sql 的表与字段必须与字典完全对齐。"""

    def test_table_sets_identical(self) -> None:
        schema_tables = set(SCHEMA_TABLES)
        dict_tables = set(DOC_TABLE_DECL)
        assert schema_tables == dict_tables, (
            f"schema.sql 与数据字典表集合不一致：\n"
            f"  仅在 schema.sql: {sorted(schema_tables - dict_tables)}\n"
            f"  仅在数据字典:    {sorted(dict_tables - schema_tables)}"
        )

    def test_table_count_baseline(self) -> None:
        assert len(SCHEMA_TABLES) == EXPECTED_TABLE_COUNT

    def test_field_count_total(self) -> None:
        total = sum(len(v) for v in SCHEMA_TABLES.values())
        assert total == EXPECTED_FIELD_TOTAL, (
            f"schema.sql 字段总数为 {total}，基线为 {EXPECTED_FIELD_TOTAL}"
        )

    def test_each_table_field_count_matches(self) -> None:
        """逐表比对字段数。"""
        mismatches: list[str] = []
        for table, declared in DOC_TABLE_DECL.items():
            actual = len(SCHEMA_TABLES.get(table, []))
            if declared != actual:
                mismatches.append(f"  {table}: 字典 {declared}, DDL {actual}")
        assert not mismatches, "DDL 与字典字段数不一致：\n" + "\n".join(mismatches)


@pytest.mark.parametrize("table", sorted(set(SCHEMA_TABLES) & set(DOC_FIELD_DETAIL)))
class TestFieldLevelMatch:
    """逐表比对字段名与顺序。"""

    def test_field_names_match(self, table: str) -> None:
        ddl_fields = SCHEMA_TABLES[table]
        dict_fields = DOC_FIELD_DETAIL[table]
        assert ddl_fields == dict_fields, (
            f"{table} 字段不一致：\n"
            f"  仅 DDL: {sorted(set(ddl_fields) - set(dict_fields))}\n"
            f"  仅字典: {sorted(set(dict_fields) - set(ddl_fields))}\n"
            f"  顺序是否一致: {ddl_fields == dict_fields}"
        )


class TestTableNamingConvention:
    """表前缀约定（schema.sql 头部注释约定 1）。"""

    def test_all_tables_have_allowed_prefix(self) -> None:
        bad = [t for t in SCHEMA_TABLES if not t.startswith(ALLOWED_PREFIXES)]
        assert not bad, f"以下表名不符合前缀约定 {ALLOWED_PREFIXES}：{bad}"

    def test_prefix_distribution(self) -> None:
        """前缀分布基线：biz_=22 / ai_=7 / sys_=9。"""
        counts: dict[str, int] = {}
        for t in SCHEMA_TABLES:
            for p in ALLOWED_PREFIXES:
                if t.startswith(p):
                    counts[p] = counts.get(p, 0) + 1
                    break
        assert counts == {"biz_": 22, "ai_": 7, "sys_": 9}, f"前缀分布变化：{counts}"


class TestMoneyAndTimeConventions:
    """schema.sql 头部约定 3/4 的机械化检查。"""

    def test_no_float_for_money(self) -> None:
        """约定 3：金额一律 DECIMAL(12,2)，禁止 float/double。"""
        import re

        from tests.contract._doc_parser import SCHEMA_SQL

        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        # 找出含金额语义的列名若使用 FLOAT/DOUBLE 则违规
        money_hint = re.compile(
            r"`(?P<col>\w*(?:amount|price|fee|freight|pay|discount)\w*)`\s+(?P<type>\w+)",
            re.IGNORECASE,
        )
        bad: list[str] = []
        for m in money_hint.finditer(sql):
            if m.group("type").upper() in {"FLOAT", "DOUBLE", "REAL", "DECIMAL_FLOAT"}:
                bad.append(f"  {m.group('col')} 使用 {m.group('type')}")
        assert not bad, "金额字段不得使用浮点类型：\n" + "\n".join(bad)

    def test_logical_delete_column_consistency(self) -> None:
        """约定 5：逻辑删除列名统一为 deleted。"""
        import re

        from tests.contract._doc_parser import SCHEMA_SQL

        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        # 检出可能的别名（is_deleted / del_flag / deleted_at 等）
        aliases = set(re.findall(r"`(is_deleted|del_flag|is_del|deleted_at)`", sql))
        assert not aliases, f"逻辑删除列存在别名，应统一为 deleted：{sorted(aliases)}"


# =====================================================================
# 反射层：需真实 MySQL 8
# =====================================================================


@pytest.mark.requires_mysql
class TestSchemaReflection:
    """连真实 MySQL 反射 information_schema，校验实际建库结果。

    由 CI 提供 MySQL 8 services 后运行：
        alembic upgrade head  →  实际建表
        pytest -m requires_mysql
    """

    def test_reflected_table_count(self, db_conn) -> None:
        rows = db_conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
        ).fetchall()
        actual = {r[0] for r in rows}
        expected = set(SCHEMA_TABLES)
        assert actual == expected, (
            f"实际建库与 DDL 不一致：\n"
            f"  库中多余: {sorted(actual - expected)}\n"
            f"  库中缺失: {sorted(expected - actual)}"
        )

    def test_reflected_field_counts(self, db_conn) -> None:
        rows = db_conn.execute(
            "SELECT table_name, COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() GROUP BY table_name"
        ).fetchall()
        actual = {r[0]: r[1] for r in rows}
        mismatches = [
            f"  {t}: DDL {len(SCHEMA_TABLES[t])}, 库中 {actual.get(t, 0)}"
            for t in SCHEMA_TABLES
            if actual.get(t, 0) != len(SCHEMA_TABLES[t])
        ]
        assert not mismatches, "反射字段数不一致：\n" + "\n".join(mismatches)
