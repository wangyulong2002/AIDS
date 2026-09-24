"""Refresh Token 的一次性存储（吊销点）。

为什么 Refresh 必须落存储，而不能只靠签名：
    Refresh Token 有效期 7 天。若它只是一张签过名的票据，那么
    「用户点了退出登录」「管理员封禁账号」「token 被盗用」这三件事都**无法生效**——
    攻击者手里的票据在 7 天内一直有效，签名校验只会告诉他"这是真的"。
    故每个 Refresh 都带一个 `jti`，签发时落存储，**用过即焚**：

        refresh 流程：consume(jti) → 命中则删除并返回 userId → 签发新的一对
        logout 流程：revoke(jti)  → 删除

    `consume` 而不是 `get`：一次性语义必须由**一次原子操作**保证。若先查后删，
    两个并发请求都能查到，等于允许 Refresh 重放。

两种实现：
    - `RedisRefreshStore`：生产用（多实例共享；Redis 的 GETDEL 天然原子）
    - `InMemoryRefreshStore`：测试与无 Redis 环境（单进程语义等价）

    没有 Redis 时的选择是**显式降级**而不是静默：`build_refresh_store("")`
    返回内存实现并在日志里说明"重启即失效、多实例不共享"。
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

logger = logging.getLogger(__name__)

# Redis key 前缀：本项目所有 key 都带 aids: 前缀，便于与同实例上的其它应用隔离
KEY_PREFIX = "aids:refresh:"


class RefreshTokenStore(Protocol):
    """Refresh Token 存储接口（`Protocol` 而非基类：实现只需形状一致）。"""

    async def save(self, jti: str, user_id: int, ttl_seconds: int) -> None:
        """登记一个 Refresh（签发时调用）。"""
        ...

    async def consume(self, jti: str) -> int | None:
        """取用并销毁。命中返回 user_id；不存在（已用过/已吊销/过期）返回 None。"""
        ...

    async def revoke(self, jti: str) -> None:
        """吊销（退出登录）。幂等：不存在也不报错。"""
        ...

    async def revoke_all_of(self, user_id: int) -> int:
        """吊销该用户的全部 Refresh（改密/封禁用），返回吊销数量。"""
        ...


class InMemoryRefreshStore:
    """进程内实现。语义与 Redis 版一致，但**重启即失效、多实例不共享**。"""

    def __init__(self) -> None:
        self._items: dict[str, tuple[int, float]] = {}

    async def save(self, jti: str, user_id: int, ttl_seconds: int) -> None:
        self._items[jti] = (user_id, time.time() + ttl_seconds)

    async def consume(self, jti: str) -> int | None:
        item = self._items.pop(jti, None)
        if item is None:
            return None
        user_id, expires_at = item
        if expires_at <= time.time():
            return None
        return user_id

    async def revoke(self, jti: str) -> None:
        self._items.pop(jti, None)

    async def revoke_all_of(self, user_id: int) -> int:
        doomed = [jti for jti, (uid, _) in self._items.items() if uid == user_id]
        for jti in doomed:
            self._items.pop(jti, None)
        return len(doomed)


class RedisRefreshStore:
    """Redis 实现（redis.asyncio）。

    `GETDEL` 需要 Redis 6.2+（compose 用的是 7.4，满足）。用它而不是
    `GET` + `DEL` 两条命令，是因为"查"与"删"之间必须无窗口——否则并发重放
    会在窗口里双双成功，一次性语义失效。
    """

    def __init__(self, client: object) -> None:
        self._client = client

    @staticmethod
    def _key(jti: str) -> str:
        return f"{KEY_PREFIX}{jti}"

    async def save(self, jti: str, user_id: int, ttl_seconds: int) -> None:
        await self._client.set(self._key(jti), str(user_id), ex=ttl_seconds)  # type: ignore[attr-defined]

    async def consume(self, jti: str) -> int | None:
        raw = await self._client.getdel(self._key(jti))  # type: ignore[attr-defined]
        return int(raw) if raw is not None else None

    async def revoke(self, jti: str) -> None:
        await self._client.delete(self._key(jti))  # type: ignore[attr-defined]

    async def revoke_all_of(self, user_id: int) -> int:
        """按值扫描删除。T1 数据量小，`SCAN` 足够；量级上来后应改为
        `SET` 记录用户维度的 jti 集合（见 TASKS 的运维收尾项）。"""
        removed = 0
        pattern = f"{KEY_PREFIX}*"
        async for key in self._client.scan_iter(match=pattern):  # type: ignore[attr-defined]
            raw = await self._client.get(key)  # type: ignore[attr-defined]
            if raw is not None and int(raw) == user_id:
                removed += await self._client.delete(key)  # type: ignore[attr-defined]
        return removed


def build_refresh_store(redis_url: str) -> RefreshTokenStore:
    """按配置构造存储。空 URL → 内存实现（显式降级，日志说明后果）。"""
    if not redis_url:
        logger.warning("未配置 REDIS_URL，Refresh 吊销退化为进程内存储：重启即失效、多实例不共享")
        return InMemoryRefreshStore()

    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    return RedisRefreshStore(client)
