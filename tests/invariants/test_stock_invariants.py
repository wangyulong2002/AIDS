"""C3/C5/C6 不变量测试：库存四段记账、幂等键、退款边界。

权威文档：docs/DATA-DICTIONARY.md §四 数据库约束与不变量
    #1  total_stock = available_stock + locked_stock + sold_stock   ← C5
    #2  库存四段均 >= 0
    #3  refund_amount <= pay_amount
    #4  refunded_amount <= amount
    #5  Σ order_item.discount_amount = order.discount_amount
    #6  同用户仅一个默认地址
    #7  仅一个默认运费模板
    #8  remain_count >= 0
    #9  库存流水幂等键唯一（DDL UNIQUE）                            ← C6
    #10 渠道交易号唯一
    #11 一个订单项一条评价
    #12 本地消息幂等

为什么在 T0 就建这些测试（此时还没业务代码）：
    库存恒等式是电商标配的"账实相符"底线。等写完库存模块再补测试，
    就等于把"验证顺序"倒过来——先写实现再想验证，必然漏。
    现在先定义不变量，实现时以测试为靶子。

本文件用纯内存模型验证"算法级"不变量（不依赖 DB），
DB 级幂等（UNIQUE 索引）由 test_unique_indexes.py 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.enums import StockChangeType

pytestmark = pytest.mark.invariant


# =====================================================================
# 库存四段记账模型（依不变量 #1/#2 与 StockChangeType 的账目影响）
# =====================================================================


@dataclass
class StockLedger:
    """库存四段记账。

    恒等式（不变量 #1）：total = available + locked + sold
    下界（不变量 #2）：四段均 >= 0

    变动语义严格取自 DATA-DICTIONARY §一 `biz_stock_log.change_type`：
        LOCK(1)     下单锁定    available-1, locked+1
        PAY(2)      支付扣减    locked-1,    sold+1
        RELEASE(3)  取消释放    locked-1,    available+1
        ADJUST(4)   手动调整    total+n,     available+n
        RETURN(5)   退货入库    sold-1,      available+1
        REFUND(6)   退款回补    sold-1,      available+1
    """

    total: int = 0
    available: int = 0
    locked: int = 0
    sold: int = 0

    def __post_init__(self) -> None:
        self.check()

    # ---- 不变量 ----

    def check(self) -> None:
        """校验不变量 #1（恒等式）与 #2（非负）。"""
        _validate(
            total=self.total,
            available=self.available,
            locked=self.locked,
            sold=self.sold,
        )

    # ---- 变动 ----

    def apply(self, change: StockChangeType, qty: int = 1) -> None:
        """按变动类型施加账目影响，随后自动校验不变量。

        实现要点：**先算后写（原子性）**。
        不能"直接改字段再校验"——那样一旦校验失败，对象已被改成非法态，
        后续调用者拿到的就是一个坏掉的账本。
        先算出新状态、校验通过、再一次性写回。

        属性测试 test_identity_holds_under_random_sequences 正是靠
        这条契约成立：操作失败时账本必须保持原样。
        """
        new = {
            "total": self.total,
            "available": self.available,
            "locked": self.locked,
            "sold": self.sold,
        }

        if change is StockChangeType.LOCK:
            new["available"] -= qty
            new["locked"] += qty
        elif change is StockChangeType.PAY:
            new["locked"] -= qty
            new["sold"] += qty
        elif change is StockChangeType.RELEASE:
            new["locked"] -= qty
            new["available"] += qty
        elif change is StockChangeType.ADJUST:
            new["total"] += qty
            new["available"] += qty
        elif change in (StockChangeType.RETURN, StockChangeType.REFUND):
            new["sold"] -= qty
            new["available"] += qty
        else:  # pragma: no cover
            raise ValueError(f"未知变动类型: {change}")

        # 先校验候选状态
        _validate(
            total=new["total"],
            available=new["available"],
            locked=new["locked"],
            sold=new["sold"],
        )

        # 校验通过才写回
        self.total = new["total"]
        self.available = new["available"]
        self.locked = new["locked"]
        self.sold = new["sold"]

    def restock(self, qty: int) -> None:
        """初始化/补货：直接调整 total 与 available。"""
        self.apply(StockChangeType.ADJUST, qty)

    def snapshot(self) -> tuple[int, int, int, int]:
        return (self.total, self.available, self.locked, self.sold)


