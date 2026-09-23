"""Redis 客户端单例与分布式锁（BE-05）。

客户端为什么单例：
    同 `app/orm/session.py` 的理由 —— 每处自建连接会线性膨胀连接数。
    本模块是**通用** Redis 入口；`app/core/refresh_store.py`（更早出现）持有的
    吊销存储将在后续任务并入这里，避免两个客户端两套池。

分布式锁为什么必须带 token + Lua：
    「SET NX」加锁后直接 DEL 释放是经典事故：业务超时后锁已过期、被第二个人持有，
    第一个人回来一句 DEL 把**别人的锁**删了。所以：
        - 加锁带随机 token（`SET key token NX PX ttl`）；
        - 释放/续期用 Lua 做「token 相同才删/续」的原子判断 —— 检查与删除若分两条
          命令，中间同样存在过期换主的窗口。

使用约定（KEY 前缀 `aids:lock:`）：
        lock = RedisLock(get_redis(), "order:pay:8001")
        if await lock.try_acquire():          # 非阻塞
            try: ... finally: await lock.release()
        async with RedisLock(get_redis(), "kb:rebuild") as lk:   # 抢不到直接抛错
            ...

    TTL 必须给足（锁不是持久的）；需要"活着就续"的场景由调用方周期性 `renew()`。
"""

from __future__ import annotations

import logging
import uuid
from types import TracebackType
from typing import Any, Final

from app.core.config import get_redis_url

logger = logging.getLogger(__name__)

KEY_PREFIX: Final[str] = "aids:lock:"

# token 不匹配就不删/不续 —— 防止误删他人锁（见模块 docstring）
_RELEASE_LUA: Final[str] = (
    'if redis.call("get", KEYS[1]) == ARGV[1] then '
    'return redis.call("del", KEYS[1]) else return 0 end'
)
_RENEW_LUA: Final[str] = (
    'if redis.call("get", KEYS[1]) == ARGV[1] then '
    'return redis.call("pexpire", KEYS[1], ARGV[2]) else return 0 end'
)


class RedisNotConfiguredError(RuntimeError):
    """未配置 REDIS_URL —— 需要 Redis 的组件（分布式锁/配置热更新）无法工作。"""


class LockNotAcquiredError(RuntimeError):
    """分布式锁未获取到（context manager 语义下抛出，避免静默并发）。"""


_client: Any = None


def get_redis() -> Any:
    """进程级 Redis 客户端单例（`decode_responses=True`：命令进出都是 str）。"""
    global _client  # noqa: PLW0603 - 与 session.py 同款进程级单例
    if _client is None:
        url = get_redis_url()
        if not url:
            raise RedisNotConfiguredError(
                "未配置 REDIS_URL；需要 Redis 的组件（分布式锁 / 配置热更新）无法启动"
            )
        import redis.asyncio as aioredis

        _client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
        logger.info("Redis 客户端已初始化")
    return _client


def reset_redis() -> None:
    """重置单例（测试 / 配置变更后）。"""
    global _client  # noqa: PLW0603
    _client = None


class RedisLock:
    """基于 Redis 的分布式锁（非阻塞获取 + token 校验的释放/续期）。"""

    def __init__(self, client: Any, name: str, *, ttl_seconds: int = 30) -> None:
        if ttl_seconds <= 0:
            raise ValueError("分布式锁 TTL 必须 > 0（Redis 锁靠过期兜底，无 TTL 等于死锁）")
        self._redis = client
        self._ttl_ms = ttl_seconds * 1000
        self.name = f"{KEY_PREFIX}{name}"
        self.token = uuid.uuid4().hex
        self._held = False

    async def try_acquire(self) -> bool:
        """非阻塞尝试加锁。成功返回 True（并持有），失败返回 False。"""
        acquired = await self._redis.set(self.name, self.token, nx=True, px=self._ttl_ms)
        self._held = bool(acquired)
        return self._held

    async def release(self) -> bool:
        """释放（仅当仍是自己的锁）。返回是否真的由本实例释放。"""
        released = bool(await self._redis.eval(_RELEASE_LUA, 1, self.name, self.token))
        self._held = self._held and not released
        return released

    async def renew(self) -> bool:
        """续期（仅当仍是自己的锁）。业务长于 TTL 时由调用方周期调用。"""
        return bool(await self._redis.eval(_RENEW_LUA, 1, self.name, self.token, self._ttl_ms))

    async def __aenter__(self) -> RedisLock:
        if not await self.try_acquire():
            raise LockNotAcquiredError(f"分布式锁未获取到：{self.name}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._held:
            await self.release()
