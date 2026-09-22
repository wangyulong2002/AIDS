# AIDS 接口清单

| 项 | 内容 |
|------|------|
| 版本 | v1.1 |
| 依据 | [PRD.md](PRD.md) §5.3/§5.4、[DATA-DICTIONARY.md](DATA-DICTIONARY.md) |
| 用途 | DOC-03 交付物；**FE-03 之后所有页面开发的阻塞点**，前后端并行开发的契约 |
| 覆盖 | 前台 C 端 / 管理后台 B 端 / 内部服务接口 / AI 客服 SSE |

---

## 一、全局约定

### 1.1 请求与响应

**基础地址**：`https://{host}/api`（Nginx 统一入口）

**统一响应体**（PRD §5.4）：

```json
{ "code": 0, "message": "success", "data": { } }
```

- `code = 0` 表示成功，非 0 为错误码
- HTTP 状态码语义：`200` 业务成功/业务失败（**业务失败也返回 200，用 code 区分**）、`401` 未认证、`403` 无权限、`429` 限流、`500` 系统异常

**统一分页**：

```
请求: { "pageNum": 1, "pageSize": 20 }        // pageNum 从 1 开始, pageSize 上限 100
响应: { "total": 128, "list": [ ] }
```

**公共请求头**：

| Header | 必填 | 说明 |
|--------|------|------|
| `Authorization` | 是（登录后） | `Bearer {accessToken}` |
| `X-Trace-Id` | 否 | 前端可不传，服务端生成并回传；用于全链路追踪 |
| `Content-Type` | 是 | `application/json`（文件上传用 `multipart/form-data`） |

**公共响应头**：

| Header | 说明 |
|--------|------|
| `X-Trace-Id` | 服务端返回的追踪 ID，前端在错误上报时携带 |

### 1.2 错误码分段（PRD §5.4）

| 段位 | 域 | 说明 |
|------|----|------|
| `0` | 成功 | — |
| `1xxxx` | 通用 | 参数、鉴权、限流、系统异常 |
| `2xxxx` | 用户 | 注册登录、地址 |
| `3xxxx` | 商品 | 商品、类目、SKU、库存 |
| `4xxxx` | 订单 | 订单、售后 |
| `5xxxx` | 支付 | 支付、退款、对账 |
| `6xxxx` | AI 客服 | 会话、知识库 |
| `7xxxx` | 营销 | 优惠券、运费 |
| `8xxxx` | 后台权限 | RBAC、审计 |
| `9xxxx` | 文件/系统 | 上传、配置 |

### 1.3 通用错误码明细

| code | 含义 | HTTP | 前端处理 |
|------|------|------|----------|
| `0` | 成功 | 200 | — |
| `10001` | 参数校验失败 | 200 | 表单标红，message 提示 |
| `10002` | 未登录或 Token 失效 | 401 | **触发无感刷新**，失败则跳登录 |
| `10003` | 无权限访问 | 403 | 提示"无权限" |
| `10004` | 资源不存在 | 200 | 展示空态 |
| `10005` | **数据越权（IDOR 拦截）** | 403 | 提示"无权访问该数据"，**不暴露资源是否存在** |
| `10006` | 请求过于频繁 | 429 | 倒计时禁用按钮 |
| `10007` | 重复提交 | 200 | 静默忽略，跳转原结果 |
| `10008` | 系统繁忙 | 500 | 提示稍后重试 |
| `10009` | 文件类型/大小不合法 | 200 | 提示重新上传 |

> **错误码使用规范**：后端抛业务异常时必须指定错误码，禁止全部返回 `10008`。新增错误码须同步本文档。

---

## 二、前台商城（C 端）

> 前缀 `/api`，除标注「免登录」外均需 `Authorization`。
> **数据权限**：所有涉及用户自有资源的接口，服务端强制以 JWT 中的 `userId` 过滤，**请求参数中不接受 `userId`**（PRD §2.2）。

### 2.1 用户模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/auth/sms/send` | 发送验证码 | 免登录 |
| POST | `/auth/register` | 手机验证码注册 | 免登录 |
| POST | `/auth/login/sms` | 验证码登录 | 免登录 |
| POST | `/auth/login/password` | 密码登录 | 免登录 |
| POST | `/auth/refresh` | 刷新 Access Token | Refresh Token |
| POST | `/auth/logout` | 退出登录（吊销 Refresh） | 登录 |
| GET | `/user/profile` | 获取个人资料 | 登录 |
| PUT | `/user/profile` | 修改资料（昵称/头像/性别） | 登录 |
| GET | `/user/address` | 地址列表 | 登录 |
| POST | `/user/address` | 新增地址 | 登录 |
| PUT | `/user/address/{id}` | 修改地址 | 登录 |
| DELETE | `/user/address/{id}` | 删除地址 | 登录 |
| PUT | `/user/address/{id}/default` | 设为默认地址 | 登录 |

