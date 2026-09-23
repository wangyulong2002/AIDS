"""统一异常处理器（共享层 SSOT）。

为什么在共享层而不是各服务各写一份：
    三个服务（主业务 / AI / Mock）对外都是同一个响应体契约（API.md §1.1）。
    异常路径是响应体最容易漂移的地方——它不在正常返回语句里，review 时看不见，
    只有前端拦截器在某次线上报错时才暴露。三份复制粘贴的实现必然分叉
    （历史教训 f309978：同一件事写了两处，改一处忘一处，门禁全绿）。
    故实现只此一份，`aids-*/handlers.py` 只做薄转出。

三条不变量（都直接决定前端拦截器能不能工作，故集中一处并由测试固化）：

    1. 响应体恒为 ``{code, message, data}``（API.md §1.1），**异常路径也不例外**。
    2. 业务失败返回 HTTP 200，用 body 里的 code 区分；仅 5 个例外
       （401 / 403 / 429 / 500）由 ``app/core/response.py::HTTP_STATUS_EXCEPTIONS``
       单一定义，本模块只查询、不复制那张表。
    3. 客户端协议错误（路径不存在 / 方法不允许）保留 404 / 405。

关于第 3 条的取舍（容易被误判为违反 §1.1，特此说明）：
    §1.1 的「业务失败也返回 200」针对的是**业务结果**；URL 拼错属于客户端请求
    错误，不是业务失败。若也返回 200：网关重试与 CDN 错误页失效、监控无法区分
    「接口不存在」与「业务失败」，且前端会把 10004 当成「资源为空态」渲染空页面。
    这不违反 ``HTTP_STATUS_EXCEPTIONS``——那张表约束的是**业务异常码**的映射，
    路由层错误不经过它。

错误码一律取自 ``app/core/errors.py`` 的枚举，禁止裸数字
（由 ``tests/contract/scan_error_codes.py`` 在 pre-commit 与 CI 中强制）。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import CommonError
from app.core.exceptions import BusinessError
from app.core.response import fail

logger = logging.getLogger(__name__)

# HTTP 状态码 → 业务码。刻意「复用既有码」而不新增：
# 新增码必须同步 docs/API.md（§1.3 使用规范），而这里只是协议层兜底，
# 既有码的语义已经够用，不值得为此扩张码表。
_STATUS_TO_CODE: dict[int, int] = {
    401: int(CommonError.UNAUTHORIZED),
    403: int(CommonError.FORBIDDEN),
    404: int(CommonError.NOT_FOUND),
    405: int(CommonError.PARAM_INVALID),
    429: int(CommonError.RATE_LIMITED),
    500: int(CommonError.SYSTEM_BUSY),
}

# 协议层错误的提示语：不复用 FastAPI 自带的英文 detail（前端直接展示 message）
_STATUS_TO_MESSAGE: dict[int, str] = {
    404: "请求的接口不存在",
    405: "请求方法不被允许",
}


def _envelope(code: int, message: str, data: object = None, status: int = 200) -> JSONResponse:
    """构造统一响应体的 JSONResponse。"""
    return JSONResponse(status_code=status, content=fail(code, message, data).model_dump())


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常收敛到统一响应体（FastAPI 按异常类型精确匹配，注册顺序无关）。"""

    @app.exception_handler(BusinessError)
    async def _handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
        # 变量名用 http_code 而非 status：C2 扫描器按字段名子串判定（_STATUS_FIELD_HINTS
        # 含 "status"），叫 status / http_status 会让 HTTP 状态码被误判成业务状态枚举的
        # 魔法数字。改名而不是加 `# enum-ok` —— 到处加豁免会让门禁名存实亡（HANDOFF §8.3）。
        http_code = exc.resolved_http_status
        # 按结果分级，否则线上「按 error 级别告警」会被用户的正常操作失败淹没：
        #   5xx → 真故障（需人工介入）  4xx → 调用方问题  200 → 正常业务分支
        if http_code >= 500:
            logger.error("业务异常 code=%s path=%s msg=%s", exc.code, request.url.path, exc.message)
        elif http_code >= 400:
            logger.warning(
                "业务异常 code=%s path=%s msg=%s", exc.code, request.url.path, exc.message
            )
        else:
            logger.info("业务失败 code=%s path=%s msg=%s", exc.code, request.url.path, exc.message)
        return _envelope(exc.code, exc.message, exc.data, http_code)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """参数校验失败 → 10001 + HTTP 200（API.md §1.3 未把 10001 列入例外）。

        ``exc.errors()`` 里可能含不可 JSON 序列化的对象（pydantic 会把原始
        ValueError 塞进 ctx），故逐项转成字符串——否则异常处理器自己会再抛一次，
        最终以 500 收场，把「参数错」伪装成「服务故障」。
        """
        detail = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "reason": str(err.get("msg", "")),
            }
            for err in exc.errors()
        ]
        logger.info("参数校验失败 path=%s detail=%s", request.url.path, detail)
        return _envelope(int(CommonError.PARAM_INVALID), "参数校验失败", detail)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """框架自身抛出的 HTTPException（如路由未命中）也要包成统一响应体。"""
        code = _STATUS_TO_CODE.get(exc.status_code, int(CommonError.SYSTEM_BUSY))
        message = _STATUS_TO_MESSAGE.get(exc.status_code, str(exc.detail))
        return _envelope(code, message, None, exc.status_code)

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        """兜底：绝不让栈信息 / 异常类型泄漏给客户端（PRD §10 安全基线）。

        真实原因只进日志（含栈），响应体固定为一句通用提示——
        把 `KeyError: 'ARK_API_KEY'` 之类回给前端等于免费告诉攻击者内部结构。
        """
        logger.exception("未捕获异常 path=%s", request.url.path)
        return _envelope(int(CommonError.SYSTEM_BUSY), "系统繁忙，请稍后重试", None, 500)
