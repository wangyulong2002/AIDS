"""Mock 渠道服务路由聚合。

当前只有探针；业务路由由 TASK 落地（docs/TASKS.md）：
    MOCK-01 支付网关：统一下单 / 沙箱收银台页面 / 异步回调 / 主动查询 / 退款 /
             T+1 对账单；以 `PaymentChannel` 接口（Protocol/ABC）抽象
    MOCK-02 短信：发送接口 + 开发环境验证码回显 + 发送记录落库
    MOCK-03 物流：运单创建 / 轨迹推进 / 轨迹查询
    MOCK-04 故障注入开关：回调延迟、重复推送、丢失概率、渠道失败率

路径前缀不在这里臆造：Mock 是**内部依赖服务**，其路径由主业务的
`MOCK_PAY_BASE_URL` 等环境变量决定，且 MOCK-01 定稿前不允许先写死
（API.md §七「待补充」明确列出该契约的定稿时点为 T1）。
"""

from __future__ import annotations

from fastapi import APIRouter

from aids_mock.api import health

api_router = APIRouter()

api_router.include_router(health.router)

# 业务模块路由注册表（不含 health），供契约测试断言「模块 ↔ 前缀」不被静默改动。
MODULE_ROUTERS: dict[str, APIRouter] = {}
