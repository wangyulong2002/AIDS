"""Mock 库会话工厂（`aids_mock`）—— 与主业务库严格隔离。

为什么独立于 `app/orm/session.py`：
    Mock 模拟第三方渠道，物理隔离库（mock_schema.sql 头部）；连接配置也独立
    （`MOCK_DATABASE_URL`），Mock 库不可用**不能**影响主业务库的连接池。

行为与主业务同款：进程级单例、`pool_pre_ping`、`expire_on_commit=False`。
未配置 `MOCK_DATABASE_URL` 时抛 `MockDatabaseNotConfiguredError`（惰性 ——
import 本模块不连库，契约测试得以在无 DB 环境收集）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MOCK_DB_ENV_KEY = "MOCK_DATABASE_URL"


class MockDatabaseNotConfiguredError(RuntimeError):
    """未配置 MOCK_DATABASE_URL。"""


def mock_database_url() -> str:
    url = os.getenv(MOCK_DB_ENV_KEY, "").strip()
    if not url:
        raise MockDatabaseNotConfiguredError(
            f"未配置 {MOCK_DB_ENV_KEY}（模板见仓库根 .env.example）；本函数在创建 engine 时才会被调用"
        )
    return url


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine  # noqa: PLW0603 - 进程级单例（与 app/orm/session.py 同理由）
    if _engine is None:
        _engine = create_async_engine(
            mock_database_url(),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=3600,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务作用域：正常提交 / 异常回滚 / 最终关闭（语义同主业务）。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入：每请求一个会话。"""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
