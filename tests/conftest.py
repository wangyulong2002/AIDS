"""pytest 全局 fixture。

关键 fixture：
    db_conn —— 真实 MySQL 连接（marker=requires_mysql 的测试用）
               未配置 DATABASE_URL 时自动 skip，保证本地无库也能跑契约测试。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest


def _database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


@pytest.fixture(scope="session")
def db_conn() -> Iterator[Any]:
    """提供真实 MySQL 连接（DBAPI 游标风格）。

    这是"反射层"契约测试的前提——静态解析只能查文档互斥，
    只有连真库才能查"实际建出来的表对不对"。

    未配置 DATABASE_URL 时 skip（而非 fail），
    这样本地开发只跑静态层也能得到有意义的结果。
    """
    url = _database_url()
    if not url:
        pytest.skip("未配置 DATABASE_URL，跳过需要真实 MySQL 的反射测试")

    sync_url = _to_sync_url(url)
    try:
        import sqlalchemy  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("未安装 sqlalchemy，跳过反射测试")

    engine = sqlalchemy.create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _to_sync_url(url: str) -> str:
    """把 async 驱动 URL 转成同步驱动 URL（反射测试不需要 async）。"""
    return (
        url.replace("mysql+asyncmy://", "mysql+pymysql://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("+asyncpg://", "+psycopg2://")
    )
