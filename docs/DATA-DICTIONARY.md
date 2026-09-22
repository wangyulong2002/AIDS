# AIDS 数据字典

| 项 | 内容 |
|------|------|
| 版本 | v1.1（v1.3 修订：新增 `sys_config` 表条目、37→38 表 / 411→419 字段、Flyway→Alembic、Java 枚举→Python str Enum） |
| 依据 | [schema.sql](sql/schema.sql) 自动生成（38 表 / 419 字段） |
| 用途 | DOC-03 交付物；前后端并行开发与数据库编码的统一依据 |
| 生成方式 | 由 `docs/tools/gen_data_dictionary.py` 从 DDL 解析生成，**DDL 变更后须重新生成**（`--check` 模式供 CI 门禁），禁止手工维护 |

> **约定**（源自 PRD §5.4）
> - 主键统一 `BIGINT UNSIGNED` 雪花 ID，应用层生成，非自增
> - 金额统一 `DECIMAL(12,2)`，禁止浮点
> - 时间统一 `DATETIME`，**UTC 存储**，前端本地化展示
> - 逻辑删除字段 `deleted`（0未删 1已删），仅用于用户/商品等主数据
> - 不使用物理外键，引用完整性由应用层保证
> - 手机号等敏感字段 AES-256-GCM 加密存储，另设 HMAC-SHA256 列用于查询

---

## 一、状态枚举映射（DDL ↔ PRD 状态机）

> **为什么单列一章**：DDL 用数字存储状态，PRD 用英文名描述状态机，Python 枚举（str Enum）与前端映射又是第三套。
> 三处漂移是电商项目最常见的缺陷来源（"10 到底是待付款还是待发货"）。本表为唯一权威映射，
> **后端枚举常量名必须与「PRD 状态名」列逐字一致**。

### `biz_order.status`  (PRD §6.1)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `10` | 待付款 | `PENDING_PAY` | 下单成功，等待支付 |
| `20` | 待发货 | `PAID` | 支付成功，等待商家发货 |
| `30` | 待收货 | `SHIPPED` | 已发货，等待用户签收 |
| `40` | 待评价 | `PENDING_REVIEW` | 已签收，评价窗口内 |
| `50` | 已完成 | `COMPLETED` | 评价完成或超期，终态 |
| `60` | 已取消 | `CANCELLED` | 未支付超时/主动取消，终态 |
| `70` | 售后中 | `AFTER_SALE` | 存在进行中的售后单 |
| `80` | 已关闭 | `CLOSED` | 已支付后取消且退款完成，终态 |

### `biz_payment.status`  (PRD §6.4)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `0` | 待支付 | `INIT` | 支付单已创建 |
| `1` | 已发起待回调 | `PAYING` | 已调渠道下单，等待回调（补偿任务扫描此态） |
| `2` | 成功 | `SUCCESS` | 渠道回调验签通过，终态 |
| `3` | 失败 | `FAILED` | 渠道返回失败 |
| `4` | 已关闭 | `CLOSED` | 超时关闭/订单取消，终态 |

### `biz_refund.status`  (PRD §6.5)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `0` | 待审核 | `APPLIED` | 买家已申请，商家审核中 |
| `1` | 已拒绝 | `REJECTED` | 商家拒绝，终态（买家可申诉） |
| `2` | 待退货 | `WAIT_RETURN` | 审核通过，待买家寄回（仅退货退款） |
| `3` | 待收货 | `WAIT_RECEIVE` | 买家已寄回，待商家收货（仅退货退款） |
| `4` | 退款中 | `REFUNDING` | 已发起渠道退款 |
| `5` | 已完成 | `REFUND_SUCCESS` | 退款成功，终态 |
| `6` | 退款失败 | `REFUND_FAILED` | 渠道退款失败，需人工介入+告警 |
| `7` | 已撤销 | `CANCELLED` | 买家主动撤销，终态 |

### `biz_user_coupon.status`  (PRD §6.3)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 未使用 | `UNUSED` | 已领取未使用 |
| `2` | 锁定中 | `LOCKED` | 下单占用，防并发重复使用 |
| `3` | 已使用 | `USED` | 支付成功核销 |
| `4` | 已过期 | `EXPIRED` | 超期未使用 |
| `5` | 冻结中 | `FROZEN` | 退款中冻结 |

### `biz_stock_log.change_type`  (PRD §6.2)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 下单锁定 | `LOCK` | available-1, locked+1 |
| `2` | 支付扣减 | `PAY` | locked-1, sold+1 |
| `3` | 取消释放 | `RELEASE` | locked-1, available+1 |
| `4` | 手动调整 | `ADJUST` | total+n, available+n（必填原因） |
| `5` | 退货入库 | `RETURN` | sold-1, available+1（退货收货后） |
| `6` | 退款回补 | `REFUND` | sold-1, available+1（仅退款） |

### `biz_order_log.operator_type`  (PRD §6.1)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 用户 | `USER` |  |
| `2` | 商家 | `MERCHANT` |  |
| `3` | 系统 | `SYSTEM` | 定时/补偿任务 |
| `4` | 客服 | `AGENT` |  |

### `ai_conversation.status`  (PRD §9.8)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | AI接待中 | `AI_SERVING` |  |
| `2` | 待人工接入 | `HANDOFF_REQUESTED` |  |
| `3` | 人工接待中 | `HUMAN_SERVING` |  |
| `4` | 已结束 | `CLOSED` |  |

