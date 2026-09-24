"""AI 智能客服服务启动入口。

gunicorn 的目标模块路径是 ``aids_ai.main:app``
（``deploy/app/ai.Dockerfile`` 的 CMD 逐字依赖它，改名必须同步改镜像）。

启动顺序不可调换：

    1. ``configure_structured_logging()`` —— 先装 JSON 日志，后面每步都可被采集
    2. ``run_ai_startup_assertions()``    —— 配置硬校验，不通过即 SystemExit
    3. ``create_app()``                   —— 装配 FastAPI

为什么断言必须早于 app 构造：
    本服务是 ``ARK_API_KEY`` 的唯一消费者，S1-c 在生产环境缺 Key 时拒绝启动——
    必须在**进程启动阶段**终止，不能等第一个用户提问才以 500 暴露。
    ``StartupAssertionError`` 故意继承 ``SystemExit``，业务层的
    ``except Exception`` 吞不掉它。

为什么用 ``run_ai_startup_assertions()`` 而不是 ``run_startup_assertions()``：
    AI 服务不连主业务库、不签 JWT、不做字段加密；用全量断言会把它与主业务的
    配置绑死（AI-01 的「配置隔离」）。断言面收窄后，AI 的启动条件 == 它真实依赖。

本模块有导入副作用（会装日志、校验环境变量），测试请改用
``aids_ai.app_factory.create_app``。
"""

from __future__ import annotations

from fastapi import FastAPI

from aids_ai.app_factory import SERVICE_NAME, create_app
from app.core.config import run_ai_startup_assertions
from app.core.logging import configure_structured_logging


def bootstrap() -> FastAPI:
    """先装结构化日志、过启动断言，再装配应用。"""
    configure_structured_logging(SERVICE_NAME)
    run_ai_startup_assertions()
    return create_app()


app = bootstrap()
