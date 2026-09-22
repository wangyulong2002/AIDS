"""营销模块路由（API.md §2.7 评价与优惠券 —— 优惠券部分）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /coupon/available             GET   可领取的优惠券列表
    /coupon/{couponId}/receive    POST  领取优惠券
    /coupon/my                    GET   我的优惠券（按状态筛选）

运费模板与后台营销管理分别属于 `/admin/*`（§3.4），不在本文件。
注意：优惠券的并发领取必须靠 DB 唯一约束/乐观锁兜底，不能只靠
「先查再插」——这是 C5/C8 覆盖的幂等场景，T2 实现时对照 DATA-DICTIONARY
不变量 #9~#12。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/coupon", tags=["营销"])
