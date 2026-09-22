"""商品模块路由（API.md §2.2 商品模块）。

BE-01 只建立分组（prefix + tag）；接口由后续任务填充：
    /product/search                GET   商品搜索
    /product/{spuId}               GET   商品详情
    /product/{spuId}/review        GET   评价列表
    /product/{spuId}/view          POST  浏览量 +1（异步落库）

同属浏览链路但前缀不同的路径（/home、/category/tree、/cart），
建组时按前缀各自成文件，不要塞进本文件的 prefix 里。
prefix 必须与 docs/API.md 一致，由 tests/api/test_route_contract.py 断言。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/product", tags=["商品"])