def _validate(*, total: int, available: int, locked: int, sold: int) -> None:
    """校验不变量 #1（恒等式）与 #2（非负）。"""
    assert total == available + locked + sold, (
        f"库存恒等式被破坏: total={total} != "
        f"available({available}) + locked({locked}) + sold({sold})"
    )
    for name, value in (
        ("total", total),
        ("available", available),
        ("locked", locked),
        ("sold", sold),
    ):
        assert value >= 0, f"{name} = {value} < 0（超卖）"


class TestStockInvariantBasics:
    """手工用例。"""

    def test_fresh_ledger_is_consistent(self) -> None:
        led = StockLedger(total=100, available=100, locked=0, sold=0)
        led.check()

    def test_lock_then_pay_preserves_identity(self) -> None:
        led = StockLedger(total=100, available=100)
        led.apply(StockChangeType.LOCK)
        assert (led.available, led.locked, led.sold) == (99, 1, 0)
        led.apply(StockChangeType.PAY)
        assert (led.available, led.locked, led.sold) == (99, 0, 1)

    def test_lock_then_release_restores(self) -> None:
        led = StockLedger(total=100, available=100)
        led.apply(StockChangeType.LOCK)
        led.apply(StockChangeType.RELEASE)
        assert (led.available, led.locked) == (100, 0)

    def test_return_adds_back_to_available(self) -> None:
        led = StockLedger(total=100, available=100)
        led.apply(StockChangeType.LOCK)
        led.apply(StockChangeType.PAY)
        led.apply(StockChangeType.RETURN)
        assert (led.available, led.sold) == (100, 0)

    def test_adjust_increases_total(self) -> None:
        led = StockLedger(total=100, available=100)
        led.apply(StockChangeType.ADJUST, 50)
        assert led.total == 150
        assert led.available == 150

    def test_oversell_is_rejected(self) -> None:
        """不变量 #2：available 不得变负。"""
        led = StockLedger(total=1, available=1)
        led.apply(StockChangeType.LOCK)
        with pytest.raises(AssertionError, match="超卖"):
            led.apply(StockChangeType.LOCK)


class TestStockIdentityDeclaredInDoc:
    """恒等式与文档声明必须一致。"""

    def test_identity_formula_matches_doc(self) -> None:
        from tests.contract._doc_parser import DATA_DICTIONARY

        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        assert "total_stock = available_stock + locked_stock + sold_stock" in text

    def test_all_six_change_types_documented(self) -> None:
        """StockChangeType 的 6 个成员必须都能在文档 §一 找到。"""
        from tests.contract._doc_parser import parse_enum_mappings

        doc = parse_enum_mappings()["biz_stock_log.change_type"]
        doc_values = set(doc)
        code_values = {int(m) for m in StockChangeType}
        assert code_values == doc_values


# =====================================================================
# 属性测试（hypothesis）：随机操作序列压恒等式
# =====================================================================

_CHANGE_STRATEGY = st.sampled_from(list(StockChangeType))


@settings(max_examples=300, deadline=None)
@given(
    restock=st.integers(min_value=10, max_value=500),
    changes=st.lists(_CHANGE_STRATEGY, min_size=1, max_size=40),
)
def test_identity_holds_under_random_sequences(
    restock: int, changes: list[StockChangeType]
) -> None:
    """核心属性：无论什么操作序列，只要不抛异常，恒等式就必须成立。

    注意：操作失败（如超卖）是**允许的**——那是防线在起作用。
    我们要保证的是"一旦操作成功，账必须平"。

    这里用宽松模式：抽到会破坏不变量的操作就跳过该步，
    继续施加后续操作，确保恒等式在整个生命周期内持续成立。
    """
    led = StockLedger(total=restock, available=restock, locked=0, sold=0)

    for change in changes:
        try:
            led.apply(change, 1)
        except AssertionError:
            # 触发防线（如超卖），跳过该步——这本身就是正确行为
            continue
        # 每次成功操作后，恒等式必须成立
        assert led.total == led.available + led.locked + led.sold

    # 循环结束后恒等式仍成立
    led.check()


