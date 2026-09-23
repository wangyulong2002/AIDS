#!/usr/bin/env python3
"""从 `docs/sql/schema.sql` 生成 ORM 模型（`app/models/`）。

=====================================================================
为什么模型是**生成物**：
    DDL（`docs/sql/schema.sql`）是本项目表结构的**唯一来源** —— 它同时也是
    `DATA-DICTIONARY.md` 与契约测试 C1 的锚点。38 张表 / 419 个字段如果靠手抄，
    必然出现"DDL 加了列、模型忘了加"的漂移，而症状是运行期的
    `Unknown column`（发生在请求链深处，排查成本高）。

    所以模型也走"单一来源 + 派生"，与 `aids-*/requirements.txt` 完全同构。

边界（务必知道）：
    - 生成物**不手改**。要加业务逻辑（relationship / 领域方法），
      写到 `app/models/relations.py` 或各 service 层 —— 重新生成会覆盖本文件。
    - 敏感字段的透明加解密（AES-GCM / HMAC TypeDecorator）属于 BE-07 的范围，
      本脚本只映射 DDL 里**已经存在**的列。
    - FULLTEXT 索引不在 DDL 里（由 BE-12 经 Alembic 迁移落地），故此处不生成。

用法：
    python3 scripts/gen_orm_models.py --write    # 重新生成 app/models/
    python3 scripts/gen_orm_models.py --check    # 校验模型与 DDL 一致（CI / pre-commit）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "sql" / "schema.sql"
OUT_DIR = ROOT / "app" / "models"

# 表前缀 → 输出文件
PREFIX_FILES: dict[str, str] = {"biz": "biz.py", "ai": "ai.py", "sys": "sys.py"}

HEADER = '''"""AIDS ORM 模型 —— `{prefix}_*` 表（**生成物，请勿手改**）。

单一来源：`docs/sql/schema.sql`
生成器：  `python3 scripts/gen_orm_models.py --write`
校验：    `python3 scripts/gen_orm_models.py --check`（CI / pre-commit）

