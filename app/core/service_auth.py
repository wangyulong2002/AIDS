"""服务间鉴权（BE-06）—— 内部接口的静态 Token + 请求签名 + 防重放 + 内网限制。

与用户 JWT（`app/core/security.py`）的关系：
    **两套正交的信任链，不得混用**。用户 JWT 证明"哪个用户在操作"；
    服务间鉴权证明"哪个服务在调用"。内部接口（API.md §四）的调用方是 AI 服务，
    用户身份由 AI 服务从其用户的 JWT 解出后**作为查询参数传入**（PRD §9.3）——
    所以内部接口的 userId 是业务入参而非凭据，信任边界是本模块。

四件套（缺一即 403/10003，API.md §四）：
    X-Internal-Token   静态 Token（环境变量下发，双方一致）
    X-Timestamp        Unix 秒；与服务器时间偏差 > 300s 拒绝（缩小重放窗口）
    X-Nonce            一次性随机串；TTL 窗口内重复出现即判定重放
    X-Sign             HMAC-SHA256(INTERNAL_SIGN_SECRET, "{timestamp}\n{nonce}")

为什么 Token 之外还要签名：
    静态 Token 是长期凭据，一旦被从日志/抓包中还原，防重放就全靠签名把
    **每次请求**绑定到"此刻 + 一次性 nonce"上 —— 泄漏的 Token 拼不出有效签名。

nonce 防重放的原子性：
    「查一下见没见过」+「记下来」若分两步，并发重放都能通过。故 `register()`
    必须**一次原子操作**完成登记与查重（Redis `SET NX EX`；进程内用 dict+TTL）。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Annotated, Any, Final, Protocol

from fastapi import Depends, Request

from app.core.config import (
    get_internal_service_token,
    get_internal_sign_secret,
    get_redis_url,
)
from app.core.exceptions import BusinessError

HEADER_TOKEN: Final[str] = "X-Internal-Token"
HEADER_TIMESTAMP: Final[str] = "X-Timestamp"
HEADER_NONCE: Final[str] = "X-Nonce"
HEADER_SIGN: Final[str] = "X-Sign"

SIGNATURE_TTL_SECONDS: Final[int] = 300

_FORBIDDEN = "服务间鉴权失败"


def compute_signature(secret: str, timestamp: str, nonce: str) -> str:
    """规范化串：`timestamp + "\\n" + nonce`（换行分隔防拼接歧义），HMAC-SHA256 hex。"""
    return hmac.new(
        secret.encode("utf-8"), f"{timestamp}\n{nonce}".encode(), hashlib.sha256
    ).hexdigest()


def _constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class NonceStore(Protocol):
    """nonce 登记器：`register` 一次原子操作完成「查重 + 登记」。"""

    async def register(self, nonce: str, ttl_seconds: int) -> bool:
        """返回 True = 首次出现；False = 窗口内已出现过（重放）。"""
        ...


class InMemoryNonceStore:
    """进程内实现（测试 / 无 Redis 降级）：多实例不共享，见 refresh_store 同款取舍。"""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    async def register(self, nonce: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        expires_at = self._seen.get(nonce)
        if expires_at is not None and expires_at > now:
            return False
        self._seen[nonce] = now + ttl_seconds
        return True


class RedisNonceStore:
    """Redis 实现：`SET NX EX` 天然原子。"""

    def __init__(self, client: Any) -> None:  # noqa: ANN401 - redis 客户端无稳定公共类型
        self._client = client

    async def register(self, nonce: str, ttl_seconds: int) -> bool:
        registered = await self._client.set(
            f"aids:internal:nonce:{nonce}", "1", nx=True, ex=ttl_seconds
        )
        return registered is True


def build_nonce_store(redis_url: str) -> NonceStore:
    """按配置构造 nonce 登记器（空 URL → 进程内实现，显式降级并告警）。"""
    if not redis_url:
        import logging

        logging.getLogger(__name__).warning(
            "未配置 REDIS_URL，内部接口防重放退化为进程内 nonce 表：多实例部署时可能放过跨实例重放"
        )
        return InMemoryNonceStore()

    import redis.asyncio as aioredis

    return RedisNonceStore(aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True))


@dataclass(frozen=True)
class InternalCredentials:
    """从请求头解出的服务间凭据四件套。"""

    token: str
    timestamp: str
    nonce: str
    signature: str


def extract_credentials(request: Request) -> InternalCredentials:
    """从请求头解出凭据；缺任一即 403（不区分缺哪个，不给探测者反馈）。"""
    credentials = InternalCredentials(
        token=request.headers.get(HEADER_TOKEN, ""),
        timestamp=request.headers.get(HEADER_TIMESTAMP, ""),
        nonce=request.headers.get(HEADER_NONCE, ""),
        signature=request.headers.get(HEADER_SIGN, ""),
    )
    if not all(
        (credentials.token, credentials.timestamp, credentials.nonce, credentials.signature)
    ):
        raise BusinessError.forbidden(_FORBIDDEN)
    return credentials


def verify_credentials(credentials: InternalCredentials, *, now: int | None = None) -> None:
    """校验静态 Token、时间窗与签名。任何失败都是 403/10003。"""
    expected_token = get_internal_service_token()
    expected_secret = get_internal_sign_secret()
    if not expected_token or not expected_secret:
        raise BusinessError.forbidden(_FORBIDDEN)

    if not _constant_time_equals(credentials.token, expected_token):
        raise BusinessError.forbidden(_FORBIDDEN)

    now = now if now is not None else int(time.time())
    try:
        timestamp = int(credentials.timestamp)
    except ValueError as exc:
        raise BusinessError.forbidden(_FORBIDDEN) from exc
    if abs(now - timestamp) > SIGNATURE_TTL_SECONDS:
        raise BusinessError.forbidden(_FORBIDDEN)

    expected_signature = compute_signature(
        expected_secret, credentials.timestamp, credentials.nonce
    )
    if not _constant_time_equals(credentials.signature, expected_signature):
        raise BusinessError.forbidden(_FORBIDDEN)


async def verify_replay_fresh(store: NonceStore, credentials: InternalCredentials) -> None:
    """nonce 一次性校验（签名有效**之后**调用，避免用垃圾 nonce 灌满存储）。"""
    fresh = await store.register(credentials.nonce, SIGNATURE_TTL_SECONDS * 2)
    if not fresh:
        raise BusinessError.forbidden(_FORBIDDEN)


def is_trusted_host(host: str | None) -> bool:
    """内网网段限制（PRD §5.3）：仅私有网段可达。

    主机名解析不出 IP（如测试客户端）视为不可信 —— 由测试显式覆盖该依赖。
    """
    import ipaddress

    if not host:
        return False
    allowed = (
        "127.0.0.1/32",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(cidr) for cidr in allowed)


# =====================================================================
# FastAPI 依赖组装（内部路由统一挂这两个依赖）
# =====================================================================

_nonce_store: NonceStore | None = None


def get_nonce_store() -> NonceStore:
    """进程级 nonce 登记器单例（Redis 优先，未配置显式降级）。"""
    global _nonce_store  # noqa: PLW0603 - 进程级单例
    if _nonce_store is None:
        _nonce_store = build_nonce_store(get_redis_url())
    return _nonce_store


def reset_nonce_store() -> None:
    global _nonce_store  # noqa: PLW0603
    _nonce_store = None


async def require_internal(
    request: Request,
    credentials: Annotated[InternalCredentials, Depends(extract_credentials)],
    store: Annotated[NonceStore, Depends(get_nonce_store)],
) -> None:
    """内部接口的鉴权依赖：凭据四件套校验 + nonce 防重放。"""
    verify_credentials(credentials)
    await verify_replay_fresh(store, credentials)


def verify_internal_network(request: Request) -> None:
    """内网网段限制（独立依赖：测试客户端的 host 不是 IP，需可单独覆盖）。"""
    host = request.client.host if request.client else None
    if not is_trusted_host(host):
        raise BusinessError.forbidden(_FORBIDDEN)
