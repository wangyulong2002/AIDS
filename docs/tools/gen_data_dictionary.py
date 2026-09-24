#!/usr/bin/env python3
"""AIDS 设计文档一致性的机器门禁。

用法（在仓库任意位置执行均可，路径按脚本自身位置解析）：
    python3 docs/tools/gen_data_dictionary.py            # = --check，全部通过退出 0
    python3 docs/tools/gen_data_dictionary.py --check -v # 逐项打印
    python3 docs/tools/gen_data_dictionary.py --gen biz_order   # 从 DDL 重新生成该表的字典明细

为什么存在：PRD/TASKS/API/DATA-DICTIONARY/schema 之间有一批「必须相等」的数字
（表数、字段数、服务数、任务数、工期、枚举映射组…）。历史上它们靠人工核对，
v1.2/v1.3 两次跨文档修订都留下了未同步的残留。本脚本把这些约定变成退出码。

--check 不修改任何文件；--gen 只往 stdout 打印，不写盘（避免静默改写文档）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
SQL = DOCS / "sql"
DEPLOY = ROOT / "deploy"

SCHEMA = SQL / "schema.sql"
MOCK_SCHEMA = SQL / "mock_schema.sql"
DICT = DOCS / "DATA-DICTIONARY.md"
TASKS = DOCS / "TASKS.md"
VERSIONS = DOCS / "VERSIONS.md"
COMPOSE = DEPLOY / "docker-compose.yml"
README = DEPLOY / "README.md"

# 与 VERSIONS.md §二 的期望值一一对应；两边不一致即 FAIL（双向防漂移）
EXPECTED = {
    "tables_main": 38,
    "columns_main": 419,
    "tables_mock": 7,
    "enum_groups": 11,
    "invariants": 12,
    "services": 9,
    "tasks": 98,
    "pitfalls": 11,
}

COL_RE = re.compile(r"^\s+`(\w+)`\s+(.*?),?\s*$")
TABLE_RE = re.compile(r"^CREATE TABLE IF NOT EXISTS `(\w+)` \($")
TCOMMENT_RE = re.compile(r"^\)\s*ENGINE=\w+(?:\s+[^;]*?)?COMMENT='(.*?)';", re.S)
DD_FIELD_RE = re.compile(r"^\|\s*`(\w+)`\s*\|")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def norm_md(s: str) -> str:
    """忽略 markdown 强调差异（`code` / *b*），只比语义。"""
    return re.sub(r"[`*]", "", s).strip()


# ---------------------------------------------------------------- DDL 解析

def parse_ddl(text: str) -> dict[str, dict]:
    """返回 {表名: {"comment":…, "cols":[{name,type,null,default,comment}], "idx":[…]}}"""
    tables: dict[str, dict] = {}
    cur: dict | None = None
    for line in text.splitlines():
        m = TABLE_RE.match(line)
        if m:
            cur = {"comment": "", "cols": [], "idx": []}
            tables[m.group(1)] = cur
            continue
        if cur is None:
            continue
        if line.startswith(")"):
            mc = re.search(r"COMMENT='(.*?)'\s*;?\s*$", line)
            if mc:
                cur["comment"] = mc.group(1)
            cur = None
            continue
        stripped = line.strip()
        # 索引 / 约束行（以关键字或 CONSTRAINT 开头，列名不会出现在行首反引号）
        if re.match(r"^(PRIMARY KEY|UNIQUE KEY|KEY|FULLTEXT|CONSTRAINT|CHECK)\b", stripped):
            if not stripped.startswith("CONSTRAINT") and not stripped.startswith("CHECK"):
                mi = re.match(r"^(PRIMARY KEY|UNIQUE KEY|KEY|FULLTEXT)\s*(?:`(\w+)`)?", stripped)
                if mi:
                    kind = {"PRIMARY KEY": "PRIMARY", "UNIQUE KEY": "UNIQUE",
                            "KEY": "INDEX", "FULLTEXT": "FULLTEXT"}[mi.group(1)]
                    cur["idx"].append((kind, mi.group(2) or ""))
            continue
        m = COL_RE.match(line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).strip()
        cur["cols"].append(parse_column(name, rest))
    return tables


def parse_column(name: str, rest: str) -> dict:
    comment = ""
    mc = re.search(r"COMMENT\s+'((?:[^']|'')*)'\s*$", rest)
    if mc:
        comment = mc.group(1).replace("''", "'")
        rest = rest[: mc.start()].strip()
    not_null = bool(re.search(r"\bNOT NULL\b", rest))
    default = "—"
    md = re.search(r"\bDEFAULT\s+(CURRENT_TIMESTAMP(?:\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP)?|NULL|'[^']*'|[\w.+-]+)",
                   rest, re.I)
    if md:
        default = canon_default(md.group(1))
    elif "ON UPDATE CURRENT_TIMESTAMP" in rest.upper():
        default = "CURRENT_TIMESTAMP"
    # 类型 = 去掉 NOT NULL / DEFAULT / AUTO_INCREMENT 后剩余部分
    # 先摘掉 DEFAULT 子句（含带引号/NULL 的形式），否则类型串会残留 "DEFAULT"
    typ = re.sub(r"DEFAULT\s+(?:'[^']*'|\S+)", "", rest, flags=re.I)
    typ = re.sub(r"\b(NOT NULL|NULL|AUTO_INCREMENT|ON UPDATE CURRENT_TIMESTAMP)\b", "", typ, flags=re.I)
    typ = re.sub(r"\s+", " ", typ).strip().upper()
    return {"name": name, "type": typ, "null": "NOT NULL" if not_null else "",
            "default": default, "comment": comment or "—"}


def canon_default(raw: str) -> str:
    u = raw.upper().strip()
    if u.startswith("CURRENT_TIMESTAMP"):
        # 既有字典约定：ON UPDATE CURRENT_TIMESTAMP 不在「默认」列体现
        return "CURRENT_TIMESTAMP"
    if u == "NULL":
        return "NULL"
    return raw.strip()  # 保留引号：既有字典对字符串默认值一律原样带引号（如 'md'、''）


# ------------------------------------------------------- DATA-DICTIONARY 解析

def parse_dict(text: str) -> dict:
    lines = text.splitlines()
    清单: dict[str, tuple[str, int]] = {}
    明细: dict[str, list[dict]] = {}
    cur: str | None = None
    in_list = False
    for ln in lines:
        if ln.startswith("## 二、"):
            in_list = True
            continue
        if ln.startswith("## 三、"):
            in_list = False
        if in_list and ln.startswith("|") and not ln.startswith("|--"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) >= 4 and cells[1].startswith("`"):
                name = cells[1].strip("`")
                try:
                    cnt = int(cells[3])
                except ValueError:
                    continue
                清单[name] = (cells[2], cnt)
        if ln.startswith("#### `"):
            m = re.match(r"^#### `(\w+)`\s*[—-]\s*(.*)$", ln)
            if m:
                cur = m.group(1)
                明细.setdefault(cur, [])
                明细[cur + "@title"] = m.group(2).strip()  # type: ignore[assignment]
            continue
        if cur and DD_FIELD_RE.match(ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) == 5:
                明细[cur].append({"name": cells[0].strip("`"), "type": cells[1],
                                  "null": cells[2], "default": cells[3], "comment": cells[4]})
    return {"清单": 清单, "明细": 明细, "text": text}


# ------------------------------------------------------------- 各项门禁

def check_ddl_vs_dict(r:Reporter)-> None:
    ddl = parse_ddl(read(SCHEMA))
    dd = parse_dict(read(DICT))
    清单, 明细 = dd["清单"], dd["明细"]

    r.section("表集合三方一致（DDL / 表清单 / 字段明细）")
    d_names, l_names, x_names = set(ddl), set(清单), {k for k in 明细 if not k.endswith("@title")}
    r.eq("DDL 表数 == VERSIONS 期望", len(d_names), EXPECTED["tables_main"])
    r.eq("表清单条数 == DDL 表数", len(l_names), len(d_names))
    r.eq("字段明细节数 == DDL 表数", len(x_names), len(d_names))
    r.eq("表清单 - DDL 差集为空", len(l_names - d_names), 0)
    r.eq("DDL - 表清单 差集为空", len(d_names - l_names), 0)
    r.eq("明细 - DDL 差集为空", len(x_names - d_names), 0)
    r.eq("DDL - 明细 差集为空", len(d_names - x_names), 0)

    r.section("逐表字段：名称序列 / 类型 / 可空 / 默认 / 说明")
    col_total = 0
    for t, spec in ddl.items():
        col_total += len(spec["cols"])
        got = spec["cols"]
        want = 明细.get(t, [])
        if [c["name"] for c in got] != [c["name"] for c in want]:
            r.fail(f"{t}: 字段名/顺序不一致 DDL={[c['name'] for c in got]} DD={[c['name'] for c in want]}")
            continue
        for g, w in zip(got, want):
            for key in ("type", "null", "default"):
                if g[key] != w[key]:
                    r.fail(f"{t}.{g['name']} [{key}] DDL={g[key]!r} DD={w[key]!r}")
            # 说明列允许 markdown 强调差异（`x` / *x*），语义等价即可
            if norm_md(g["comment"]) != norm_md(w["comment"]):
                r.fail(f"{t}.{g['name']} [comment] DDL={g['comment']!r} DD={w['comment']!r}")
        title_want = spec["comment"]
        title_got = 明细.get(t + "@title", "")
        if title_got and title_want and title_want not in title_got and title_got not in title_want:
            r.fail(f"{t}: 表标题 DD={title_got!r} 与 DDL COMMENT={title_want!r} 不符")

    r.section("字段总数")
    r.eq("DDL 字段总数 == VERSIONS 期望", col_total, EXPECTED["columns_main"])
    hh = re.search(r"自动生成（(\d+) 表 / (\d+) 字段）", dd["text"])
    if not hh:
        r.fail("DATA-DICTIONARY 表头未找到「N 表 / M 字段」声明")
    else:
        r.eq("表头声明表数 == 实际", int(hh.group(1)), len(ddl))
        r.eq("表头声明字段数 == 实际", int(hh.group(2)), col_total)

    r.section("Mock 库表数")
    r.eq("mock_schema CREATE TABLE", len(parse_ddl(read(MOCK_SCHEMA))), EXPECTED["tables_mock"])


def check_dict_sections(r: Reporter) -> None:
    text = read(DICT)
    lines = text.splitlines()
    groups = 0
    inv = 0
    sec = None
    for ln in lines:
        if ln.startswith("## "):
            sec = ln[:6]
        if sec and sec.startswith("## 一") and ln.startswith("### "):
            groups += 1
        if sec and sec.startswith("## 四") and re.match(r"^\|\s*\d+\s*\|", ln):
            inv += 1
    r.section("数据字典章节计数")
    r.eq("状态枚举映射组数", groups, EXPECTED["enum_groups"])
    r.eq("不变量条数", inv, EXPECTED["invariants"])


def check_cross_doc(r: Reporter) -> None:
    tasks = read(TASKS)
    compose = read(COMPOSE)
    readme = read(README)
    r.section("跨文档常量")
    svc = set()
    inside = False
    for ln in compose.splitlines():
        if ln.startswith("services:"):
            inside = True
            continue
        if inside:
            if ln and not ln.startswith(" ") and not ln.startswith("\t"):
                break
            m = re.match(r"^  (\w[\w-]*):\s*$", ln)
            if m:
                svc.add(m.group(1))
    r.eq("compose 服务数", len(svc), EXPECTED["services"])
    n_tasks = len(re.findall(r"^\| (?:BE|FE|AI|MOCK|DEP|DOC)-\d+ \|", tasks, re.M))
    r.eq("TASKS 任务条目数", n_tasks, EXPECTED["tasks"])
    r.eq("README 踩坑条数", len(re.findall(r"^\*\*\d+\.", readme, re.M)), EXPECTED["pitfalls"])
    p = read(DOCS / "PRD.md")
    # 只抓「把 16 周当作结论」的写法；`16 周 → 17 周`（沿革）与 `1 周 + 16 周 = 17 周`（算式）不算残留
    bad16 = []
    for src, txt in (("TASKS", tasks), ("PRD", p)):
        for ln in txt.splitlines():
            for m in re.finditer(r"16\s*周", ln):
                if ln[m.end():m.end() + 3].lstrip().startswith("→"):
                    continue
                if "+" in ln[max(0, m.start() - 3):m.start()]:
                    continue
                bad16.append(f"{src}: {ln.strip()[:60]}")
    r.eq("把 16 周当工期结论的残留", len(bad16), 0)
    for b in bad16:
        print("     ↳", b)
    ports = set(re.findall(r"EXPOSE (\d+)",
                           read(DEPLOY / "app" / "backend.Dockerfile")
                           + read(DEPLOY / "app" / "ai.Dockerfile")
                           + read(DEPLOY / "app" / "mock.Dockerfile")))
    r.eq("三服务 EXPOSE 端口集合", ports, {"8080", "8000", "8081"})
    prd_gb = set(re.findall(r"全量档\s*≥\s*(\d+)GB", p))
    deploys = set(re.findall(r"全量档.*?≥\s*(\d+)GB", readme, re.S) +
                  re.findall(r"全量档 all\s+≥\s*(\d+)GB", compose))
    r.eq("全量档内存下限 PRD == deploy", prd_gb, deploys)
    for sec in ("nacos", "nacos-data"):
        r.eq(f"compose 无 {sec}", compose.count(sec), 0)


def check_versions_matrix(r: Reporter) -> None:
    v = read(VERSIONS)
    r.section("VERSIONS.md 与脚本期望值互认（双向防漂移）")
    mapping = {"tables_main": "主库表数", "tables_mock": "Mock 库表数",
               "enum_groups": "状态枚举映射组", "invariants": "不变量条数",
               "services": "中间件服务数", "tasks": "任务总数",
               "pitfalls": "部署踩坑条数", "columns_main": "主库字段数"}
    for key, label in mapping.items():
        m = re.search(r"\|\s*[^|]*\|\s*" + re.escape(label) + r"\s*\|\s*\**(\d+)\**", v)
        if not m:
            r.fail(f"VERSIONS.md 未找到常量「{label}」")
            continue
        r.eq(f"VERSIONS「{label}」== 脚本期望", int(m.group(1)), EXPECTED[key])


JAVA_RE = re.compile(r"\bjava\b|spring|mybatis|jvm|maven|mvnw|pom\.xml|actuator|nacos|flyway", re.I)

# 允许出现的"已注记"信号：**必须是语义词或版本号，不得是纯标点**。
#
# 历史教训：本元组曾含 `"→"`，而判断是"整行 in"，于是**任何带箭头的行**
# 都整行豁免（如"… 的配置由 `xxx` → `yyy` 提供"）——去 Java 残留检查
# 退化成"自愿声明"。凡是想豁免，必须写出"替代/移除/去 xxx"这类词，
# 或带上版本号（v1.1/v1.2/v1.3）说明这是沿革对照。
ALLOW = ("v1.1", "v1.2", "v1.3", "替代", "移除", "已删除", "归档", "去 java", "历史记录",
         "本表保留", "沿革", "alembic", "flyway→", "flyway →", "jvm 指标",
         "actuator/health` →", "nacos_auth_token",
         "application/javascript", "text/javascript", "无 nacos", "见附录", "该组件",
         "改为", "去 nacos", "已随", "不再",
         # 检查自身的名字（"Java 残留"这是断言名，不是技术栈引用）
         "java 残留", "旧技术栈")

# 这些章节按设计就是历史/归档/对照区，整段豁免
ARCHIVE_HEAD = ("附录", "历史实测", "归档", "修订", "沿革", "与 v1.1 的差异", "差异")


def _env_keys(path: Path) -> list[str]:
    """取 .env 文件里定义的键（跳过空行与注释行；保留重复项供上层检测）。"""
    return [ln.split("=", 1)[0].strip() for ln in read(path).splitlines()
            if ln.strip() and not ln.strip().startswith("#") and "=" in ln]


def check_env_hygiene(r: Reporter) -> None:
    r.section("环境变量卫生")
    for name in (".env", ".env.example"):
        p = DEPLOY / name
        if not p.exists():
            r.fail(f"缺少 deploy/{name}")
            continue
        keys = _env_keys(p)
        dups = sorted({k for k in keys if keys.count(k) > 1})
        # 同名键在 .env 里后者静默覆盖前者：改上面「端口」段的值会被下面应用段悄悄盖掉
        r.eq(f"{name} 无重复定义的键", dups, [])

    # ---- 应用侧权威命名（仓库根 .env.example），§9.6 ----
    # 这份文件长期不在任何门禁覆盖内，于是"根改名、编排层没跟上"这类漂移
    # 不会被任何检查抓到 —— 直到 production 因 S1-c 缺必需 Key 直接启动失败。
    # 断言方向：根的键必须被 deploy 全部包含（deploy 允许有编排专属的额外键）。
    root_example = ROOT / ".env.example"
    if not root_example.exists():
        r.fail("缺少仓库根 .env.example（应用侧变量的权威命名）")
    else:
        root_keys = _env_keys(root_example)
        root_dups = sorted({k for k in root_keys if root_keys.count(k) > 1})
        r.eq("根 .env.example 无重复定义的键", root_dups, [])

        deploy_defined = set(_env_keys(DEPLOY / ".env.example"))
        undeclared = sorted(set(root_keys) - deploy_defined)
        r.eq("根 .env.example 的键在 deploy/.env.example 中均有定义", undeclared, [])

    compose = read(COMPOSE)
    refs = set(re.findall(r"\$\{([A-Z0-9_]+)(?::-[^}]*)?\}", compose))
    defined = set(_env_keys(DEPLOY / ".env.example"))
    missing = sorted(refs - defined)
    r.eq("compose 引用的变量在 .env.example 中均有定义", missing, [])


def check_java_residue(r: Reporter) -> None:
    r.section("去 Java 残留（允许带版本注记的历史/对照说明）")
    bad = []
    files = [p for p in list(DOCS.rglob("*.md")) + list(DOCS.rglob("*.sql"))
             if p.is_file() and "tools" not in p.parts]
    files += [p for p in DEPLOY.rglob("*") if p.is_file()]
    for p in files:
        try:
            txt = read(p)
        except Exception:
            continue
        archived = False
        for i, ln in enumerate(txt.splitlines(), 1):
            if p.suffix == ".md" and ln.startswith("## "):  # 归档状态只随 H2 切换，子章节继承
                archived = any(k in ln for k in ARCHIVE_HEAD)
            if not JAVA_RE.search(ln):
                continue
            if archived:
                continue
            low = ln.lower()
            if not any(a in low for a in ALLOW):
                bad.append(f"{p.relative_to(ROOT)}:{i}: {ln.strip()[:90]}")
    if bad:
        for b in bad[:20]:
            r.fail("活跃 Java 引用 -> " + b)
    else:
        r.ok("无未加注记的 Java/Spring/MyBatis/Nacos/JVM/Flyway/actuator 引用")


class Reporter:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.failures: list[str] = []
        self.passed = 0
        self._sec = ""

    def section(self, name: str) -> None:
        self._sec = name
        if self.verbose:
            print(f"\n\033[1m{name}\033[0m")

    def ok(self, msg: str) -> None:
        self.passed += 1
        if self.verbose:
            print(f"  \033[32m✓\033[0m {msg}")

    def fail(self, msg: str) -> None:
        self.failures.append(f"[{self._sec}] {msg}")
        print(f"  \033[31m✗\033[0m {msg}")

    def eq(self, what: str, got, want) -> None:
        if got == want:
            self.ok(f"{what} = {got}")
        else:
            self.fail(f"{what}: 实际 {got!r} != 期望 {want!r}")


def gen(table: str) -> int:
    ddl = parse_ddl(read(SCHEMA))
    if table not in ddl:
        print(f"未知表名 {table}；可选: {', '.join(sorted(ddl))}", file=sys.stderr)
        return 2
    spec = ddl[table]
    print(f"#### `{table}` — {spec['comment']}\n")
    print("| 字段 | 类型 | 空 | 默认 | 说明 |")
    print("|------|------|----|------|------|")
    for c in spec["cols"]:
        print(f"| `{c['name']}` | {c['type']} | {c['null']} | {c['default']} | {c['comment']} |")
    idx = "；".join(f"{k}{' ' + n if n else ''}" for k, n in spec["idx"])
    print(f"\n**索引**：{idx}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="AIDS 文档一致性门禁")
    ap.add_argument("--check", action="store_true", help="运行全部一致性检查（默认）")
    ap.add_argument("--gen", metavar="TABLE", help="从 DDL 生成该表的数据字典明细到 stdout")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每一项通过的检查")
    a = ap.parse_args()
    if a.gen:
        return gen(a.gen)
    r = Reporter(a.verbose)
    check_ddl_vs_dict(r)
    check_dict_sections(r)
    check_cross_doc(r)
    check_env_hygiene(r)
    check_versions_matrix(r)
    check_java_residue(r)
    print(f"\n{'=' * 62}")
    if r.failures:
        print(f"\033[31mFAIL\033[0m  {len(r.failures)} 项不一致 / {r.passed + len(r.failures)} 项检查")
        for f in r.failures:
            print("  - " + f)
        return 1
    print(f"\033[32mPASS\033[0m  {r.passed} 项检查全部一致（表/字段/枚举/服务数/任务数/工期/内存口径/Java 残留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
