# Alembic 迁移版本目录

**当前为空** —— T0 / BE-02 只搭基建，尚未纳管已有 DDL。

## 为什么这里还没有迁移

`docs/sql/schema.sql` 目前仍是建表的**权威来源**（由 `deploy/mysql/` 自建镜像在首次
启动时按 `schema → mock_schema → seed` 顺序执行）。`docs/TASKS.md` 的 DEP-04 明确写着
「Alembic 纳管留待 T1」，因此这里刻意没有初始 revision。

## T1 把 38 张表纳管进来的正确做法

1. 在一个**空的**测试库上执行
   `alembic -c aids-backend/alembic.ini revision --autogenerate -m "init schema"`；
2. **人工审查**生成的迁移 —— autogenerate 对以下内容还原不完整，必须逐项核对：
   - `FULLTEXT ... WITH PARSER ngram`（BE-12 新增，autogenerate 不识别）
   - `CHECK` 约束
   - 列的 `COMMENT`（模型里有，但部分方言不生成）
   - 索引的 `COMMENT`
3. 与 `docs/sql/schema.sql` 逐表比对，确认二者等价（可用
   `tests/contract/test_orm_matches_schema.py` 的解析器做对照）；
4. 确认无误后再切换建表链路，并同步 `deploy/mysql/init/` 的说明。

## 写迁移的硬要求（S5）

每个 revision 必须实现 `downgrade()`。CI 会实跑
`upgrade → downgrade → upgrade`；空的 `pass` 会让回滚失效。
详见 `script.py.mako` 的文件头说明。
