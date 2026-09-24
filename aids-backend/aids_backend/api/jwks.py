"""JWKS 公钥端点（供 AI 服务与 Mock 服务验签）。

跨服务鉴权为什么用「公钥分发」而不是「共享对称密钥」：
    主业务签发，AI 服务只需**验签**。若用 HS256 共享密钥，AI 服务就同时拥有了
    签发能力——一处被攻破等于全部失守，且密钥轮换要同时改两个服务的配置。
    RS256 下 AI 服务只拿公钥，即便泄漏也只能验签，不能伪造。

路径为什么是 `/.well-known/jwks.json`：
    这不是本项目临时发明的路径：**PRD §5.3 已约定**「主业务服务暴露
    `/.well-known/jwks.json`，AI 服务启动时拉取并缓存」。它同时是 RFC 7517 / OIDC 的
    标准发现路径，PyJWT 的 `PyJWKClient` 默认就能消费。

    该端点**被机器消费**（且是标准文档），故刻意**不套统一响应体**——套上
    `{code,message,data}` 之后所有现成的 JWT 客户端库都会解析失败，
    为了"结构统一"去改写标准文档收益为负。这是 API.md §1.1 的唯一例外，已在文档注明。

    注意：它不在 `/api/**` 下，因此不经过 Nginx 的反代规则。
    服务间调用走内网直连（compose 服务名 + 端口），不需要走网关。

    T5（AI 服务）实现时的约定：**启动时拉取并缓存**，不要每请求去取；
    轮换密钥时本方会换 kid，缓存需要有 TTL 或按 kid 未命中时刷新——否则
    私钥轮换后 AI 服务会一直用旧公钥，表现为"新登录的用户在 AI 侧全部未授权"。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from aids_backend.deps import get_jwt_keys
from app.core.jwt import JwtKeys

router = APIRouter(tags=["鉴权"])


@router.get("/.well-known/jwks.json", summary="JWT 公钥集（JWKS）")
def jwks(keys: Annotated[JwtKeys, Depends(get_jwt_keys)]) -> dict[str, Any]:
    """返回公钥集合。只含公钥参数（`n`/`e`），**不含私钥参数 `d`**（有测试断言）。"""
    return keys.jwks()
