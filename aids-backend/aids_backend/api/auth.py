"""鉴权路由（API.md §2.1 的 Token 生命周期部分）。

本模块只负责 **Token 的生命周期**：刷新、退出登录。
登录/注册/验证码（`/auth/sms/send`、`/auth/register`、`/auth/login/*`）属 BE-07，
它们依赖 BE-05 的 Redis 频控与短信抽象，且要读写 `biz_user` 表；
BE-03 刻意**不碰数据库**——鉴权内核应当能在没有 DB 的情况下被完整测试。

为什么 Refresh 用 POST 而不是放到请求头：
    Refresh Token 是**凭证**，不能被浏览器自动携带（那是 Cookie 的问题），
    放 body 也便于前端在无感刷新队列里精确控制"哪一次请求换到了新 token"。
    这是 API.md §2.1 的既定契约。

为什么 logout 只收 Access Token：
    Access 与 Refresh 共享 `sid`（会话标识），故凭 Access 就能吊销整个会话。
    若改成"必须回传 Refresh"，前端一旦忘传，退出登录会静默变成"没退出成功"。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from aids_backend.deps import CurrentUser, get_current_user, get_jwt_keys, get_refresh_store
from app.core.config import get_jwt_access_ttl, get_jwt_refresh_ttl
from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.core.jwt import (
    REFRESH_TYP,
    JwtKeys,
    decode_token,
    issue_token_pair,
    roles_of,
    session_id_of,
    user_id_of,
)
from app.core.refresh_store import RefreshTokenStore
from app.core.response import ApiResponse, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["鉴权"])


class RefreshRequest(BaseModel):
    """刷新请求。字段名与 API.md §2.1 的响应字段（accessToken/refreshToken）同一套驼峰。"""

    refreshToken: str = Field(min_length=1, description="登录时下发的 Refresh Token")


class TokenPairData(BaseModel):
    """刷新响应体（与登录响应同构，前端只写一套处理逻辑）。"""

    accessToken: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int


@router.post("/refresh", summary="刷新 Access Token")
async def refresh(
    payload: RefreshRequest,
    keys: Annotated[JwtKeys, Depends(get_jwt_keys)],
    store: Annotated[RefreshTokenStore, Depends(get_refresh_store)],
) -> ApiResponse:
    """用 Refresh Token 换一对新 Token（**轮换**：旧的立即作废）。

    轮换而不是续期，是为了让被盗的 Refresh 只有一次使用机会：
    攻击者用掉之后，真正的用户再刷新会失败 → 立刻暴露异常（可观测），
    而不是双方在 7 天里各自静默使用同一个凭证。
    """
    claims = decode_token(keys, payload.refreshToken, expected_typ=REFRESH_TYP)
    user_id = user_id_of(claims)
    sid = session_id_of(claims)
    if not sid:
        raise BusinessError.unauthorized()

    # 一次性消费：命中即删。GETDEL/内存 pop 都是原子的，避免并发重放双双成功。
    owner = await store.consume(sid)
    if owner is None or owner != user_id:
        logger.warning("Refresh 重放或已吊销：user_id=%s sid=%.8s", user_id, sid)
        raise BusinessError(int(CommonError.UNAUTHORIZED), "登录状态已失效，请重新登录")

    pair = issue_token_pair(
        keys,
        user_id=user_id,
        roles=roles_of(claims),
        access_ttl=get_jwt_access_ttl(),
        refresh_ttl=get_jwt_refresh_ttl(),
    )
    await store.save(pair.refresh_jti, user_id, get_jwt_refresh_ttl())
    return ok(
        TokenPairData(
            accessToken=pair.access_token,
            expiresIn=pair.expires_in,
            refreshToken=pair.refresh_token,
            refreshExpiresIn=pair.refresh_expires_in,
        ).model_dump()
    )


@router.post("/logout", summary="退出登录（吊销 Refresh）")
async def logout(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    store: Annotated[RefreshTokenStore, Depends(get_refresh_store)],
) -> ApiResponse:
    """吊销当前会话的 Refresh；Access 本身不撤销（它最多再活 2 小时）。

    幂等：重复退出不报错——用户连点两次"退出"是常态，不该看到失败。
    """
    if current_user.sid:
        await store.revoke(current_user.sid)
    logger.info("退出登录：user_id=%s sid=%.8s", current_user.user_id, current_user.sid or "-")
    return ok(None)
