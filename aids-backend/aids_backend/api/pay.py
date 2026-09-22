"""支付模块路由（API.md §2.5 支付模块）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /payment/create               POST  发起支付（返回收银台地址）
    /payment/{orderNo}/status     GET   支付状态（前端轮询）
    /payment/callback             POST  渠道异步回调（**免登录 + 验签**）

`/payment/callback` 是本模块唯一免登录接口，鉴权方式为渠道 RSA2 验签
（失败返回 50002 并记审计日志）——**不能**套用 JWT 依赖，否则 Mock 渠道
回调会被 401 挡掉，表现为「支付成功但订单不发货」。T2 实现时须单独标注。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/payment", tags=["支付"])