**`POST /auth/sms/send`**

```json
// 请求
{ "mobile": "13800138000", "scene": "LOGIN" }   // scene: LOGIN | REGISTER
// 响应
{ "code": 0, "message": "success", "data": { "expireSeconds": 300 } }
```

| code | 含义 |
|------|------|
| `20001` | 手机号格式错误 |
| `20002` | 验证码发送过于频繁（60s 内重复发送） |
| `20003` | 当日发送次数超限 |
| `10006` | 触发限流 |

> 开发环境：Mock 短信服务的验证码会直接返回在日志中，seed 数据下固定为 `123456`（由 MOCK-02 决定）。

**`POST /auth/login/password`**

```json
// 请求
{ "mobile": "13800138000", "password": "Admin@123456" }
// 响应
{
  "code": 0, "message": "success",
  "data": {
    "accessToken": "eyJhbGci...", "expiresIn": 7200,
    "refreshToken": "eyJhbGci...", "refreshExpiresIn": 604800,
    "userInfo": { "id": 7001, "nickname": "测试用户A", "avatar": null, "mobile": "138****8000" }
  }
}
```

| code | 含义 |
|------|------|
| `20004` | 账号或密码错误 |
| `20005` | 账号被锁定（连续失败 5 次，锁 30min） |
| `20006` | 账号被禁用 |

> **注意**：响应中的 `mobile` 必须脱敏为 `138****8000`（PRD §4.3）。

### 2.2 商品模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/home` | 首页聚合（轮播/类目/推荐） | 免登录 |
| GET | `/category/tree` | 三级类目树 | 免登录 |
| GET | `/product/search` | 商品搜索 | 免登录 |
| GET | `/product/{spuId}` | 商品详情 | 免登录 |
| GET | `/product/{spuId}/review` | 商品评价列表 | 免登录 |
| POST | `/product/{spuId}/view` | 浏览量 +1（异步落库） | 免登录 |

**`GET /product/search`**

```
Query: keyword, categoryId, brandId, minPrice, maxPrice,
       sortBy=price_asc|price_desc|sales_desc|time_desc, pageNum, pageSize
```

> **实现（v1.2）**：由 MySQL 8 全文索引（`ngram` 分词）承载，**不再依赖 Elasticsearch**（PRD §5.6）。
> 接口契约、入参与响应结构均不变，前端无需感知后端实现变化。仅返回已上架商品（`status=2`）。

```json
{
  "code": 0, "message": "success",
  "data": {
    "total": 128,
    "list": [{
      "spuId": 8401, "name": "Apple iPhone 15 Pro", "subtitle": "钛金属设计, A17 Pro 芯片",
      "mainPic": "https://...", "minPrice": 7999.00, "maxPrice": 9999.00,
      "sales": 120, "hasStock": true
    }]
  }
}
```

**`GET /product/{spuId}`**

```json
{
  "code": 0, "message": "success",
  "data": {
    "spuId": 8401, "name": "Apple iPhone 15 Pro", "subtitle": "...",
    "mainPic": "https://...", "detail": "<p>富文本</p>",
    "images": ["https://..."],
    "categoryId": 8101, "brandId": 8201, "brandName": "Apple",
    "minPrice": 7999.00, "maxPrice": 9999.00,
    "sales": 120, "viewCount": 3421,
    "specs": [ { "name": "颜色", "values": ["原色钛金属", "蓝色钛金属"] },
               { "name": "容量", "values": ["256GB", "512GB"] } ],
    "skus": [
      { "skuId": 8501, "price": 8999.00, "originalPrice": 9999.00, "pic": "https://...",
        "specs": {"颜色":"原色钛金属","容量":"256GB"},
        "availableStock": 70, "status": 1 }
    ]
  }
}
```

> `availableStock` 即 `biz_sku_stock.available_stock`（**可售库存，非总库存**）。前端展示"仅剩 N 件"阈值可配置。

| code | 含义 |
|------|------|
| `30001` | 商品不存在或已下架 |

