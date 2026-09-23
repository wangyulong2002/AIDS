# Alembic 迁移基建（主库）

`docs/TASKS.md` BE-02 的交付之一。**本目录只搭基建，尚无迁移版本** ——
`docs/sql/schema.sql` 仍是建表的权威来源（DEP-04 原文：「Alembic 纳管留待 T1」）。

## 目录

| 文件 | 作用 |
|---|---|
| `../alembic.ini` | 配置（`script_location` 指向本目录） |
| `env.py` | 运行环境：注入 `app/` 路径、导入**全部模型**、从环境变量取 `DATABASE_URL` |
| `script.py.mako` | 生成 revision 的模板（**downgrade 未实现时生成 `raise` 而不是 `pass`**） |
| `versions/` | 迁移版本（当前为空，理由见其 README） |

## ⚠️ `alembic.ini` 必须是纯 ASCII

这是本项目实测踩过的坑，写在这里避免后人重犯：

Alembic 用 `configparser.read(path, encoding="locale")` 读 ini，而
`locale.getencoding()` 在中文 Windows 上是 **cp936(GBK)** 而不是 utf-8。
因此 ini 里任何一个 UTF-8 中文字符都会直接抛：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position ...
```

**`PYTHONUTF8=1` 救不了它** —— 那个开关影响的是 `getpreferredencoding()`，
而 Alembic 用的是 `getencoding()`（本机实测两者分别是 `utf-8` 与 `cp936`）。

所以：**ini 里不写中文**，中文说明一律放本文件与 `env.py` 的 docstring。
（CI 是 Linux/UTF-8 不会暴露这个问题，所以它只在本地开发时发作——
典型的"本地红、CI 绿"，最容易被误判成环境噪音。）

## 常用命令

```bash
# 校验配置可加载（不需要数据库）
alembic -c aids-backend/alembic.ini heads

# 打印将要执行的 SQL（offline 模式；也会加载 env.py，但不需要数据库）
alembic -c aids-backend/alembic.ini upgrade head --sql

# 生成迁移（需要能连库；DATABASE_URL 由 env.py 从环境变量读取）
alembic -c aids-backend/alembic.ini revision --autogenerate -m "add xxx"

alembic -c aids-backend/alembic.ini upgrade head
alembic -c aids-backend/alembic.ini downgrade -1
```

## S5：迁移必须可回退

CI 会实跑 `upgrade → downgrade → upgrade`（见 `docs/工程化门禁方案.md` §5）。
`script.py.mako` 刻意把未填写的 downgrade 生成为 `raise NotImplementedError(...)`
而不是 Alembic 默认的 `pass` —— 让"忘了写回滚"在**第一次执行时**就暴露，
而不是等到真要回滚的那天才发现。