### `ai_handoff_record.reason`  (PRD §9.4)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 低置信度 | `LOW_CONFIDENCE` | S1/S2/S3 任一低置信 |
| `2` | 用户要求 | `USER_REQUEST` | 用户显式要求转人工 |
| `3` | 订单争议 | `ORDER_DISPUTE` | 涉及金额争议/投诉/法律 |
| `4` | 多次不满 | `NEGATIVE_SENTIMENT` | 连续两次表达不满 |
| `5` | 连续未解决 | `UNRESOLVED` | 同一问题连续 3 轮未解决 |

### `ai_handoff_record.queue_status`  (PRD §9.5)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 排队中 | `QUEUED` |  |
| `2` | 接入中 | `SERVING` |  |
| `3` | 已完成 | `FINISHED` |  |
| `4` | 用户放弃 | `ABANDONED` | 超 60s 无坐席接入 |

### `ai_message.role`  (PRD §9.1)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `1` | 用户 | `USER` |  |
| `2` | 助手 | `ASSISTANT` |  |
| `3` | 系统 | `SYSTEM` |  |
| `4` | 工具返回 | `TOOL` | 订单查询等工具调用结果 |

### `ai_kb_document.status`  (PRD §9.2)

| 值 | DB 注释名 | PRD 状态名（代码常量） | 说明 |
|----|-----------|------------------------|------|
| `0` | 待处理 | `PENDING` |  |
| `1` | 处理中 | `PROCESSING` |  |
| `2` | 已生效 | `ACTIVE` |  |
| `3` | 处理失败 | `FAILED` | 原因见 error_msg |

---

## 二、表清单

| 域 | 表 | 说明 | 字段数 |
|----|----|------|--------|
| 用户域 | `biz_user` | 用户表 | 13 |
|  | `biz_address` | 收货地址表 | 13 |
| 商品域 | `biz_category` | 商品分类表 | 9 |
|  | `biz_brand` | 品牌表 | 8 |
|  | `biz_spu` | SPU商品表 | 18 |
|  | `biz_spu_image` | SPU图集表 | 5 |
|  | `biz_sku` | SKU表 | 10 |
|  | `biz_sku_stock` | SKU库存表 | 9 |
|  | `biz_stock_log` | 库存变更流水表 | 12 |
|  | `biz_product_review` | 商品评价表 | 15 |
| 交易域 | `biz_order` | 订单主表 | 24 |
|  | `biz_order_item` | 订单明细表 | 17 |
|  | `biz_order_log` | 订单状态流转日志表 | 10 |
|  | `biz_payment` | 支付流水表 | 13 |
|  | `biz_refund` | 售后单表 | 19 |
|  | `biz_cart` | 购物车表 | 7 |
| 营销域 | `biz_freight_template` | 运费模板表 | 10 |
|  | `biz_coupon_template` | 优惠券模板表 | 19 |
|  | `biz_user_coupon` | 用户优惠券表 | 10 |
| 物流域 | `biz_delivery` | 运单表 | 12 |
|  | `biz_delivery_trace` | 物流轨迹表 | 7 |
| AI 客服域 | `ai_kb_document` | AI知识库文档表 | 13 |
|  | `ai_kb_chunk` | AI知识库切片表 | 7 |
|  | `ai_conversation` | AI会话表 | 10 |
|  | `ai_message` | AI消息表 | 13 |
|  | `ai_feedback` | AI会话评价表 | 7 |
|  | `ai_handoff_record` | 转人工记录表 | 12 |
|  | `ai_agent` | 客服坐席表 | 9 |
| 系统域 | `sys_user` | 后台用户表 | 11 |
|  | `sys_role` | 角色表 | 8 |
|  | `sys_permission` | 权限表 | 10 |
|  | `sys_user_role` | 用户角色关联表 | 3 |
|  | `sys_role_permission` | 角色权限关联表 | 3 |
|  | `sys_audit_log` | 操作审计日志表 | 14 |
|  | `sys_local_message` | 本地消息表 | 11 |
|  | `sys_dead_letter` | 死信表 | 11 |
|  | `sys_config` | 系统动态配置表（v1.3 替代 Nacos） | 8 |
| 通知域 | `biz_message` | 站内信表 | 9 |

---

## 三、字段明细

### 用户域

#### `biz_user` — 用户表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | 雪花ID |
| `mobile` | VARCHAR(128) | NOT NULL | — | 手机号密文(AES-256-GCM, Base64) |
| `mobile_hash` | CHAR(64) | NOT NULL | — | 手机号HMAC-SHA256(登录查询/唯一约束) |
| `password` | CHAR(60) |  | NULL | 密码(BCrypt), 验证码注册可为空 |
| `nickname` | VARCHAR(32) |  | NULL | 昵称 |
| `avatar` | VARCHAR(255) |  | NULL | 头像URL |
| `gender` | TINYINT | NOT NULL | 0 | 性别: 0未知 1男 2女 |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1正常 0禁用 |
| `register_channel` | TINYINT | NOT NULL | 1 | 注册渠道: 1手机验证码 2密码 3微信 4支付宝 |
| `last_login_time` | DATETIME |  | NULL | 最后登录时间 |
| `deleted` | TINYINT | NOT NULL | 0 | 逻辑删除: 0否 1是 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_mobile_hash(`mobile_hash`)