### 2.3 购物车模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/cart` | 购物车列表 | 登录 |
| POST | `/cart` | 加入购物车 | 登录 |
| PUT | `/cart/{skuId}` | 修改数量/勾选状态 | 登录 |
| DELETE | `/cart/{skuId}` | 删除单项 | 登录 |
| DELETE | `/cart` | 批量删除（body 传 skuIds） | 登录 |
| PUT | `/cart/checked` | 全选/取消全选 | 登录 |

**`GET /cart`** — 返回时**实时校验**价格、库存、上下架状态：

```json
{
  "code": 0, "message": "success",
  "data": {
    "items": [{
      "skuId": 8501, "spuId": 8401, "spuName": "Apple iPhone 15 Pro",
      "skuPic": "https://...", "specs": {"颜色":"原色钛金属","容量":"256GB"},
      "price": 8999.00, "quantity": 2, "checked": 1,
      "totalAmount": 17998.00,
      "availableStock": 70,
      "valid": true, "invalidReason": null
    }],
    "totalCount": 3, "checkedCount": 2, "checkedAmount": 17998.00
  }
}
```

> `valid=false` 时 `invalidReason` 取值：`OFF_SHELF`（已下架）/ `STOCK_INSUFFICIENT`（库存不足）/ `DELETED`（商品已删除）。失效商品前端置灰且不可结算。

### 2.4 订单模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/order/confirm` | 订单确认页（试算） | 登录 |
| GET | `/order/submit-token` | 获取防重 token | 登录 |
| POST | `/order/submit` | 提交订单 | 登录 |
| GET | `/order` | 我的订单分页 | 登录 |
| GET | `/order/{orderNo}` | 订单详情 | 登录 |
| PUT | `/order/{orderNo}/cancel` | 取消订单（仅待付款） | 登录 |
| PUT | `/order/{orderNo}/confirm-receive` | 确认收货 | 登录 |
| GET | `/order/{orderNo}/trace` | 物流轨迹 | 登录 |

**`POST /order/confirm`**（试算，不落库）

```json
// 请求
{ "addressId": 7101, "userCouponId": 8901,
  "items": [ { "skuId": 8501, "quantity": 2 } ] }
// 响应
{
  "code": 0, "message": "success",
  "data": {
    "receiver": { "receiver": "张三", "phone": "138****8000",
                  "fullAddress": "浙江省 杭州市 西湖区 文三路 100 号 1 幢 201 室" },
    "items": [ { "skuId": 8501, "spuName": "...", "price": 8999.00, "quantity": 2,
                 "totalAmount": 17998.00, "discountAmount": 50.00, "payAmount": 17948.00,
                 "availableStock": 70 } ],
    "totalAmount": 17998.00,
    "discountAmount": 50.00,
    "freightAmount": 0.00,
    "payAmount": 17948.00,
    "coupon": { "userCouponId": 8901, "name": "新人专享 50 元券", "discountAmount": 50.00 },
    "availableCoupons": [ /* 当前订单可用券列表，供用户切换 */ ]
  }
}
```

> **金额一律服务端重算**（PRD §6.6）。提交订单时服务端会再次计算，与试算不一致则以提交时为准并校验。

| code | 含义 |
|------|------|
| `30002` | 商品已下架 |
| `30003` | 库存不足（`data` 中返回具体 skuId 与剩余量） |
| `70001` | 优惠券不可用（已过期/已使用/不满足门槛） |
| `70002` | 优惠券不属于当前用户 |
| `20007` | 收货地址不存在或不属于当前用户 |

**`POST /order/submit`**

```json
// 请求
{ "submitToken": "uuid-from-get-submit-token", "addressId": 7101,
  "userCouponId": 8901, "items": [ { "skuId": 8501, "quantity": 2 } ],
  "clientAmount": 17948.00 }        // 前端试算金额，仅用于比对
// 响应
{ "code": 0, "message": "success",
  "data": { "orderNo": "SO20260919120001", "payAmount": 17948.00,
            "expireSeconds": 900 } }
```

| code | 含义 |
|------|------|
| `10007` | 重复提交（submitToken 已使用），`data` 返回原订单号 |
| `40001` | 订单金额校验失败（前端金额与服务端不一致） |
| `30003` | 库存不足 |

> **幂等语义**：同一 `submitToken` 并发提交 100 次，仅生成 1 笔订单，其余返回 `10007` 并携带同一订单号（PRD §12.2 验收项）。

**`GET /order`**

```
Query: status（可多值，如 status=10,20）, pageNum, pageSize
```

