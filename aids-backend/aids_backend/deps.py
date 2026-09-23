"""鉴权依赖注入的服务侧转出（实现已上移到共享层）。

为什么是薄转出而不是本文件实现：
    1. 三个服务都要验 JWT、都要取"当前用户"，实现只有一处：
       `app/core/security.py`（S4 扫描器把它列为 userId 的唯一合法来源）。
    2. `tests/api/*` 与 `aids_backend.app_factory` 的既有导入路径保持不变；
       `app.dependency_overrides[get_jwt_keys]` 依赖**同一个函数对象**，
       转出（re-export）不是包装，对象身份不变，覆盖行为不受影响。

需要"用户上下文"的业务代码：
    `from aids_backend.deps import CurrentUser, get_current_user, require_roles`
    取到 `CurrentUser.user_id` 后，行级过滤交给 `app/orm/repository.OwnedRepository`，
    **禁止**再从请求参数里读 userId（静态扫描会拦）。
"""

from __future__ import annotations

from app.core.security import (
    CurrentUser,
    get_current_user,
    get_jwt_keys,
    get_refresh_store,
    require_roles,
    reset_refresh_store,
)

__all__ = [
    "CurrentUser",
    "get_current_user",
    "get_jwt_keys",
    "get_refresh_store",
    "require_roles",
    "reset_refresh_store",
]
