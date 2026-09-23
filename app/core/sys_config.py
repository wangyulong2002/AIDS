"""sys_config 动态配置（BE-05）—— 环境变量之外的可热更配置。

与 `app/core/config.py`（环境变量 + 启动断言）的分工：
    环境变量是**骨架**（部署拓扑、密钥、连接串 —— 改它们必须重启）；
    `sys_config` 表是**运行参数**（检索阈值、开关、限额 —— 管理员在后台改，
    通过 Redis 发布订阅广播失效，各实例重载，**全程不重启**）。
    这是 v1.3 移除配置中心后自建的轻量替代（PRD v1.3 修订说明）。

热更新机制：
    管理端改库后 `PUBLISH aids:sys_config:changed` → 各实例的订阅任务收到消息 →
    重新加载全表 → 内存缓存整体替换。为什么"重载全表"而不是按 key 删：
    配置行总量极小（个位数到几十行），全量替换不会出现"改了两个 key 只广播了
    一个"的半新半旧状态。

读取语义：
    `get(key, default)` 是**同步**的 —— 业务代码一行可用；缓存未加载时返回
    default 并打警告（宁可退到保守值，也不要为一个配置把请求拖死在 DB 上）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Final, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysConfig

logger = logging.getLogger(__name__)

CHANNEL: Final[str] = "aids:sys_config:changed"

# value_type（DDL 注释）：0 string 1 int 2 float 3 bool 4 json
TYPE_STRING: Final[int] = 0
TYPE_INT: Final[int] = 1
TYPE_FLOAT: Final[int] = 2
TYPE_BOOL: Final[int] = 3
TYPE_JSON: Final[int] = 4

_TRUE_STRINGS: Final[frozenset[str]] = frozenset({"1", "true", "TRUE", "True", "on", "yes"})
_FALSE_STRINGS: Final[frozenset[str]] = frozenset({"0", "false", "FALSE", "False", "off", "no"})


def parse_value(raw: str, value_type: int) -> Any:  # noqa: ANN401 - 返回类型由 value_type 决定
    """按 `value_type` 把字符串解析为真实类型。解析失败抛 ValueError（宁可响也不要静默串型）。"""
    if value_type == TYPE_STRING:
        return raw
    if value_type == TYPE_INT:
        return int(raw)
    if value_type == TYPE_FLOAT:
        return float(raw)
    if value_type == TYPE_BOOL:
        if raw in _TRUE_STRINGS:
            return True
        if raw in _FALSE_STRINGS:
            return False
        raise ValueError(f"无法把 {raw!r} 解析为 bool（合法：1/0/true/false/on/off/yes/no）")
    if value_type == TYPE_JSON:
        return json.loads(raw)
    raise ValueError(f"未知的 value_type：{value_type}")


class ConfigSource(Protocol):
    """配置缓存的数据来源（便于测试替身与将来的多级缓存）。"""

    async def load(self) -> dict[str, Any]:
        """返回 key → 已解析值的完整快照。"""
        ...


class DbConfigSource:
    """从 `sys_config` 表加载（每实例一次全量；会话由调用方提供与销毁）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self) -> dict[str, Any]:
        result = await self._session.execute(select(SysConfig))
        values: dict[str, Any] = {}
        for row in result.scalars().all():
            try:
                values[row.config_key] = parse_value(row.config_value, row.value_type)
            except (ValueError, json.JSONDecodeError) as exc:
                # 单条配置坏了不该拖垮整个缓存：跳过 + 告警，等管理员修正
                logger.error("sys_config 解析失败（跳过该键）：%s —— %s", row.config_key, exc)
        return values


class ConfigCache:
    """进程内配置快照。`bind()` 换数据源，`load()` 全量重载，`get()` 同步读取。"""

    def __init__(self, source: ConfigSource | None = None) -> None:
        self._source = source
        self._values: dict[str, Any] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def bind(self, source: ConfigSource) -> None:
        """替换数据源（失效广播触发重载时，每次用新会话构造新 source）。"""
        self._source = source

    async def load(self) -> None:
        """全量重载（启动时 / 收到失效广播时）。"""
        if self._source is None:
            raise RuntimeError("ConfigCache 未绑定数据源")
        self._values = await self._source.load()
        self._loaded = True
        logger.info("sys_config 已加载 %s 项", len(self._values))

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401 - 默认值类型随调用方
        """同步读取。未加载时返回 default 并打警告 —— 宁可保守，不要阻塞请求。"""
        if not self._loaded:
            logger.warning("sys_config 尚未加载，返回默认值：key=%s default=%r", key, default)
            return default
        return self._values.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        """当前缓存副本（诊断用；修改副本不影响缓存）。"""
        return dict(self._values)


_cache: ConfigCache | None = None


def get_config_cache() -> ConfigCache:
    """进程级配置缓存单例（首次 `load()` 由应用 lifespan 接线，T3 落地）。"""
    global _cache  # noqa: PLW0603 - 进程级单例
    if _cache is None:
        _cache = ConfigCache()
    return _cache


def reset_config_cache() -> None:
    global _cache  # noqa: PLW0603
    _cache = None


async def reload_config() -> None:
    """用进程级会话工厂重载缓存（订阅任务 / 管理端改库后调用）。"""
    from app.orm.session import session_scope  # noqa: PLC0415 - 延迟导入避免 import 期建 engine

    cache = get_config_cache()
    async with session_scope() as session:
        cache.bind(DbConfigSource(session))
        await cache.load()


async def watch_invalidations(
    client: Any, cache: ConfigCache, source_factory: Callable[[], ConfigSource]
) -> None:
    """订阅失效广播并重载（每个实例一个订阅任务，随应用 lifespan 启停）。

    `source_factory` 在每次失效时构造**新的**数据源（新会话）—— 跨事务复用
    长连接会话会带来事务语义问题；配置重载频率极低，新开会话没有成本。
    """
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    logger.info("已订阅配置失效广播：%s", CHANNEL)
    async for message in pubsub.listen():
        if isinstance(message, dict) and message.get("type") == "message":
            try:
                cache.bind(source_factory())
                await cache.load()
                logger.info("sys_config 已热更新（收到失效广播）")
            except Exception:  # noqa: BLE001 - 单次重载失败只告警，订阅不能停
                logger.exception("sys_config 热更新失败（保留旧缓存，等待下次广播）")