**`GET /order/{orderNo}`** — 返回含状态流转时间线：

```json
{ "code": 0, "message": "success",
  "data": {
    "orderNo": "SO20260919120001",
    "status": 20, "statusName": "PAID",
    "totalAmount": 17998.00, "discountAmount": 50.00,
    "freightAmount": 0.00, "payAmount": 17948.00, "refundAmount": 0.00,
    "receiver": {...}, "items": [...],
    "payTime": "2026-09-19T12:05:00Z", "deliverTime": null,
    "delivery": { "companyName": "顺丰速运", "deliveryNo": "SF1234567890", "status": 1 },
    "timeline": [
      { "event": "SUBMIT", "toStatus": 10, "time": "2026-09-19T12:00:00Z" },
      { "event": "PAY",    "toStatus": 20, "time": "2026-09-19T12:05:00Z" }
    ],
    "remainSeconds": null        // 待付款时返回剩余支付秒数，供倒计时
  }
}
```

| code | 含义 |
|------|------|
| `40002` | 订单不存在 |
| `10005` | 订单不属于当前用户 |

### 2.5 支付模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/payment/create` | 发起支付（获取收银台地址） | 登录 |
| GET | `/payment/{orderNo}/status` | 查询支付状态（前端轮询） | 登录 |
| POST | `/payment/callback` | **渠道异步回调** | **免登录 + 验签** |

**`POST /payment/create`**

```json
// 请求
{ "orderNo": "SO20260919120001", "payType": 1 }   // 1支付宝 2微信
// 响应
{ "code": 0, "message": "success",
  "data": { "paymentNo": "PAY20260919120001", "payUrl": "http://localhost:8081/cashier?token=xxx" } }
```

> `payUrl` 指向 **Mock 沙箱收银台页面**（MOCK-01），页面提供「确认支付 / 取消支付 / 超时」三个操作，用于演示与故障注入。

| code | 含义 |
|------|------|
| `50001` | 支付单创建失败 |
| `40003` | 订单状态不允许支付（非待付款） |
| `40004` | 订单已超时关闭 |

**`POST /payment/callback`**（Mock 渠道回调）

- **Content-Type**: `application/x-www-form-urlencoded`
- 参数：`out_trade_no`、`trade_no`、`total_amount`、`trade_status`、`sign`、`sign_type=RSA2`
- **成功响应必须返回纯文本 `success`**（渠道据此停止重推），否则渠道会按退避策略重推

处理流程（PRD §8.2）：

```
验签 → 校验金额与订单号 → 校验支付单状态（幂等）
     → 订单 PENDING_PAY → PAID → 扣减 DB 锁定库存 → 核销优惠券
     → 发 Kafka order.paid → 返回 "success"
```

| code | 含义 |
|------|------|
| `50002` | 验签失败（记录审计日志） |
| `50003` | 回调金额与订单金额不符（告警） |
| `50004` | 支付单不存在 |

> 重复回调同一 `trade_no` 时，幂等返回 `success` 但不重复触发业务（PRD §12.3 验收项）。

### 2.6 售后模块

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/refund/apply` | 申请售后 | 登录 |
| GET | `/refund` | 我的售后列表 | 登录 |
| GET | `/refund/{refundNo}` | 售后详情 | 登录 |
| PUT | `/refund/{refundNo}/cancel` | 撤销申请 | 登录 |
| POST | `/refund/{refundNo}/return` | 填写退货物流 | 登录 |

**`POST /refund/apply`**

```json
// 请求
{ "orderNo": "SO20260919120001", "orderItemId": 123456,
  "type": 1,                      // 1仅退款 2退货退款
  "reason": "商品与描述不符", "evidence": ["https://.../1.jpg"] }
// 响应
{ "code": 0, "message": "success", "data": { "refundNo": "RF20260919120001", "amount": 8999.00 } }
```

| code | 含义 |
|------|------|
| `40005` | 订单状态不允许申请售后 |
| `40006` | 该订单项已有进行中的售后单 |
| `40007` | 超出可申请时限（签收超 7 天） |
| `40008` | 售后状态不允许此操作 |

### 2.7 评价与优惠券

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/review` | 发表评价 | 登录 |
| GET | `/review/my` | 我的评价 | 登录 |
| GET | `/coupon/available` | 可领取的优惠券列表 | 登录 |
| POST | `/coupon/{couponId}/receive` | 领取优惠券 | 登录 |
| GET | `/coupon/my` | 我的优惠券（按状态筛选） | 登录 |
| GET | `/message` | 站内信列表 | 登录 |
| PUT | `/message/{id}/read` | 标记已读 | 登录 |
| GET | `/message/unread-count` | 未读数 | 登录 |