@settings(max_examples=200, deadline=None)
@given(
    restock=st.integers(min_value=1, max_value=100),
    locks=st.integers(min_value=1, max_value=200),
)
def test_no_oversell_under_excessive_lock(restock: int, locks: int) -> None:
    """超卖防线：锁定次数超过库存时，实际锁定数不得超过 total。

    这是不变量 #2 的直接推论，也是最关键的一条——
    超卖是电商最严重的事故类型。
    """
    led = StockLedger(total=restock, available=restock)
    succeeded = 0
    for _ in range(locks):
        try:
            led.apply(StockChangeType.LOCK)
            succeeded += 1
        except AssertionError:
            break  # 防线生效

    assert succeeded <= restock, f"锁定 {succeeded} 次超过库存 {restock}（超卖）"
    assert led.locked <= led.total
    assert led.available >= 0
    # 关键：防线触发后账本必须保持合法（原子性，不留半改状态）
    led.check()


@settings(max_examples=200, deadline=None)
@given(restock=st.integers(min_value=1, max_value=100))
def test_lock_pay_release_cycle_is_identity(restock: int) -> None:
    """循环不变性：LOCK → RELEASE 之后状态必须回到原点。"""
    led = StockLedger(total=restock, available=restock)
    before = (led.total, led.available, led.locked, led.sold)
    led.apply(StockChangeType.LOCK)
    led.apply(StockChangeType.RELEASE)
    after = (led.total, led.available, led.locked, led.sold)
    assert before == after


@settings(max_examples=200, deadline=None)
@given(restock=st.integers(min_value=1, max_value=100))
def test_lock_pay_return_cycle_is_identity(restock: int) -> None:
    """循环不变性：LOCK → PAY → RETURN 之后状态必须回到原点。"""
    led = StockLedger(total=restock, available=restock)
    before = (led.total, led.available, led.locked, led.sold)
    led.apply(StockChangeType.LOCK)
    led.apply(StockChangeType.PAY)
    led.apply(StockChangeType.RETURN)
    after = (led.total, led.available, led.locked, led.sold)
    assert before == after


# =====================================================================
# C6 幂等键构造规则（DATA-DICTIONARY §四 末尾）
# =====================================================================


@dataclass
class IdempotentKeyBuilder:
    """幂等键构造器。

    文档规则（DATA-DICTIONARY「幂等键构造规则」）：
        下单锁定  LOCK:{orderNo}:{skuId}
        支付扣减  PAY:{orderNo}:{skuId}
        取消释放  RELEASE:{orderNo}:{skuId}
        退货入库  RETURN:{refundNo}:{skuId}
        退款回补  REFUND:{refundNo}:{skuId}
        手动调整  NULL（允许同一 SKU 重复调整）

    注意 RETURN/REFUND 用 refundNo 而非 orderNo——
    是为支持同一订单项多次部分退款。
    """

    @staticmethod
    def _build(prefix: str, no: str, sku_id: int) -> str:
        return f"{prefix}:{no}:{sku_id}"

    def lock(self, order_no: str, sku_id: int) -> str:
        return self._build("LOCK", order_no, sku_id)

    def pay(self, order_no: str, sku_id: int) -> str:
        return self._build("PAY", order_no, sku_id)

    def release(self, order_no: str, sku_id: int) -> str:
        return self._build("RELEASE", order_no, sku_id)

    def return_(self, refund_no: str, sku_id: int) -> str:
        return self._build("RETURN", refund_no, sku_id)

    def refund(self, refund_no: str, sku_id: int) -> str:
        return self._build("REFUND", refund_no, sku_id)

    def adjust(self) -> None:
        return None


