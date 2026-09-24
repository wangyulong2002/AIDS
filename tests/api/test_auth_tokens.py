"""BE-03 鉴权模块验收测试：过期 / 伪造 / 刷新 / 吊销 / JWKS。

对应 TASKS 的验收原文：「**验收：过期/伪造/刷新用例全部通过单测**」。
三类反常路径各写成可复现的用例，而不是只测 happy path——鉴权模块的价值
几乎全在反常路径上，happy path 用肉眼看也知道没错。

三条跨服务契约（本文件固化，日后改动会红）：

    1. **Access 与 Refresh 不可互换**：Refresh 长期有效，若能当 Access 用就是万能通行证。
    2. **Refresh 一次性**：用过即焚，重放必须失败（否则"退出登录"形同虚设）。
    3. **JWKS 只含公钥**：AI 服务拿它验签；一旦把私钥参数（`d`）吐出去，
       等于把签发能力公开——这是不可逆的泄密。

测试不依赖真实 Redis 与真实时钟：吊销存储用 `InMemoryRefreshStore`（形状与 Redis 版一致），
过期用例用 `now=` 注入时间（不 sleep）。全部用例保持同步函数，
避免"在运行中的事件循环里用同步 TestClient"这类偶发挂死。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.core.jwt import (
    ACCESS_TYP,
    ALGORITHM,
    REFRESH_TYP,
    JwtKeys,
    TokenPair,
    decode_token,
    encode_token,
    issue_token_pair,
    roles_of,
    user_id_of,
)
from app.core.refresh_store import InMemoryRefreshStore

pytestmark = [pytest.mark.contract, pytest.mark.task("BE-03")]

ACCESS_TTL = 7200
REFRESH_TTL = 604800
USER_ID = 7001


@pytest.fixture
def keys() -> JwtKeys:
    """临时密钥对（生产密钥由环境注入；测试现场生成，2048 位约几十毫秒）。"""
    return JwtKeys.generate()


@pytest.fixture
def store() -> InMemoryRefreshStore:
    return InMemoryRefreshStore()


@pytest.fixture
def client(keys: JwtKeys, store: InMemoryRefreshStore) -> TestClient:
    """装配真实 app，只把「密钥」与「吊销存储」两处外部依赖替换掉。

    用 dependency_overrides 而不是改环境变量：默认密钥路径在仓库里不存在，
    改环境变量会让测试依赖文件系统状态（干净 clone 上必红，而那是环境问题不是代码问题）。
    """
    from aids_backend.app_factory import create_app
    from aids_backend.deps import get_jwt_keys, get_refresh_store

    app = create_app()
    app.dependency_overrides[get_jwt_keys] = lambda: keys
    app.dependency_overrides[get_refresh_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


def _save(store: InMemoryRefreshStore, jti: str, user_id: int = USER_ID) -> None:
    asyncio.run(store.save(jti, user_id, REFRESH_TTL))


def _issue(keys: JwtKeys, store: InMemoryRefreshStore, user_id: int = USER_ID) -> TokenPair:
    """签发一对 token 并登记 Refresh（等价于登录成功后的那一半动作）。"""
    pair = issue_token_pair(
        keys, user_id=user_id, roles=("user",), access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
    )
    _save(store, pair.refresh_jti, user_id)
    return pair


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =====================================================================
# 一、Token 内核：签发与解析
# =====================================================================


class TestTokenCore:
    def test_access_token_carries_user_and_roles(self, keys: JwtKeys) -> None:
        pair = issue_token_pair(
            keys,
            user_id=USER_ID,
            roles=("user", "admin"),
            access_ttl=ACCESS_TTL,
            refresh_ttl=REFRESH_TTL,
        )
        claims = decode_token(keys, pair.access_token, expected_typ=ACCESS_TYP)
        assert user_id_of(claims) == USER_ID
        assert roles_of(claims) == ("user", "admin")

    def test_access_and_refresh_use_distinct_typ(self, keys: JwtKeys) -> None:
        pair = issue_token_pair(
            keys, user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        assert decode_token(keys, pair.access_token, expected_typ=ACCESS_TYP)["typ"] == ACCESS_TYP
        assert (
            decode_token(keys, pair.refresh_token, expected_typ=REFRESH_TYP)["typ"] == REFRESH_TYP
        )

    def test_expired_token_rejected(self, keys: JwtKeys) -> None:
        """过期 → 10002（API.md §1.3 的五个 HTTP 例外之一，对外 401）。"""
        token = encode_token(
            keys, typ=ACCESS_TYP, user_id=USER_ID, roles=(), ttl=-10, jti="j", now=time.time()
        )
        with pytest.raises(BusinessError) as exc:
            decode_token(keys, token, expected_typ=ACCESS_TYP)
        assert exc.value.code == int(CommonError.UNAUTHORIZED)

    def test_forged_token_signed_by_other_key_rejected(self, keys: JwtKeys) -> None:
        """伪造：攻击者用自己的密钥签同一份 payload。"""
        attacker = JwtKeys.generate()
        token = encode_token(
            attacker, typ=ACCESS_TYP, user_id=USER_ID, roles=("admin",), ttl=600, jti="j"
        )
        with pytest.raises(BusinessError):
            decode_token(keys, token, expected_typ=ACCESS_TYP)

    def test_alg_none_token_rejected(self, keys: JwtKeys) -> None:
        """算法混淆：`alg=none` 是 JWT 最经典的绕过手法。

        `decode_token` 显式传 `algorithms=[RS256]`，PyJWT 会拒绝声明为 none 的 token。
        """
        now = int(time.time())
        payload = {
            "iss": "aids-backend",
            "aud": "aids",
            "sub": str(USER_ID),
            "uid": USER_ID,
            "typ": ACCESS_TYP,
            "exp": now + 600,
            "iat": now,
        }
        token = pyjwt.encode(payload, key="", algorithm="none")
        with pytest.raises(BusinessError):
            decode_token(keys, token, expected_typ=ACCESS_TYP)

    def test_tampered_payload_rejected(self, keys: JwtKeys) -> None:
        """篡改：把 payload 改掉。签名覆盖整个 payload，必然失败。"""
        pair = issue_token_pair(
            keys, user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        header, payload, signature = pair.access_token.split(".")
        forged = f"{header}.{payload[:-4]}AAAA.{signature}"
        with pytest.raises(BusinessError):
            decode_token(keys, forged, expected_typ=ACCESS_TYP)

    def test_refresh_token_rejected_as_access(self, keys: JwtKeys) -> None:
        """跨类型使用：长期有效的 Refresh 不能当 Access 用。"""
        pair = issue_token_pair(
            keys, user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        with pytest.raises(BusinessError):
            decode_token(keys, pair.refresh_token, expected_typ=ACCESS_TYP)


# =====================================================================
# 二、JWKS（供 AI 服务验签）
# =====================================================================


class TestJwks:
    def test_jwks_exposes_public_material_only(self, client: TestClient) -> None:
        """只允许公钥参数：出现 `d` 就是把签发能力公开了。"""
        response = client.get("/.well-known/jwks.json")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"keys"}
        key = body["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == ALGORITHM
        assert key["kid"]
        assert key["n"] and key["e"]
        assert "d" not in key, "JWKS 泄漏了私钥参数"

    def test_kid_in_jwks_matches_token_header(self, client: TestClient, keys: JwtKeys) -> None:
        """kid 必须能用来选对公钥——轮换密钥时靠它定位。"""
        pair = issue_token_pair(
            keys, user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        header = pyjwt.get_unverified_header(pair.access_token)
        jwks = client.get("/.well-known/jwks.json").json()
        assert header["kid"] == jwks["keys"][0]["kid"]
        assert header["alg"] == ALGORITHM

    def test_ai_service_can_verify_token_with_jwks(self, client: TestClient, keys: JwtKeys) -> None:
        """跨服务契约：持公钥的一方（AI 服务）能独立验签，且无需任何私钥。"""
        pair = issue_token_pair(
            keys, user_id=USER_ID, roles=("user",), access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        jwk = pyjwt.PyJWK(client.get("/.well-known/jwks.json").json()["keys"][0])
        claims = pyjwt.decode(
            pair.access_token,
            jwk.key,
            algorithms=[ALGORITHM],
            audience="aids",
            issuer="aids-backend",
        )
        assert claims["uid"] == USER_ID


# =====================================================================
# 三、受保护路由的鉴权拦截（依赖注入）
# =====================================================================


class TestProtectedRoute:
    """以 `POST /auth/logout`（API.md §2.1 标注「登录」）作为受保护路由的样本。"""

    def test_missing_token_returns_401(self, client: TestClient) -> None:
        response = client.post("/auth/logout")
        assert response.status_code == 401
        assert response.json()["code"] == int(CommonError.UNAUTHORIZED)

    def test_malformed_authorization_header_returns_401(self, client: TestClient) -> None:
        response = client.post("/auth/logout", headers={"Authorization": "token-without-scheme"})
        assert response.status_code == 401

    def test_expired_access_token_returns_401(self, client: TestClient, keys: JwtKeys) -> None:
        token = encode_token(keys, typ=ACCESS_TYP, user_id=USER_ID, roles=(), ttl=-10, jti="j")
        response = client.post("/auth/logout", headers=_auth(token))
        assert response.status_code == 401
        assert response.json()["code"] == int(CommonError.UNAUTHORIZED)

    def test_forged_access_token_returns_401(self, client: TestClient) -> None:
        token = encode_token(
            JwtKeys.generate(), typ=ACCESS_TYP, user_id=USER_ID, roles=("admin",), ttl=600, jti="j"
        )
        assert client.post("/auth/logout", headers=_auth(token)).status_code == 401

    def test_refresh_token_cannot_access_protected_route(
        self, client: TestClient, keys: JwtKeys
    ) -> None:
        """最危险的一类越权：把长期 Refresh 当 Access 用。

        用**同一对真实密钥**签的 Refresh —— 这样才会走到 typ 校验（而不是提前
        倒在签名校验上，让用例看起来通过但没测到点子上）。
        """
        pair = issue_token_pair(
            keys, user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        assert client.post("/auth/logout", headers=_auth(pair.refresh_token)).status_code == 401

    def test_valid_access_token_passes(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        pair = _issue(keys, store)
        response = client.post("/auth/logout", headers=_auth(pair.access_token))
        assert response.status_code == 200
        assert response.json()["code"] == 0

    def test_role_guard_rejects_insufficient_role(self) -> None:
        """403 路径：角色不足。直接调用依赖函数（无需为它造一个尚不存在的业务路由）。"""
        from aids_backend.deps import CurrentUser, require_roles

        guard = require_roles("admin")
        with pytest.raises(BusinessError) as exc:
            asyncio.run(guard(CurrentUser(user_id=USER_ID, roles=("user",), sid=None)))
        assert exc.value.code == int(CommonError.FORBIDDEN)

    def test_role_guard_allows_matching_role(self) -> None:
        from aids_backend.deps import CurrentUser, require_roles

        guard = require_roles("admin")
        current = CurrentUser(user_id=USER_ID, roles=("user", "admin"), sid=None)
        assert asyncio.run(guard(current)) is current


# =====================================================================
# 四、刷新与吊销（一次性语义）
# =====================================================================


class TestRefreshFlow:
    def test_refresh_returns_new_pair(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        pair = _issue(keys, store)
        response = client.post("/auth/refresh", json={"refreshToken": pair.refresh_token})
        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data) >= {"accessToken", "expiresIn", "refreshToken", "refreshExpiresIn"}
        assert data["expiresIn"] == ACCESS_TTL
        assert data["refreshExpiresIn"] == REFRESH_TTL
        assert data["refreshToken"] != pair.refresh_token, "刷新后必须轮换 Refresh"

    def test_old_refresh_token_is_single_use(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        """重放防护：同一个 Refresh 用第二次必须失败。"""
        pair = _issue(keys, store)
        assert (
            client.post("/auth/refresh", json={"refreshToken": pair.refresh_token}).status_code
            == 200
        )
        replay = client.post("/auth/refresh", json={"refreshToken": pair.refresh_token})
        assert replay.status_code == 401
        assert replay.json()["code"] == int(CommonError.UNAUTHORIZED)

    def test_new_refresh_after_rotation_works(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        pair = _issue(keys, store)
        rotated = client.post("/auth/refresh", json={"refreshToken": pair.refresh_token}).json()
        again = client.post("/auth/refresh", json={"refreshToken": rotated["data"]["refreshToken"]})
        assert again.status_code == 200

    def test_refresh_with_access_token_rejected(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        pair = _issue(keys, store)
        response = client.post("/auth/refresh", json={"refreshToken": pair.access_token})
        assert response.status_code == 401

    def test_refresh_with_forged_token_rejected(self, client: TestClient, keys: JwtKeys) -> None:
        forged = issue_token_pair(
            JwtKeys.generate(), user_id=USER_ID, access_ttl=ACCESS_TTL, refresh_ttl=REFRESH_TTL
        )
        response = client.post("/auth/refresh", json={"refreshToken": forged.refresh_token})
        assert response.status_code == 401

    def test_refresh_with_missing_body_rejected(self, client: TestClient) -> None:
        """缺字段 → 10001 参数校验失败（HTTP 200，API.md §1.3 未把它列入例外）。"""
        response = client.post("/auth/refresh", json={})
        assert response.json()["code"] == int(CommonError.PARAM_INVALID)

    def test_logout_revokes_refresh_token(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        """退出登录必须让 Refresh 立刻失效——否则"退出"只是前端删了个变量。"""
        pair = _issue(keys, store)
        assert client.post("/auth/logout", headers=_auth(pair.access_token)).status_code == 200
        revoked = client.post("/auth/refresh", json={"refreshToken": pair.refresh_token})
        assert revoked.status_code == 401

    def test_refresh_expiry_is_enforced(
        self, client: TestClient, keys: JwtKeys, store: InMemoryRefreshStore
    ) -> None:
        """过期的 Refresh 即便"还在存储里"也必须被拒（签名层的 exp 先拦）。"""
        jti = uuid.uuid4().hex
        expired = encode_token(
            keys, typ=REFRESH_TYP, user_id=USER_ID, roles=(), ttl=-60, jti=jti, now=time.time()
        )
        _save(store, jti)  # 存储层还以为它有效
        response = client.post("/auth/refresh", json={"refreshToken": expired})
        assert response.status_code == 401
