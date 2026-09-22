"""管理后台路由（API.md §三 管理后台 B 端）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /admin/auth/*     §3.1  后台登录 / 登出 / 当前用户信息 + 权限 + 菜单树
    /admin/user/*     §3.1  后台用户与角色（system:user / system:role 权限码）
    /admin/*          §3.2  商品与库存（库存调整必须带原因，否则 80001）
    /admin/*          §3.3  订单与售后（发货、运单号唯一）
    /admin/*          §3.4  营销管理
    /admin/kb/*       §3.5  知识库与客服工作台

后台接口的鉴权与 C 端**不同**：走 RBAC 权限码校验（8xxxx 段错误码），
不是「登录即可访问」。T2 实现时须挂对应的权限依赖，不能只校验 JWT。
本 router 有 prefix 无 routes，因此它对**所有** /admin/* 路径负责——
后续新增后台子域时在此文件内挂子 router，不要另起平行根前缀。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["管理后台"])
