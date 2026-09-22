"""健康检查。

路径为什么是 `/health`（而不是 /healthz、/ping）：
    `deploy/app/*.Dockerfile` 的 HEALTHCHECK 写死了
        CMD curl -sf http://localhost:{port}/health || exit 1
    两边不一致时容器会被持续标成 unhealthy，而这类「名字对不上」不会有任何
    编译或类型错误——只能靠测试固化。由 `tests/api/test_route_contract.py`
    比对 Dockerfile 与本文件的路径。

只暴露「活着」与当前环境：不返回版本号/依赖连通性/构建时间，
避免探针变成信息泄露面（PRD §10 安全基线）。探针是**未鉴权**接口。
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
