"""Mock 渠道服务路由聚合。

模块 → 前缀（与 PRD §8 / TASKS MOCK-01~03 对应；报文为 v0-draft，见各模块 docstring）：
    | 模块      | prefix      | 任务    |
    |-----------|-------------|---------|
    | health    | （无）      | 部署探针 |
    | payment   | /payment + /cashier + /recon + /callbacks | MOCK-01 |
    | sms       | /sms        | MOCK-02 |
    | logistics | /logistics  | MOCK-03 |

Mock 是**内部依赖服务**（只被主业务调用 + 收银台被用户短暂访问），
路径不进 API.md 的对外契约 —— 渠道报文细节见各模块 docstring 与 `TASKS.md`。
"""

from __future__ import annotations

from fastapi import APIRouter

from aids_mock.api import health
from aids_mock.routes_logistics import router as logistics_router
from aids_mock.routes_payment import router as payment_router
from aids_mock.routes_sms import router as sms_router

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(payment_router)
api_router.include_router(sms_router)
api_router.include_router(logistics_router)

# 业务模块路由注册表（不含 health），供契约测试断言「模块 ↔ 前缀」不被静默改动。
MODULE_ROUTERS: dict[str, APIRouter] = {
    "payment": payment_router,
    "sms": sms_router,
    "logistics": logistics_router,
}
