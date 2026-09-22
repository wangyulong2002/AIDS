"""C7 契约测试：统一响应体。

校验链：
    docs/API.md §1.1  ←→  app/core/response.py

为什么重要：
    前端 axios 拦截器、JWT 无感刷新全部依赖这个结构。
    一旦漂移，表现是"前端报未知错误"——排查成本极高。

关键约定（API.md §1.1）：
    1. 响应体恒为 { code, message, data }
    2. code=0 成功，非 0 错误码
    3. **业务失败也返回 HTTP 200**，用 code 区分
    4. 分页：请求 pageNum(从1开始)/pageSize(上限100)；响应 total/list
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import SUCCESS, CommonError
from app.core.response import (
    HTTP_STATUS_EXCEPTIONS,
    PAGE_SIZE_MAX,
    ApiResponse,
    PageData,
    PageQuery,
    fail,
    ok,
    paginated,
)
from tests.contract._doc_parser import API_DOC

pytestmark = pytest.mark.contract


class TestResponseEnvelopeShape:
    """响应体字段名与结构必须与文档一致。"""

    def test_envelope_has_exactly_three_fields(self) -> None:
        assert set(ApiResponse.model_fields) == {"code", "message", "data"}

    def test_required_field_names(self) -> None:
        fields = ApiResponse.model_fields
        assert "code" in fields
        assert "message" in fields
        assert "data" in fields

    def test_ok_helper_shape(self) -> None:
        resp = ok({"id": 1})
        dumped = resp.model_dump()
        assert dumped == {"code": 0, "message": "success", "data": {"id": 1}}

    def test_ok_default_message(self) -> None:
        assert ok().message == "success"

    def test_fail_uses_given_code(self) -> None:
        resp = fail(int(CommonError.PARAM_INVALID), "参数错误")
        assert resp.code == 10001
        assert resp.message == "参数错误"
        assert resp.data is None

    def test_code_zero_means_success(self) -> None:
        """文档：code = 0 表示成功。"""
        assert SUCCESS == 0
        assert ok().code == SUCCESS


class TestPagination:
    """分页结构约定。"""

    def test_page_query_defaults(self) -> None:
        q = PageQuery()
        assert q.pageNum == 1
        assert q.pageSize == 20

    def test_page_num_min_is_one(self) -> None:
        """文档：pageNum 从 1 开始。"""
        with pytest.raises(ValidationError):
            PageQuery(pageNum=0)

    def test_page_size_max_is_100(self) -> None:
        """文档：pageSize 上限 100。"""
        assert PAGE_SIZE_MAX == 100
        PageQuery(pageSize=100)
        with pytest.raises(ValidationError):
            PageQuery(pageSize=101)

    def test_page_data_shape(self) -> None:
        assert set(PageData.model_fields) == {"total", "list"}

    def test_paginated_helper(self) -> None:
        resp = paginated(total=128, items=[{"id": 1}])
        assert resp.code == 0
        assert resp.data is not None
        assert resp.data.total == 128
        assert resp.data.list == [{"id": 1}]

    def test_doc_field_names_match(self) -> None:
        """对照 API.md §1.1 原文的字段名。"""
        text = API_DOC.read_text(encoding="utf-8")
        assert '"pageNum"' in text
        assert '"pageSize"' in text
        assert '"total"' in text
        assert '"list"' in text


class TestHttpStatusSemantics:
    """HTTP 状态码语义（文档明确列举）。"""

    def test_business_failure_status_is_200(self) -> None:
        """核心约定：业务失败也返回 HTTP 200，用 code 区分。

        本测试固化"哪些码例外"；其余全部走 200。
        """
        exceptions = set(HTTP_STATUS_EXCEPTIONS)
        assert exceptions == {10002, 10003, 10005, 10006, 10008}, (
            f"HTTP 状态码例外集合变化：{sorted(exceptions)}；"
            f"若为有意变更，请同步 API.md §1.1 并更新本断言。"
        )

    def test_unauthorized_maps_to_401(self) -> None:
        assert HTTP_STATUS_EXCEPTIONS[int(CommonError.UNAUTHORIZED)] == 401

    def test_forbidden_maps_to_403(self) -> None:
        assert HTTP_STATUS_EXCEPTIONS[int(CommonError.FORBIDDEN)] == 403

    def test_idor_maps_to_403(self) -> None:
        """IDOR 拦截返回 403，且不暴露资源是否存在（API.md §1.3）。"""
        assert HTTP_STATUS_EXCEPTIONS[int(CommonError.DATA_FORBIDDEN)] == 403

    def test_rate_limit_maps_to_429(self) -> None:
        assert HTTP_STATUS_EXCEPTIONS[int(CommonError.RATE_LIMITED)] == 429

    def test_system_busy_maps_to_500(self) -> None:
        assert HTTP_STATUS_EXCEPTIONS[int(CommonError.SYSTEM_BUSY)] == 500

    def test_other_business_errors_are_200(self) -> None:
        """除例外外，业务错误码的 HTTP 状态码应为 200。"""
        business_codes = [
            10001,  # 参数校验失败
            10004,  # 资源不存在
            10007,  # 重复提交
            10009,  # 文件不合法
            20001,  # 手机号格式
            30003,  # 库存不足
            40001,  # 金额校验失败
            70001,  # 优惠券不可用
        ]
        for code in business_codes:
            assert code not in HTTP_STATUS_EXCEPTIONS, f"{code} 不应映射到非 200"

    def test_doc_mentions_200_for_business_failure(self) -> None:
        text = API_DOC.read_text(encoding="utf-8")
        assert "业务失败也返回 200" in text


class TestResponseSerializationStability:
    """响应体序列化稳定（前端拦截器依赖），字段不缺不漏。"""

    def test_dump_always_contains_all_keys(self) -> None:
        for resp in (ok(), ok({"a": 1}), fail(10001, "x")):
            dumped = resp.model_dump()
            assert set(dumped) == {"code", "message", "data"}

    def test_data_defaults_to_none_not_missing(self) -> None:
        assert "data" in ok().model_dump()
        assert ok().model_dump()["data"] is None