| code | 含义 |
|------|------|
| `30004` | 该订单项已评价过 |
| `70003` | 优惠券已领完 |
| `70004` | 超出每人限领数量 |
| `70005` | 优惠券未开始或已结束 |

---

## 三、管理后台（B 端）

> 前缀 `/api/admin`，全部需登录 + 对应权限点（PRD §2.2 功能权限）。
> 权限点命名规则：`{模块}:{动作}`，如 `order:ship`。**每个接口须在注释中标注所需权限点**。

### 3.1 认证与权限

| 方法 | 路径 | 权限点 | 说明 |
|------|------|--------|------|
| POST | `/admin/auth/login` | 免登录 | 后台登录 |
| POST | `/admin/auth/logout` | 登录 | 退出 |
| GET | `/admin/auth/userinfo` | 登录 | 当前用户信息 + 权限列表 + 菜单树 |
| GET | `/admin/user` | `system:user` | 后台用户列表 |
| POST | `/admin/user` | `system:user` | 新增后台用户 |
| PUT | `/admin/user/{id}` | `system:user` | 修改 |
| DELETE | `/admin/user/{id}` | `system:user` | 删除 |
| PUT | `/admin/user/{id}/roles` | `system:user` | 分配角色 |
| GET | `/admin/role` | `system:role` | 角色列表 |
| POST | `/admin/role` | `system:role` | 新增角色 |
| PUT | `/admin/role/{id}` | `system:role` | 修改（含 `data_scope`） |
| DELETE | `/admin/role/{id}` | `system:role` | 删除 |
| PUT | `/admin/role/{id}/permissions` | `system:role` | 分配权限 |
| GET | `/admin/permission/tree` | `system:permission` | 权限树 |
| GET | `/admin/audit-log` | `system:audit` | 审计日志分页查询 |

**`GET /admin/auth/userinfo`** — 前端据此渲染动态菜单：

```json
{ "code": 0, "message": "success",
  "data": {
    "userInfo": { "id": 1001, "username": "admin", "realName": "超级管理员" },
    "roles": ["admin"],
    "permissions": ["product:list", "product:add", "order:ship", "..."],
    "menus": [
      { "id": 3001, "name": "商品管理", "path": "/product", "sort": 10,
        "children": [ { "id": 3002, "name": "商品列表", "path": "/product/list" } ] }
    ]
  }
}
```

### 3.2 商品与库存

| 方法 | 路径 | 权限点 | 说明 |
|------|------|--------|------|
| GET | `/admin/product` | `product:list` | SPU 列表 |
| GET | `/admin/product/{id}` | `product:list` | SPU 详情（含 SKU） |
| POST | `/admin/product` | `product:add` | 新增 SPU + SKU |
| PUT | `/admin/product/{id}` | `product:edit` | 修改 |
| DELETE | `/admin/product/{id}` | `product:edit` | 删除（逻辑） |
| PUT | `/admin/product/{id}/shelf` | `product:shelf` | 上下架 |
| PUT | `/admin/product/{id}/audit` | `product:audit` | 审核（通过/驳回 + 意见） |
| GET | `/admin/category` | `product:list` | 类目树 |
| POST/PUT/DELETE | `/admin/category[/{id}]` | `product:edit` | 类目维护 |
| GET | `/admin/brand` | `product:list` | 品牌列表 |
| POST/PUT/DELETE | `/admin/brand[/{id}]` | `product:edit` | 品牌维护 |
| GET | `/admin/stock` | `stock:list` | 库存列表 |
| PUT | `/admin/stock/{skuId}/adjust` | `stock:adjust` | **手动调整（必填原因）** |
| GET | `/admin/stock/log` | `stock:log` | 库存流水查询 |

**`PUT /admin/stock/{skuId}/adjust`**

```json
// 请求
{ "changeNum": 50, "reason": "供应商补货" }    // 正数入库, 负数出库
// 响应
{ "code": 0, "message": "success",
  "data": { "skuId": 8501, "beforeAvailable": 70, "afterAvailable": 120, "totalStock": 150 } }
```

> 手动调整必须记录 `biz_stock_log`（`change_type=4`，`operator_id` 为当前后台用户），并在 `sys_audit_log` 留痕。

