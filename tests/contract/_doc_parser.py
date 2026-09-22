"""契约测试公共工具：从 docs/*.md 解析"文档事实"。

设计原则（docs/工程化门禁方案.md）：
    文档管「为什么」，测试管「必须」。
    测试必须直接读文档原文，而不是把文档内容再抄一遍——
    否则抄写这一步就成了新的漂移点。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# 项目根：tests/contract/_doc_parser.py → 上溯 2 层
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DOCS: Path = PROJECT_ROOT / "docs"

DATA_DICTIONARY: Path = DOCS / "DATA-DICTIONARY.md"
API_DOC: Path = DOCS / "API.md"
SCHEMA_SQL: Path = DOCS / "sql" / "schema.sql"


# ---------------------------------------------------------------------
# 通用 markdown 表格解析
# ---------------------------------------------------------------------

_SEP_CELL = re.compile(r"^:?-{2,}:?$")


def _split_row(line: str) -> list[str]:
    """切分 markdown 表格行，返回单元格文本（已 strip，保留内联反引号）。"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_markdown_table(lines: list[str]) -> list[list[str]]:
    """解析一组 markdown 表格行（不含表头/分隔行之外的噪音）。

    返回数据行列表（已剔除表头行与 |---| 分隔行）。
    """
    rows: list[list[str]] = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if all(_SEP_CELL.match(c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    return rows


def strip_backticks(text: str) -> str:
    """去掉单元格里包裹的反引号与加粗标记，便于比较。"""
    return text.replace("`", "").replace("**", "").strip()


# ---------------------------------------------------------------------
# DATA-DICTIONARY.md §一 状态枚举映射
# ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def parse_enum_mappings() -> dict[str, dict[int, tuple[str, str]]]:
    """解析 DATA-DICTIONARY.md §一 的全部枚举映射表。

    返回：{ "biz_order.status": { 10: ("待付款", "PENDING_PAY"), ... }, ... }

    文档格式：
        ### `biz_order.status`  (PRD §6.1)

        | 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
        |----|-----------|------------------------|------|
        | `10` | 待付款 | `PENDING_PAY` | 下单成功，等待支付 |
    """
    text = DATA_DICTIONARY.read_text(encoding="utf-8")
    lines = text.splitlines()

    # 只在 §一 章节内解析（到下一个 ## 为止）
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 一、"))
    end = next(
        (i for i, ln in enumerate(lines[start + 1 :], start + 1) if ln.startswith("## ")),
        len(lines),
    )
    section = lines[start:end]

    heading_re = re.compile(r"^###\s+`([^`]+)`")
    result: dict[str, dict[int, tuple[str, str]]] = {}
    current: str | None = None

    for line in section:
        m = heading_re.match(line)
        if m:
            current = m.group(1).strip()
            result[current] = {}
            continue
        if current is None or not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 3:
            continue
        raw_value = strip_backticks(cells[0])
        if not raw_value.isdigit():  # 跳过表头与分隔行
            continue
        db_name = cells[1].strip()
        code_name = strip_backticks(cells[2])
        result[current][int(raw_value)] = (db_name, code_name)

    return result


# ---------------------------------------------------------------------
# DATA-DICTIONARY.md §二 表清单
# ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def parse_table_declaration() -> dict[str, int]:
    """解析 DATA-DICTIONARY.md §二 表清单。

    返回：{ "biz_user": 13, ... }（表名 → 声明字段数）
    """
    text = DATA_DICTIONARY.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 二、"))
    end = next(
        (i for i, ln in enumerate(lines[start + 1 :], start + 1) if ln.startswith("## ")),
        len(lines),
    )
    section = lines[start:end]

    result: dict[str, int] = {}
    for line in section:
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 4:
            continue
        table_cell = cells[1]
        if "`" not in table_cell:
            continue  # 表头/分隔行/非表名行
        table = strip_backticks(table_cell)
        count_cell = cells[3].strip()
        if not count_cell.isdigit():
            continue
        result[table] = int(count_cell)
    return result


# ---------------------------------------------------------------------
# DATA-DICTIONARY.md §三 字段明细
# ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def parse_field_details() -> dict[str, list[str]]:
    """解析 DATA-DICTIONARY.md §三 字段明细。

    返回：{ "biz_user": ["id", "mobile", ...], ... }（表名 → 字段名列表）
    """
    text = DATA_DICTIONARY.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 三、"))
    end = next(
        (i for i, ln in enumerate(lines[start + 1 :], start + 1) if ln.startswith("## ")),
        len(lines),
    )
    section = lines[start:end]

    heading_re = re.compile(r"^####\s+`([^`]+)`")
    result: dict[str, list[str]] = {}
    current: str | None = None

    for line in section:
        m = heading_re.match(line)
        if m:
            current = m.group(1).strip()
            result[current] = []
            continue
        if current is None or not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 4:
            continue
        field_cell = cells[0]
        if "`" not in field_cell:
            continue  # 表头/分隔行
        result[current].append(strip_backticks(field_cell))

    return result


# ---------------------------------------------------------------------
# API.md 错误码
# ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def parse_api_error_codes() -> dict[int, str]:
    """解析 API.md 全文中出现的全部 5 位错误码。

    返回：{ 10001: "参数校验失败", ... }

    注意：只取行内以 ``| `NNNNN` |`` 形式出现的表格行（即码表明细），
    避免把正文里引用的码当成定义。
    """
    text = API_DOC.read_text(encoding="utf-8")
    row_re = re.compile(r"^\|\s*`(\d{5})`\s*\|\s*([^|]+?)\s*\|")

    result: dict[int, str] = {}
    for line in text.splitlines():
        m = row_re.match(line)
        if not m:
            continue
        code = int(m.group(1))
        desc = m.group(2).strip()
        # 同一码多次出现时保留首个定义
        result.setdefault(code, desc)
    return result


@lru_cache(maxsize=1)
def parse_error_segments() -> dict[int, str]:
    """解析 API.md §1.2 错误码分段表。

    返回：{ 0: "成功", 1: "通用", ... }
    """
    text = API_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = next(i for i, ln in enumerate(lines) if "错误码分段" in ln)
    # 分段表只到下一个 ### 或 ## 为止（否则会吞掉 §1.3 明细表）
    end = next(
        (
            i
            for i, ln in enumerate(lines[start + 1 :], start + 1)
            if ln.startswith("## ") or ln.startswith("### ")
        ),
        len(lines),
    )

    result: dict[int, str] = {}
    for line in lines[start:end]:
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 3:
            continue
        seg = strip_backticks(cells[0])
        if seg.isdigit():
            result[int(seg)] = cells[1].strip()
        elif re.fullmatch(r"[1-9]x{4}", seg):
            result[int(seg[0])] = cells[1].strip()
    return result


# ---------------------------------------------------------------------
# schema.sql 解析
# ---------------------------------------------------------------------

_CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+`(?P<name>\w+)`\s*\((?P<body>.*?)\n\)\s*ENGINE",
    re.DOTALL,
)


@lru_cache(maxsize=1)
def parse_schema_tables() -> dict[str, list[str]]:
    """解析 docs/sql/schema.sql，返回 { 表名: [字段名, ...] }。

    字段名来源：行首反引号包裹的第一列标识符。
    排除 ``PRIMARY KEY`` / ``UNIQUE KEY`` / ``INDEX`` / ``KEY`` / ``CONSTRAINT`` / ``CHECK``。
    """
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}

    for m in _CREATE_TABLE.finditer(sql):
        name = m.group("name")
        body = m.group("body")
        fields: list[str] = []
        for raw in body.splitlines():
            line = raw.strip()
            fm = re.match(r"^`(?P<col>\w+)`\s+", line)
            if not fm:
                continue
            col = fm.group("col")
            if col.upper() in {
                "PRIMARY",
                "UNIQUE",
                "INDEX",
                "KEY",
                "CONSTRAINT",
                "CHECK",
                "FULLTEXT",
            }:
                continue
            fields.append(col)
        result[name] = fields

    return result


@lru_cache(maxsize=1)
def parse_schema_unique_indexes() -> dict[str, set[str]]:
    """解析 schema.sql 中每个表的 UNIQUE 索引列集合。

    返回：{ "biz_user": {"uk_mobile_hash"}, ... }
    """
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}

    for m in _CREATE_TABLE.finditer(sql):
        name = m.group("name")
        body = m.group("body")
        keys: set[str] = set()
        for raw in body.splitlines():
            line = raw.strip()
            km = re.match(r"^(?:UNIQUE\s+)?(?:KEY|INDEX)\s+`(?P<kname>\w+)`", line)
            if km and line.upper().startswith("UNIQUE"):
                keys.add(km.group("kname"))
        result[name] = keys

    return result
