# Alembic 迁移版本目录

**状态：已纳管（2026-09-24）。** 存量 38 张表由 `*_init_schema.py` 一次性纳管，
DEP-04 里那句「Alembic 纳管留待 T1」已落地。

## 目录里有什么

| 文件 | 说明 |
|------|------|
| `20260924_2011_7cdfce83dff8_init_schema.py` | 基线迁移：38 张表 + 1 个 CHECK 约束。`down_revision = (base)` |

## 三条使用路径

```bash
# ① 存量库纳管（**只有已由 docs/sql 建好表的库**才这么做，一次性、是手工操作）
alembic -c aids-backend/alembic.ini stamp head

# ② 全新库：用 SQL 建表（仍是部署链路），然后打标
cd deploy && docker compose up -d --wait mysql
alembic -c aids-backend/alembic.ini stamp head

# ③ 此后新增表/字段：走迁移
alembic -c aids-backend/alembic.ini revision --autogenerate -m "add xxx"
alembic -c aids-backend/alembic.ini upgrade head
```

> `stamp` **只写 `alembic_version` 表**，不执行任何 DDL —— 所以对已经建好表的库安全。
> 不要对空库 `stamp` 后再 `upgrade`（会认为已经升到 head，什么也不做）。

## 生成一条新迁移后的必做动作（顺序不能反）

1. **人工审查**生成的迁移。autogenerate 还原不了这些，必须逐项核对：
   - **`CHECK` 约束** —— 本仓库唯一一个（`chk_stock_non_negative`）就是手写的；
   - `FULLTEXT ... WITH PARSER ngram`（BE-12 落地后会出现，autogenerate 不识别）；
   - **表注释**：'__table_args__' 里带 `{"comment": ...}` 时才会生成；
   - 索引 `COMMENT`（SQLAlchemy `Index` 不接受 `comment=`，只能留在模型的行尾注释里）。
2. 把 `downgrade()` 写实 —— 模板给的是 `raise NotImplementedError`，
   `tests/contract/test_alembic_migrations.py` 会因此判红（这是 S5 的底线）。
   CI 实跑 `upgrade → downgrade → upgrade`。
3. 跑 `python scripts/task_runner.py verify` 再提交。

## 已知残差（基线迁移与 `schema.sql` 的差异）

基线迁移**不是** `schema.sql` 的逐字节复刻。在 MySQL 8.4.11 上把两者建出的库
做过 `information_schema` 全量比对，结论写在迁移文件的 docstring 里，摘要：

- **完全一致**：索引 146/146、表元信息（引擎 / 排序规则 / 表注释）38/38、
  CHECK 约束 1/1（子句逐字一致，名字被 MySQL 规范化为 `ck_<表>_<约束名>`）。
- **仅剩 24 行非行为性差异**：① Mixin 的统一注释 vs DDL 的逐表注释措辞；
  ② `server_default=text("0.00")` 被记为表达式默认（`DEFAULT_GENERATED`）。
- **权威口径仍是 `docs/sql/schema.sql`**（见 `docs/VERSIONS.md` §四）。
  迁移只负责让**后续变更**可受版本控制，不改变「DDL 是唯一来源」这条约定。

## 为什么这里不参与 ruff

`pyproject.toml` 的 `[tool.ruff] exclude` 收录了本目录：文件由 Alembic 自己的
渲染器生成，缩进/引号风格与 ruff format 不同 —— 每次 `revision` 都要先格式化再提交，
纯属噪音（与 `app/models/` 被排除是同一个理由）。类型仍由 pyright 覆盖；
模板层的问题（例如生成物带行尾空格）在 `script.py.mako` 里根治。
