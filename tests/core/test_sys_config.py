"""BE-05 · sys_config 动态配置的单元测试（不连库、不连 Redis）。

被锁死的语义：
    - `value_type` 解析失败必须**响**（ValueError），静默串型比没有配置更危险；
    - 单条配置损坏只跳过该键并告警，不拖垮整个缓存；
    - 缓存未加载时 `get()` 返回 default 并告警 —— 宁可保守，不要阻塞请求；
    - 失效广播触发**全量重载**（新会话新数据源），单次失败保留旧缓存。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sys_config import (
    CHANNEL,
    TYPE_BOOL,
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_JSON,
    TYPE_STRING,
    ConfigCache,
    DbConfigSource,
    parse_value,
    watch_invalidations,
)
from app.models.sys import SysConfig

pytestmark = [pytest.mark.unit, pytest.mark.task("BE-05")]


# =====================================================================
# 一、value_type 解析
# =====================================================================


def test_parse_string_int_float() -> None:
    assert parse_value("hello", TYPE_STRING) == "hello"
    assert parse_value("42", TYPE_INT) == 42
    assert parse_value("0.8", TYPE_FLOAT) == 0.8


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "on", "yes"])
def test_parse_bool_true(raw: str) -> None:
    assert parse_value(raw, TYPE_BOOL) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "off", "no"])
def test_parse_bool_false(raw: str) -> None:
    assert parse_value(raw, TYPE_BOOL) is False


def test_parse_bool_invalid_is_loud() -> None:
    with pytest.raises(ValueError, match="解析为 bool"):
        parse_value("maybe", TYPE_BOOL)


def test_parse_json() -> None:
    assert parse_value('{"a": 1}', TYPE_JSON) == {"a": 1}
    with pytest.raises(ValueError):
        parse_value("{broken", TYPE_JSON)


def test_parse_unknown_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知的 value_type"):
        parse_value("x", 9)


# =====================================================================
# 二、DbConfigSource（单条损坏只跳过）
# =====================================================================


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[SysConfig]) -> None:
        self._rows = rows

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


def _config(key: str, value: str, value_type: int) -> SysConfig:
    return SysConfig(config_key=key, config_value=value, value_type=value_type)


async def test_source_parses_rows() -> None:
    session = cast(
        AsyncSession,
        _FakeSession(
            [
                _config("ai.threshold", "0.8", TYPE_FLOAT),
                _config("feature.enabled", "1", TYPE_BOOL),
                _config("welcome.text", "欢迎", TYPE_STRING),
                _config("limits", '{"daily": 5}', TYPE_JSON),
            ]
        ),
    )
    values = await DbConfigSource(session).load()
    assert values == {
        "ai.threshold": 0.8,
        "feature.enabled": True,
        "welcome.text": "欢迎",
        "limits": {"daily": 5},
    }


async def test_source_skips_broken_row_without_dying() -> None:
    session = cast(
        AsyncSession,
        _FakeSession(
            [
                _config("bad.bool", "maybe", TYPE_BOOL),
                _config("good", "1", TYPE_INT),
            ]
        ),
    )
    values = await DbConfigSource(session).load()
    assert values == {"good": 1}, "单条损坏的配置不应拖垮整个缓存"


# =====================================================================
# 三、缓存读取语义
# =====================================================================


class _FakeSource:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def load(self) -> dict[str, Any]:
        return self.values


async def test_unloaded_cache_returns_default() -> None:
    cache = ConfigCache(_FakeSource({"k": 1}))
    assert cache.loaded is False
    assert cache.get("k", "fallback") == "fallback"


async def test_loaded_cache_returns_typed_values() -> None:
    cache = ConfigCache(_FakeSource({"k": 1, "missing_case": None}))
    await cache.load()
    assert cache.loaded is True
    assert cache.get("k") == 1
    assert cache.get("nope", "dft") == "dft"
    assert cache.snapshot()["k"] == 1


async def test_cache_without_source_load_raises() -> None:
    cache = ConfigCache()
    with pytest.raises(RuntimeError, match="未绑定数据源"):
        await cache.load()


# =====================================================================
# 四、失效广播 → 全量重载
# =====================================================================


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def listen(self) -> Any:
        for message in self._messages:
            yield message


class _FakeClient:
    """watch_invalidations 的入参是 client（client.pubsub() 才是订阅对象）。"""

    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


class _CountingSource:
    instances = 0

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values
        type(self).instances += 1

    async def load(self) -> dict[str, Any]:
        return self.values


async def test_watch_invalidations_reloads_on_message() -> None:
    """收到失效广播 → 用**新数据源**（新会话）全量重载；非 message 类型忽略。"""
    _CountingSource.instances = 0
    cache = ConfigCache()
    pubsub = _FakePubSub(
        [
            {"type": "subscribe", "channel": CHANNEL},  # 订阅确认，应被忽略
            {"type": "message", "channel": CHANNEL, "data": "changed"},
        ]
    )
    source_factory = lambda: _CountingSource({"threshold": 0.9})  # noqa: E731 - 测试用闭包

    await watch_invalidations(_FakeClient(pubsub), cache, source_factory)

    assert pubsub.subscribed == [CHANNEL]
    assert _CountingSource.instances == 1, "只有 message 类型的广播才触发重载"
    assert cache.loaded is True
    assert cache.get("threshold") == 0.9


async def test_watch_invalidations_keeps_old_cache_on_failure() -> None:
    """单次重载失败只告警：订阅不能停，旧缓存必须保住。"""

    class _BrokenSource(_CountingSource):
        async def load(self) -> dict[str, Any]:
            raise RuntimeError("db hiccup")

    cache = ConfigCache(_FakeSource({"threshold": 0.8}))
    await cache.load()

    pubsub = _FakePubSub([{"type": "message", "channel": CHANNEL, "data": "changed"}])
    await watch_invalidations(_FakeClient(pubsub), cache, lambda: _BrokenSource({"threshold": 0.9}))

    assert cache.get("threshold") == 0.8, "重载失败必须保留旧缓存"
