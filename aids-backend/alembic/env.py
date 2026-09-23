"""Alembic 运行环境。

只做三件事：
    1. 把仓库根（`app/` 所在处）与服务目录加入 `sys.path`；
    2. 导入 `app.models`，让**全部 38 张表注册进 `Base.metadata`** ——
       少导入一个模块，autogenerate 会认为那些表"被删了"并生成 `DROP TABLE`，
       这是迁移工具最危险的一类失误；
    3. 从**环境变量**取 DATABASE_URL（与应用运行时同源），并换成同步驱动。

为什么必须换同步驱动：
    Alembic 默认用同步引擎，而应用侧是 `mysql+asyncmy://`；
    直接喂给 Alembic 会报 `Can't load plugin: sqlalchemy.dialects:mysql.asyncmy`。
    `tests/conftest.py::_to_sync_url` 做的是同一件事（那边供反射层测试用）——
    两处逻辑相近但用途不同，暂不合并，避免测试与迁移工具互相依赖。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND_DIR.parent
for _path in (str(_ROOT), str(_BACKEND_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import app.models  # noqa: E402,F401  —— 副作用导入：注册全部表
from app.orm.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """环境变量优先（与应用同源），回退到 ini 里的占位；并转为同步驱动。"""
    url = os.getenv("DATABASE_URL", "").strip() or config.get_main_option("sqlalchemy.url", "")
    return (
        url.replace("mysql+asyncmy://", "mysql+pymysql://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("+asyncpg://", "+psycopg2://")
    )


def _common_options() -> dict[str, object]:
    return {
        "target_metadata": target_metadata,
        # 检测列类型变化。不开的话，"VARCHAR(32) → VARCHAR(64)" 这类变更
        # autogenerate 会**静默忽略**，迁移里就少了一句 ALTER。
        "compare_type": True,
        # 刻意**不**开启 server_default 比对：MySQL 会把 `DEFAULT 0` 反射成
        # `'0'`、把 CURRENT_TIMESTAMP 反射成 `CURRENT_TIMESTAMP()`，
        # 与模型里写的 `text("0")` 形式不同，开启后会产出大量"假 diff"，
        # 反而让真正需要人工确认的变更被淹没。
        "compare_server_default": False,
    }


def run_migrations_offline() -> None:
    """离线模式：只把 SQL 打印出来，不连库（用于生成交付给 DBA 的脚本）。"""
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_options(),  # type: ignore[arg-type]
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连真实库执行迁移。"""
    section = dict(config.get_section(config.config_ini_section) or {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, **_common_options())  # type: ignore[arg-type]
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
