"""async 会话工厂 —— 全进程唯一入口。

=====================================================================
为什么必须集中一处：
    `create_async_engine()` 每调用一次就新建一个**独立的连接池**。
    若各模块自己 `engine = create_async_engine(...)`，连接数会随模块数
    线性膨胀，在 MySQL `max_connections=500`（见 deploy/docker-compose.yml）
    下很快撞上 "Too many connections"，且症状是"某几个接口随机 500"。

    所以 engine 与 sessionmaker 都是**进程级单例**，只在这里创建。
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

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class MissingDatabaseUrlError(RuntimeError):
    """未配置 DATABASE_URL。"""


def database_url() -> str:
    """读 DATABASE_URL。

    只在**建 engine 时**调用一次：engine 本身是惰性的，不执行语句就不会连接，
    因此本地无库时 import 本模块不会失败（契约测试得以在无 DB 环境下收集）。
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise MissingDatabaseUrlError(
            "未配置 DATABASE_URL（模板见仓库根 .env.example）；本函数在创建 engine 时才会被调用"
        )
    return url


def get_engine() -> AsyncEngine:
    """取进程级 engine（双检锁外的简单懒加载；调用发生在启动期，无需加锁）。"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url(),
            # 连接前 ping。MySQL 侧空闲超过 wait_timeout 会**静默**断开连接，
            # 没有 pre_ping 时表现为"偶发一个请求 500"，且只在低峰期出现 ——
            # 正是最难排查的那类故障。
            pool_pre_ping=True,
            # 池容量与 4 个 uvicorn worker（见 backend.Dockerfile）匹配：
            # 4 × (20 + 10) = 120 条上限，相对 max_connections=500 留足余量。
            pool_size=20,
            max_overflow=10,
            # 必须小于 MySQL 的 wait_timeout（默认 28800s），提前回收更稳妥。
            pool_recycle=3600,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            # ★ 必须 False。async 下若为 True，commit 后再访问任何已加载属性
            #   都会触发一次隐式 IO（重新 SELECT），而那时已不在 greenlet 上下文，
            #   直接抛 MissingGreenlet —— 报错点离真正原因极远，非常难查。
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务作用域：正常提交 / 异常回滚 / 最终关闭。

    用法：
        async with session_scope() as s:
            s.add(obj)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用：每请求一个会话（语义同 `session_scope`）。"""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """释放连接池（应用 shutdown 时调用）。

    不 dispose 会让容器滚动更新时旧进程的连接挂在 MySQL 上直到超时，
    表现为"新版本上线后数据库连接数翻倍"。
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
