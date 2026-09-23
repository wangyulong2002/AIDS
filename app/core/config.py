"""配置与启动断言。

=====================================================================
结构性防御 S1：启动断言（Startup Assertions）
=====================================================================

为什么需要：
    bysj 的教训——"测试别连生产库"只写在文档里 → 至今未隔离；
    而 RAG 的隔离检查写成了代码护栏 → 永久生效。
    结论：能在启动时用代码拦住的，绝不依赖人的自觉。

本模块在应用启动时执行硬校验，任一不满足直接 sys.exit(1)，**不允许带病启动**。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# =====================================================================
# 环境判定
# =====================================================================

PRODUCTION: str = "production"
STAGING: str = "staging"
DEVELOPMENT: str = "development"
TEST: str = "test"

# 测试/开发环境标识词——生产环境出现这些词即视为配置错误
_NON_PROD_DB_MARKERS: tuple[str, ...] = ("test", "dev", "local", "sandbox", "_tmp", "tmp")

# 明确不允许在生产出现的默认密钥
_FORBIDDEN_DEFAULT_SECRETS: tuple[str, ...] = (
    "changeme",
    "secret",
    "test",
    "123456",
    "default",
    "aids-dev-secret",
)


class StartupAssertionError(SystemExit):
    """启动断言失败。

    继承 SystemExit 以确保无法被业务层的 `except Exception` 吞掉——
    配置错误必须在进程启动阶段终止，不能降级为"一个业务异常"。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"[启动断言失败] {reason}")


def get_env() -> str:
    """读取当前运行环境。"""
    return os.getenv("APP_ENV", DEVELOPMENT).strip().lower()


def is_production() -> bool:
    return get_env() == PRODUCTION


# =====================================================================
# 断言集合
# =====================================================================


def assert_db_not_test_env_in_prod(
    db_url: str | None = None,
    env: str | None = None,
) -> None:
    """S1-a：生产环境绝不允许连到含 test/dev 字样的库。

    根治 bysj 的"测试连生产库"历史顽疾——反向也拦：
    生产环境不得连测试库，测试环境不得连生产库（见 assert_db_is_isolated）。
    """
    env = env or get_env()
    # ★ 用 `is not None` 而不是 `or`：显式传空串的语义是"我知道它是空的"，
    #   与"根本没传、应去读环境变量"是两种不同意图。
    #   用 `or` 会让显式空串**静默回退到环境变量** —— 于是"生产环境没配
    #   DATABASE_URL"这条断言在被测环境恰好设了 DATABASE_URL 时就失效了
    #   （tests/invariants/test_startup_assertions.py 正是靠传 "" 模拟该场景）。
    db_url = db_url if db_url is not None else os.getenv("DATABASE_URL", "")

    if env != PRODUCTION:
        return

    if not db_url:
        raise StartupAssertionError("生产环境必须显式配置 DATABASE_URL")

    lowered = db_url.lower()
    for marker in _NON_PROD_DB_MARKERS:
        if marker in lowered:
            raise StartupAssertionError(
                f"生产环境(APP_ENV={env}) 的 DATABASE_URL 含非生产标识 '{marker}'；"
                f"疑似连到测试库，拒绝启动。"
            )


def assert_secret_key_not_default(secret: str | None = None, env: str | None = None) -> None:
    """S1-b：生产环境密钥不得为默认值/弱值。"""
    env = env or get_env()
    secret = secret if secret is not None else os.getenv("SECRET_KEY", "")

    if env != PRODUCTION:
        return

    if not secret:
        raise StartupAssertionError("生产环境必须显式配置 SECRET_KEY")

    lowered = secret.lower()
    for forbidden in _FORBIDDEN_DEFAULT_SECRETS:
        if lowered == forbidden or forbidden in lowered:
            raise StartupAssertionError(
                f"生产环境 SECRET_KEY 为默认/弱值（命中 '{forbidden}'），拒绝启动。"
            )

    if len(secret) < 32:
        raise StartupAssertionError(
            f"生产环境 SECRET_KEY 长度 {len(secret)} < 32，强度不足，拒绝启动。"
        )


def assert_required_keys_present(env: str | None = None) -> None:
    """S1-c：生产环境必需的外部凭据必须存在。

    覆盖 PRD §10 第三方依赖：Ark API Key、加密密钥、渠道密钥。
    """
    env = env or get_env()
    if env != PRODUCTION:
        return

    required = {
        "ARK_API_KEY": "AI 客服 Ark 大模型 Key（PRD §9 / §10）",
        "FIELD_ENCRYPT_KEY": "敏感字段 AES-256-GCM 加密密钥（schema.sql 头部约定 9）",
        "FIELD_HMAC_KEY": "敏感字段 HMAC-SHA256 哈希密钥（schema.sql 头部约定 9）",
    }
    missing = [f"{k}（{v}）" for k, v in required.items() if not os.getenv(k)]
    if missing:
        raise StartupAssertionError("生产环境缺少必需的环境变量：\n  - " + "\n  - ".join(missing))


