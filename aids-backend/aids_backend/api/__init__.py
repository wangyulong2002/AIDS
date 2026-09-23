"""路由聚合。

模块 → 前缀映射（与 docs/API.md 的路径逐条对应，**禁止随手新增根前缀**）：

    | 模块      | prefix     | 文档位置        |
    |-----------|------------|-----------------|
    | health    | （无）     | 部署探针        |
    | jwks      | （无）     | `/.well-known/jwks.json`（BE-03，跨服务验签） |
    | auth      | /auth      | API.md §2.1     |
    | user      | /user      | API.md §2.1     |
    | product   | /product   | API.md §2.2     |
    | order     | /order     | API.md §2.4     |
    | pay       | /payment   | API.md §2.5     |
    | marketing | /coupon    | API.md §2.7     |
    | admin     | /admin     | API.md §3.1~3.5 |
    | internal  | /internal  | API.md §四（BE-06，仅内网） |

本映射由 `tests/api/test_route_contract.py` 断言。改前缀必须同步改文档与测试
两处——否则会出现「文档写 /coupon、代码挂 /marketing」这类漂移，前端联调时
才发现（bysj 的典型故障）。

尚未建立分组的路径（按 TASKS 由后续任务补齐，勿在此臆造）：
    /cart、/refund、/review、/category、/home、/message、/api/ai（AI 服务）
"""

from __future__ import annotations

from fastapi import APIRouter

from aids_backend.api import (
    admin,
    auth,
    health,
    internal,
    jwks,
    marketing,
    order,
    pay,
    product,
    user,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(jwks.router)
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(product.router)
api_router.include_router(order.router)
api_router.include_router(pay.router)
api_router.include_router(marketing.router)
api_router.include_router(admin.router)
api_router.include_router(internal.router)

# 业务模块路由（不含 health / jwks：前者是探针，后者是标准发现文档）。
# 单独登记成注册表，供契约测试断言「模块 ↔ 前缀」不被静默改动——
# 直接从 app.routes 反推是做不到的：空 router 在 include 后不产生 Route。
MODULE_ROUTERS: dict[str, APIRouter] = {
    "auth": auth.router,
    "user": user.router,
    "product": product.router,
    "order": order.router,
    "pay": pay.router,
    "marketing": marketing.router,
    "admin": admin.router,
    "internal": internal.router,
}