#### `biz_address` — 收货地址表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | 雪花ID |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | 用户ID |
| `receiver` | VARCHAR(32) | NOT NULL | — | 收货人 |
| `phone` | VARCHAR(128) | NOT NULL | — | 联系电话密文(AES-256-GCM) |
| `phone_hash` | CHAR(64) | NOT NULL | — | 联系电话HMAC-SHA256(仅查询, 不唯一) |
| `province` | VARCHAR(32) | NOT NULL | — | 省 |
| `city` | VARCHAR(32) | NOT NULL | — | 市 |
| `district` | VARCHAR(32) | NOT NULL | — | 区/县 |
| `detail` | VARCHAR(255) | NOT NULL | — | 详细地址 |
| `is_default` | TINYINT | NOT NULL | 0 | 默认地址: 0否 1是 |
| `deleted` | TINYINT | NOT NULL | 0 | 逻辑删除 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_user_id(`user_id`)

### 商品域

#### `biz_category` — 商品分类表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `parent_id` | BIGINT UNSIGNED | NOT NULL | 0 | 父类目ID, 0为根 |
| `name` | VARCHAR(32) | NOT NULL | — | 类目名称 |
| `level` | TINYINT | NOT NULL | — | 层级: 1/2/3 |
| `icon` | VARCHAR(255) |  | NULL | 图标URL |
| `sort` | INT | NOT NULL | 0 | 排序, 越小越靠前 |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_parent_id(`parent_id`)

#### `biz_brand` — 品牌表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `name` | VARCHAR(64) | NOT NULL | — | 品牌名称 |
| `logo` | VARCHAR(255) |  | NULL | 品牌LOGO |
| `description` | VARCHAR(500) |  | NULL | 品牌描述 |
| `sort` | INT | NOT NULL | 0 | — |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_name(`name`)

#### `biz_spu` — SPU商品表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `category_id` | BIGINT UNSIGNED | NOT NULL | — | 三级类目ID |
| `brand_id` | BIGINT UNSIGNED |  | NULL | 品牌ID |
| `freight_template_id` | BIGINT UNSIGNED |  | NULL | 运费模板ID(PRD §6.6), NULL 用默认模板 |
| `name` | VARCHAR(128) | NOT NULL | — | 商品名称 |
| `subtitle` | VARCHAR(255) |  | NULL | 副标题 |
| `main_pic` | VARCHAR(255) |  | NULL | 主图URL |
| `detail` | LONGTEXT |  | — | 商品富文本详情 |
| `min_price` | DECIMAL(12,2) | NOT NULL | 0.00 | SKU最低价(冗余, 列表排序用) |
| `max_price` | DECIMAL(12,2) | NOT NULL | 0.00 | SKU最高价(冗余) |
| `total_stock` | INT | NOT NULL | 0 | 总库存(冗余, 各SKU total_stock 之和) |
| `sales` | INT UNSIGNED | NOT NULL | 0 | 销量(冗余) |
| `view_count` | BIGINT UNSIGNED | NOT NULL | 0 | 浏览量(异步落库) |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0草稿 1待审核 2上架 3下架 |
| `audit_opinion` | VARCHAR(255) |  | NULL | 审核意见 |
| `deleted` | TINYINT | NOT NULL | 0 | 逻辑删除 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_category_status(`category_id`, `status`)；INDEX idx_brand_id(`brand_id`)；FULLTEXT ft_spu_name(`name`, `subtitle`) WITH PARSER ngram（**v1.2 新增**：商品搜索用，见 PRD §5.6；由 BE-12 经 Alembic 迁移落地）

#### `biz_spu_image` — SPU图集表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `spu_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `url` | VARCHAR(255) | NOT NULL | — | 图片URL |
| `sort` | INT | NOT NULL | 0 | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_spu_id(`spu_id`)

#### `biz_sku` — SKU表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `spu_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_code` | VARCHAR(64) | NOT NULL | — | SKU编码(唯一) |
| `specs` | JSON | NOT NULL | — | 销售规格, 如 [{"k":"颜色","v":"红"},{"k":"尺寸","v":"XL"}] |
| `price` | DECIMAL(12,2) | NOT NULL | — | 售价 |
| `original_price` | DECIMAL(12,2) |  | NULL | 划线价 |
| `pic` | VARCHAR(255) |  | NULL | SKU图片 |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_sku_code(`sku_code`)；INDEX idx_spu_id(`spu_id`)

#### `biz_sku_stock` — SKU库存表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `total_stock` | INT | NOT NULL | 0 | 总库存 |
| `available_stock` | INT | NOT NULL | 0 | 可售库存 |
| `locked_stock` | INT | NOT NULL | 0 | 锁定库存(下单未支付预扣) |
| `sold_stock` | INT | NOT NULL | 0 | 已售出库存 |
| `version` | INT | NOT NULL | 0 | 乐观锁版本号 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_sku_id(`sku_id`)

#### `biz_stock_log` — 库存变更流水表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) |  | NULL | 关联订单号(手动调整为NULL) |
| `refund_no` | VARCHAR(32) |  | NULL | 关联售后单号(退货/退款回补时) |
| `change_type` | TINYINT | NOT NULL | — | 类型: 1下单锁定 2支付扣减 3取消释放 4手动调整 5退货入库 6退款回补 |
| `change_num` | INT | NOT NULL | — | 变更数量(正入负出) |
| `before_num` | INT | NOT NULL | — | 变更前可用库存 |
| `after_num` | INT | NOT NULL | — | 变更后可用库存 |
| `operator_id` | BIGINT UNSIGNED |  | NULL | 操作人(手动调整时) |
| `remark` | VARCHAR(255) |  | NULL | — |
| `idempotent_key` | VARCHAR(96) |  | NULL | 幂等键(PRD §6.8), 构造规则见上 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_idempotent_key(`idempotent_key`)；INDEX idx_sku_id(`sku_id`)；INDEX idx_order_no(`order_no`)；INDEX idx_refund_no(`refund_no`)

