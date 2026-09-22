"""C8 契约测试：关键 UNIQUE 索引与不变量约束。

校验链：
    docs/DATA-DICTIONARY.md §四 不变量 #9~#12  ←→  docs/sql/schema.sql

为什么重要：
    这四条是"幂等性"的物理保证——
        #9  库存流水幂等键唯一   → 防重复扣减
        #10 渠道交易号唯一       → 防重复发货
        #11 一订单项一评价       → 防刷评价
        #12 本地消息幂等         → 防重复投递
    如果索引在迁移中丢失，应用层的幂等逻辑就失去最后一道兜底，
    表面看代码没改、测试也过，线上却开始重复扣库存。
    —— 这正是"重构时漏写 UNIQUE"这类事故的典型形态。

同时校验 §四 12 条不变量的"强制方式"声明与 DDL 实际一致。
"""

from __future__ import annotations

import re

import pytest

from tests.contract._doc_parser import (
    DATA_DICTIONARY,
    SCHEMA_SQL,
    parse_schema_unique_indexes,
)

pytestmark = pytest.mark.contract

UNIQUE_INDEXES: dict[str, set[str]] = parse_schema_unique_indexes()

# ---------------------------------------------------------------------
# 不变量 §四 中声明"由 DDL 强制"的条目（其余为应用层保证）
# ---------------------------------------------------------------------

DDL_ENFORCED_UNIQUE = {
    "biz_stock_log": "idempotent_key",  # #9
    "biz_payment": "channel_trade_no",  # #10
    "biz_product_review": "order_item_id",  # #11
}


class TestDocInvariantsSection:
    """§四 不变量章节完整性。"""

    def test_has_twelve_invariants(self) -> None:
        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        start = text.index("## 四、数据库约束与不变量")
        section = text[start:]
        # 表格行形如 | 1 | ... |
        numbered = re.findall(r"^\|\s*(\d+)\s*\|", section, re.MULTILINE)
        assert len(numbered) == 12, f"§四 不变量应为 12 条，实为 {len(numbered)}"
        assert [int(n) for n in numbered] == list(range(1, 13)), "不变量编号不连续"

    def test_stock_identity_declared(self) -> None:
        """#1 库存恒等式必须存在。"""
        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        assert "total_stock = available_stock + locked_stock + sold_stock" in text

    def test_check_constraint_declared(self) -> None:
        """#2 库存非负的 CHECK 约束名必须存在。"""
        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        assert "chk_stock_non_negative" in text


class TestUniqueIndexesPresent:
    """DDL 中的 UNIQUE 索引必须真实存在。"""

    @pytest.mark.parametrize(
        "table,column",
        sorted((t, c) for t, c in DDL_ENFORCED_UNIQUE.items()),
    )
    def test_unique_index_exists(self, table: str, column: str) -> None:
        """不变量 #9/#10/#11 声明的 UNIQUE 必须在 DDL 中真实建出。"""
        assert table in UNIQUE_INDEXES, f"schema.sql 中找不到表 {table}"
        keys = UNIQUE_INDEXES[table]
        assert keys, f"{table} 没有任何 UNIQUE KEY（不变量要求列 {column} 唯一）"

    def test_local_message_unique(self) -> None:
        """#12 本地消息幂等：sys_local_message 必须有 UNIQUE 索引。"""
        assert UNIQUE_INDEXES.get("sys_local_message"), (
            "sys_local_message 缺少 UNIQUE 索引（不变量 #12 要求 (biz_type,biz_no,topic) 唯一）"
        )

    def test_user_mobile_hash_unique(self) -> None:
        """biz_user.mobile_hash 必须唯一（登录查询与账号唯一性的物理保证）。"""
        assert "uk_mobile_hash" in UNIQUE_INDEXES.get("biz_user", set())


class TestUniqueIndexColumnMapping:
    """UNIQUE 索引覆盖的列须与不变量声明一致。"""

    def test_stock_log_idempotent_key_unique(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        block = _table_block(sql, "biz_stock_log")
        assert re.search(r"UNIQUE\s+KEY\s+`\w+`\s*\(\s*`idempotent_key`", block), (
            "biz_stock_log 的 UNIQUE 索引必须建在 idempotent_key 上"
        )

    def test_payment_channel_trade_no_unique(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        block = _table_block(sql, "biz_payment")
        assert re.search(r"UNIQUE\s+KEY\s+`\w+`\s*\(\s*`channel_trade_no`", block), (
            "biz_payment 的 UNIQUE 索引必须建在 channel_trade_no 上"
        )

    def test_review_order_item_id_unique(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        block = _table_block(sql, "biz_product_review")
        assert re.search(r"UNIQUE\s+KEY\s+`\w+`\s*\(\s*`order_item_id`", block), (
            "biz_product_review 的 UNIQUE 索引必须建在 order_item_id 上"
        )


class TestStockCheckConstraint:
    """库存非负 CHECK 约束（不变量 #2）。"""

    def test_check_constraint_in_ddl(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        assert "chk_stock_non_negative" in sql, "DDL 缺少 chk_stock_non_negative CHECK 约束"

    def test_check_covers_four_segments(self) -> None:
        """CHECK 必须覆盖库存四段（total/available/locked/sold）全部 >= 0。"""
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        block = _table_block(sql, "biz_sku_stock")
        constraint = re.search(r"CONSTRAINT\s+`chk_stock_non_negative`[^,]*", block)
        assert constraint, "biz_sku_stock 中找不到 chk_stock_non_negative"
        expr = constraint.group(0)
        for col in ("total_stock", "available_stock", "locked_stock", "sold_stock"):
            assert col in expr, f"CHECK 约束未覆盖 {col}"

    def test_mysql_version_requirement_documented(self) -> None:
        """CHECK 约束需 MySQL >= 8.0.16，该要求必须写在 DDL 头部。"""
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        assert "8.0.16" in sql


def _table_block(sql: str, table: str) -> str:
    """取出某表的 CREATE TABLE 语句块。"""
    pattern = re.compile(
        rf"CREATE TABLE IF NOT EXISTS\s+`{re.escape(table)}`\s*\((?P<body>.*?)\n\)\s*ENGINE",
        re.DOTALL,
    )
    m = pattern.search(sql)
    assert m, f"schema.sql 中找不到表 {table} 的 CREATE TABLE 语句"
    return m.group("body")
