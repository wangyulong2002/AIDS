"""C2 契约测试：状态枚举映射（DDL ↔ PRD ↔ 代码）。

校验链：
    docs/DATA-DICTIONARY.md §一   ←→   app/domain/enums.py

为什么这是最高价值的测试：
    "10 到底是待付款还是待发货" 是电商项目最常见的缺陷来源。
    文档已把三处映射收拢成一张表，本测试确保代码侧永不漂移。

失败意味着：
    - 有人改了文档没改代码，或反过来
    - 修复：两处同步，或检查是否引用了错误的枚举
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

import pytest

from app.domain.enums import ENUM_REGISTRY
from tests.contract._doc_parser import parse_enum_mappings

pytestmark = pytest.mark.contract

DOC_MAPPINGS: dict[str, dict[int, tuple[str, str]]] = parse_enum_mappings()


class TestEnumRegistryCoverage:
    """登记表必须与文档章节一一对应（防漏登记）。"""

    def test_doc_has_expected_group_count(self) -> None:
        """文档 §一 应有 11 组枚举映射（当前基线）。"""
        assert len(DOC_MAPPINGS) == 11, (
            f"文档 §一 枚举映射组数由 11 变为 {len(DOC_MAPPINGS)}；"
            f"若为有意变更，请同步更新本断言与 ENUM_REGISTRY。"
        )

    def test_registry_covers_all_doc_groups(self) -> None:
        """文档里的每一组枚举，代码侧都必须有对应定义。"""
        doc_keys = set(DOC_MAPPINGS)
        reg_keys = set(ENUM_REGISTRY)
        missing = doc_keys - reg_keys
        assert not missing, f"文档有而代码 ENUM_REGISTRY 未登记：{sorted(missing)}"

    def test_registry_has_no_extra_groups(self) -> None:
        """代码侧不得登记文档中不存在的枚举（防臆造）。"""
        doc_keys = set(DOC_MAPPINGS)
        reg_keys = set(ENUM_REGISTRY)
        extra = reg_keys - doc_keys
        assert not extra, f"代码 ENUM_REGISTRY 登记了文档中不存在的枚举：{sorted(extra)}"


@pytest.mark.parametrize("table_field", sorted(ENUM_REGISTRY))
class TestEnumMappingConsistency:
    """逐组比对「值 ↔ 代码常量名」。"""

    def test_values_match_doc(self, table_field: str) -> None:
        """枚举的取值集合必须与文档「值」列完全一致。"""
        enum_cls, _field = ENUM_REGISTRY[table_field]
        doc_values = set(DOC_MAPPINGS[table_field])
        code_values = {int(m.value) for m in enum_cls}

        assert code_values == doc_values, (
            f"{table_field} 值集合不一致："
            f"仅文档有 {sorted(doc_values - code_values)}，"
            f"仅代码有 {sorted(code_values - doc_values)}"
        )

    def test_constant_names_match_doc(self, table_field: str) -> None:
        """枚举成员名必须与文档「PRD 状态名（代码常量）」列逐字一致。

        这是文档明确要求的硬约束：
        「后端枚举常量名必须与「PRD 状态名」列逐字一致」
        """
        enum_cls, _field = ENUM_REGISTRY[table_field]
        mismatches: list[str] = []

        for value, (_db_name, doc_const) in DOC_MAPPINGS[table_field].items():
            member = enum_cls(value)
            if member.name != doc_const:
                mismatches.append(f"  值 {value}: 文档={doc_const} 代码={member.name}")

        assert not mismatches, f"{table_field} 常量名漂移：\n" + "\n".join(mismatches)

    def test_enum_is_int_subclass(self, table_field: str) -> None:
        """状态枚举必须是 IntEnum（DDL 用 TINYINT 存储，需可直接比较）。"""
        enum_cls, _field = ENUM_REGISTRY[table_field]
        assert issubclass(enum_cls, IntEnum), f"{table_field} 的枚举类必须继承 IntEnum"


class TestEnumFieldNaming:
    """ENUM_REGISTRY 的字段名须与表名字符串一致。"""

    @pytest.mark.parametrize("table_field", sorted(ENUM_REGISTRY))
    def test_field_segment_matches(self, table_field: str) -> None:
        _enum_cls, field = ENUM_REGISTRY[table_field]
        assert table_field.endswith(f".{field}"), (
            f"{table_field} 的登记字段名 '{field}' 与字符串不符"
        )


class TestEnumDuplicationGuard:
    """同名枚举值不得在不同枚举类间串用（如 USER 同时出现在 operator_type 与 role）。"""

    def test_shared_names_allowed_but_values_documented(self) -> None:
        """允许同名（如 USER/SYSTEM），但每个都必须能在文档中找到出处。

        这是刻意设计的「弱断言」：跨枚举同名是业务事实（用户/系统在多处出现），
        但要防止有人复制粘贴时把值带错——由上面的逐组比对兜住。
        """
        for table_field, (enum_cls, _field) in ENUM_REGISTRY.items():
            doc = DOC_MAPPINGS[table_field]
            for member in enum_cls:
                assert int(member.value) in doc, (
                    f"{table_field}.{member.name}={member.value} 在文档中无对应行"
                )


def _write_snippet(tmp_path: Path, snippet: str) -> Path:
    """把一段代码写进临时文件，供扫描器回归测试使用。"""
    target = tmp_path / "sample.py"
    target.write_text(snippet + "\n", encoding="utf-8")
    return target


class TestScannerCatchesMagicNumbers:
    """回归：以下写法曾是 C2 扫描器的"指缝"（全部静默通过）。

    为什么要在契约测试里钉住扫描器：门禁的价值全在"拦得住"。
    扫描器的规则一松（例如把 0/1 整体豁免），门禁就退化成心理安慰，
    而**没有任何东西会告诉你它失效了**——除了这组回归用例。
    """

    # 这些必须被拦下
    _EVASIONS = [
        "order.status == 20",  # 属性比较
        "payment.status == 0",  # 0/1 曾是"无语义数字"被整体放行
        'd["status"] == 30',  # 下标比较
        "order.status = 40",  # 属性赋值
        "q.filter(status=20)",  # 关键字参数
        'd["status"] = 30',  # 下标赋值
        "order.status += 10",  # 增量赋值
        "status = 30",  # 裸名赋值
    ]

    # 这些不得误报
    _ALLOWED = [
        "ROLE_ADMIN = 1",  # 全大写常量定义（命名约定）
        "ADJUST_REASON_REQUIRED = 80001",  # 同上：错误码枚举成员
        "order.status == OrderStatus.PAID",
        "total = total + 1",
        "order.status = 1  # enum-ok: 与状态语义无关",
    ]

    @pytest.mark.parametrize("snippet", _EVASIONS)
    def test_scanner_flags(self, tmp_path: Path, snippet: str) -> None:
        from tests.contract.scan_enum_magic_numbers import scan_file

        assert scan_file(_write_snippet(tmp_path, snippet)), f"C2 扫描器漏检：{snippet}"

    @pytest.mark.parametrize("snippet", _ALLOWED)
    def test_scanner_allows(self, tmp_path: Path, snippet: str) -> None:
        from tests.contract.scan_enum_magic_numbers import scan_file

        assert not scan_file(_write_snippet(tmp_path, snippet)), f"C2 扫描器误报：{snippet}"
