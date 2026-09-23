"""健康检查。

路径为什么是 `/health`（而不是 /healthz、/ping）：
    `deploy/app/mock.Dockerfile` 的 HEALTHCHECK 写死了
        CMD curl -sf http://localhost:8081/health || exit 1
    两边不一致时容器会被持续标成 unhealthy，而这类「名字对不上」不会有任何
    编译或类型错误——只能靠测试固化。

data 的字段集合与其余两个服务一致（恰好 `status`/`env` 两个键）：
    探针是**未鉴权**接口，不返回版本号/依赖连通性/构建时间（PRD §10）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_env
from app.core.response import ApiResponse, ok

router = APIRouter(tags=["健康检查"])


@router.get("/health", summary="存活探针")
def health() -> ApiResponse:
    """存活探针。容器编排与负载均衡依赖它。"""
    return ok({"status": "up", "env": get_env()})
