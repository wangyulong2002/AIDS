"""订单模块路由（API.md §2.4 订单模块）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /order/confirm                     POST  订单确认页（试算）
    /order/submit-token                GET   防重 token
    /order/submit                      POST  提交订单
    /order                             GET   我的订单分页
    /order/{orderNo}                   GET   订单详情
    /order/{orderNo}/cancel            PUT   取消（仅待付款）
    /order/{orderNo}/confirm-receive   PUT   确认收货
    /order/{orderNo}/trace             GET   物流轨迹

注意：`/order/submit-token` 必须注册在 `/order/{orderNo}` **之前**，
否则 "submit-token" 会被 `{orderNo}` 抢先匹配——这是 FastAPI 按注册顺序
匹配的常见坑，T2 填充时容易踩。
售后 `/refund/*`（§2.6）前缀不同，另建文件。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/order", tags=["订单"])