| code | 含义 |
|------|------|
| `30005` | 调整后库存不能为负 |
| `80001` | 缺少调整原因 |

### 3.3 订单与售后

| 方法 | 路径 | 权限点 | 说明 |
|------|------|--------|------|
| GET | `/admin/order` | `order:list` | 订单列表（多维筛选） |
| GET | `/admin/order/{orderNo}` | `order:detail` | 订单详情（含全链路时间线） |
| POST | `/admin/order/{orderNo}/ship` | `order:ship` | 发货（快递公司 + 单号，支持拆包） |
| GET | `/admin/refund` | `order:refund:audit` | 售后单列表 |
| PUT | `/admin/refund/{refundNo}/audit` | `order:refund:audit` | 审核（同意/拒绝 + 意见） |
| PUT | `/admin/refund/{refundNo}/receive` | `order:refund:audit` | 确认收到退货 |
| GET | `/admin/dashboard/*` | `dashboard` | 看板数据（GMV/订单量/转化率/增长） |

**`POST /admin/order/{orderNo}/ship`**

```json
{ "packages": [ { "companyCode": "SF", "companyName": "顺丰速运", "deliveryNo": "SF1234567890" } ] }
```

| code | 含义 |
|------|------|
| `40009` | 订单状态不允许发货（非待发货） |
| `40010` | 运单号已存在 |

### 3.4 营销管理

| 方法 | 路径 | 权限点 | 说明 |
|------|------|--------|------|
| GET | `/admin/coupon` | `coupon:list` | 优惠券模板列表 |
| POST | `/admin/coupon` | `coupon:issue` | 创建模板 |
| PUT | `/admin/coupon/{id}` | `coupon:issue` | 修改 |
| PUT | `/admin/coupon/{id}/status` | `coupon:issue` | 启停（发放中/已结束/已下架） |
| POST | `/admin/coupon/{id}/grant` | `coupon:issue` | 定向发放给指定用户 |
| GET | `/admin/freight-template` | `freight:list` | 运费模板列表 |
| POST/PUT/DELETE | `/admin/freight-template[/{id}]` | `freight:list` | 运费模板维护 |

### 3.5 知识库与客服工作台

| 方法 | 路径 | 权限点 | 说明 |
|------|------|--------|------|
| GET | `/admin/kb/document` | `kb:list` | 知识库文档列表 |
| POST | `/admin/kb/document` | `kb:upload` | 上传文档（触发切片+向量化） |
| PUT | `/admin/kb/document/{id}` | `kb:upload` | 编辑内容 |
| PUT | `/admin/kb/document/{id}/enabled` | `kb:upload` | 启停 |
| POST | `/admin/kb/rebuild` | `kb:rebuild` | 手动触发索引重建 |
| GET | `/admin/kb/document/{id}/chunks` | `kb:list` | 查看切片（调试用） |
| GET | `/admin/cs/conversation` | `cs:conversation` | 会话列表（排队中/进行中/已结束） |
| GET | `/admin/cs/conversation/{id}` | `cs:conversation` | 会话详情（消息记录 + AI 旁听） |
| POST | `/admin/cs/conversation/{id}/takeover` | `cs:takeover` | 接管会话 |
| POST | `/admin/cs/conversation/{id}/close` | `cs:close` | 结束会话（标记是否解决） |
| GET | `/admin/cs/stats` | `cs:conversation` | 解决率/转人工率统计 |

**`POST /admin/kb/document`** — `multipart/form-data`，字段：`file`、`title`、`domain`（product/policy/faq）

```json
{ "code": 0, "message": "success",
  "data": { "documentId": 9008, "status": 0, "message": "已提交处理，切片完成后生效" } }
```

> 处理为异步：上传后返回 `status=0`（待处理），前端轮询文档列表查看进度。失败时 `status=3` 且 `error_msg` 有值。

---

## 四、内部服务接口（主业务 ↔ AI 服务）

> 前缀 `/api/internal`，**不经过 Nginx 对外暴露**，仅内网可达。
> 鉴权：`X-Internal-Token`（环境变量下发的静态 Token）+ `X-Timestamp` + `X-Nonce` + `X-Sign`（HMAC 防重放）。

