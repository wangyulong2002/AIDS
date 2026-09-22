"""统一响应体（SSOT）。

权威文档：docs/API.md §1.1 请求与响应 / PRD §5.4

约定：
    统一响应体：{ "code": 0, "message": "success", "data": { } }
    - code = 0 表示成功，非 0 为错误码
    - HTTP 状态码语义：
        200  业务成功 / 业务失败（**业务失败也返回 200，用 code 区分**）
        401  未认证
        403  无权限
        429  限流
        500  系统异常

    统一分页：
        请求 { "pageNum": 1, "pageSize": 20 }   // pageNum 从 1 开始, pageSize 上限 100
        响应 { "total": 128, "list": [ ] }

由 tests/contract/test_response_envelope.py 校验。
"""

from __future__ import annotations

import builtins
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import SUCCESS

T = TypeVar("T")

# 分页上限（API.md §1.1）
PAGE_SIZE_MAX: int = 100
PAGE_NUM_MIN: int = 1


class ApiResponse(BaseModel, Generic[T]):
    """统一响应体。

    所有 HTTP 接口（含 AI SSE 的非流式接口）必须返回本结构。
    """

    code: int = Field(default=SUCCESS, description="0=成功，非 0 为错误码")
    message: str = Field(default="success", description="提示信息")
    data: T | None = Field(default=None, description="业务数据")


class PageData(BaseModel, Generic[T]):
    """统一分页数据体（放在 ApiResponse.data 内）。

    注意：内部字段 `list` 与内置 `list` 同名，且泛型参数 T 需要在
    模块级可解析。必须用 `typing.List` 显式标注，避免 pydantic
    在解析 `list[T]` 时把字段名 `list`（FieldInfo）当作类型构造器。

    model_config 的 arbitrary_types_allowed 与 defer_build 无关，
    真正要点是：模块级 __getattr__ 不介入，T 保持可解析。
    """

    model_config = ConfigDict(arbitrary_types_allowed=False)

    total: int = Field(ge=0, description="总条数")
    # 用 typing.List 避免与字段名 list 冲突
    list: builtins.list[T] = Field(default_factory=list, description="当前页数据")


class PageQuery(BaseModel):
    """统一分页请求参数。"""

    pageNum: int = Field(default=1, ge=PAGE_NUM_MIN, description="页码，从 1 开始")
    pageSize: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX, description="每页条数，上限 100")


def ok(data: Any = None, message: str = "success") -> ApiResponse:
    """构造成功响应（code=0，HTTP 200）。"""
    return ApiResponse(code=SUCCESS, message=message, data=data)


def fail(code: int, message: str, data: Any = None) -> ApiResponse:
    """构造业务失败响应。

    注意：**业务失败也返回 HTTP 200**（API.md §1.1），用 code 区分。
    仅未认证(401)/无权限(403)/限流(429)/系统异常(500) 使用非 200 HTTP 状态码。
    """
    return ApiResponse(code=code, message=message, data=data)


def paginated(total: int, items: list[Any]) -> ApiResponse:
    """构造分页成功响应。"""
    return ok(data=PageData(total=total, list=items))


# 业务失败仍返回 HTTP 200 的错误码段位白名单之外的例外：
# 这些错误码对应的 HTTP 状态码必须为非 200。
HTTP_STATUS_EXCEPTIONS: dict[int, int] = {
    10002: 401,  # 未登录或 Token 失效
    10003: 403,  # 无权限访问
    10005: 403,  # 数据越权（IDOR 拦截）
    10006: 429,  # 请求过于频繁
    10008: 500,  # 系统繁忙
}
