"""雪花 ID 生成器的不变量测试。

为什么这些性质必须被测（而不是"看着对就行"）：
    ID 生成器是整个数据层的**地基**。它静默出错（撞号）时，症状是
    "主键冲突"出现在毫不相关的业务模块里，排查成本极高；
    而"时钟回拨"与"序列号用尽"这两个分支，正常情况下几乎永远不会走到。
"""

from __future__ import annotations

import threading

import pytest

from app.orm.snowflake import (
    ENV_WORKER_ID,
    MAX_SEQUENCE,
    MAX_WORKER_ID,
    ClockMovedBackwardsError,
    SnowflakeGenerator,
    decode,
    get_generator,
    next_id,
    resolve_worker_id,
)

pytestmark = [pytest.mark.invariant, pytest.mark.task("BE-02")]

# 任意一个"当前附近"的毫秒值，用作冻结时钟的基准
_BASE_MS = 1_800_000_000_000


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, values: list[int]) -> None:
    """把生成器的时钟换成一串预设值（用于测回拨 / 序列用尽）。"""
    it = iter(values)
    monkeypatch.setattr(SnowflakeGenerator, "now_ms", staticmethod(lambda: next(it)))


class TestUniqueness:
    def test_sequential_ids_are_unique(self) -> None:
        gen = SnowflakeGenerator(0)
        ids = [gen.next_id() for _ in range(10_000)]
        assert len(set(ids)) == len(ids)

    def test_no_collision_across_threads(self) -> None:
        """并发是主键冲突最容易漏测的场景：多线程共享一个生成器。"""
        gen = SnowflakeGenerator(7)
        collected: list[int] = []
        lock = threading.Lock()

        def produce(count: int) -> None:
            local = [gen.next_id() for _ in range(count)]
            with lock:
                collected.extend(local)

        threads = [threading.Thread(target=produce, args=(2_000,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(collected) == 16_000
        assert len(set(collected)) == len(collected), "并发下出现重复 ID"

    def test_different_workers_never_collide_in_same_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同一毫秒内不同 workerId 也必须产出不同 ID（位域不重叠）。"""
        _freeze_clock(monkeypatch, [_BASE_MS] * 4)
        a = SnowflakeGenerator(0)
        b = SnowflakeGenerator(MAX_WORKER_ID)
        assert a.next_id() != b.next_id()
        assert a.next_id() != b.next_id()


class TestMonotonicity:
    def test_ids_increase_within_one_generator(self) -> None:
        gen = SnowflakeGenerator(3)
        ids = [gen.next_id() for _ in range(1_000)]
        assert ids == sorted(ids), "同实例产出的 ID 必须单调递增"
        assert len(set(ids)) == len(ids)


class TestBitLayout:
    def test_id_fits_in_signed_63_bits(self) -> None:
        """最高位必须恒为 0，否则落到 BIGINT 负区间（列是 UNSIGNED 但别处可能是有符号）。"""
        gen = SnowflakeGenerator(MAX_WORKER_ID)
        for _ in range(1_000):
            assert 0 <= gen.next_id() < (1 << 63)

    def test_components_decode_to_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _freeze_clock(monkeypatch, [_BASE_MS] * 8)
        gen = SnowflakeGenerator(511)
        first = gen.next_id()
        second = gen.next_id()

        assert decode(first) == (_BASE_MS, 511, 0)
        assert decode(second) == (_BASE_MS, 511, 1)

    def test_worker_id_is_isolated_to_its_bit_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _freeze_clock(monkeypatch, [_BASE_MS] * 4)
        low = decode(SnowflakeGenerator(0).next_id())
        high = decode(SnowflakeGenerator(MAX_WORKER_ID).next_id())
        assert low[1] == 0
        assert high[1] == MAX_WORKER_ID
        assert low[0] == high[0], "workerId 不得污染时间戳位"


class TestSequenceExhaustion:
    def test_rolls_over_to_next_millisecond(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一毫秒发满 2^12 个后，必须自动等到下一毫秒而不是撞号。"""
        # 4097 次取号（第 4097 次使序列由 4095 进位归零）+ 自旋时读到下一毫秒
        values = [_BASE_MS] * (MAX_SEQUENCE + 2) + [_BASE_MS + 1]
        _freeze_clock(monkeypatch, values)

        gen = SnowflakeGenerator(1)
        ids = [gen.next_id() for _ in range(MAX_SEQUENCE + 2)]

        assert len(set(ids)) == len(ids), "序列号进位时出现重复 ID"
        last_ms, _worker, last_seq = decode(ids[-1])
        assert last_ms == _BASE_MS + 1
        assert last_seq == 0


class TestClockBackwards:
    def test_small_backwards_is_absorbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """小幅回拨（NTP 微调）应等待过去并继续发号，而不是直接失败。"""
        #        第1次      第2次(回拨3ms)  第3次(sleep后重读)
        _freeze_clock(monkeypatch, [_BASE_MS, _BASE_MS - 3, _BASE_MS])
        gen = SnowflakeGenerator(1)

        first = gen.next_id()
        second = gen.next_id()  # 触发回拨分支

        assert second > first
        assert decode(second)[0] == _BASE_MS

    def test_large_backwards_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """大幅回拨必须硬失败 —— 继续发号会产生重复 ID。"""
        _freeze_clock(monkeypatch, [_BASE_MS, _BASE_MS - 10_000])
        gen = SnowflakeGenerator(1)
        gen.next_id()

        with pytest.raises(ClockMovedBackwardsError, match="时钟回拨"):
            gen.next_id()


class TestWorkerIdValidation:
    @pytest.mark.parametrize("bad", [-1, MAX_WORKER_ID + 1, 99_999])
    def test_out_of_range_rejected(self, bad: int) -> None:
        with pytest.raises(ValueError, match="worker_id 必须在"):
            SnowflakeGenerator(bad)

    def test_env_var_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_WORKER_ID, "42")
        assert resolve_worker_id() == 42

    def test_env_var_must_be_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_WORKER_ID, "abc")
        with pytest.raises(ValueError, match="必须是整数"):
            resolve_worker_id()

    def test_env_var_out_of_range_raises_instead_of_masking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """越界必须报错：静默掩码会让 1024 与 0 落到同一 workerId 而撞号。"""
        monkeypatch.setenv(ENV_WORKER_ID, str(MAX_WORKER_ID + 1))
        with pytest.raises(ValueError, match="必须在 0 ~"):
            resolve_worker_id()

    def test_fallback_is_stable_and_in_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_WORKER_ID, raising=False)
        first = resolve_worker_id()
        assert first == resolve_worker_id(), "主机名派生必须稳定"
        assert 0 <= first <= MAX_WORKER_ID


class TestModuleLevelEntryPoint:
    def test_next_id_uses_a_singleton(self) -> None:
        """进程级入口必须复用同一个生成器 —— 每次 new 会各自维护序列号而撞号。"""
        assert get_generator() is get_generator()
        ids = [next_id() for _ in range(100)]
        assert len(set(ids)) == len(ids)

    def test_decoded_id_is_near_now(self) -> None:
        """端到端：产出的 ID 解出来应当就是"刚刚"。"""
        import time

        ms, worker_id, _seq = decode(next_id())
        assert abs(ms - time.time_ns() // 1_000_000) < 5_000
        assert 0 <= worker_id <= MAX_WORKER_ID