#### `biz_product_review` — 商品评价表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_item_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `spu_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `score` | TINYINT | NOT NULL | — | 评分: 1-5星 |
| `content` | VARCHAR(500) |  | NULL | 评价内容 |
| `images` | JSON |  | — | 评价图片URL数组 |
| `is_anonymous` | TINYINT | NOT NULL | 0 | 匿名评价: 0否 1是 |
| `reply` | VARCHAR(500) |  | NULL | 商家回复 |
| `reply_time` | DATETIME |  | NULL | — |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1显示 0隐藏(违规) |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_order_item_id(`order_item_id`)；INDEX idx_spu_id_score(`spu_id`, `score`)；INDEX idx_spu_time(`spu_id`, `create_time`)

### 交易域

#### `biz_order` — 订单主表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) | NOT NULL | — | 业务订单号(对外唯一) |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | 下单用户(预留分片键) |
| `status` | TINYINT | NOT NULL | 10 | 状态: 10待付款 20待发货 30待收货 40待评价 50已完成 60已取消 70售后中 80已关闭 |
| `total_amount` | DECIMAL(12,2) | NOT NULL | — | 商品总额 = Σ order_item.total_amount |
| `discount_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 优惠金额 = Σ order_item.discount_amount |
| `freight_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 运费 |
| `pay_amount` | DECIMAL(12,2) | NOT NULL | — | 实付金额 = total - discount + freight |
| `refund_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 累计已退金额(含运费), 约束 <= pay_amount |
| `coupon_id` | BIGINT UNSIGNED |  | NULL | 使用的用户优惠券ID(biz_user_coupon.id) |
| `receiver` | VARCHAR(32) | NOT NULL | — | 收货人快照 |
| `receiver_phone` | VARCHAR(128) | NOT NULL | — | 电话快照密文(AES-256-GCM) |
| `receiver_addr` | VARCHAR(500) | NOT NULL | — | 完整地址快照(省市区拼接) |
| `pay_type` | TINYINT |  | NULL | 支付方式: 1支付宝 2微信 |
| `pay_time` | DATETIME |  | NULL | — |
| `delivery_company` | VARCHAR(64) |  | NULL | 快递公司(冗余自 biz_delivery, 列表展示用) |
| `delivery_no` | VARCHAR(64) |  | NULL | 主运单号(冗余自 biz_delivery) |
| `deliver_time` | DATETIME |  | NULL | 发货时间 |
| `finish_time` | DATETIME |  | NULL | 完成时间 |
| `cancel_reason` | VARCHAR(255) |  | NULL | 取消原因 |
| `cancel_time` | DATETIME |  | NULL | — |
| `version` | INT | NOT NULL | 0 | 乐观锁 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_order_no(`order_no`)；INDEX idx_user_status(`user_id`, `status`)；INDEX idx_status_create(`status`, `create_time`)；INDEX idx_create_time(`create_time`)

#### `biz_order_item` — 订单明细表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) | NOT NULL | — | 冗余订单号 |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | 冗余, 售后查询用 |
| `spu_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `spu_name` | VARCHAR(128) | NOT NULL | — | 商品名称快照 |
| `sku_pic` | VARCHAR(255) |  | NULL | SKU图片快照 |
| `sku_specs` | JSON |  | — | 规格快照 |
| `price` | DECIMAL(12,2) | NOT NULL | — | 成交单价快照 |
| `quantity` | INT | NOT NULL | — | 购买数量 |
| `total_amount` | DECIMAL(12,2) | NOT NULL | — | 小计 = price * quantity |
| `discount_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 优惠券分摊金额(按小计比例分摊, 最后一件补差) |
| `pay_amount` | DECIMAL(12,2) | NOT NULL | — | 实付小计 = total_amount - discount_amount |
| `refund_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 累计已退金额, 约束 <= pay_amount |
| `refund_status` | TINYINT | NOT NULL | 0 | 售后状态: 0无 1申请中 2退款中 3已退款 4已拒绝 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_order_id(`order_id`)；INDEX idx_order_no(`order_no`)；INDEX idx_user_id(`user_id`)；INDEX idx_spu_id(`spu_id`)

#### `biz_order_log` — 订单状态流转日志表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) | NOT NULL | — | — |
| `from_status` | TINYINT |  | NULL | 变更前状态, NULL 表示创建 |
| `to_status` | TINYINT | NOT NULL | — | 变更后状态 |
| `event` | VARCHAR(32) | NOT NULL | — | 触发事件: SUBMIT/PAY/TIMEOUT_CANCEL/DELIVER/CONFIRM/AFTER_SALE... |
| `operator_type` | TINYINT | NOT NULL | 1 | 操作者: 1用户 2商家 3系统 4客服 |
| `operator_id` | BIGINT UNSIGNED |  | NULL | — |
| `remark` | VARCHAR(255) |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_order_id(`order_id`, `create_time`)