要加业务逻辑（relationship / 领域方法），写到 `app/models/relations.py`
或各 service 层 —— 重新生成会覆盖本文件。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Index, UniqueConstraint, text
from sqlalchemy.dialects.mysql import (
    BIGINT,
    CHAR,
    DATETIME,
    DECIMAL,
    INTEGER,
    JSON,
    LONGTEXT,
    TEXT,
    TINYINT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.orm.base import Base
from app.orm.mixins import (
    CreateTimeMixin,
    OptimisticLockMixin,
    PKMixin,
    SoftDeleteMixin,
    TimestampMixin,
)
'''


class Column:
    __slots__ = ("comment", "default", "name", "nullable", "sql_type", "unsigned")

    def __init__(
        self,
        name: str,
        sql_type: str,
        unsigned: bool,
        nullable: bool,
        default: str | None,
        comment: str | None,
    ) -> None:
        self.name = name
        self.sql_type = sql_type
        self.unsigned = unsigned
        self.nullable = nullable
        self.default = default
        self.comment = comment

    @property
    def py_type(self) -> str:
        base = self.sql_type.split("(")[0].upper()
        if base in ("VARCHAR", "CHAR", "TEXT", "LONGTEXT"):
            t = "str"
        elif base == "DATETIME":
            t = "dt.datetime"
        elif base == "DECIMAL":
            t = "Decimal"
        elif base == "JSON":
            t = "Any"
        else:
            t = "int"
        return f"{t} | None" if self.nullable else t

    @property
    def sa_type(self) -> str:
        sql = self.sql_type
        m = re.match(r"^([A-Za-z]+)(?:\(([^)]*)\))?$", sql)
        assert m, f"无法解析的类型: {sql}"
        name, args = m.group(1).upper(), m.group(2)
        mapped = {
            "BIGINT": "BIGINT",
            "INT": "INTEGER",
            "INTEGER": "INTEGER",
            "TINYINT": "TINYINT",
            "SMALLINT": "INTEGER",
            "VARCHAR": "VARCHAR",
            "CHAR": "CHAR",
            "TEXT": "TEXT",
            "LONGTEXT": "LONGTEXT",
            "DATETIME": "DATETIME",
            "DATE": "DATETIME",
            "DECIMAL": "DECIMAL",
            "JSON": "JSON",
        }[name]
        call = f"{mapped}({args})" if args is not None else mapped
        if self.unsigned:
            call = f"{mapped}({args + ', ' if args else ''}unsigned=True)"
        return call


class Table:
    def __init__(self, name: str, comment: str | None) -> None:
        self.name = name
        self.comment = comment
        self.columns: list[Column] = []
        self.pk: list[str] = []
        self.indexes: list[tuple[str, bool, tuple[str, ...], str | None]] = []
        self.checks: list[str] = []


_CREATE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+`(?P<name>\w+)`\s*\((?P<body>.*?)\n\)\s*ENGINE[^;]*?(?:COMMENT='(?P<comment>[^']*)')?;",
    re.DOTALL,
)
# 注意两点，都是实测踩出来的：
#   1) 用 `^\s*` 而不是 `^\s+`：列定义在 _split_columns 里已 strip，段首没有空白，
#      要求至少一个空白会导致**一个列都匹配不上**；
#   2) `rest` 不能写成 `[^,]*`：COMMENT 里允许出现逗号
#      （如 COMMENT '手机号密文(AES-256-GCM, Base64)'），那样这些列会整行匹配失败。
#      段已经是"顶层逗号"切分的结果，段内不会再有多余分隔逗号。
_COLUMN = re.compile(
    r"^\s*`(?P<name>\w+)`\s+(?P<type>[A-Za-z]+)(?:\((?P<args>[^)]*)\))?"
    r"(?P<unsigned>\s+UNSIGNED)?(?P<rest>.*)",
    re.DOTALL,
)
_KEY = re.compile(
    r"^\s*(?P<unique>UNIQUE\s+)?(?:KEY|INDEX)\s+`(?P<name>\w+)`\s*\((?P<cols>[^)]*)\)"
    r"(?P<rest>.*?),?\s*$"
)
_PK = re.compile(r"^\s*PRIMARY KEY\s*\((?P<cols>[^)]*)\)")
_CHECK = re.compile(r"CHECK\s*\((?P<body>[^)]*\([^)]*\)[^)]*)\)")
_COMMENT = re.compile(r"COMMENT\s*'(?P<text>[^']*)'")
_STRING_LITERAL = re.compile(r"'[^']*'")


def _split_columns(body: str) -> list[str]:
    """按顶层逗号切分表体。

    两个必须处理的细节：
      - 括号内的逗号不是分隔符（`DECIMAL(12,2)`）；
      - **引号内的括号与逗号都不是**（`COMMENT '密文(AES-256-GCM, Base64)'`）——
        只数括号的话，注释里不成对的括号会让深度错乱，把一整段列吞掉。
    """
    parts: list[str] = []
    depth = 0
    in_quote = False
    current: list[str] = []
    for ch in body:
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def parse_schema() -> dict[str, Table]:
    sql = SCHEMA.read_text(encoding="utf-8")
    tables: dict[str, Table] = {}

    for m in _CREATE.finditer(sql):
        table = Table(m.group("name"), m.group("comment"))
        for part in _split_columns(m.group("body")):
            if pk := _PK.match(part):
                table.pk = [c.strip().strip("`") for c in pk.group("cols").split(",") if c.strip()]
                continue
            if key := _KEY.match(part):
                cols = tuple(
                    c.strip().strip("`") for c in key.group("cols").split(",") if c.strip()
                )
                cm = _COMMENT.search(key.group("rest"))
                table.indexes.append(
                    (
                        key.group("name"),
                        bool(key.group("unique")),
                        cols,
                        cm.group("text") if cm else None,
                    )
                )
                continue
            if part.upper().startswith(("CONSTRAINT", "CHECK")):
                if check := _CHECK.search(part):
                    table.checks.append(check.group("body"))
                continue
            if col := _COLUMN.match(part):
                rest = col.group("rest")
                cm = _COMMENT.search(rest)
                dm = re.search(r"DEFAULT\s+('(?:[^']*)'|[\w.()]+)", rest)
                table.columns.append(
                    Column(
                        name=col.group("name"),
                        sql_type=col.group("type")
                        + (f"({col.group('args')})" if col.group("args") else ""),
                        unsigned=bool(col.group("unsigned")),
                        nullable="NOT NULL" not in rest.upper(),
                        default=dm.group(1) if dm else None,
                        comment=cm.group("text") if cm else None,
                    )
                )
        tables[table.name] = table
    return tables


def _mixin_list(t: Table) -> list[str]:
    """按**实际列**决定 Mixin 组合（而不是"默认全给"）。"""
    cols = {c.name for c in t.columns}
    bases = ["Base", "PKMixin"]
    if "create_time" in cols and "update_time" in cols:
        bases.append("TimestampMixin")
    elif "create_time" in cols:
        bases.append("CreateTimeMixin")
    if "deleted" in cols:
        bases.append("SoftDeleteMixin")
    if "version" in cols:
        bases.append("OptimisticLockMixin")
    return bases


def _skip_column(t: Table, c: Column) -> bool:
    """Mixin 已提供的列不再重复声明。"""
    if c.name == "id":
        return True
    if c.name in ("create_time", "update_time") and any(
        b in _mixin_list(t) for b in ("TimestampMixin", "CreateTimeMixin")
    ):
        return c.name == "update_time" or "CreateTimeMixin" in _mixin_list(t)
    if c.name == "deleted" and "SoftDeleteMixin" in _mixin_list(t):
        return True
    # 刻意写成直接返回条件：`if cond: return True / return False` 会被 ruff 的
    # SIM103 判为可简化。scripts/ 此前不在 CI 的 ruff 扫描面内（只扫 app/tests/aids-*），
    # 而 pre-commit 的 ruff 钩子扫全仓 —— 于是 L1 会红、L2 却绿。现已把 scripts/ 纳入 CI。
    return c.name == "version" and "OptimisticLockMixin" in _mixin_list(t)


def _class_name(table: str) -> str:
    return "".join(part.capitalize() for part in table.split("_"))


def _render_column(c: Column) -> str:
    args = [c.sa_type]
    if c.nullable:
        args.append("nullable=True")
    if c.default is not None:
        raw = c.default
        if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
            # 去掉 SQL 的单引号后用 repr 重新转义 —— DDL 注释/默认值里出现
            # 双引号是常态（COMMENT '轨迹描述(如"已签收")'），直接拼字面量会出语法错误。
            args.append(f"server_default=text({raw[1:-1]!r})")
        elif raw.upper() == "CURRENT_TIMESTAMP":
            args.append('server_default=text("CURRENT_TIMESTAMP")')
        else:
            args.append(f"server_default=text({raw!r})")
    if c.comment:
        args.append(f"comment={c.comment!r}")
    return f"    {c.name}: Mapped[{c.py_type}] = mapped_column({', '.join(args)})"


def _render_table(t: Table) -> str:
    lines = [
        f"class {_class_name(t.name)}({', '.join(_mixin_list(t))}):",
        f'    """{t.comment or t.name}（`{t.name}`）。"""',
        "",
        f'    __tablename__ = "{t.name}"',
    ]

    body = [_render_column(c) for c in t.columns if not _skip_column(t, c)]

    constraints: list[str] = []
    for name, unique, cols, comment in t.indexes:
        cols_repr = ", ".join(f'"{c}"' for c in cols)
        # 索引注释**不作为 SQLAlchemy 参数**：Index 只接受 `<dialect>_<arg>` 形式的
        # 额外关键字，传 comment= 会直接 TypeError。信息不丢 —— 注释的权威来源是
        # DDL 本身，这里保留为行尾 Python 注释，可读且不依赖版本差异。
        note = f"  # {comment}" if comment else ""
        if unique:
            # UniqueConstraint(*cols, name=...)
            constraints.append(f'        UniqueConstraint({cols_repr}, name="{name}"),{note}')
        else:
            # 注意签名差异：Index 的第一个位置参数是**索引名**，不是列 ——
            # 写成 Index(cols..., name=...) 会在导入时直接 TypeError。
            constraints.append(f'        Index("{name}", {cols_repr}),{note}')

    if body:
        lines.append("")
        lines.extend(body)
    if constraints:
        lines.extend(["", "    __table_args__ = (", *constraints, "    )"])
    if t.checks:
        lines.append("")
        lines.append(f"    # DDL 侧 CHECK 约束（Alembic 已建）：{'; '.join(t.checks)}")

    return "\n".join(lines)


def render_all() -> dict[str, str]:
    tables = parse_schema()
    grouped: dict[str, list[Table]] = {p: [] for p in PREFIX_FILES}
    for name, t in tables.items():
        prefix = name.split("_", 1)[0]
        if prefix not in grouped:
            raise SystemExit(f"未登记的表前缀 {prefix!r}（表 {name}）—— 请更新 PREFIX_FILES")
        grouped[prefix].append(t)

    out: dict[str, str] = {}
    for prefix, fname in PREFIX_FILES.items():
        body = "\n\n\n".join(
            _render_table(t) for t in sorted(grouped[prefix], key=lambda x: x.name)
        )
        out[fname] = HEADER.format(prefix=prefix) + "\n\n" + body + "\n"

    imports = "\n".join(
        f"from app.models.{f[:-3]} import *  # noqa: F401,F403" for f in PREFIX_FILES.values()
    )
    out["__init__.py"] = (
        '"""ORM 模型汇总（**生成物**）。\n\n'
        "导入本包即让全部表注册进 `Base.metadata` —— Alembic autogenerate\n"
        "与应用启动都依赖这一点（少导入一个模块，生成的迁移就会缺表）。\n"
        '"""\n\nfrom __future__ import annotations\n\n' + imports + "\n"
    )
    return out


def write(rendered: dict[str, str]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fname, text in rendered.items():
        (OUT_DIR / fname).write_text(text, encoding="utf-8")
        print(f"  ✓ 已写入 app/models/{fname}")
    return 0


def check(rendered: dict[str, str]) -> int:
    problems: list[str] = []
    for fname, expected in rendered.items():
        path = OUT_DIR / fname
        if not path.exists():
            problems.append(f"缺少 app/models/{fname}")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"app/models/{fname} 与 schema.sql 不一致（被手改过或 DDL 已变更）")
    if problems:
        print("[ORM 模型一致性] 发现问题：", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        print("  修复：python3 scripts/gen_orm_models.py --write", file=sys.stderr)
        return 1
    print(f"[ORM 模型一致性] ok：{len(rendered) - 1} 个模型文件与 schema.sql 一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 schema.sql 生成 ORM 模型")
    ap.add_argument("--write", action="store_true", help="重新生成 app/models/")
    ap.add_argument("--check", action="store_true", help="校验模型与 DDL 一致（默认）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印表清单")
    args = ap.parse_args(argv)

    rendered = render_all()
    if args.verbose:
        tables = parse_schema()
        print(f"schema.sql → {len(tables)} 张表")
        for name in sorted(tables):
            t = tables[name]
            print(
                f"   {name:24s} {len(t.columns):2d} 列, {len(t.indexes)} 索引, mixins={','.join(_mixin_list(t)[1:]) or '-'}"
            )

    return write(rendered) if args.write else check(rendered)


if __name__ == "__main__":
    raise SystemExit(main())