class TestIdempotentKeyRules:
    """幂等键格式必须与文档逐字一致（C6）。"""

    def setup_method(self) -> None:
        self.b = IdempotentKeyBuilder()

    def test_lock_key_format(self) -> None:
        assert self.b.lock("SO20260922001", 1001) == "LOCK:SO20260922001:1001"

    def test_pay_key_format(self) -> None:
        assert self.b.pay("SO20260922001", 1001) == "PAY:SO20260922001:1001"

    def test_release_key_format(self) -> None:
        assert self.b.release("SO20260922001", 1001) == "RELEASE:SO20260922001:1001"

    def test_return_key_uses_refund_no(self) -> None:
        """关键：RETURN 用 refundNo，不是 orderNo。"""
        key = self.b.return_("RF20260922001", 1001)
        assert key == "RETURN:RF20260922001:1001"
        assert "SO" not in key, "RETURN 键不得使用 orderNo（须支持多次部分退款）"

    def test_refund_key_uses_refund_no(self) -> None:
        key = self.b.refund("RF20260922001", 1001)
        assert key == "REFUND:RF20260922001:1001"
        assert "SO" not in key

    def test_adjust_key_is_null(self) -> None:
        """手动调整允许重复，幂等键为 NULL。"""
        assert self.b.adjust() is None

    def test_keys_are_unique_across_sku(self) -> None:
        """同一订单不同 SKU 的键必须不同（否则会互相幂等掉）。"""
        assert self.b.lock("SO1", 1001) != self.b.lock("SO1", 1002)

    def test_keys_are_unique_across_prefix(self) -> None:
        """同订单同 SKU 不同操作类型的键必须不同。"""
        keys = {
            self.b.lock("SO1", 1001),
            self.b.pay("SO1", 1001),
            self.b.release("SO1", 1001),
        }
        assert len(keys) == 3

    def test_partial_refunds_have_distinct_keys(self) -> None:
        """核心场景：同一订单项多次部分退款，必须产生不同幂等键。"""
        first = self.b.refund("RF001", 1001)
        second = self.b.refund("RF002", 1001)
        assert first != second, "多次部分退款必须有不同幂等键，否则第二次会被幂等掉"

    def test_doc_rules_all_covered(self) -> None:
        """文档列出的 6 条规则，本测试须全部覆盖。"""
        from tests.contract._doc_parser import DATA_DICTIONARY

        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        for rule in (
            "LOCK:{orderNo}:{skuId}",
            "PAY:{orderNo}:{skuId}",
            "RELEASE:{orderNo}:{skuId}",
            "RETURN:{refundNo}:{skuId}",
            "REFUND:{refundNo}:{skuId}",
        ):
            assert rule in text, f"文档缺少幂等键规则: {rule}"


# =====================================================================
# 退款边界（不变量 #3/#4）—— 资损防线
# =====================================================================


class TestRefundBoundaries:
    """不变量 #3/#4：退款金额不得超过支付金额。"""

    def setup_method(self) -> None:
        self.pay_amount = 19900  # 分

    def assert_refund_allowed(self, refund_amount: int, refunded_amount: int) -> None:
        """模拟应用层校验（BE-27/BE-28）。

        不变量 #3: refund_amount <= pay_amount
        不变量 #4: refunded_amount <= amount
        """
        assert refund_amount <= self.pay_amount, "退款额超过支付额（不变量 #3 违反）"
        assert refunded_amount + refund_amount <= self.pay_amount, (
            "累计退款额超过支付额（不变量 #4 违反）"
        )

    def test_single_full_refund_allowed(self) -> None:
        self.assert_refund_allowed(self.pay_amount, 0)

    def test_over_refund_rejected(self) -> None:
        with pytest.raises(AssertionError, match="不变量 #3"):
            self.assert_refund_allowed(self.pay_amount + 1, 0)

    def test_cumulative_over_refund_rejected(self) -> None:
        """已退一半后再退超过剩余的部分，必须拒绝。"""
        half = self.pay_amount // 2
        with pytest.raises(AssertionError, match="不变量 #4"):
            self.assert_refund_allowed(half + 1, half)

    def test_partial_refunds_sum_within_limit(self) -> None:
        part = self.pay_amount // 3
        self.assert_refund_allowed(part, 0)
        self.assert_refund_allowed(part, part)
        self.assert_refund_allowed(part, part * 2)

    def test_doc_declares_both_invariants(self) -> None:
        from tests.contract._doc_parser import DATA_DICTIONARY

        text = DATA_DICTIONARY.read_text(encoding="utf-8")
        assert "refund_amount <= pay_amount" in text
        assert "refunded_amount <= amount" in text


