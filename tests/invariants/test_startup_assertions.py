"""S1 结构性防御测试：启动断言。

验证 app/core/config.py 的启动拦截逻辑真的会拦住错误配置。

为什么必须测断言本身：
    启动断言是"代码护栏"，但如果护栏自己写错了（比如条件反了），
    它会安静地不生效——这比没有护栏更危险。
    bysj 的 _require_isolated_db() 之所以有效，正是因为它真的会被执行且真的会拦。
    本测试确保 AIDS 的护栏同样"真的会拦"。
"""

from __future__ import annotations

import pytest

from app.core.config import (
    StartupAssertionError,
    assert_db_is_isolated,
    assert_db_not_test_env_in_prod,
    assert_required_keys_present,
    assert_secret_key_not_default,
    get_env,
    is_production,
)

pytestmark = pytest.mark.invariant


class TestDbNotTestEnvInProd:
    """生产环境不得连测试库。"""

    def test_prod_with_test_db_rejected(self) -> None:
        with pytest.raises(StartupAssertionError, match="非生产标识"):
            assert_db_not_test_env_in_prod(db_url="mysql://u:p@h/aids_shop_test", env="production")

    def test_prod_with_dev_db_rejected(self) -> None:
        with pytest.raises(StartupAssertionError):
            assert_db_not_test_env_in_prod(db_url="mysql://u:p@h/aids_dev", env="production")

    def test_prod_with_local_db_rejected(self) -> None:
        with pytest.raises(StartupAssertionError):
            assert_db_not_test_env_in_prod(db_url="mysql://u:p@h/local_shop", env="production")

    def test_prod_without_db_url_rejected(self) -> None:
        with pytest.raises(StartupAssertionError, match="必须显式配置 DATABASE_URL"):
            assert_db_not_test_env_in_prod(db_url="", env="production")

    def test_prod_with_clean_db_allowed(self) -> None:
        assert_db_not_test_env_in_prod(db_url="mysql://u:p@db.internal/aids_shop", env="production")

    def test_dev_with_test_db_allowed(self) -> None:
        """非生产环境不做这个检查（由 assert_db_is_isolated 负责反向）。"""
        assert_db_not_test_env_in_prod(db_url="mysql://u:p@h/aids_shop_test", env="development")


class TestDbIsolated:
    """测试/开发环境不得连生产库（bysj 历史顽疾的代码化根治）。"""

    def test_test_env_with_prod_db_rejected(self) -> None:
        """核心用例：测试环境连了生产库名 aids_shop，必须拦住。"""
        with pytest.raises(StartupAssertionError, match="连到生产库名"):
            assert_db_is_isolated(db_url="mysql://u:p@h/aids_shop", env="test")

    def test_dev_env_with_prod_db_rejected(self) -> None:
        with pytest.raises(StartupAssertionError, match="连到生产库名"):
            assert_db_is_isolated(db_url="mysql://u:p@h/aids_shop", env="development")

    def test_test_env_with_test_db_allowed(self) -> None:
        assert_db_is_isolated(db_url="mysql://u:p@h/aids_shop_test", env="test")

    def test_prod_env_unaffected(self) -> None:
        """生产环境连生产库是正常的，不该被拦。"""
        assert_db_is_isolated(db_url="mysql://u:p@h/aids_shop", env="production")

    def test_empty_url_skipped(self) -> None:
        assert_db_is_isolated(db_url="", env="test")


class TestSecretKeyNotDefault:
    """生产环境密钥强度。"""

    @pytest.mark.parametrize(
        "weak",
        ["", "changeme", "secret", "test", "123456", "default", "aids-dev-secret"],
    )
    def test_weak_secrets_rejected_in_prod(self, weak: str) -> None:
        with pytest.raises(StartupAssertionError):
            assert_secret_key_not_default(secret=weak, env="production")

    def test_short_secret_rejected_in_prod(self) -> None:
        with pytest.raises(StartupAssertionError, match="长度"):
            assert_secret_key_not_default(secret="a" * 31, env="production")

    def test_strong_secret_allowed_in_prod(self) -> None:
        assert_secret_key_not_default(secret="x" * 64, env="production")

    def test_weak_secret_allowed_in_dev(self) -> None:
        """开发环境不拦（否则本地没法起服务）。"""
        assert_secret_key_not_default(secret="changeme", env="development")


class TestRequiredKeysPresent:
    """生产环境必需凭据。"""

    def test_missing_keys_rejected_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("ARK_API_KEY", "FIELD_ENCRYPT_KEY", "FIELD_HMAC_KEY"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(StartupAssertionError, match="缺少必需的环境变量"):
            assert_required_keys_present(env="production")

    def test_all_keys_present_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARK_API_KEY", "ark-xxx")
        monkeypatch.setenv("FIELD_ENCRYPT_KEY", "enc-key")
        monkeypatch.setenv("FIELD_HMAC_KEY", "hmac-key")
        assert_required_keys_present(env="production")

    def test_dev_env_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("ARK_API_KEY", "FIELD_ENCRYPT_KEY", "FIELD_HMAC_KEY"):
            monkeypatch.delenv(key, raising=False)
        assert_required_keys_present(env="development")


class TestEnvReading:
    """环境变量读取。"""

    def test_default_is_development(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APP_ENV", raising=False)
        assert get_env() == "development"
        assert not is_production()

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "PRODUCTION")
        assert get_env() == "production"
        assert is_production()

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "  production  ")
        assert get_env() == "production"


class TestAssertionErrorIsSystemExit:
    """关键设计：启动断言必须继承 SystemExit，不能被业务层 except Exception 吞掉。"""

    def test_inherits_system_exit(self) -> None:
        assert issubclass(StartupAssertionError, SystemExit)

    def test_not_caught_by_except_exception(self) -> None:
        """模拟业务层写 `except Exception` 不得吞掉启动断言。"""
        caught = False
        try:
            try:
                raise StartupAssertionError("测试")
            except Exception:  # noqa: BLE001 - 故意测试这个反模式
                caught = True
        except SystemExit:
            caught = False
        assert not caught, "StartupAssertionError 被 except Exception 吞掉了——护栏失效！"

    def test_reason_attribute_preserved(self) -> None:
        err = StartupAssertionError("具体原因")
        assert err.reason == "具体原因"
