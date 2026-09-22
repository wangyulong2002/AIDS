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

from pathlib import Path

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


def _write_snippet(tmp_path: Path, snippet: str) -> Path:
    """把一段代码写进临时文件，供扫描器回归测试使用。"""
    target = tmp_path / "sample.py"
    target.write_text(snippet + "\n", encoding="utf-8")
    return target


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


class TestSegmentOwnership:
    """每个声明的非 0 段位都必须有归属枚举类。

    为什么必须断言：API.md §1.2 与 PRD §5.4 声明了 9 个业务段位。
    若某段位在代码里没有归属枚举类，它就成为"文档说存在、代码不认、
    扫描器也扫不到"的**悬空段**——9xxxx 曾长期如此：
    扫描区间止于 89999，该段的硬编码错误码永远漏检。
    """

    def test_every_segment_has_owning_class(self) -> None:
        declared = {seg for seg in ERROR_SEGMENTS if seg != 0}
        owned = set(ERROR_DOMAIN_MAP)
        assert owned == declared, (
            f"段位与枚举类不匹配：声明 {sorted(declared)}，有归属 {sorted(owned)}；"
            f"缺归属 {sorted(declared - owned)}，多余归属 {sorted(owned - declared)}"
        )


class TestScannerCoverage:
    """C4 扫描器的码区间必须覆盖全部分段。"""

    def test_scanner_range_covers_all_segments(self) -> None:
        """区间与 ERROR_SEGMENTS 两处各写一套，必然漂移——这里把它钉住。"""
        from tests.contract.scan_error_codes import _CODE_MAX, _CODE_MIN

        segments = [seg for seg in ERROR_SEGMENTS if seg != 0]
        lowest = min(segments) * 10000 + 1
        highest = max(segments) * 10000 + 9999
        assert lowest >= _CODE_MIN, f"扫描器下界 {_CODE_MIN} 高于最小段位码 {lowest}"
        assert highest <= _CODE_MAX, (
            f"扫描器上界 {_CODE_MAX} 低于最大段位码 {highest}——"
            f"{max(segments)}xxxx 段的硬编码错误码会被静默放过"
        )


class TestScannerCatchesHardcodedCodes:
    """回归：以下写法曾全部从 C4 扫描器的指缝里漏过去。"""

    _EVASIONS = [
        'fail(10001, "参数错误")',  # 位置参数
        '{"code": 40002}',  # 字典字面量
        "raise BizError(50001)",  # 构造式位置参数
        "error_code = 70001",  # 赋值给 code 类变量
        '{"code": 90001}',  # 9xxxx 段（曾整个落在扫描区间之外）
        "{**base, 'error_code': 30003}",  # 展开字典里的 code
    ]

    _ALLOWED = [
        "raise BizError(code=CommonError.PARAM_INVALID)",
        "order_id = 40002",  # 名字不含 code，不是错误码字段
        "timeout = 30000",  # 常见配置值，不得误报
    ]

    @pytest.mark.parametrize("snippet", _EVASIONS)
    def test_scanner_flags(self, tmp_path: Path, snippet: str) -> None:
        from tests.contract.scan_error_codes import scan_file

        assert scan_file(_write_snippet(tmp_path, snippet)), f"C4 扫描器漏检：{snippet}"

    @pytest.mark.parametrize("snippet", _ALLOWED)
    def test_scanner_allows(self, tmp_path: Path, snippet: str) -> None:
        from tests.contract.scan_error_codes import scan_file

        assert not scan_file(_write_snippet(tmp_path, snippet)), f"C4 扫描器误报：{snippet}"


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
