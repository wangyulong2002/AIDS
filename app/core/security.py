"""用户上下文（userId 的唯一合法来源）。

为什么这个文件被 S4 静态扫描器（tests/invariants/test_idor_guard.py）豁免：
    数据权限的硬约束是「业务代码禁止从请求参数读 userId 做权限判断」，
    而 userId 必须有**一个**合法来源 —— 从 JWT 解出。全项目只有这里允许出现
    "从请求中取得用户身份"这件事；扫描器按**路径**豁免本文件（不按文件名，
    否则任何 `**/security.py` 都会自动免疫）。

为什么放在共享层而不是主业务服务里：
    **三个服务都要验 JWT、都要知道"当前用户是谁"**：主业务签发+拦截，
    AI 服务凭 Access Token 判断会话归属（API.md §五），Mock 的内部回调同样要验。
    验签内核（`app/core/jwt.py`）在共享层，这里的依赖注入只是把它接到 FastAPI 上。
    若各服务自己写一份，就会分叉出"主业务认、AI 不认"这类最难查的故障。

依赖可替换（测试用 `app.dependency_overrides` 覆盖，不必改环境变量）：
    - `get_jwt_keys`：密钥来自环境变量指定的 PEM 文件
    - `get_refresh_store`：Refresh 吊销存储（Redis / 进程内）

错误口径：未认证 → 10002（HTTP 401），权限不足 → 10003（HTTP 403），
两者都在 `app/core/response.py::HTTP_STATUS_EXCEPTIONS` 里，不在这里另写。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_jwt_private_key_path, get_jwt_public_key_path, get_redis_url
from app.core.exceptions import BusinessError
from app.core.jwt import (
    ACCESS_TYP,
    JwtKeys,
    decode_token,
    roles_of,
    session_id_of,
    user_id_of,
)
from app.core.refresh_store import RefreshTokenStore, build_refresh_store

logger = logging.getLogger(__name__)

# auto_error=False：缺 Authorization 头时返回 None 而不是让 FastAPI 直接抛 403，
# 这样"未登录"统一走我们的 10002 → 401（API.md §1.3），前端拦截器只认一套口径。
_bearer = HTTPBearer(auto_error=False, description="RS256 Access Token（Bearer）")


@dataclass(frozen=True)
class CurrentUser:
    """当前请求的用户上下文。业务代码只依赖它，不依赖请求头。"""

    user_id: int
    roles: tuple[str, ...]
    sid: str | None = None  # 会话标识（= Refresh 的 jti），退出登录时用它吊销

    def has_role(self, *roles: str) -> bool:
        return bool(set(roles) & set(self.roles))


def get_jwt_keys() -> JwtKeys:
    """加载签名/验签密钥。生产环境缺文件由启动断言 S1-e 提前拦下。"""
    return JwtKeys.from_files(get_jwt_private_key_path(), get_jwt_public_key_path())


_store: RefreshTokenStore | None = None


def get_refresh_store() -> RefreshTokenStore:
    """Refresh 吊销存储（进程内单例）。

    刻意单例：Redis 客户端自带连接池，每请求新建会打爆连接数。
    """
    global _store  # noqa: PLW0603 - 进程级单例，替代 lru_cache（可被测试重置）
    if _store is None:
        _store = build_refresh_store(get_redis_url())
    return _store


def reset_refresh_store() -> None:
    """重置单例（供测试与配置变更后调用）。"""
    global _store  # noqa: PLW0603
    _store = None


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    keys: Annotated[JwtKeys, Depends(get_jwt_keys)],
) -> CurrentUser:
    """从 `Authorization: Bearer <access token>` 解出当前用户。

    任何失败（缺头、方案不对、过期、伪造、拿 Refresh 当 Access）都是 10002，
    对外措辞一致——不给攻击者区分"签名错"与"过期"的反馈。
    """
    if credentials is None:
        raise BusinessError.unauthorized()

    claims = decode_token(keys, credentials.credentials, expected_typ=ACCESS_TYP)
    return CurrentUser(
        user_id=user_id_of(claims),
        roles=roles_of(claims),
        sid=session_id_of(claims),
    )


def require_roles(*required: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """角色守卫工厂：`Depends(require_roles("admin"))`。

    语义是「**任一**角色命中即通过」——多角色场景下"全部满足"几乎总是需求误读。

    注意 roles 来自 Token 声明：角色变更在**下次登录/刷新**才生效。
    这是无状态 JWT 的固有取舍（换来看不到 DB 的鉴权路径），
    需要即时生效的场景（封禁、降权）走 `RefreshTokenStore.revoke_all_of` 踢下线。
    """

    async def _guard(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if not current_user.has_role(*required):
            logger.warning(
                "角色不足：user_id=%s 需要 %s，实际 %s",
                current_user.user_id,
                list(required),
                list(current_user.roles),
            )
            raise BusinessError.forbidden()
        return current_user

    return _guard
