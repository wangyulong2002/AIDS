"""AI 服务路由聚合。

路径口径（重要，别按主业务的习惯想当然）：
    Nginx 的 ``location /api/ai/`` 用的是不带 URI 的 ``proxy_pass http://aids_ai;``
    （``deploy/nginx/conf.d/default.conf``），即**原始 URI 逐字透传**——
    外部访问 ``/api/ai/chat``，到达本服务的路径仍是 ``/api/ai/chat``。
    因此 AI 业务路由的前缀是 ``/api/ai``，而不是 ``/``。

    容器 HEALTHCHECK 打的是容器内的 ``/health``（不经 Nginx），故探针路径不在
    该前缀下。两边路径都由 ``tests/contract/test_service_skeletons.py`` 断言。

当前只有探针；业务路由按 docs/API.md §五 由 T5 落地：
    POST /api/ai/chat         SSE 流式问答（event: message / done / error / handoff）
    知识库、会话、工作台接口见 API.md §3.5 与 TASKS AI-06~AI-18。
"""

from __future__ import annotations

from fastapi import APIRouter

from aids_ai.api import health

api_router = APIRouter()

api_router.include_router(health.router)

# 业务模块路由注册表（不含 health），供契约测试断言「模块 ↔ 前缀」不被静默改动。
# 空不代表可以随手增删：新增模块请同时登记到 docs/API.md §五 的路径下。
MODULE_ROUTERS: dict[str, APIRouter] = {}
