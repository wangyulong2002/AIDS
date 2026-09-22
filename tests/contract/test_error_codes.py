"""C4 契约测试：错误码与 API.md 一致 + 分段正确。

校验链：
    docs/API.md §1.2 分段表 + §1.3 明细 + 各业务模块码表
        ←→  app/core/errors.py

为什么重要：
    bysj 的常见病是"所有异常都返回 10008"，导致前端无法区分。
    API.md 明确要求：「后端抛业务异常时必须指定错误码，禁止全部返回 10008」。

失败意味着：
    - 代码里用了文档未登记的码（前端拿到未知码无法处理）
    - 码放错了段位（如把订单错误写成 2xxxx）
    - 文档新增了码但代码没跟上（或反之）
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    ERROR_DOMAIN_MAP,
    ERROR_SEGMENTS,
    SUCCESS,
    all_error_classes,
    segment_of,
)
from tests.contract._doc_parser import parse_api_error_codes, parse_error_segments

pytestmark = pytest.mark.contract

DOC_CODES: dict[int, str] = parse_api_error_codes()
DOC_SEGMENTS: dict[int, str] = parse_error_segments()


def _code_to_class() -> dict[int, str]:
    """构建 { 错误码: 枚举类名.成员名 } 全量映射。"""
    out: dict[int, str] = {}
    for cls in all_error_classes():
        for member in cls:
            out[int(member.value)] = f"{cls.__name__}.{member.name}"
    return out


CODE_INDEX: dict[int, str] = _code_to_class()


class TestSegmentsMatchDoc:
    """分段表（§1.2）必须与代码常量逐字一致。"""

    def test_segment_count(self) -> None:
        assert len(DOC_SEGMENTS) == 10, f"API.md 分段表应有 10 段，实为 {len(DOC_SEGMENTS)}"

    def test_segments_equal(self) -> None:
        assert ERROR_SEGMENTS == DOC_SEGMENTS, (
            "错误码分段表不一致：\n"
            f"  仅文档: { {k: v for k, v in DOC_SEGMENTS.items() if ERROR_SEGMENTS.get(k) != v} }\n"
            f"  仅代码: { {k: v for k, v in ERROR_SEGMENTS.items() if DOC_SEGMENTS.get(k) != v} }"
        )


class TestCodesCoveredByDoc:
    """代码里定义的每个错误码都必须在 API.md 中出现。"""

    def test_no_undocumented_codes(self) -> None:
        undocumented = {code: name for code, name in CODE_INDEX.items() if code not in DOC_CODES}
        assert not undocumented, (
            "以下错误码已在代码中定义但 API.md 未登记（新增码须同步文档）：\n"
            + "\n".join(f"  {code} ({name})" for code, name in sorted(undocumented.items()))
        )

    def test_no_doc_codes_missing_in_code(self) -> None:
        """API.md 中登记的业务错误码，代码侧必须有常量（防漏实现）。"""
        missing = {code: desc for code, desc in DOC_CODES.items() if code not in CODE_INDEX}
        assert not missing, "以下错误码已在 API.md 登记但代码未定义：\n" + "\n".join(
            f"  {code} ({desc})" for code, desc in sorted(missing.items())
        )


@pytest.mark.parametrize("code", sorted(CODE_INDEX))
class TestCodeSegmentCorrectness:
    """每个错误码的段位必须与其所属域一致。"""

    def test_segment_matches_owning_class(self, code: int) -> None:
        owning = CODE_INDEX[code]
        cls = next(c for c in all_error_classes() if owning.startswith(c.__name__))
        expected_segment = next(seg for seg, dom_cls in ERROR_DOMAIN_MAP.items() if dom_cls is cls)
        actual = segment_of(code)
        assert actual == expected_segment, (
            f"{owning} 的码 {code} 段位应为 {expected_segment}xxxx，实际 {actual}xxxx"
        )

    def test_code_exists_in_doc(self, code: int) -> None:
        assert code in DOC_CODES, f"{CODE_INDEX[code]} 的码 {code} 未在 API.md 登记"


class TestSuccessCode:
    """成功码恒为 0。"""

    def test_success_is_zero(self) -> None:
        assert SUCCESS == 0

    def test_zero_not_in_segments(self) -> None:
        assert DOC_SEGMENTS[0] == "成功"


class TestErrorCodeHygiene:
    """错误码卫生检查。"""

    def test_no_duplicate_codes_across_classes(self) -> None:
        """同一码不得在两个枚举类里重复定义。"""
        seen: dict[int, str] = {}
        dups: list[str] = []
        for cls in all_error_classes():
            for member in cls:
                code = int(member.value)
                if code in seen:
                    dups.append(f"  {code}: {seen[code]} 与 {cls.__name__}.{member.name}")
                seen[code] = f"{cls.__name__}.{member.name}"
        assert not dups, "错误码跨枚举类重复：\n" + "\n".join(dups)

    def test_all_codes_are_five_digits(self) -> None:
        """所有业务错误码（非 0）必须是 5 位。"""
        bad = [code for code in CODE_INDEX if not (10000 <= code <= 99999)]
        assert not bad, f"以下错误码不是 5 位：{bad}"

    def test_doc_codes_are_five_digits(self) -> None:
        bad = [code for code in DOC_CODES if not (10000 <= code <= 99999)]
        assert not bad, f"API.md 中出现非 5 位码：{bad}"

    def test_system_busy_is_not_catch_all(self) -> None:
        """反模式护栏：10008 不得被命名为过于宽泛的名字，提醒别当兜底码用。

        API.md：「禁止全部返回 10008」。本断言确保它保持单一语义。
        """
        from app.core.errors import CommonError

        assert CommonError.SYSTEM_BUSY.name == "SYSTEM_BUSY"
        assert int(CommonError.SYSTEM_BUSY.value) == 10008
