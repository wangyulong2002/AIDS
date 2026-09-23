"""统一异常处理器的服务侧转出（实现已上移到共享层）。

为什么是薄转出而不是本文件实现：
    三个服务对外共用同一个响应体契约（API.md §1.1），异常路径尤其容易分叉
    ——它不在正常返回语句里，只有前端拦截器在报错时才暴露。
    实现只有一处：``app/core/handlers.py``；三份复制粘贴必然有一份忘记同步。

保留本模块的两个理由：
    1. 既有调用方（`aids_backend.app_factory`、`tests/api/*`）的导入路径不变；
    2. 服务装配处读起来仍是「本服务的 handlers」，跨服务实现留在 app/ 里。
"""

from __future__ import annotations

from app.core.handlers import register_exception_handlers

__all__ = ["register_exception_handlers"]