| 方法 | 路径 | 调用方 | 说明 |
|------|------|--------|------|
| GET | `/internal/order/list` | AI 服务 | 按用户查订单列表 |
| GET | `/internal/order/{orderNo}` | AI 服务 | 订单详情 |
| GET | `/internal/order/{orderNo}/trace` | AI 服务 | 物流轨迹 |
| GET | `/internal/refund/{refundNo}` | AI 服务 | 售后进度 |
| GET | `/internal/product/{spuId}` | AI 服务 | 商品详情（供知识库同步） |
| POST | `/internal/kb/notify` | 主业务服务 | 知识库变更通知 **（同步直呼；异步广播走 Kafka `kb.index.rebuild`，见 §六）** |

**`GET /internal/order/list`**

```
Query: userId（必填）, status, pageNum, pageSize
Header: X-Internal-Token, X-Timestamp, X-Nonce, X-Sign
```

```json
{ "code": 0, "message": "success",
  "data": { "total": 3, "list": [
    { "orderNo": "SO20260919120001", "status": 20, "statusName": "PAID",
      "payAmount": 17948.00, "createTime": "2026-09-19T12:00:00Z",
      "items": [ { "spuName": "Apple iPhone 15 Pro", "quantity": 2 } ] }
  ] } }
```

> **安全要求**：`userId` 由 FastAPI 从用户 JWT 解析后传入，**禁止从对话内容中提取**——否则用户可诱导 AI 查询他人订单（PRD §9.3）。返回数据须脱敏。

---

## 五、AI 客服 SSE 接口

**`POST /api/ai/chat`** — `Content-Type: text/event-stream`

**鉴权（关键）**：`EventSource` 不支持自定义 Header，因此：

| 方案 | 说明 | 采用 |
|------|------|------|
| `fetch` + `ReadableStream` 手动解析 | 可带 `Authorization` 头 | ✅ **主方案** |
| URL 传短期 ticket | Redis 存 60s 一次性票据，用后即焚 | ✅ 降级备选 |
| URL 直接传 Access Token | 会进浏览器历史、Nginx 日志、Referer | ❌ **禁止** |

**请求**：

```json
{ "conversationId": "CV20260919120001",   // 为空则新建会话
  "message": "我买的东西什么时候到？",
  "spuId": 8401 }                          // 可选，商品页发起时携带
```

**响应事件流**：

```
event: message
data: {"delta":"您的","index":0}

event: message
data: {"delta":"订单已发货，","index":1}

event: message
data: {"delta":"预计明天送达。","index":2}

event: done
data: {"conversationId":"CV20260919120001","messageId":123456,
       "intent":"order","sources":[{"chunkId":9105,"title":"配送与物流时效"}],
       "tokens":{"prompt":1820,"completion":86},"latencyMs":1240}
```

> **`tokens.prompt` 口径说明**：示例值 `1820` 是**命中简短 FAQ 的轻上下文单轮**；PRD §10 成本估算用的 `3000`
> 是**含系统提示词 + Top5 切片 + 多轮历史的保守上界基线**，两者不矛盾（一个是样例，一个是预算基线）。
> 前端做用量展示按实际 `prompt`/`completion`；成本核算一律走 PRD §10 的 3000 基线，勿拿示例值当均值。

**异常与转人工**：

```
event: error
data: {"code":60001,"message":"智能客服繁忙，已为您转接人工"}

event: handoff
data: {"handoffId":7001,"reason":1,"queuePosition":2,"estimatedWaitSeconds":60}
```

| event | 触发时机 | 前端处理 |
|-------|----------|----------|
| `message` | 每个流式片段 | 追加到气泡（打字机效果） |
| `done` | 生成结束 | 结束 loading，渲染引用来源与 👍👎 |
| `error` | 降级/异常 | 展示提示，提供「重试」按钮 |
| `handoff` | 转人工 | 展示排队信息，切换为人工会话 UI |

**错误码**：

| code | 含义 |
|------|------|
| `60001` | Ark 服务不可用，已降级（返回 FAQ 原文 + 转人工提示） |
| `60002` | 输入命中敏感词，已拦截 |
| `60003` | 会话不存在或不属于当前用户 |
| `60004` | 并发对话数超限 |

