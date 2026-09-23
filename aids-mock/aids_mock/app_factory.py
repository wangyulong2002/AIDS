"""FastAPI 应用装配（工厂）。

为什么 `create_app` 与 `main.py` 分成两个文件：
    `main.py` 在**导入时**执行启动断言（S1：配置错误必须拒绝启动）。
    测试若直接 `import aids_mock.main`，会把「环境校验」的副作用带进 pytest。
    故：装配（无副作用，可自由导入）与启动（有副作用）分文件。

与主业务服务的关系：
    异常处理实现来自共享层 `app/core/handlers.py`——Mock 是**被主业务调用**的
    服务，它的报错格式若不统一，主业务的渠道调用分支就会多出一套解析逻辑。
    三个服务共用一份实现，是本服务骨架期就该定下的事。
"""

from __future__ import annotations

from fastapi import FastAPI

from aids_mock.api import api_router
from app.core.config import is_production
from app.core.handlers import register_exception_handlers

SERVICE_NAME = "aids-mock"
API_TITLE = "AIDS Mock 渠道服务"
API_VERSION = "0.1.0"


def create_app() -> FastAPI:
    """装配 FastAPI 应用。无副作用，可重复调用（测试可自由构造实例）。"""
    docs_enabled = not is_production()

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app