#### `biz_payment` — 支付流水表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `payment_no` | VARCHAR(32) | NOT NULL | — | 支付单号(商户订单号 out_trade_no) |
| `order_no` | VARCHAR(32) | NOT NULL | — | 订单号 |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `pay_type` | TINYINT | NOT NULL | — | 支付方式: 1支付宝 2微信 |
| `amount` | DECIMAL(12,2) | NOT NULL | — | 支付金额 |
| `refunded_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 累计已退金额, 约束 <= amount |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待支付 1已发起待回调 2成功 3失败 4已关闭 |
| `channel_trade_no` | VARCHAR(64) |  | NULL | 渠道交易号 |
| `channel_resp` | JSON |  | — | 渠道回调原始报文(验签后) |
| `callback_time` | DATETIME |  | NULL | 成功回调时间 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_payment_no(`payment_no`)；UNIQUE uk_channel_trade_no(`channel_trade_no`)；INDEX idx_order_no(`order_no`)；INDEX idx_status_update(`status`, `update_time`)

#### `biz_refund` — 售后单表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `refund_no` | VARCHAR(32) | NOT NULL | — | 售后单号 |
| `order_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) | NOT NULL | — | 冗余订单号 |
| `order_item_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `type` | TINYINT | NOT NULL | — | 类型: 1仅退款 2退货退款 |
| `amount` | DECIMAL(12,2) | NOT NULL | — | 退款金额(商品分摊金额, 运费单独退) |
| `freight_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 退还运费(PRD §6.6: 运费全额退还) |
| `reason` | VARCHAR(255) | NOT NULL | — | 申请原因 |
| `evidence` | JSON |  | — | 凭证图片URL数组 |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待审核 1已拒绝 2待退货 3待收货 4退款中 5已完成 6退款失败 7已撤销 |
| `audit_opinion` | VARCHAR(255) |  | NULL | 审核意见 |
| `audit_user_id` | BIGINT UNSIGNED |  | NULL | 审核人 |
| `audit_time` | DATETIME |  | NULL | — |
| `channel_refund_no` | VARCHAR(64) |  | NULL | 渠道退款单号 |
| `finish_time` | DATETIME |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_refund_no(`refund_no`)；INDEX idx_order_no(`order_no`)；INDEX idx_user_id(`user_id`)；INDEX idx_order_item_id(`order_item_id`)；INDEX idx_status(`status`)

#### `biz_cart` — 购物车表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `sku_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `quantity` | INT | NOT NULL | 1 | 数量(>0) |
| `checked` | TINYINT | NOT NULL | 1 | 是否勾选: 0否 1是 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_user_sku(`user_id`, `sku_id`)

### 营销域

#### `biz_freight_template` — 运费模板表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `name` | VARCHAR(64) | NOT NULL | — | 模板名称 |
| `free_threshold` | DECIMAL(12,2) | NOT NULL | 0.00 | 包邮门槛(商品实付金额), 0为不包邮 |
| `base_freight` | DECIMAL(12,2) | NOT NULL | 0.00 | 基础运费 |
| `remote_extra` | DECIMAL(12,2) | NOT NULL | 0.00 | 偏远地区附加费 |
| `remote_regions` | JSON |  | — | 偏远地区省份数组, 如 ["新疆","西藏"] |
| `is_default` | TINYINT | NOT NULL | 0 | 是否默认模板: 0否 1是(全局唯一) |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)

#### `biz_coupon_template` — 优惠券模板表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `name` | VARCHAR(64) | NOT NULL | — | 券名称 |
| `type` | TINYINT | NOT NULL | — | 类型: 1满减 2折扣 3无门槛 |
| `threshold_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 使用门槛(满X元), 0为无门槛 |
| `discount_amount` | DECIMAL(12,2) | NOT NULL | 0.00 | 优惠金额(满减/无门槛) |
| `discount_rate` | DECIMAL(3,2) |  | NULL | 折扣率(折扣券, 如0.90) |
| `max_discount` | DECIMAL(12,2) |  | NULL | 最大优惠(折扣券封顶) |
| `total_count` | INT | NOT NULL | 0 | 发放总量 |
| `remain_count` | INT | NOT NULL | 0 | 剩余数量(原子扣减: UPDATE ... WHERE remain_count >= 1) |
| `per_limit` | TINYINT | NOT NULL | 1 | 每人限领张数(应用层校验已领数) |
| `start_time` | DATETIME | NOT NULL | — | 可领取开始时间 |
| `end_time` | DATETIME | NOT NULL | — | 可领取结束时间 |
| `valid_days` | INT | NOT NULL | 7 | 领取后有效天数 |
| `scope_type` | TINYINT | NOT NULL | 1 | 适用范围: 1全场 2指定类目 3指定商品 |
| `scope_ids` | JSON |  | — | 指定类目/商品ID数组 |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0草稿 1发放中 2已结束 3已下架 |
| `deleted` | TINYINT | NOT NULL | 0 | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_status_time(`status`, `start_time`, `end_time`)

#### `biz_user_coupon` — 用户优惠券表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `coupon_id` | BIGINT UNSIGNED | NOT NULL | — | biz_coupon_template.id |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1未使用 2锁定中 3已使用 4已过期 5冻结中(退款) |
| `order_id` | BIGINT UNSIGNED |  | NULL | 锁定/使用的订单ID |
| `receive_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 领取时间 |
| `expire_time` | DATETIME | NOT NULL | — | 过期时间 |
| `use_time` | DATETIME |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_user_status(`user_id`, `status`)；INDEX idx_coupon_id(`coupon_id`)；INDEX idx_status_expire(`status`, `expire_time`)

