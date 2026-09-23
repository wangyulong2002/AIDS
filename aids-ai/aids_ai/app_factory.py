"""FastAPI 应用装配（工厂）。

为什么 `create_app` 与 `main.py` 分成两个文件：
    `main.py` 在**导入时**执行启动断言（S1：配置错误必须拒绝启动）。
    如果测试直接 `import aids_ai.main`，就会把「环境校验」的副作用带进 pytest
    ——开发者 shell 里只要有一个指向生产库的 DATABASE_URL，整个测试进程就会被
    SystemExit 干掉，而报错信息和被测代码毫无关系。故：装配（无副作用，可自由
    导入）与启动（有副作用）分文件。

与主业务服务的关系：
    异常处理实现来自共享层 `app/core/handlers.py`——三个服务的响应体契约
    （API.md §1.1）必须逐字一致，任何一份本地实现都会成为漂移起点。
"""

from __future__ import annotations

from fastapi import FastAPI

from aids_ai.api import api_router
from app.core.config import is_production
from app.core.handlers import register_exception_handlers

SERVICE_NAME = "aids-ai"
API_TITLE = "AIDS AI 智能客服服务"
API_VERSION = "0.1.0"


def create_app() -> FastAPI:
    """装配 FastAPI 应用。无副作用，可重复调用（测试可自由构造实例）。"""
    # 生产环境关闭交互式文档：/docs 会完整暴露接口清单与参数结构，
    # 对未鉴权的攻击者等于免费的资产测绘（PRD §10）。
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
