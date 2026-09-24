"""JWT 双 Token 内核（RS256 + JWKS）。

为什么放共享层而不是主业务服务里：
    **签发在主业务，验签在三个服务**。AI 服务要凭 Access Token 判断
    「这个会话属于哪个用户」（API.md §五 的 SSE 鉴权），Mock 服务的内部回调同样
    需要校验服务间 Token。若验签逻辑写在各服务里，就会出现「主业务认为 token 有效、
    AI 服务认为无效」这种最麻烦的故障——两边都"能跑"，只是算法参数不一致。
    故：内核一处，参数一处（`app/core/config.py` 读环境变量）。

设计要点（每条都有对应的失败模式）：

    1. **RS256 而非 HS256**：AI 服务只需公钥即可验签，拿不到签发能力。
       `algorithms=[RS256]` 显式限定，杜绝 `alg=none` 与算法混淆攻击。
    2. **双 Token 且带 `typ` 声明**：Access（2h）用于访问，Refresh（7d）只用于换新。
       `typ` 校验保证 Refresh 不能当 Access 用（反之亦然）——不校验的话，
       长期有效的 Refresh 会变成万能通行证。
    3. **Refresh 一次性（轮换）**：每换一次新 token 就作废旧 jti，
       旧 Refresh 重放会被拒。见 `app/core/refresh_store.py`。
    4. **kid 与 JWKS**：kid 取公钥的 RFC 7638 指纹，轮换密钥时不破坏在途 token；
       JWKS 端点只输出公钥材料（`n`/`e`），**绝不含 `d`**（有测试断言）。
    5. **时间可注入**：`now` 参数让"过期"用例不必 sleep——测试必须快且确定。

错误码：一律 `CommonError.UNAUTHORIZED`(10002) → HTTP 401（API.md §1.3 的五个例外之一），
且**不区分**「过期」「伪造」「类型不对」的对外措辞（避免给攻击者反馈），
真实原因只进日志。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.errors import CommonError
from app.core.exceptions import BusinessError

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
ISSUER = "aids-backend"
AUDIENCE = "aids"

# token 类型声明（不是业务状态枚举，故不进 app/domain/enums.py —— 那边是
# DATA-DICTIONARY §一 的 11 组状态映射，由 C2 契约测试锁死，不能随手加）
ACCESS_TYP = "access"
REFRESH_TYP = "refresh"

_CLAIM_USER_ID = "uid"
_CLAIM_ROLES = "roles"
_CLAIM_TYP = "typ"
_CLAIM_JTI = "jti"
# 会话标识（= Refresh 的 jti）。Access 与 Refresh 共享它，这样退出登录时
# 只凭 Access Token 就能吊销该会话的 Refresh —— 否则 logout 必须要求前端
# 把 Refresh 一起回传，而"前端忘了传"就会静默变成"没退出成功"。
_CLAIM_SID = "sid"


@dataclass(frozen=True)
class JwtKeys:
    """签名所需的一对密钥 + kid。"""

    private_pem: bytes | None
    public_pem: bytes
    kid: str

    @property
    def can_sign(self) -> bool:
        return self.private_pem is not None

    @classmethod
    def generate(cls) -> JwtKeys:
        """生成一对临时密钥（开发与测试用；生产密钥由环境注入，不入库）。"""
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return cls(private_pem=private_pem, public_pem=public_pem, kid=_thumbprint(public_pem))

    @classmethod
    def from_files(cls, private_path: Path, public_path: Path) -> JwtKeys:
        """从 PEM 文件加载（生产路径）。

        只给公钥也能工作（AI 服务的角色：只验签不签发）。
        缺文件时抛 `StartupAssertionError` 之外的普通异常由调用方包装——
        这里是配置问题，`app/core/config.py` 的 S1 断言会先一步拦住生产环境。
        """
        public_pem = public_path.read_bytes()
        private_pem = private_path.read_bytes() if private_path.is_file() else None
        return cls(private_pem=private_pem, public_pem=public_pem, kid=_thumbprint(public_pem))

    def public_jwk(self) -> dict[str, Any]:
        """单个 JWK（只含公钥参数）。"""
        jwk: dict[str, Any] = dict(RSAAlgorithm.to_jwk(self.public_key(), as_dict=True))
        jwk.update({"kid": self.kid, "use": "sig", "alg": ALGORITHM})
        return jwk

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        """JWKS 文档（RFC 7517）。"""
        return {"keys": [self.public_jwk()]}

    def public_key(self) -> rsa.RSAPublicKey:
        return _load_rsa_public_key(self.public_pem)


def _load_rsa_public_key(pem: bytes) -> rsa.RSAPublicKey:
    """加载 PEM 公钥并钉死为 RSA。

    本项目固定 RS256，若误配了 EC/Ed25519 密钥，`to_jwk`/`encode` 会在不同位置
    以难懂的类型错误炸开；这里提前给出可读的原因（且让类型检查器能收窄类型）。
    """
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("JWT 公钥必须是 RSA 公钥（本项目固定 RS256）")
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _thumbprint(public_pem: bytes) -> str:
    """RFC 7638 JWK 指纹：对规范化 JSON 取 SHA-256。

    用指纹当 kid 而不是时间戳/序号：**同一把公钥永远得到同一个 kid**，
    服务重启或多实例部署时不会因为 kid 变化让在途 token 失效。
    """
    jwk = RSAAlgorithm.to_jwk(_load_rsa_public_key(public_pem), as_dict=True)
    canonical = json.dumps(
        {"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]}, separators=(",", ":"), sort_keys=True
    )
    return _b64url(hashlib.sha256(canonical.encode("utf-8")).digest())


def _unauthorized(reason: str) -> BusinessError:
    """对外统一措辞，真实原因只进日志（不给攻击者反馈）。"""
    logger.warning("JWT 校验失败：%s", reason)
    return BusinessError(int(CommonError.UNAUTHORIZED), "未登录或登录已过期，请重新登录")


@dataclass(frozen=True)
class TokenPair:
    """双 Token 与它们的有效期（秒）。"""

    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    refresh_jti: str


def _now_ts(now: float | None) -> int:
    return int(now if now is not None else time.time())


def encode_token(
    keys: JwtKeys,
    *,
    typ: str,
    user_id: int,
    roles: tuple[str, ...],
    ttl: int,
    jti: str,
    sid: str | None = None,
    now: float | None = None,
) -> str:
    """签发一个 token。

    `jti` 由调用方给出（Refresh 靠它做一次性校验）；
    `sid` 是会话标识，缺省等于 `jti` —— Access Token 必须带上它，
    否则退出登录无从知道要吊销哪个会话。
    """
    private_pem = keys.private_pem
    if private_pem is None:
        raise RuntimeError("当前 JwtKeys 只有公钥，无法签发 —— 签发方必须持有私钥")

    issued_at = _now_ts(now)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        _CLAIM_USER_ID: user_id,
        _CLAIM_ROLES: list(roles),
        _CLAIM_TYP: typ,
        _CLAIM_JTI: jti,
        _CLAIM_SID: sid or jti,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return jwt.encode(payload, private_pem, algorithm=ALGORITHM, headers={"kid": keys.kid})


def issue_token_pair(
    keys: JwtKeys,
    *,
    user_id: int,
    roles: tuple[str, ...] = (),
    access_ttl: int,
    refresh_ttl: int,
    now: float | None = None,
) -> TokenPair:
    """签发一对 Access + Refresh。Refresh 的 jti 由调用方拿去落库/入 Redis。"""
    refresh_jti = uuid.uuid4().hex
    access = encode_token(
        keys,
        typ=ACCESS_TYP,
        user_id=user_id,
        roles=roles,
        ttl=access_ttl,
        jti=uuid.uuid4().hex,
        sid=refresh_jti,  # 与 Refresh 共享会话标识 → logout 凭 Access 即可吊销会话
        now=now,
    )
    refresh = encode_token(
        keys,
        typ=REFRESH_TYP,
        user_id=user_id,
        roles=roles,
        ttl=refresh_ttl,
        jti=refresh_jti,
        now=now,
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=access_ttl,
        refresh_expires_in=refresh_ttl,
        refresh_jti=refresh_jti,
    )


def decode_token(
    keys: JwtKeys,
    token: str,
    *,
    expected_typ: str,
    now: float | None = None,
) -> dict[str, Any]:
    """校验并解出 claims。任何问题一律 401（原因只进日志）。

    显式限定 `algorithms=[ALGORITHM]`：不加这一条，PyJWT 会按 token 头部的 alg
    选择算法，`alg=none` 与 RS256↔HS256 混淆攻击都能得手。
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            keys.public_pem,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", _CLAIM_TYP, _CLAIM_USER_ID]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized(f"token 非法（{exc.__class__.__name__}）") from exc

    if claims.get(_CLAIM_TYP) != expected_typ:
        raise _unauthorized(f"token 类型不匹配：期望 {expected_typ}，实际 {claims.get(_CLAIM_TYP)}")

    if now is not None and int(claims["exp"]) <= _now_ts(now):
        raise _unauthorized("token 已过期（显式时间校验）")

    return claims


def user_id_of(claims: dict[str, Any]) -> int:
    return int(claims[_CLAIM_USER_ID])


def roles_of(claims: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(role) for role in claims.get(_CLAIM_ROLES, ()))


def session_id_of(claims: dict[str, Any]) -> str | None:
    sid = claims.get(_CLAIM_SID)
    return str(sid) if sid else None