### 物流域

#### `biz_delivery` — 运单表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `company_code` | VARCHAR(32) | NOT NULL | — | 快递公司编码(如 SF/YTO) |
| `company_name` | VARCHAR(64) | NOT NULL | — | 快递公司名称 |
| `delivery_no` | VARCHAR(64) | NOT NULL | — | 运单号 |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待揽收 1运输中 2派送中 3已签收 4异常 |
| `deliver_time` | DATETIME |  | NULL | 发货时间 |
| `sign_time` | DATETIME |  | NULL | 签收时间 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_delivery_no(`delivery_no`)；INDEX idx_order_no(`order_no`)

#### `biz_delivery_trace` — 物流轨迹表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `delivery_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `delivery_no` | VARCHAR(64) | NOT NULL | — | 冗余运单号 |
| `trace_time` | DATETIME | NOT NULL | — | 轨迹发生时间 |
| `status_desc` | VARCHAR(64) | NOT NULL | — | 轨迹描述(如"已签收") |
| `location` | VARCHAR(128) |  | NULL | 所在地点 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_delivery_time_desc(`delivery_no`, `trace_time`, `status_desc`)；INDEX idx_delivery_id(`delivery_id`)

### AI 客服域

#### `ai_kb_document` — AI知识库文档表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `title` | VARCHAR(128) | NOT NULL | — | 文档标题 |
| `domain` | VARCHAR(16) | NOT NULL | — | 知识域: product/policy/faq |
| `file_type` | VARCHAR(16) | NOT NULL | 'md' | 类型: pdf/docx/md/text |
| `file_url` | VARCHAR(255) |  | NULL | 原始文件URL(手工录入为NULL) |
| `content` | LONGTEXT |  | — | 纯文本内容 |
| `chunk_count` | INT | NOT NULL | 0 | 切片数量 |
| `enabled` | TINYINT | NOT NULL | 1 | 是否生效: 0否 1是 |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待处理 1处理中 2已生效 3处理失败 |
| `error_msg` | VARCHAR(500) |  | NULL | 处理失败原因 |
| `version` | INT | NOT NULL | 1 | 内容版本(重建索引用) |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_domain_enabled(`domain`, `enabled`)

#### `ai_kb_chunk` — AI知识库切片表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | 同时作为 Milvus 向量记录主键 |
| `document_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `domain` | VARCHAR(16) | NOT NULL | — | 冗余知识域, 检索路由用 |
| `content` | TEXT | NOT NULL | — | 切片原文 |
| `token_count` | INT | NOT NULL | 0 | 切片token数 |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1生效 0失效(文档重建后旧切片置0) |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_document_id(`document_id`)；INDEX idx_domain_status(`domain`, `status`)

#### `ai_conversation` — AI会话表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `conversation_no` | VARCHAR(32) | NOT NULL | — | 会话编号 |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | 游客使用匿名会话ID(负数或独立号段) |
| `channel` | TINYINT | NOT NULL | 1 | 来源: 1商品页 2订单页 3独立对话页 |
| `spu_id` | BIGINT UNSIGNED |  | NULL | 咨询商品(商品页来源时) |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1 AI接待中 2待人工接入 3人工接待中 4已结束 |
| `resolved` | TINYINT |  | NULL | 是否解决: 1是 0否 (结束时按 PRD §9.6 判定) |
| `close_time` | DATETIME |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_conversation_no(`conversation_no`)；INDEX idx_user_id(`user_id`, `status`)；INDEX idx_status_update(`status`, `update_time`)

#### `ai_message` — AI消息表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `conversation_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `role` | TINYINT | NOT NULL | — | 角色: 1用户 2助手 3系统 4工具返回 |
| `content` | LONGTEXT |  | — | 消息内容 |
| `intent` | VARCHAR(16) |  | NULL | 识别意图: presale/order/aftersale/chat/handoff |
| `confidence` | DECIMAL(4,3) |  | NULL | 意图置信度 0-1 |
| `retrieved_chunk_ids` | JSON |  | — | 本次检索命中的切片ID数组(溯源) |
| `model` | VARCHAR(64) |  | NULL | 使用的Ark模型 |
| `prompt_tokens` | INT |  | NULL | — |
| `completion_tokens` | INT |  | NULL | — |
| `latency_ms` | INT |  | NULL | 首字延迟ms |
| `filtered` | TINYINT | NOT NULL | 0 | 是否被敏感词拦截: 0否 1是 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_conversation_id(`conversation_id`, `create_time`)

#### `ai_feedback` — AI会话评价表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `conversation_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `message_id` | BIGINT UNSIGNED | NOT NULL | — | 针对的助手消息 |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `rating` | TINYINT | NOT NULL | — | 评价: 1赞 -1踩 |
| `comment` | VARCHAR(255) |  | NULL | 补充说明 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_message_user(`message_id`, `user_id`)；INDEX idx_conversation_id(`conversation_id`)

