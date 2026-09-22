"""用户模块路由（API.md §2.1 用户模块）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /user/profile            GET/PUT     个人资料
    /user/address            GET/POST    收货地址
    /user/address/{id}       PUT/DELETE  地址增删改
    /user/address/{id}/default  PUT      设为默认

认证类接口 `/auth/*`（§2.1 前半段）归属 BE-03 鉴权模块，不在本文件。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/user", tags=["用户"])