def assert_db_is_isolated(db_url: str | None = None, env: str | None = None) -> None:
    """S1-d：测试环境必须连独立测试库，禁止连生产库。

    这是 bysj "测试别连生产库"约定的代码化——当时只写文档，所以失效；
    现在写进启动路径，跑测试时自动生效。
    """
    env = env or get_env()
    # 同 assert_db_not_test_env_in_prod：显式空串不得回退到环境变量
    db_url = db_url if db_url is not None else os.getenv("DATABASE_URL", "")

    if env not in (TEST, DEVELOPMENT):
        return

    if not db_url:
        return  # 本地未配置时由 pytest fixture 兜底

    lowered = db_url.lower()
    # 生产库名判定：aids_shop 是生产库名（docs/sql/schema.sql）
    suspicious = "aids_shop" in lowered and "test" not in lowered
    if suspicious:
        raise StartupAssertionError(
            f"当前环境 APP_ENV={env} 却连到生产库名 'aids_shop'；"
            f"测试必须使用独立库（如 aids_shop_test）。"
        )


def assert_jwt_keys_configured(env: str | None = None) -> None:
    """S1-e：生产环境必须能读到 JWT 签名/验签密钥文件。

    为什么必须在**启动**阶段拦：
        私钥缺失的症状不是"启动失败"，而是"第一个用户登录时才 500"——
        那时服务已经在对外提供服务了。公钥缺失更隐蔽：签发照常，
        AI 服务验签全部失败，表现为"AI 客服总说未登录"。
    非生产环境跳过：本地与测试用临时密钥（`JwtKeys.generate()`）。
    """
    env = env or get_env()
    if env != PRODUCTION:
        return

    missing = [
        f"{name}（{path}）"
        for name, path in (
            ("JWT_PRIVATE_KEY_PATH", get_jwt_private_key_path()),
            ("JWT_PUBLIC_KEY_PATH", get_jwt_public_key_path()),
        )
        if not path.is_file()
    ]
    if missing:
        raise StartupAssertionError(
            "生产环境 JWT 密钥文件不存在：\n  - " + "\n  - ".join(missing) + "\n"
            "生成：openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt_private.pem\n"
            "      openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem"
        )


# =====================================================================
# JWT / Redis 配置（BE-03 鉴权模块）
#
# 键名以根 .env.example 为唯一权威（deploy/.env.example 必须同名，
# 由文档门禁 check_env_hygiene 强制）。这里只做读取与默认值，不新造名字。
# =====================================================================

DEFAULT_JWT_ACCESS_TTL: int = 7200  # 2h，与 API.md §2.1 的 expiresIn 一致
DEFAULT_JWT_REFRESH_TTL: int = 604800  # 7d
DEFAULT_JWT_PRIVATE_KEY_PATH: str = "./secrets/jwt_private.pem"
DEFAULT_JWT_PUBLIC_KEY_PATH: str = "./secrets/jwt_public.pem"


def _int_env(name: str, default: int) -> int:
    """读整数环境变量。非法值直接抛错——静默回退默认值是"配置没生效但没人发现"的经典成因。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise StartupAssertionError(f"{name} 必须是整数，当前值 {raw!r}") from exc


def get_jwt_access_ttl() -> int:
    return _int_env("JWT_ACCESS_TTL", DEFAULT_JWT_ACCESS_TTL)


def get_jwt_refresh_ttl() -> int:
    return _int_env("JWT_REFRESH_TTL", DEFAULT_JWT_REFRESH_TTL)


def get_jwt_private_key_path() -> Path:
    return Path(os.getenv("JWT_PRIVATE_KEY_PATH") or DEFAULT_JWT_PRIVATE_KEY_PATH)


def get_jwt_public_key_path() -> Path:
    return Path(os.getenv("JWT_PUBLIC_KEY_PATH") or DEFAULT_JWT_PUBLIC_KEY_PATH)


def get_redis_url() -> str:
    """Redis 连接串。空串表示未配置（Refresh 吊销会显式降级为进程内存储）。"""
    return os.getenv("REDIS_URL", "").strip()


# =====================================================================
# 统一入口
# =====================================================================


def run_startup_assertions() -> None:
    """应用启动时调用（在 FastAPI app 创建前）。

    main.py 里首行调用，任何一项失败即终止进程。
    """
    env = get_env()
    assert_db_not_test_env_in_prod(env=env)
    assert_db_is_isolated(env=env)
    assert_secret_key_not_default(env=env)
    assert_required_keys_present(env=env)
    assert_jwt_keys_configured(env=env)
    print(f"[启动断言] 通过 (APP_ENV={env})", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    run_startup_assertions()