#### `ai_handoff_record` — 转人工记录表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `conversation_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `order_no` | VARCHAR(32) |  | NULL | 关联订单号(争议场景) |
| `reason` | TINYINT | NOT NULL | — | 转接原因: 1低置信度 2用户要求 3订单争议 4多次不满 5连续未解决 |
| `queue_status` | TINYINT | NOT NULL | 1 | 状态: 1排队中 2接入中 3已完成 4用户放弃 |
| `agent_id` | BIGINT UNSIGNED |  | NULL | 客服人员(ai_agent.id) |
| `agent_name` | VARCHAR(32) |  | NULL | — |
| `accept_time` | DATETIME |  | NULL | 接入时间 |
| `close_time` | DATETIME |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_conversation_id(`conversation_id`)；INDEX idx_agent_status(`agent_id`, `queue_status`)；INDEX idx_queue_status_time(`queue_status`, `create_time`)

#### `ai_agent` — 客服坐席表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | 关联 sys_user.id |
| `agent_no` | VARCHAR(32) | NOT NULL | — | 坐席工号 |
| `nickname` | VARCHAR(32) | NOT NULL | — | 对外展示名 |
| `max_concurrent` | TINYINT | NOT NULL | 5 | 最大同时接待会话数 |
| `skill_tags` | VARCHAR(128) |  | NULL | 技能标签(逗号分隔, 用于路由) |
| `status` | TINYINT | NOT NULL | 0 | 启用状态: 0停用 1启用(实时在线态见Redis) |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_agent_no(`agent_no`)；UNIQUE uk_user_id(`user_id`)

### 系统域 (RBAC + 审计 + 可靠投递 + 动态配置)

#### `sys_user` — 后台用户表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `username` | VARCHAR(32) | NOT NULL | — | 登录名 |
| `password` | CHAR(60) | NOT NULL | — | 密码(BCrypt) |
| `real_name` | VARCHAR(32) |  | NULL | 姓名 |
| `mobile` | VARCHAR(128) |  | NULL | 手机号密文(AES-256-GCM) |
| `mobile_hash` | CHAR(64) |  | NULL | 手机号HMAC-SHA256 |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1正常 0禁用 |
| `last_login_time` | DATETIME |  | NULL | — |
| `deleted` | TINYINT | NOT NULL | 0 | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_username(`username`)；INDEX idx_mobile_hash(`mobile_hash`)

#### `sys_role` — 角色表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `name` | VARCHAR(32) | NOT NULL | — | 角色名称 |
| `code` | VARCHAR(32) | NOT NULL | — | 角色编码, 如 admin/agent/ops |
| `data_scope` | TINYINT | NOT NULL | 1 | 数据权限范围: 1仅本人 2本部门 3全量(PRD §2.2) |
| `remark` | VARCHAR(255) |  | NULL | — |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_code(`code`)

#### `sys_permission` — 权限表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `parent_id` | BIGINT UNSIGNED | NOT NULL | 0 | 父权限, 0为根 |
| `name` | VARCHAR(32) | NOT NULL | — | 权限名称 |
| `type` | TINYINT | NOT NULL | — | 类型: 1菜单 2按钮 3接口 |
| `perm_code` | VARCHAR(64) | NOT NULL | — | 权限标识, 如 order:ship |
| `path` | VARCHAR(128) |  | NULL | 前端路由/后端接口路径 |
| `sort` | INT | NOT NULL | 0 | — |
| `status` | TINYINT | NOT NULL | 1 | 状态: 1启用 0禁用 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_perm_code(`perm_code`)；INDEX idx_parent_id(`parent_id`)

#### `sys_user_role` — 用户角色关联表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `role_id` | BIGINT UNSIGNED | NOT NULL | — | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_user_role(`user_id`, `role_id`)；INDEX idx_role_id(`role_id`)

#### `sys_role_permission` — 角色权限关联表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `role_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `permission_id` | BIGINT UNSIGNED | NOT NULL | — | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_role_perm(`role_id`, `permission_id`)；INDEX idx_permission_id(`permission_id`)

#### `sys_audit_log` — 操作审计日志表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `operator_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `operator_name` | VARCHAR(32) | NOT NULL | — | 冗余操作人姓名 |
| `module` | VARCHAR(32) | NOT NULL | — | 模块, 如 order/product |
| `action` | VARCHAR(32) | NOT NULL | — | 动作, 如 ship/audit/refund |
| `method` | VARCHAR(8) |  | NULL | HTTP方法 |
| `path` | VARCHAR(255) |  | NULL | 请求路径 |
| `params` | JSON |  | — | 请求参数(脱敏后) |
| `result_code` | INT |  | NULL | 响应code |
| `ip` | VARCHAR(64) |  | NULL | 操作IP |
| `user_agent` | VARCHAR(255) |  | NULL | — |
| `cost_ms` | INT |  | NULL | 耗时 |
| `trace_id` | VARCHAR(64) |  | NULL | 全链路追踪ID |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_operator_time(`operator_id`, `create_time`)；INDEX idx_module_action(`module`, `action`)；INDEX idx_trace_id(`trace_id`)