**其他 AI 接口**：

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/api/ai/conversation` | 我的会话历史 | 登录 |
| GET | `/api/ai/conversation/{id}` | 会话消息记录 | 登录 |
| POST | `/api/ai/conversation/{id}/feedback` | 满意度评价（👍/👎） | 登录 |
| POST | `/api/ai/conversation/{id}/handoff` | 主动请求转人工 | 登录 |
| WS | `/api/ai/ws/agent` | **客服工作台实时通道**（WebSocket） | 客服登录 |

> 转人工通知客服端采用 **WebSocket**（AI-13），轮询延迟不可接受。

---

## 六、Kafka 消息契约

> 对应 TASKS.md「接口契约」表。所有消息体为 JSON，公共字段：`eventId`（雪花 ID，幂等用）、`eventTime`（UTC）、`traceId`。

| Topic | 生产者 | 消费者 | 消息体关键字段 |
|-------|--------|--------|----------------|
| `order.created` | 订单服务 | 通知服务 | `orderNo`, `userId`, `payAmount` |
| `order.paid` | 支付服务 | 库存/通知/物流 | `orderNo`, `paymentNo`, `payAmount`, `payTime` |
| `order.cancelled` | 订单服务 | 库存/营销 | `orderNo`, `cancelReason`, `items[{skuId,quantity}]` |
| `order.completed` | 订单服务 | 通知 | `orderNo`, `userId`, `finishTime` |
| `kb.index.rebuild` | 后台/FastAPI | FastAPI | `documentId`, `domain`, `version` |
| `notify.send` | 各服务 | 通知服务 | `userId`, `type`（SMS/IN_APP）, `templateCode`, `params` |

**消费端统一要求**（PRD §5.5）：手动 ACK、幂等（`eventId` + Redis 去重表 TTL 24h）、失败重试 3 次后进 `{topic}.dlq` 并落 `sys_dead_letter` 告警。

---

## 七、待补充（后续梯次定稿）

| 内容 | 定稿时点 | 责任任务 | 逾期未定稿的阻塞影响（按 TASKS 依赖列实测推导） |
|------|----------|----------|------------------------------------------------|
| Mock 渠道报文与签名算法细节 | T1 内 | MOCK-01 | **直接堵死 T3**：BE-23（支付下单）与 BE-28（渠道退款）均依赖 MOCK-01，MOCK-04（故障注入，T2）也依赖它；BE-23 → BE-24 → BE-25/BE-29/BE-32 整条链顺延，即**交易闭环与对账全部停摆** |
| 库存预扣 Redis key 命名与 Lua 脚本出入参 | **T3 前**（**存疑，见下**） | BE-11 | **堵死 T2 出口与整条 T3**：载体 BE-11（库存模块）与下游 BE-15（购物车）**均属 T2**，而 BE-19（提交订单，T3）依赖 BE-11，BE-19 又是 BE-21/BE-22/BE-23 的上游；BE-36、FE-17 同时停。**PRD §12.2 的 50 TPS 无超卖验收无法开始** |
| 客服工作台 WebSocket 消息协议 | T5 内 | AI-13 | 影响面限于 T5：AI-16（依赖 AI-13）与坐席工作台前端停；**F10 的「转人工可接管」这一答辩演示项无法验收**，但交易链路不受牵连 |
| 看板各指标的统计口径与 SQL | T4 内 | BE-32 | 只影响 FE-20 数据看板；**影响面最小**——TASKS 裁剪建议里 FE-20 正是第一顺位可裁项，必要时可直接砍掉而不危及其他任务 |

**⚠️ 定稿时点存疑（本次核对发现，需 DOC/API 责任人裁决）**：该契约标注「T3 前」定稿，但它的载体任务 BE-11 与其直接下游 BE-15 **都排在 T2**——按依赖顺序，契约定稿必须落在 **T2 内**（建议与 BE-10 同步、早于 BE-11 开工），否则 T2 出口判据本身就无法达成。本表保留原文「T3 前」不擅自改动，仅在此标记矛盾，待 T2 开工前正式更正。

**逾期降级预案**（补齐「只给了时点、没给兜底」的缺口）：

- **前两项属于硬阻塞**，不允许拖过所在梯次开工日：若 T1 结束（DEP-02 交付日）Mock 报文仍未定稿，**立即冻结 MOCK-01 的字段定义**——先按「RSA2 + 现有 API.md 响应体结构」出最小可用版本并标 `v0-draft`，T3 期间只允许向后兼容地加字段，不允许推翻；
- 若 BE-11 的 Redis key/Lua 出入参未在 T3 开工日前定稿，**T3 首日先做 BE-16（运费模板）、BE-17（优惠券）、BE-20（延迟任务框架）**——三者只依赖 BE-10/BE-05，不受该契约牵连，可并行推进而不空转；契约定稿压缩进半天评审后再接 BE-18/BE-19；
- 后两项（AI-13 WebSocket、BE-32 看板口径）逾期时按 TASKS 裁剪建议处理：可顺延或裁掉，**不占用关键路径**。
