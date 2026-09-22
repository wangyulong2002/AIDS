"""主业务服务启动入口。

gunicorn / uvicorn 的目标模块路径是 ``aids_backend.main:app``
（``deploy/app/backend.Dockerfile`` 的 CMD 逐字依赖它，改名必须同步改镜像）。

启动顺序不可调换：

    1. ``run_startup_assertions()`` —— 配置硬校验，不通过即 SystemExit
    2. ``create_app()``             —— 装配 FastAPI

为什么断言必须早于 app 构造：
    「测试连生产库」「生产用默认密钥」「缺 ARK_API_KEY」这类配置错误必须在
    **进程启动阶段**终止，不能等第一个请求进来才暴露——那时服务已经在对外
    提供（错误的）服务了。``StartupAssertionError`` 故意继承 ``SystemExit``，
    业务层的 ``except Exception`` 吞不掉它。

本模块有导入副作用（会校验环境变量），测试请改用
``aids_backend.app_factory.create_app``。
"""

from __future__ import annotations

from fastapi import FastAPI

from aids_backend.app_factory import create_app
from app.core.config import run_startup_assertions


def bootstrap() -> FastAPI:
    """先过启动断言，再装配应用。"""
    run_startup_assertions()
    return create_app()


app = bootstrap()
