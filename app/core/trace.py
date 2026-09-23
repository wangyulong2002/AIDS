"""traceId 全链路（BE-05）。

链路：Nginx 生成/透传 `X-Trace-Id` → 本中间件接收（可信则沿用，否则新生成）→
写进 `ContextVar` → 业务代码/日志/下游调用随取随用 → 响应头回传（前端错误上报携带）。

为什么用**纯 ASGI 中间件**而不是 `BaseHTTPMiddleware`：
    BaseHTTPMiddleware 会把响应包装一层，对 `StreamingResponse`（AI 客服的 SSE）
    与后台任务有已知的缓冲/时序副作用。三个服务共用本中间件，其中 AI 服务是
    流式响应 —— 必须选不碰响应体的实现。

为什么 traceId 用 ContextVar 而不是 request.state：
    `request.state` 只在请求处理函数里可达；日志、Kafka 消息头、本地消息表的
    补偿任务都可能在"没有 request 对象"的上下文里产生（如投递循环），
    ContextVar 随任务上下文传播，取值不需要层层传参。

可信判定：只接受 `8~64` 位的十六进制/连字符串。外部传入的任意字符串
（超长、特殊字符）会进日志与下游消息头，是注入面 —— 不合规格就重新生成。
"""

from __future__ import annotations

import contextvars
import re
import uuid
from typing import Any, Final

from starlette.datastructures import Headers, MutableHeaders

TRACE_HEADER: Final[str] = "X-Trace-Id"

# 可信的 traceId 形态：8~64 位十六进制/连字符（nginx $request_id 是 32 位 hex）
_VALID_TRACE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F-]{8,64}$")

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def current_trace_id() -> str | None:
    """当前请求的 traceId（无请求上下文 / 未经过中间件时为 None）。"""
    return _trace_id.get()


def new_trace_id() -> str:
    """生成 32 位十六进制 traceId（与 Nginx `$request_id` 同形态）。"""
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> contextvars.Token[str | None]:
    """显式设置 traceId（投递循环等非 HTTP 上下文使用；用完请 reset）。"""
    return _trace_id.set(trace_id)


def reset_trace_id(token: contextvars.Token[str | None]) -> None:
    _trace_id.reset(token)


class TraceIdMiddleware:
    """读取/生成 traceId → 写入 ContextVar → 回传响应头（纯 ASGI，不改响应体）。"""

    def __init__(self, app: Any) -> None:  # noqa: ANN401 - ASGI 应用无公共类型
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(TRACE_HEADER)
        trace_id = incoming if incoming and _VALID_TRACE.match(incoming) else new_trace_id()

        token = _trace_id.set(trace_id)

        async def send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(TRACE_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _trace_id.reset(token)
