"""雪花 ID（Snowflake ID）生成器。

=====================================================================
为什么需要（schema.sql 全局约定 2 / DATA-DICTIONARY 前置约定）：
    主键统一 `BIGINT UNSIGNED` 雪花 ID，**由应用层生成、非自增**。

    为什么不用自增主键：
      1. 多副本 + 分库时自增会退化成单点（要么集中取号，要么冲突）；
      2. 自增 ID 可被外部按序枚举 —— **按递增 ID 扫描正是 IDOR 的典型入口**
         （见 BE-04 验收标准："批量递增 ID 扫描无一条越权数据泄露"）。

位分配（共 63 位；最高位恒 0，保证落在 signed BIGINT 的正区间，
        因此列类型即使是 BIGINT UNSIGNED 也不会溢出）：

    | 符号(1) | 时间戳(41) | workerId(10) | 序列号(12) |
    |    0    |  相对纪元   |   0 ~ 1023   |  0 ~ 4095  |

    - 41 位时间戳以 `_EPOCH_MS` 为起点 → 可用约 69.7 年（到 2095 年）
    - 同一毫秒内单节点可发 4096 个 ID

纪元为什么是 2026-01-01 而不是 Unix 纪元(1970)：
    从 1970 起算的毫秒数需要约 45 位，41 位**装不下**；
    自定义纪元把可用年限从"已过去 55 年"变成"未来 69 年"。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Final

# 自定义纪元：2026-01-01T00:00:00Z 的 Unix 毫秒数
_EPOCH_MS: Final[int] = 1_767_225_600_000

_WORKER_ID_BITS: Final[int] = 10
_SEQUENCE_BITS: Final[int] = 12

MAX_WORKER_ID: Final[int] = (1 << _WORKER_ID_BITS) - 1  # 1023
MAX_SEQUENCE: Final[int] = (1 << _SEQUENCE_BITS) - 1  # 4095

_WORKER_ID_SHIFT: Final[int] = _SEQUENCE_BITS  # 12
_TIMESTAMP_SHIFT: Final[int] = _SEQUENCE_BITS + _WORKER_ID_BITS  # 22

# 时钟回拨容忍上限（毫秒）。
# 小幅回拨（NTP 微调、容器时间同步）等它过去即可；
# 超过则说明系统时钟被人为改动过，继续发号会产生**重复 ID** —— 必须硬失败。
_MAX_BACKWARD_MS: Final[int] = 5

# workerId 环境变量名。多副本部署时每个副本必须取不同值。
ENV_WORKER_ID: Final[str] = "SNOWFLAKE_WORKER_ID"


class ClockMovedBackwardsError(RuntimeError):
    """系统时钟大幅回拨 —— 继续发号会产生重复 ID，故拒绝发号。

    不继承 StartupAssertionError：那是"配置错误拒绝启动"（SystemExit），
    而这是运行期偶发故障，应当由调用方决定重试或告警，不该直接杀进程。
    """


class SnowflakeGenerator:
    """雪花 ID 生成器（线程安全）。

    应当**全局单例**（每进程一个）：同一 workerId 起两个实例会各自维护
    序列号，同一毫秒内必然撞号。进程级单例见 `get_generator()`。
    """

    __slots__ = ("_last_ms", "_lock", "_sequence", "_worker_id")

    def __init__(self, worker_id: int) -> None:
        if not 0 <= worker_id <= MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须在 0 ~ {MAX_WORKER_ID} 之间，当前为 {worker_id}")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_ms = -1
        self._lock = threading.Lock()

    @property
    def worker_id(self) -> int:
        return self._worker_id

    @staticmethod
    def now_ms() -> int:
        """当前 Unix 毫秒（UTC）。"""
        return time.time_ns() // 1_000_000

    def next_id(self) -> int:
        """生成下一个 ID（线程安全）。"""
        with self._lock:
            now = self.now_ms()

            if now < self._last_ms:
                backward = self._last_ms - now
                if backward > _MAX_BACKWARD_MS:
                    raise ClockMovedBackwardsError(
                        f"系统时钟回拨 {backward}ms（超过容忍值 {_MAX_BACKWARD_MS}ms）——"
                        f"继续发号会产生重复 ID，请检查 NTP / 容器时间同步"
                    )
                time.sleep(backward / 1000)
                now = self.now_ms()

            if now == self._last_ms:
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    # 本毫秒的 4096 个序列号已用尽 → 自旋等到下一毫秒
                    now = self._spin_to_next_ms(self._last_ms)
            else:
                self._sequence = 0

            self._last_ms = now
            return (
                ((now - _EPOCH_MS) << _TIMESTAMP_SHIFT)
                | (self._worker_id << _WORKER_ID_SHIFT)
                | self._sequence
            )

    def _spin_to_next_ms(self, last_ms: int) -> int:
        now = self.now_ms()
        while now <= last_ms:
            now = self.now_ms()
        return now


def resolve_worker_id() -> int:
    """解析 workerId：环境变量优先，缺失时按主机名派生。

    环境变量 `SNOWFLAKE_WORKER_ID` 优先（容器编排时可显式指定）；
    未设置时用主机名哈希兜底 —— 同一主机稳定、不同主机大概率不同，
    比"恒为 0"可靠得多（恒 0 会让多副本**必然**撞号）。
    """
    raw = os.getenv(ENV_WORKER_ID)
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{ENV_WORKER_ID} 必须是整数，当前为 {raw!r}") from exc
        # 越界**显式报错**而不是掩码取低位：掩码会让 1024 与 0 落到同一个
        # workerId（两者都 & 1023 得 0），多副本必然撞号且毫无提示。
        if not 0 <= value <= MAX_WORKER_ID:
            raise ValueError(f"{ENV_WORKER_ID} 必须在 0 ~ {MAX_WORKER_ID} 之间，当前为 {value}")
        return value

    host = os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME") or "localhost"
    digest = hashlib.sha256(host.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") & MAX_WORKER_ID


_generator: SnowflakeGenerator | None = None
_generator_lock = threading.Lock()


def get_generator() -> SnowflakeGenerator:
    """取进程级单例生成器（双检锁）。"""
    global _generator
    if _generator is None:
        with _generator_lock:
            if _generator is None:
                _generator = SnowflakeGenerator(resolve_worker_id())
    return _generator


def next_id() -> int:
    """生成一个雪花 ID（走进程级单例）。"""
    return get_generator().next_id()


def decode(snowflake_id: int) -> tuple[int, int, int]:
    """把雪花 ID 解回 `(unix_ms, worker_id, sequence)`。

    给运维排查用：线上拿到一个 ID，就能立刻知道它由哪个节点、什么时刻生成，
    不必反查日志。
    """
    return (
        (snowflake_id >> _TIMESTAMP_SHIFT) + _EPOCH_MS,
        (snowflake_id >> _WORKER_ID_SHIFT) & MAX_WORKER_ID,
        snowflake_id & MAX_SEQUENCE,
    )
