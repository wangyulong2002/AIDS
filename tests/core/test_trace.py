"""BE-05 · traceId 全链路的单元测试。

三个服务共用 `TraceIdMiddleware`（纯 ASGI，不碰响应体 —— AI 服务的 SSE 依赖这一点）：
    1. 无头 → 生成 32 位十六进制并回传；
    2. 合法头 → 原样透传（全链路串起来的前提）；
    3. 非法头 → 重新生成（traceId 会进日志与下游消息头，是注入面）；
    4. 路由内 `current_trace_id()` 与响应头一致。
"""

from __future__ import annotations

import re
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.core.response import ApiResponse, ok
from app.core.trace import TRACE_HEADER, current_trace_id, new_trace_id

pytestmark = [pytest.mark.unit]

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _app() -> TestClient:
    """真实 app（覆盖接线是否正确）+ 一个把 traceId 回显出来的探针路由。"""
    from aids_backend.app_factory import create_app

    router = APIRouter()

    @router.get("/probe/trace")
    async def _trace(current: Annotated[str | None, Depends(current_trace_id)]) -> ApiResponse:
        return ok({"trace_id": current})

    app = create_app()
    app.include_router(router)
    return TestClient(app)


def test_new_trace_id_is_32_hex() -> None:
    assert _HEX32.match(new_trace_id())


def test_middleware_generates_and_returns_trace_id() -> None:
    response = _app().get("/probe/trace")
    assert response.status_code == 200
    returned = response.headers[TRACE_HEADER]
    assert _HEX32.match(returned), f"traceId 应为 32 位十六进制：{returned}"
    assert response.json()["data"]["trace_id"] == returned, "路由内取到的 traceId 与响应头不一致"


def test_incoming_trace_id_is_preserved() -> None:
    """全链路串接的前提：外部给的合法 traceId 必须原样沿用。"""
    client = _app()
    first = client.get("/probe/trace", headers={TRACE_HEADER: "abc-def-12345678"})
    assert first.headers[TRACE_HEADER] == "abc-def-12345678"


@pytest.mark.parametrize("bad", ["", "x" * 100, "a;drop table", "trace id with space", "../../etc"])
def test_untrusted_trace_id_is_replaced(bad: str) -> None:
    # 非 ASCII 头在 HTTP 编码层就发不出去，故只测"能进来的非法形态"
    response = _app().get("/probe/trace", headers={TRACE_HEADER: bad})
    assert _HEX32.match(response.headers[TRACE_HEADER]), "非法 traceId 必须被替换而非透传"


def test_trace_id_available_without_request_object() -> None:
    """ContextVar 语义：中间件设置后，非参数注入的取值点也能拿到。"""
    from app.core import trace as trace_mod

    assert trace_mod.current_trace_id() is None  # 模块级默认（测试进程无请求上下文）
    token = trace_mod.set_trace_id("a" * 32)
    try:
        assert trace_mod.current_trace_id() == "a" * 32
    finally:
        trace_mod.reset_trace_id(token)
    assert trace_mod.current_trace_id() is None
