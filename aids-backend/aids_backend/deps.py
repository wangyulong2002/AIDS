"""鉴权依赖注入（BE-03）。

把「谁在请求」变成 FastAPI 的依赖，业务代码只声明 `current_user: CurrentUser`
而不碰 Token 细节。这样做的直接收益是**越权防线只有一处**：

    BE-04（数据权限 / IDOR 防护）要往 Repository 注入 `WHERE user_id=?`，
    它需要的 userId 只能来自这个依赖，而**不允许**从请求参数里读（PRD §2.2）。
    若各接口自己解析 Token，就会出现「A 的 Token + 请求里传 B 的 userId」这类
    越权——而且它没有任何编译/类型错误，只有靠纪律。依赖注入把它变成"做不到"。

两个可替换点（测试用 `app.dependency_overrides` 覆盖，不必改环境变量）：
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
