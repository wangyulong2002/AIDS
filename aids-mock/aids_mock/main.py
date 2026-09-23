"""Mock 渠道服务启动入口。

gunicorn 的目标模块路径是 ``aids_mock.main:app``
（``deploy/app/mock.Dockerfile`` 的 CMD 逐字依赖它，改名必须同步改镜像）。

启动顺序不可调换：

    1. ``run_startup_assertions()`` —— 配置硬校验，不通过即 SystemExit
    2. ``create_app()``             —— 装配 FastAPI

本模块有导入副作用（会校验环境变量），测试请改用
``aids_mock.app_factory.create_app``。
"""

from __future__ import annotations

from fastapi import FastAPI

from aids_mock.app_factory import create_app
from app.core.config import run_startup_assertions


def bootstrap() -> FastAPI:
    """先过启动断言，再装配应用。"""
    run_startup_assertions()
    return create_app()


app = bootstrap()