#### `sys_local_message` — 本地消息表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `biz_type` | VARCHAR(32) | NOT NULL | — | 业务类型: ORDER/PAYMENT/STOCK/PRODUCT... |
| `biz_no` | VARCHAR(64) | NOT NULL | — | 业务单号(幂等键) |
| `topic` | VARCHAR(64) | NOT NULL | — | 目标Kafka topic |
| `payload` | JSON | NOT NULL | — | 消息体 |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待投递 1已投递 2投递失败(超重试上限) |
| `retry_count` | INT | NOT NULL | 0 | — |
| `next_retry_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 下次重试时间(指数退避) |
| `error_msg` | VARCHAR(500) |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；UNIQUE uk_biz_topic(`biz_type`, `biz_no`, `topic`)；INDEX idx_status_retry(`status`, `next_retry_time`)

#### `sys_dead_letter` — 死信表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `source` | VARCHAR(32) | NOT NULL | — | 来源: KAFKA/DELAY_TASK/LOCAL_MSG |
| `ref_key` | VARCHAR(128) | NOT NULL | — | 业务唯一键(如 order_no) |
| `topic` | VARCHAR(64) |  | NULL | 来源topic |
| `payload` | JSON |  | — | 原始消息体 |
| `error_msg` | VARCHAR(1000) |  | NULL | — |
| `retry_count` | INT | NOT NULL | 0 | — |
| `status` | TINYINT | NOT NULL | 0 | 状态: 0待处理 1已重放 2已忽略 |
| `handle_time` | DATETIME |  | NULL | — |
| `handler_id` | BIGINT UNSIGNED |  | NULL | 处理人 |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_source_status(`source`, `status`)；INDEX idx_ref_key(`ref_key`)

#### `sys_config` — 系统动态配置表（v1.3 替代 Nacos 配置中心）

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | 雪花ID |
| `config_key` | VARCHAR(128) | NOT NULL | — | 配置键, 如 `ai.retrieval_score_threshold` |
| `config_value` | VARCHAR(512) | NOT NULL | — | 配置值(字符串, 按 `value_type` 解析) |
| `value_type` | TINYINT | NOT NULL | 0 | 值类型: 0 string 1 int 2 float 3 bool 4 json |
| `description` | VARCHAR(255) | NOT NULL | '' | 配置说明 |
| `updated_by` | BIGINT UNSIGNED | NOT NULL | 0 | 最后修改人 sys_user.id |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |
| `update_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY(`id`)；UNIQUE uk_config_key(`config_key`)

> **用途**：后台可视化调整 AI 阈值、坐席接待上限等动态配置；写入后经 Redis 发布订阅通知各服务热更新，无需重启。静态配置仍走 pydantic-settings（`.env`）。

### 通知域

#### `biz_message` — 站内信表

| 字段 | 类型 | 空 | 默认 | 说明 |
|------|------|----|------|------|
| `id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `user_id` | BIGINT UNSIGNED | NOT NULL | — | — |
| `type` | TINYINT | NOT NULL | — | 类型: 1订单 2售后 3系统 4营销 |
| `title` | VARCHAR(64) | NOT NULL | — | — |
| `content` | VARCHAR(500) | NOT NULL | — | — |
| `biz_id` | BIGINT UNSIGNED |  | NULL | 关联业务ID(订单/售后单) |
| `is_read` | TINYINT | NOT NULL | 0 | 已读: 0否 1是 |
| `read_time` | DATETIME |  | NULL | — |
| `create_time` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | — |

**索引**：PRIMARY PRIMARY(`id`)；INDEX idx_user_read_time(`user_id`, `is_read`, `create_time`)

---

## 四、数据库约束与不变量

> 以下约束由 DDL 或应用层强制，**压测与对账任务须逐条校验**（PRD §12.2）。

| # | 约束 | 位置 | 强制方式 | 违反后果 |
|---|------|------|----------|----------|
| 1 | `total_stock = available_stock + locked_stock + sold_stock` | `biz_sku_stock` | 应用层保证，BE-36 定时对账 | 库存账实不符 |
| 2 | 库存四段均 `>= 0` | `biz_sku_stock` | DDL `CHECK chk_stock_non_negative` | 超卖 |
| 3 | `refund_amount <= pay_amount` | `biz_order` / `biz_order_item` | 应用层（BE-27） | 超额退款资损 |
| 4 | `refunded_amount <= amount` | `biz_payment` | 应用层（BE-28） | 超额退款资损 |
| 5 | `Σ order_item.discount_amount = order.discount_amount` | 交易域 | 应用层（BE-19 分摊算法） | 优惠金额不平 |
| 6 | 同用户仅一个默认地址 | `biz_address` | 应用层（BE-08 互斥更新） | 下单取错地址 |
| 7 | 仅一个默认运费模板 | `biz_freight_template` | 应用层 + seed 校验 | 运费计算不确定 |
| 8 | `remain_count >= 0` | `biz_coupon_template` | 原子扣减 `UPDATE ... WHERE remain_count >= 1` | 超发券 |
| 9 | 库存流水幂等键唯一 | `biz_stock_log.idempotent_key` | DDL UNIQUE | 重复扣减 |
| 10 | 渠道交易号唯一 | `biz_payment.channel_trade_no` | DDL UNIQUE（NULL 不冲突） | 重复发货 |
| 11 | 一个订单项一条评价 | `biz_product_review.order_item_id` | DDL UNIQUE | 刷评价 |
| 12 | 本地消息幂等 | `sys_local_message(biz_type,biz_no,topic)` | DDL UNIQUE | 重复投递 |

**幂等键构造规则**（`biz_stock_log.idempotent_key`，PRD §6.8）：

```
下单锁定  LOCK:{orderNo}:{skuId}
支付扣减  PAY:{orderNo}:{skuId}
取消释放  RELEASE:{orderNo}:{skuId}
退货入库  RETURN:{refundNo}:{skuId}
退款回补  REFUND:{refundNo}:{skuId}
手动调整  NULL（允许同一 SKU 重复调整）
```

> 退款类用 `refundNo` 而非 `orderNo` 作键，是为支持同一订单项多次部分退款。
