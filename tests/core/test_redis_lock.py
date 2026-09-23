"""BE-05 · 分布式锁的单元测试（真实 Redis；不可达则整文件 skip）。

为什么连真库而不是替身：
    锁的语义（NX 互斥、token 校验释放、过期）都发生在 **Redis 服务端**，
    替身只能复述实现，测不出竞态。本机 compose 已跑 Redis（见 HANDOFF §1.1）；
    CI 无 Redis 服务 → 自动 skip（锁的调用方语义由 outbox/库存任务的真实链路覆盖）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.redis import KEY_PREFIX, LockNotAcquiredError, RedisLock

pytestmark = pytest.mark.unit

_REDIS_URL = "redis://:aids_redis_2026@127.0.0.1:6379/0"


def _redis_available() -> bool:
    try:
        import redis.asyncio as aioredis

        async def _probe() -> bool:
            client = aioredis.from_url(_REDIS_URL, encoding="utf-8", decode_responses=True)
            try:
                return bool(await asyncio.wait_for(client.ping(), timeout=2))
            finally:
                await client.aclose()

        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001 - 探测失败一律视为不可用
        return False


pytestmark = [  # noqa: RUF012 - 先声明 unit 标记，再按 Redis 可达性决定是否整文件 skip
    pytest.mark.unit,
    pytest.mark.skipif(not _redis_available(), reason="本机 Redis 不可达，跳过分布式锁测试"),
]


def _client():
    import redis.asyncio as aioredis

    return aioredis.from_url(_REDIS_URL, encoding="utf-8", decode_responses=True)


def _lock(name: str, **kwargs: int) -> RedisLock:
    return RedisLock(_client(), f"test:{name}:{uuid.uuid4().hex[:8]}", **kwargs)


def test_try_acquire_is_mutually_exclusive() -> None:
    async def _run() -> None:
        lock = _lock("mutex", ttl_seconds=10)
        assert await lock.try_acquire() is True
        second = _lock("mutex", ttl_seconds=10)
        second.name = lock.name  # 同一把锁：第二个持有者必须失败
        second.token = uuid.uuid4().hex
        assert await second.try_acquire() is False
        assert await lock.release() is True

    asyncio.run(_run())


def test_release_requires_matching_token() -> None:
    async def _run() -> None:
        lock = _lock("token", ttl_seconds=10)
        await lock.try_acquire()

        impostor = RedisLock(_client(), lock.name, ttl_seconds=10)
        impostor.token = "attacker-token"
        assert await impostor.release() is False, "token 不匹配不得释放他人的锁"

        assert await lock.release() is True  # 自己仍能正常释放

    asyncio.run(_run())


def test_renew_requires_matching_token() -> None:
    async def _run() -> None:
        lock = _lock("renew", ttl_seconds=10)
        await lock.try_acquire()
        assert await lock.renew() is True

        impostor = RedisLock(_client(), lock.name, ttl_seconds=10)
        impostor.token = "attacker-token"
        assert await impostor.renew() is False

        await lock.release()
        assert await lock.renew() is False  # 已释放，续期失败

    asyncio.run(_run())


def test_context_manager_raises_when_busy() -> None:
    async def _run() -> None:
        holder = _lock("busy", ttl_seconds=10)
        await holder.try_acquire()

        busy = _lock("busy", ttl_seconds=10)
        busy.name = holder.name  # 同名 → 必须拿不到
        with pytest.raises(LockNotAcquiredError):
            async with busy:
                pass  # pragma: no cover - 不应进入

    asyncio.run(_run())


def test_invalid_ttl_rejected() -> None:
    with pytest.raises(ValueError, match="TTL"):
        RedisLock(_client(), "no-ttl", ttl_seconds=0)


def test_key_prefix_is_namespaced() -> None:
    lock = _lock("prefix")
    assert lock.name.startswith(f"{KEY_PREFIX}test:")