# =====================================================================
# 优惠分摊（不变量 #5）
# =====================================================================


class TestDiscountAllocation:
    """不变量 #5：Σ order_item.discount_amount = order.discount_amount。"""

    @staticmethod
    def largest_remainder(total_discount: int, weights: list[int]) -> list[int]:
        """最大余数法分摊，保证分毫不差（BE-19 分摊算法）。

        这是经典的"分摊后合计必须等于原值"问题——
        浮点或简单四舍五入都会丢分。用整数运算 + 余数补足。
        """
        weight_sum = sum(weights)
        if weight_sum == 0:
            return [0] * len(weights)

        raw = [total_discount * w // weight_sum for w in weights]
        remainder = total_discount - sum(raw)

        # 按小数部分从大到小补足余数
        fractions = [(total_discount * w % weight_sum, i) for i, w in enumerate(weights)]
        fractions.sort(key=lambda x: (-x[0], x[1]))
        for _, idx in fractions[:remainder]:
            raw[idx] += 1

        return raw

    def test_allocation_sums_exactly(self) -> None:
        items = [3000, 5000, 2000]
        result = self.largest_remainder(100, items)
        assert sum(result) == 100, f"分摊后合计 {sum(result)} != 100"

    @settings(max_examples=300, deadline=None)
    @given(
        total=st.integers(min_value=1, max_value=100000),
        weights=st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=20),
    )
    def test_allocation_never_loses_a_cent(self, total: int, weights: list[int]) -> None:
        """属性：任意金额、任意权重组合，分摊合计必须精确等于原值。"""
        result = self.largest_remainder(total, weights)
        assert sum(result) == total, f"分摊丢失: {total} -> {sum(result)}"
        assert len(result) == len(weights)

    @settings(max_examples=200, deadline=None)
    @given(
        total=st.integers(min_value=0, max_value=10000),
        weights=st.lists(st.integers(min_value=0, max_value=10000), min_size=1, max_size=10),
    )
    def test_allocation_is_non_negative(self, total: int, weights: list[int]) -> None:
        result = self.largest_remainder(total, weights)
        assert all(x >= 0 for x in result), f"分摊出现负数: {result}"


# =====================================================================
# 超发券防线（不变量 #8）
# =====================================================================


@dataclass
class CouponRemain:
    """优惠券剩余量（不变量 #8：remain_count >= 0，原子扣减）。"""

    remain_count: int
    issued: int = field(default=0)

    def atomic_claim(self) -> bool:
        """模拟 `UPDATE ... WHERE remain_count >= 1`。

        返回是否领取成功。绝不出现 remain_count < 0。
        """
        if self.remain_count < 1:
            return False
        self.remain_count -= 1
        self.issued += 1
        return True


class TestCouponNoOverIssue:
    """不变量 #8：券不得超发。"""

    def test_claim_stops_at_zero(self) -> None:
        c = CouponRemain(remain_count=3)
        results = [c.atomic_claim() for _ in range(10)]
        assert results == [True, True, True] + [False] * 7
        assert c.remain_count == 0
        assert c.issued == 3

    @settings(max_examples=200, deadline=None)
    @given(
        total=st.integers(min_value=0, max_value=50),
        attempts=st.integers(min_value=1, max_value=200),
    )
    def test_never_negative(self, total: int, attempts: int) -> None:
        c = CouponRemain(remain_count=total)
        for _ in range(attempts):
            c.atomic_claim()

        assert c.remain_count >= 0, "券剩余量变负（超发）"
        # 不变量 #8 的核心：发放数与剩余数之和恒等于初始库存
        assert c.issued + c.remain_count == total, (
            f"券账不平: issued({c.issued}) + remain({c.remain_count}) != total({total})"
        )
        # 领取次数足够时，应恰好发完（不多不少）
        if attempts >= total:
            assert c.issued == total, "领取次数充足时应当正好发完"
