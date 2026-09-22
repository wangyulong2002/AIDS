-- =============================================================
-- AIDS 企业级电商平台 - 数据库 DDL（主业务库）
-- 版本: v1.2 | 数据库: MySQL 8.0.16+ | 字符集: utf8mb4 / utf8mb4_0900_ai_ci
-- 约定（源自 PRD §5.4 / §5.7）:
--   1. 表前缀: biz_(业务) / ai_(AI客服) / sys_(系统)
--   2. 主键: BIGINT UNSIGNED 雪花ID, 由应用层生成, 非自增
--   3. 金额: 一律 DECIMAL(12,2), 禁止 float/double
--   4. 时间: DATETIME, 统一 UTC 存储。应用连接串需设置 connectionTimeZone=UTC
--      或连接后执行 SET time_zone='+00:00'，否则 CURRENT_TIMESTAMP 取的是服务器本地时区
--   5. 逻辑删除: deleted (0未删 1已删), 仅用于用户/商品等主数据
--   6. 状态字段: TINYINT, 注释中枚举取值，并与 PRD 状态机名称一一对应
--   7. 乐观锁: version 字段, 用于库存扣减/订单并发场景
--   8. 外键: 不使用物理外键, 由应用层保证引用完整性（便于分库与数据归档）
--   9. 敏感字段: 手机号 AES-256-GCM 加密存储, 另设 HMAC-SHA256 哈希列用于查询与唯一约束
--      注意必须用 HMAC(带密钥) 而非裸 SHA-256 —— 手机号仅 11 位(约 14 亿种组合),
--      裸哈希可被彩虹表瞬间反查, 等同于明文。密钥由环境变量注入, 与数据库分开保管。
--      （由 SQLAlchemy TypeDecorator 透明加解密，业务代码无感）
--  10. 需要 MySQL >= 8.0.16 以支持 CHECK 约束
--
-- 配套文件:
--   mock_schema.sql —— Mock 支付/短信/物流服务独立库（独立部署，独立 schema）
--
-- 【CI 如何建到隔离测试库】本文件的库名是固定字面量 `aids_shop`。CI 的目标是
--   独立库 `aids_shop_test`，做法是导入前用 sed 替换库名（见 .github/workflows/ci.yml
--   的「载入 DDL」步骤），而不是在这里引入变量。
--   为什么不写成 ${AIDS_MAIN_DB:-aids_shop}：**mysql 客户端不展开这种写法**。
--   实测（mysql 8.4.11）：建库后 SHOW DATABASES 出现字面名为
--   "${AIDS_MAIN_DB:-aids_shop}" 的库，38 张表全建了进去。它看起来像 shell 变量，
--   但 SQL 文件里的 ${...} 对客户端只是普通文本，既不展开也不报错——
--   静默把表建进一个垃圾库，比直接失败更难排查。（CI 步骤里因此加了表数自检。）
--   为什么不能反过来"让 CI 也建 aids_shop 再改连接串"：启动断言 S1-d
--   （app/core/config.py::assert_db_is_isolated）**禁止** test 环境连库名含
--   aids_shop 的库。建的时候图省事，测试连上的瞬间就会拒启。
-- =============================================================
CREATE DATABASE IF NOT EXISTS `aids_shop`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `aids_shop`;

-- =============================================================
-- 一、用户域
-- =============================================================

-- 用户表
CREATE TABLE IF NOT EXISTS `biz_user` (
  `id`                BIGINT UNSIGNED NOT NULL COMMENT '雪花ID',
  `mobile`            VARCHAR(128)    NOT NULL COMMENT '手机号密文(AES-256-GCM, Base64)',
  `mobile_hash`       CHAR(64)        NOT NULL COMMENT '手机号HMAC-SHA256(登录查询/唯一约束)',
  `password`          CHAR(60)                 DEFAULT NULL COMMENT '密码(BCrypt), 验证码注册可为空',
  `nickname`          VARCHAR(32)              DEFAULT NULL COMMENT '昵称',
  `avatar`            VARCHAR(255)             DEFAULT NULL COMMENT '头像URL',
  `gender`            TINYINT         NOT NULL DEFAULT 0 COMMENT '性别: 0未知 1男 2女',
  `status`            TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1正常 0禁用',
  `register_channel`  TINYINT         NOT NULL DEFAULT 1 COMMENT '注册渠道: 1手机验证码 2密码 3微信 4支付宝',
  `last_login_time`   DATETIME                 DEFAULT NULL COMMENT '最后登录时间',
  `deleted`           TINYINT         NOT NULL DEFAULT 0 COMMENT '逻辑删除: 0否 1是',
  `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mobile_hash` (`mobile_hash`)
) ENGINE=InnoDB COMMENT='用户表';

-- 收货地址表
CREATE TABLE IF NOT EXISTS `biz_address` (
  `id`          BIGINT UNSIGNED NOT NULL COMMENT '雪花ID',
  `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
  `receiver`    VARCHAR(32)     NOT NULL COMMENT '收货人',
  `phone`       VARCHAR(128)    NOT NULL COMMENT '联系电话密文(AES-256-GCM)',
  `phone_hash`  CHAR(64)        NOT NULL COMMENT '联系电话HMAC-SHA256(仅查询, 不唯一)',
  `province`    VARCHAR(32)     NOT NULL COMMENT '省',
  `city`        VARCHAR(32)     NOT NULL COMMENT '市',
  `district`    VARCHAR(32)     NOT NULL COMMENT '区/县',
  `detail`      VARCHAR(255)    NOT NULL COMMENT '详细地址',
  `is_default`  TINYINT         NOT NULL DEFAULT 0 COMMENT '默认地址: 0否 1是',
  `deleted`     TINYINT         NOT NULL DEFAULT 0 COMMENT '逻辑删除',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_id` (`user_id`)
) ENGINE=InnoDB COMMENT='收货地址表';

-- =============================================================
-- 二、商品域
-- =============================================================

-- 商品分类表 (三级类目树)
CREATE TABLE IF NOT EXISTS `biz_category` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `parent_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '父类目ID, 0为根',
  `name`        VARCHAR(32)     NOT NULL COMMENT '类目名称',
  `level`       TINYINT         NOT NULL COMMENT '层级: 1/2/3',
  `icon`        VARCHAR(255)             DEFAULT NULL COMMENT '图标URL',
  `sort`        INT             NOT NULL DEFAULT 0 COMMENT '排序, 越小越靠前',
  `status`      TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_parent_id` (`parent_id`)
) ENGINE=InnoDB COMMENT='商品分类表';

-- 品牌表
CREATE TABLE IF NOT EXISTS `biz_brand` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(64)     NOT NULL COMMENT '品牌名称',
  `logo`        VARCHAR(255)             DEFAULT NULL COMMENT '品牌LOGO',
  `description` VARCHAR(500)             DEFAULT NULL COMMENT '品牌描述',
  `sort`        INT             NOT NULL DEFAULT 0,
  `status`      TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_name` (`name`)
) ENGINE=InnoDB COMMENT='品牌表';

-- SPU表 (标准产品单位)
CREATE TABLE IF NOT EXISTS `biz_spu` (
  `id`             BIGINT UNSIGNED NOT NULL,
  `category_id`    BIGINT UNSIGNED NOT NULL COMMENT '三级类目ID',
  `brand_id`       BIGINT UNSIGNED          DEFAULT NULL COMMENT '品牌ID',
  `freight_template_id` BIGINT UNSIGNED     DEFAULT NULL COMMENT '运费模板ID(PRD §6.6), NULL 用默认模板',
  `name`           VARCHAR(128)    NOT NULL COMMENT '商品名称',
  `subtitle`       VARCHAR(255)             DEFAULT NULL COMMENT '副标题',
  `main_pic`       VARCHAR(255)             DEFAULT NULL COMMENT '主图URL',
  `detail`         LONGTEXT                 COMMENT '商品富文本详情',
  `min_price`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT 'SKU最低价(冗余, 列表排序用)',
  `max_price`      DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT 'SKU最高价(冗余)',
  `total_stock`    INT             NOT NULL DEFAULT 0 COMMENT '总库存(冗余, 各SKU total_stock 之和)',
  `sales`          INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '销量(冗余)',
  `view_count`     BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '浏览量(异步落库)',
  `status`         TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0草稿 1待审核 2上架 3下架',
  `audit_opinion`  VARCHAR(255)             DEFAULT NULL COMMENT '审核意见',
  `deleted`        TINYINT         NOT NULL DEFAULT 0 COMMENT '逻辑删除',
  `create_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_category_status` (`category_id`, `status`),
  KEY `idx_brand_id` (`brand_id`)
) ENGINE=InnoDB COMMENT='SPU商品表';

-- SPU 图集表
CREATE TABLE IF NOT EXISTS `biz_spu_image` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `spu_id`      BIGINT UNSIGNED NOT NULL,
  `url`         VARCHAR(255)    NOT NULL COMMENT '图片URL',
  `sort`        INT             NOT NULL DEFAULT 0,
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_spu_id` (`spu_id`)
) ENGINE=InnoDB COMMENT='SPU图集表';

-- SKU表 (最小库存单位)
CREATE TABLE IF NOT EXISTS `biz_sku` (
  `id`             BIGINT UNSIGNED NOT NULL,
  `spu_id`         BIGINT UNSIGNED NOT NULL,
  `sku_code`       VARCHAR(64)     NOT NULL COMMENT 'SKU编码(唯一)',
  `specs`          JSON            NOT NULL COMMENT '销售规格, 如 [{"k":"颜色","v":"红"},{"k":"尺寸","v":"XL"}]',
  `price`          DECIMAL(12,2)   NOT NULL COMMENT '售价',
  `original_price` DECIMAL(12,2)            DEFAULT NULL COMMENT '划线价',
  `pic`            VARCHAR(255)             DEFAULT NULL COMMENT 'SKU图片',
  `status`         TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sku_code` (`sku_code`),
  KEY `idx_spu_id` (`spu_id`)
) ENGINE=InnoDB COMMENT='SKU表';

-- SKU 库存表 (防超卖核心表, 乐观锁 + 恒等式校验)
-- 恒等式: total_stock = available_stock + locked_stock + sold_stock
--   下单预扣: available-1, locked+1
--   支付成功: locked-1, sold+1
--   超时/取消: locked-1, available+1
--   退款回补: sold-1, available+1
--   手动调整: total+n, available+n
-- 每一步均保持恒等式成立; 定时对账任务按该式校验一致性
CREATE TABLE IF NOT EXISTS `biz_sku_stock` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `sku_id`          BIGINT UNSIGNED NOT NULL,
  `total_stock`     INT             NOT NULL DEFAULT 0 COMMENT '总库存',
  `available_stock` INT             NOT NULL DEFAULT 0 COMMENT '可售库存',
  `locked_stock`    INT             NOT NULL DEFAULT 0 COMMENT '锁定库存(下单未支付预扣)',
  `sold_stock`      INT             NOT NULL DEFAULT 0 COMMENT '已售出库存',
  `version`         INT             NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sku_id` (`sku_id`),
  CONSTRAINT `chk_stock_non_negative`
    CHECK (`total_stock` >= 0 AND `available_stock` >= 0
           AND `locked_stock` >= 0 AND `sold_stock` >= 0)
) ENGINE=InnoDB COMMENT='SKU库存表';

-- 库存变更流水表 (审计 + 幂等 + 对账)
-- idempotent_key 由应用层构造, 保证同一业务动作只落一条流水:
--   下单锁定  LOCK:{orderNo}:{skuId}
--   支付扣减  PAY:{orderNo}:{skuId}
--   取消释放  RELEASE:{orderNo}:{skuId}
--   退货入库  RETURN:{refundNo}:{skuId}
--   退款回补  REFUND:{refundNo}:{skuId}
--   手动调整  留 NULL(允许同一 SKU 重复调整)
-- 用 refundNo 而非 orderNo 作为退款类键, 是为支持同一订单项多次部分退款(PRD §6.6)
CREATE TABLE IF NOT EXISTS `biz_stock_log` (
  `id`             BIGINT UNSIGNED NOT NULL,
  `sku_id`         BIGINT UNSIGNED NOT NULL,
  `order_no`       VARCHAR(32)              DEFAULT NULL COMMENT '关联订单号(手动调整为NULL)',
  `refund_no`      VARCHAR(32)              DEFAULT NULL COMMENT '关联售后单号(退货/退款回补时)',
  `change_type`    TINYINT         NOT NULL COMMENT '类型: 1下单锁定 2支付扣减 3取消释放 4手动调整 5退货入库 6退款回补',
  `change_num`     INT             NOT NULL COMMENT '变更数量(正入负出)',
  `before_num`     INT             NOT NULL COMMENT '变更前可用库存',
  `after_num`      INT             NOT NULL COMMENT '变更后可用库存',
  `operator_id`    BIGINT UNSIGNED          DEFAULT NULL COMMENT '操作人(手动调整时)',
  `remark`         VARCHAR(255)             DEFAULT NULL,
  `idempotent_key` VARCHAR(96)              DEFAULT NULL COMMENT '幂等键(PRD §6.8), 构造规则见上',
  `create_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_idempotent_key` (`idempotent_key`),
  KEY `idx_sku_id` (`sku_id`),
  KEY `idx_order_no` (`order_no`),
  KEY `idx_refund_no` (`refund_no`)
) ENGINE=InnoDB COMMENT='库存变更流水表';

-- 商品评价表
CREATE TABLE IF NOT EXISTS `biz_product_review` (
  `id`             BIGINT UNSIGNED NOT NULL,
  `order_id`       BIGINT UNSIGNED NOT NULL,
  `order_item_id`  BIGINT UNSIGNED NOT NULL,
  `user_id`        BIGINT UNSIGNED NOT NULL,
  `spu_id`         BIGINT UNSIGNED NOT NULL,
  `sku_id`         BIGINT UNSIGNED NOT NULL,
  `score`          TINYINT         NOT NULL COMMENT '评分: 1-5星',
  `content`        VARCHAR(500)             DEFAULT NULL COMMENT '评价内容',
  `images`         JSON                     COMMENT '评价图片URL数组',
  `is_anonymous`   TINYINT         NOT NULL DEFAULT 0 COMMENT '匿名评价: 0否 1是',
  `reply`          VARCHAR(500)             DEFAULT NULL COMMENT '商家回复',
  `reply_time`     DATETIME                 DEFAULT NULL,
  `status`         TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1显示 0隐藏(违规)',
  `create_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_order_item_id` (`order_item_id`) COMMENT '一个订单项一条评价',
  KEY `idx_spu_id_score` (`spu_id`, `score`),
  KEY `idx_spu_time` (`spu_id`, `create_time`) COMMENT '商品评价列表按时间倒序'
) ENGINE=InnoDB COMMENT='商品评价表';

-- =============================================================
-- 三、交易域
-- =============================================================

-- 订单主表 (地址信息为下单时快照)
-- 状态机(PRD §6.1): 10 PENDING_PAY → 20 PAID → 30 SHIPPED → 40 PENDING_REVIEW → 50 COMPLETED
--   分支: 10 → 60 CANCELLED(超时/主动取消) / 80 CLOSED(已支付后取消, 退款完成)
--        任意态 → 70 AFTER_SALE(存在进行中的售后单)
CREATE TABLE IF NOT EXISTS `biz_order` (
  `id`               BIGINT UNSIGNED NOT NULL,
  `order_no`         VARCHAR(32)     NOT NULL COMMENT '业务订单号(对外唯一)',
  `user_id`          BIGINT UNSIGNED NOT NULL COMMENT '下单用户(预留分片键)',
  `status`           TINYINT         NOT NULL DEFAULT 10 COMMENT '状态: 10待付款 20待发货 30待收货 40待评价 50已完成 60已取消 70售后中 80已关闭',
  `total_amount`     DECIMAL(12,2)   NOT NULL COMMENT '商品总额 = Σ order_item.total_amount',
  `discount_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '优惠金额 = Σ order_item.discount_amount',
  `freight_amount`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '运费',
  `pay_amount`       DECIMAL(12,2)   NOT NULL COMMENT '实付金额 = total - discount + freight',
  `refund_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '累计已退金额(含运费), 约束 <= pay_amount',
  `coupon_id`        BIGINT UNSIGNED          DEFAULT NULL COMMENT '使用的用户优惠券ID(biz_user_coupon.id)',
  `receiver`         VARCHAR(32)     NOT NULL COMMENT '收货人快照',
  `receiver_phone`   VARCHAR(128)    NOT NULL COMMENT '电话快照密文(AES-256-GCM)',
  `receiver_addr`    VARCHAR(500)    NOT NULL COMMENT '完整地址快照(省市区拼接)',
  `pay_type`         TINYINT                  DEFAULT NULL COMMENT '支付方式: 1支付宝 2微信',
  `pay_time`         DATETIME                 DEFAULT NULL,
  `delivery_company` VARCHAR(64)              DEFAULT NULL COMMENT '快递公司(冗余自 biz_delivery, 列表展示用)',
  `delivery_no`      VARCHAR(64)              DEFAULT NULL COMMENT '主运单号(冗余自 biz_delivery)',
  `deliver_time`     DATETIME                 DEFAULT NULL COMMENT '发货时间',
  `finish_time`      DATETIME                 DEFAULT NULL COMMENT '完成时间',
  `cancel_reason`    VARCHAR(255)             DEFAULT NULL COMMENT '取消原因',
  `cancel_time`      DATETIME                 DEFAULT NULL,
  `version`          INT             NOT NULL DEFAULT 0 COMMENT '乐观锁',
  `create_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_order_no` (`order_no`),
  KEY `idx_user_status` (`user_id`, `status`),
  KEY `idx_status_create` (`status`, `create_time`) COMMENT '超时关单/自动收货兜底扫描(PRD §6.7)',
  KEY `idx_create_time` (`create_time`)
) ENGINE=InnoDB COMMENT='订单主表';

-- 订单明细表 (商品信息与金额分摊均为下单时快照)
CREATE TABLE IF NOT EXISTS `biz_order_item` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `order_id`        BIGINT UNSIGNED NOT NULL,
  `order_no`        VARCHAR(32)     NOT NULL COMMENT '冗余订单号',
  `user_id`         BIGINT UNSIGNED NOT NULL COMMENT '冗余, 售后查询用',
  `spu_id`          BIGINT UNSIGNED NOT NULL,
  `sku_id`          BIGINT UNSIGNED NOT NULL,
  `spu_name`        VARCHAR(128)    NOT NULL COMMENT '商品名称快照',
  `sku_pic`         VARCHAR(255)             DEFAULT NULL COMMENT 'SKU图片快照',
  `sku_specs`       JSON                     COMMENT '规格快照',
  `price`           DECIMAL(12,2)   NOT NULL COMMENT '成交单价快照',
  `quantity`        INT             NOT NULL COMMENT '购买数量',
  `total_amount`    DECIMAL(12,2)   NOT NULL COMMENT '小计 = price * quantity',
  `discount_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '优惠券分摊金额(按小计比例分摊, 最后一件补差)',
  `pay_amount`      DECIMAL(12,2)   NOT NULL COMMENT '实付小计 = total_amount - discount_amount',
  `refund_amount`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '累计已退金额, 约束 <= pay_amount',
  `refund_status`   TINYINT         NOT NULL DEFAULT 0 COMMENT '售后状态: 0无 1申请中 2退款中 3已退款 4已拒绝',
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_order_id` (`order_id`),
  KEY `idx_order_no` (`order_no`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_spu_id` (`spu_id`)
) ENGINE=InnoDB COMMENT='订单明细表';

-- 订单状态流转日志 (审计 + 后台"订单全链路视图"数据源)
CREATE TABLE IF NOT EXISTS `biz_order_log` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `order_id`      BIGINT UNSIGNED NOT NULL,
  `order_no`      VARCHAR(32)     NOT NULL,
  `from_status`   TINYINT                  DEFAULT NULL COMMENT '变更前状态, NULL 表示创建',
  `to_status`     TINYINT         NOT NULL COMMENT '变更后状态',
  `event`         VARCHAR(32)     NOT NULL COMMENT '触发事件: SUBMIT/PAY/TIMEOUT_CANCEL/DELIVER/CONFIRM/AFTER_SALE...',
  `operator_type` TINYINT         NOT NULL DEFAULT 1 COMMENT '操作者: 1用户 2商家 3系统 4客服',
  `operator_id`   BIGINT UNSIGNED          DEFAULT NULL,
  `remark`        VARCHAR(255)             DEFAULT NULL,
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_order_id` (`order_id`, `create_time`)
) ENGINE=InnoDB COMMENT='订单状态流转日志表';

-- 支付流水表 (渠道回调幂等锚点)
-- 状态机(PRD §6.4): 0 INIT → 1 PAYING → 2 SUCCESS / 3 FAILED / 4 CLOSED
--   PAYING 用于主动查询补偿: 定时扫描 PAYING 超 5min 的支付单主动查渠道(PRD §8.2)
CREATE TABLE IF NOT EXISTS `biz_payment` (
  `id`                BIGINT UNSIGNED NOT NULL,
  `payment_no`        VARCHAR(32)     NOT NULL COMMENT '支付单号(商户订单号 out_trade_no)',
  `order_no`          VARCHAR(32)     NOT NULL COMMENT '订单号',
  `user_id`           BIGINT UNSIGNED NOT NULL,
  `pay_type`          TINYINT         NOT NULL COMMENT '支付方式: 1支付宝 2微信',
  `amount`            DECIMAL(12,2)   NOT NULL COMMENT '支付金额',
  `refunded_amount`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '累计已退金额, 约束 <= amount',
  `status`            TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待支付 1已发起待回调 2成功 3失败 4已关闭',
  `channel_trade_no`  VARCHAR(64)              DEFAULT NULL COMMENT '渠道交易号',
  `channel_resp`      JSON                     COMMENT '渠道回调原始报文(验签后)',
  `callback_time`     DATETIME                 DEFAULT NULL COMMENT '成功回调时间',
  `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_payment_no` (`payment_no`),
  UNIQUE KEY `uk_channel_trade_no` (`channel_trade_no`) COMMENT '渠道回调幂等(MySQL 允许多个 NULL)',
  KEY `idx_order_no` (`order_no`),
  KEY `idx_status_update` (`status`, `update_time`) COMMENT '主动查询补偿扫描'
) ENGINE=InnoDB COMMENT='支付流水表';

-- 售后单表 (仅退款 / 退货退款 共用, 由 type 区分)
-- 状态机(PRD §6.5):
--   仅退款  : 0 APPLIED → 4 REFUNDING → 5 REFUND_SUCCESS / 6 REFUND_FAILED
--   退货退款: 0 APPLIED → 2 WAIT_RETURN → 3 WAIT_RECEIVE → 4 REFUNDING → 5 REFUND_SUCCESS
CREATE TABLE IF NOT EXISTS `biz_refund` (
  `id`                BIGINT UNSIGNED NOT NULL,
  `refund_no`         VARCHAR(32)     NOT NULL COMMENT '售后单号',
  `order_id`          BIGINT UNSIGNED NOT NULL,
  `order_no`          VARCHAR(32)     NOT NULL COMMENT '冗余订单号',
  `order_item_id`     BIGINT UNSIGNED NOT NULL,
  `user_id`           BIGINT UNSIGNED NOT NULL,
  `type`              TINYINT         NOT NULL COMMENT '类型: 1仅退款 2退货退款',
  `amount`            DECIMAL(12,2)   NOT NULL COMMENT '退款金额(商品分摊金额, 运费单独退)',
  `freight_amount`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '退还运费(PRD §6.6: 运费全额退还)',
  `reason`            VARCHAR(255)    NOT NULL COMMENT '申请原因',
  `evidence`          JSON                     COMMENT '凭证图片URL数组',
  `status`            TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待审核 1已拒绝 2待退货 3待收货 4退款中 5已完成 6退款失败 7已撤销',
  `audit_opinion`     VARCHAR(255)             DEFAULT NULL COMMENT '审核意见',
  `audit_user_id`     BIGINT UNSIGNED          DEFAULT NULL COMMENT '审核人',
  `audit_time`        DATETIME                 DEFAULT NULL,
  `channel_refund_no` VARCHAR(64)              DEFAULT NULL COMMENT '渠道退款单号',
  `finish_time`       DATETIME                 DEFAULT NULL,
  `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_refund_no` (`refund_no`),
  KEY `idx_order_no` (`order_no`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_order_item_id` (`order_item_id`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB COMMENT='售后单表';

-- 购物车表 (Redis 为主存储, 此表用于持久化/跨端同步兜底)
CREATE TABLE IF NOT EXISTS `biz_cart` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `user_id`     BIGINT UNSIGNED NOT NULL,
  `sku_id`      BIGINT UNSIGNED NOT NULL,
  `quantity`    INT             NOT NULL DEFAULT 1 COMMENT '数量(>0)',
  `checked`     TINYINT         NOT NULL DEFAULT 1 COMMENT '是否勾选: 0否 1是',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_sku` (`user_id`, `sku_id`)
) ENGINE=InnoDB COMMENT='购物车表';

-- =============================================================
-- 四、营销域
-- =============================================================

-- 运费模板表 (PRD §6.6: 满X元包邮, 否则固定运费Y, 偏远地区附加费Z)
CREATE TABLE IF NOT EXISTS `biz_freight_template` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `name`            VARCHAR(64)     NOT NULL COMMENT '模板名称',
  `free_threshold`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '包邮门槛(商品实付金额), 0为不包邮',
  `base_freight`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '基础运费',
  `remote_extra`    DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '偏远地区附加费',
  `remote_regions`  JSON                     COMMENT '偏远地区省份数组, 如 ["新疆","西藏"]',
  `is_default`      TINYINT         NOT NULL DEFAULT 0 COMMENT '是否默认模板: 0否 1是(全局唯一)',
  `status`          TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB COMMENT='运费模板表';

-- 优惠券模板表
CREATE TABLE IF NOT EXISTS `biz_coupon_template` (
  `id`               BIGINT UNSIGNED NOT NULL,
  `name`             VARCHAR(64)     NOT NULL COMMENT '券名称',
  `type`             TINYINT         NOT NULL COMMENT '类型: 1满减 2折扣 3无门槛',
  `threshold_amount` DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '使用门槛(满X元), 0为无门槛',
  `discount_amount`  DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '优惠金额(满减/无门槛)',
  `discount_rate`    DECIMAL(3,2)             DEFAULT NULL COMMENT '折扣率(折扣券, 如0.90)',
  `max_discount`     DECIMAL(12,2)            DEFAULT NULL COMMENT '最大优惠(折扣券封顶)',
  `total_count`      INT             NOT NULL DEFAULT 0 COMMENT '发放总量',
  `remain_count`     INT             NOT NULL DEFAULT 0 COMMENT '剩余数量(原子扣减: UPDATE ... WHERE remain_count >= 1)',
  `per_limit`        TINYINT         NOT NULL DEFAULT 1 COMMENT '每人限领张数(应用层校验已领数)',
  `start_time`       DATETIME        NOT NULL COMMENT '可领取开始时间',
  `end_time`         DATETIME        NOT NULL COMMENT '可领取结束时间',
  `valid_days`       INT             NOT NULL DEFAULT 7 COMMENT '领取后有效天数',
  `scope_type`       TINYINT         NOT NULL DEFAULT 1 COMMENT '适用范围: 1全场 2指定类目 3指定商品',
  `scope_ids`        JSON                     COMMENT '指定类目/商品ID数组',
  `status`           TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0草稿 1发放中 2已结束 3已下架',
  `deleted`          TINYINT         NOT NULL DEFAULT 0,
  `create_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_status_time` (`status`, `start_time`, `end_time`)
) ENGINE=InnoDB COMMENT='优惠券模板表';

-- 用户优惠券表
-- 状态机(PRD §6.3): 1 UNUSED → 2 LOCKED(下单占用) → 3 USED(支付成功) / 回滚为 1 UNUSED(超时取消)
--                   1 UNUSED → 4 EXPIRED(定时扫描过期)
--                   3 USED  → 5 FROZEN(退款中)
-- LOCKED 是并发防重的关键: 同一张券在同一时刻只能被一个订单锁定
CREATE TABLE IF NOT EXISTS `biz_user_coupon` (
  `id`           BIGINT UNSIGNED NOT NULL,
  `user_id`      BIGINT UNSIGNED NOT NULL,
  `coupon_id`    BIGINT UNSIGNED NOT NULL COMMENT 'biz_coupon_template.id',
  `status`       TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1未使用 2锁定中 3已使用 4已过期 5冻结中(退款)',
  `order_id`     BIGINT UNSIGNED          DEFAULT NULL COMMENT '锁定/使用的订单ID',
  `receive_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '领取时间',
  `expire_time`  DATETIME        NOT NULL COMMENT '过期时间',
  `use_time`     DATETIME                 DEFAULT NULL,
  `create_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_status` (`user_id`, `status`),
  KEY `idx_coupon_id` (`coupon_id`),
  KEY `idx_status_expire` (`status`, `expire_time`) COMMENT '过期扫描'
) ENGINE=InnoDB COMMENT='用户优惠券表';

-- =============================================================
-- 五、物流域
-- =============================================================

-- 运单表 (一单可拆多包裹, 故与订单为 1:N)
CREATE TABLE IF NOT EXISTS `biz_delivery` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `order_id`      BIGINT UNSIGNED NOT NULL,
  `order_no`      VARCHAR(32)     NOT NULL,
  `user_id`       BIGINT UNSIGNED NOT NULL,
  `company_code`  VARCHAR(32)     NOT NULL COMMENT '快递公司编码(如 SF/YTO)',
  `company_name`  VARCHAR(64)     NOT NULL COMMENT '快递公司名称',
  `delivery_no`   VARCHAR(64)     NOT NULL COMMENT '运单号',
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待揽收 1运输中 2派送中 3已签收 4异常',
  `deliver_time`  DATETIME                 DEFAULT NULL COMMENT '发货时间',
  `sign_time`     DATETIME                 DEFAULT NULL COMMENT '签收时间',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_delivery_no` (`delivery_no`),
  KEY `idx_order_no` (`order_no`)
) ENGINE=InnoDB COMMENT='运单表';

-- 物流轨迹表 (对接 Mock 物流服务, 定时拉取)
CREATE TABLE IF NOT EXISTS `biz_delivery_trace` (
  `id`           BIGINT UNSIGNED NOT NULL,
  `delivery_id`  BIGINT UNSIGNED NOT NULL,
  `delivery_no`  VARCHAR(64)     NOT NULL COMMENT '冗余运单号',
  `trace_time`   DATETIME        NOT NULL COMMENT '轨迹发生时间',
  `status_desc`  VARCHAR(64)     NOT NULL COMMENT '轨迹描述(如"已签收")',
  `location`     VARCHAR(128)             DEFAULT NULL COMMENT '所在地点',
  `create_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_delivery_time_desc` (`delivery_no`, `trace_time`, `status_desc`) COMMENT '拉取幂等',
  KEY `idx_delivery_id` (`delivery_id`)
) ENGINE=InnoDB COMMENT='物流轨迹表';

-- =============================================================
-- 六、AI 客服域
-- =============================================================

-- 知识库文档表
CREATE TABLE IF NOT EXISTS `ai_kb_document` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `title`         VARCHAR(128)    NOT NULL COMMENT '文档标题',
  `domain`        VARCHAR(16)     NOT NULL COMMENT '知识域: product/policy/faq',
  `file_type`     VARCHAR(16)     NOT NULL DEFAULT 'md' COMMENT '类型: pdf/docx/md/text',
  `file_url`      VARCHAR(255)             DEFAULT NULL COMMENT '原始文件URL(手工录入为NULL)',
  `content`       LONGTEXT                 COMMENT '纯文本内容',
  `chunk_count`   INT             NOT NULL DEFAULT 0 COMMENT '切片数量',
  `enabled`       TINYINT         NOT NULL DEFAULT 1 COMMENT '是否生效: 0否 1是',
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待处理 1处理中 2已生效 3处理失败',
  `error_msg`     VARCHAR(500)             DEFAULT NULL COMMENT '处理失败原因',
  `version`       INT             NOT NULL DEFAULT 1 COMMENT '内容版本(重建索引用)',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_domain_enabled` (`domain`, `enabled`)
) ENGINE=InnoDB COMMENT='AI知识库文档表';

-- 知识库切片表 (向量存 Milvus, 此表存原文与映射)
-- Milvus 中向量记录的主键直接使用本表 id, 无需额外 embedding_id 列
CREATE TABLE IF NOT EXISTS `ai_kb_chunk` (
  `id`            BIGINT UNSIGNED NOT NULL COMMENT '同时作为 Milvus 向量记录主键',
  `document_id`   BIGINT UNSIGNED NOT NULL,
  `domain`        VARCHAR(16)     NOT NULL COMMENT '冗余知识域, 检索路由用',
  `content`       TEXT            NOT NULL COMMENT '切片原文',
  `token_count`   INT             NOT NULL DEFAULT 0 COMMENT '切片token数',
  `status`        TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1生效 0失效(文档重建后旧切片置0)',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_document_id` (`document_id`),
  KEY `idx_domain_status` (`domain`, `status`)
) ENGINE=InnoDB COMMENT='AI知识库切片表';

-- 会话表
-- 状态机(PRD §9.8): 1 AI_SERVING → 2 HANDOFF_REQUESTED → 3 HUMAN_SERVING → 4 CLOSED
--   支持 3 → 1 (客服转回AI)、4 → 1 (用户重新发起)
CREATE TABLE IF NOT EXISTS `ai_conversation` (
  `id`               BIGINT UNSIGNED NOT NULL,
  `conversation_no`  VARCHAR(32)     NOT NULL COMMENT '会话编号',
  `user_id`          BIGINT UNSIGNED NOT NULL COMMENT '游客使用匿名会话ID(负数或独立号段)',
  `channel`          TINYINT         NOT NULL DEFAULT 1 COMMENT '来源: 1商品页 2订单页 3独立对话页',
  `spu_id`           BIGINT UNSIGNED          DEFAULT NULL COMMENT '咨询商品(商品页来源时)',
  `status`           TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1 AI接待中 2待人工接入 3人工接待中 4已结束',
  `resolved`         TINYINT                  DEFAULT NULL COMMENT '是否解决: 1是 0否 (结束时按 PRD §9.6 判定)',
  `close_time`       DATETIME                 DEFAULT NULL,
  `create_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_conversation_no` (`conversation_no`),
  KEY `idx_user_id` (`user_id`, `status`),
  KEY `idx_status_update` (`status`, `update_time`) COMMENT '会话超时结束扫描'
) ENGINE=InnoDB COMMENT='AI会话表';

-- 消息表
CREATE TABLE IF NOT EXISTS `ai_message` (
  `id`                 BIGINT UNSIGNED NOT NULL,
  `conversation_id`    BIGINT UNSIGNED NOT NULL,
  `role`               TINYINT         NOT NULL COMMENT '角色: 1用户 2助手 3系统 4工具返回',
  `content`            LONGTEXT                 COMMENT '消息内容',
  `intent`             VARCHAR(16)              DEFAULT NULL COMMENT '识别意图: presale/order/aftersale/chat/handoff',
  `confidence`         DECIMAL(4,3)             DEFAULT NULL COMMENT '意图置信度 0-1',
  `retrieved_chunk_ids` JSON                    COMMENT '本次检索命中的切片ID数组(溯源)',
  `model`              VARCHAR(64)              DEFAULT NULL COMMENT '使用的Ark模型',
  `prompt_tokens`      INT                      DEFAULT NULL,
  `completion_tokens`  INT                      DEFAULT NULL,
  `latency_ms`         INT                      DEFAULT NULL COMMENT '首字延迟ms',
  `filtered`           TINYINT         NOT NULL DEFAULT 0 COMMENT '是否被敏感词拦截: 0否 1是',
  `create_time`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_conversation_id` (`conversation_id`, `create_time`)
) ENGINE=InnoDB COMMENT='AI消息表';

-- 会话评价表
CREATE TABLE IF NOT EXISTS `ai_feedback` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `message_id`      BIGINT UNSIGNED NOT NULL COMMENT '针对的助手消息',
  `user_id`         BIGINT UNSIGNED NOT NULL,
  `rating`          TINYINT         NOT NULL COMMENT '评价: 1赞 -1踩',
  `comment`         VARCHAR(255)             DEFAULT NULL COMMENT '补充说明',
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_message_user` (`message_id`, `user_id`) COMMENT '同一用户对同一消息仅一次评价',
  KEY `idx_conversation_id` (`conversation_id`)
) ENGINE=InnoDB COMMENT='AI会话评价表';

-- 转人工记录表
CREATE TABLE IF NOT EXISTS `ai_handoff_record` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `user_id`         BIGINT UNSIGNED NOT NULL,
  `order_no`        VARCHAR(32)              DEFAULT NULL COMMENT '关联订单号(争议场景)',
  `reason`          TINYINT         NOT NULL COMMENT '转接原因: 1低置信度 2用户要求 3订单争议 4多次不满 5连续未解决',
  `queue_status`    TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1排队中 2接入中 3已完成 4用户放弃',
  `agent_id`        BIGINT UNSIGNED          DEFAULT NULL COMMENT '客服人员(ai_agent.id)',
  `agent_name`      VARCHAR(32)              DEFAULT NULL,
  `accept_time`     DATETIME                 DEFAULT NULL COMMENT '接入时间',
  `close_time`      DATETIME                 DEFAULT NULL,
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_conversation_id` (`conversation_id`),
  KEY `idx_agent_status` (`agent_id`, `queue_status`),
  KEY `idx_queue_status_time` (`queue_status`, `create_time`) COMMENT '排队超时扫描'
) ENGINE=InnoDB COMMENT='转人工记录表';

-- 客服坐席表 (静态配置; 实时在线状态存 Redis, 见 PRD §9.5)
CREATE TABLE IF NOT EXISTS `ai_agent` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `user_id`         BIGINT UNSIGNED NOT NULL COMMENT '关联 sys_user.id',
  `agent_no`        VARCHAR(32)     NOT NULL COMMENT '坐席工号',
  `nickname`        VARCHAR(32)     NOT NULL COMMENT '对外展示名',
  `max_concurrent`  TINYINT         NOT NULL DEFAULT 5 COMMENT '最大同时接待会话数',
  `skill_tags`      VARCHAR(128)             DEFAULT NULL COMMENT '技能标签(逗号分隔, 用于路由)',
  `status`          TINYINT         NOT NULL DEFAULT 0 COMMENT '启用状态: 0停用 1启用(实时在线态见Redis)',
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_no` (`agent_no`),
  UNIQUE KEY `uk_user_id` (`user_id`)
) ENGINE=InnoDB COMMENT='客服坐席表';

-- =============================================================
-- 七、系统域 (RBAC + 审计 + 可靠投递 + 动态配置)
-- =============================================================

-- 后台用户表
CREATE TABLE IF NOT EXISTS `sys_user` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `username`        VARCHAR(32)     NOT NULL COMMENT '登录名',
  `password`        CHAR(60)        NOT NULL COMMENT '密码(BCrypt)',
  `real_name`       VARCHAR(32)              DEFAULT NULL COMMENT '姓名',
  `mobile`          VARCHAR(128)             DEFAULT NULL COMMENT '手机号密文(AES-256-GCM)',
  `mobile_hash`     CHAR(64)                 DEFAULT NULL COMMENT '手机号HMAC-SHA256',
  `status`          TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1正常 0禁用',
  `last_login_time` DATETIME                 DEFAULT NULL,
  `deleted`         TINYINT         NOT NULL DEFAULT 0,
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`),
  KEY `idx_mobile_hash` (`mobile_hash`)
) ENGINE=InnoDB COMMENT='后台用户表';

-- 角色表
CREATE TABLE IF NOT EXISTS `sys_role` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(32)     NOT NULL COMMENT '角色名称',
  `code`        VARCHAR(32)     NOT NULL COMMENT '角色编码, 如 admin/agent/ops',
  `data_scope`  TINYINT         NOT NULL DEFAULT 1 COMMENT '数据权限范围: 1仅本人 2本部门 3全量(PRD §2.2)',
  `remark`      VARCHAR(255)             DEFAULT NULL,
  `status`      TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`)
) ENGINE=InnoDB COMMENT='角色表';

-- 权限表 (菜单/按钮/接口)
CREATE TABLE IF NOT EXISTS `sys_permission` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `parent_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '父权限, 0为根',
  `name`        VARCHAR(32)     NOT NULL COMMENT '权限名称',
  `type`        TINYINT         NOT NULL COMMENT '类型: 1菜单 2按钮 3接口',
  `perm_code`   VARCHAR(64)     NOT NULL COMMENT '权限标识, 如 order:ship',
  `path`        VARCHAR(128)             DEFAULT NULL COMMENT '前端路由/后端接口路径',
  `sort`        INT             NOT NULL DEFAULT 0,
  `status`      TINYINT         NOT NULL DEFAULT 1 COMMENT '状态: 1启用 0禁用',
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_perm_code` (`perm_code`),
  KEY `idx_parent_id` (`parent_id`)
) ENGINE=InnoDB COMMENT='权限表';

-- 用户-角色关联
CREATE TABLE IF NOT EXISTS `sys_user_role` (
  `id`      BIGINT UNSIGNED NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `role_id` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_role` (`user_id`, `role_id`),
  KEY `idx_role_id` (`role_id`)
) ENGINE=InnoDB COMMENT='用户角色关联表';

-- 角色-权限关联
CREATE TABLE IF NOT EXISTS `sys_role_permission` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `role_id`       BIGINT UNSIGNED NOT NULL,
  `permission_id` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_perm` (`role_id`, `permission_id`),
  KEY `idx_permission_id` (`permission_id`)
) ENGINE=InnoDB COMMENT='角色权限关联表';

-- 操作审计日志表
CREATE TABLE IF NOT EXISTS `sys_audit_log` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `operator_id`   BIGINT UNSIGNED NOT NULL,
  `operator_name` VARCHAR(32)     NOT NULL COMMENT '冗余操作人姓名',
  `module`        VARCHAR(32)     NOT NULL COMMENT '模块, 如 order/product',
  `action`        VARCHAR(32)     NOT NULL COMMENT '动作, 如 ship/audit/refund',
  `method`        VARCHAR(8)               DEFAULT NULL COMMENT 'HTTP方法',
  `path`          VARCHAR(255)             DEFAULT NULL COMMENT '请求路径',
  `params`        JSON                     COMMENT '请求参数(脱敏后)',
  `result_code`   INT                      DEFAULT NULL COMMENT '响应code',
  `ip`            VARCHAR(64)              DEFAULT NULL COMMENT '操作IP',
  `user_agent`    VARCHAR(255)             DEFAULT NULL,
  `cost_ms`       INT                      DEFAULT NULL COMMENT '耗时',
  `trace_id`      VARCHAR(64)              DEFAULT NULL COMMENT '全链路追踪ID',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_operator_time` (`operator_id`, `create_time`),
  KEY `idx_module_action` (`module`, `action`),
  KEY `idx_trace_id` (`trace_id`)
) ENGINE=InnoDB COMMENT='操作审计日志表';

-- 本地消息表 (PRD §4.2 / §5.6 可靠投递核心)
-- 写业务与写消息在同一本地事务内完成, 事务提交后由投递任务发 Kafka, 保证消息不丢
CREATE TABLE IF NOT EXISTS `sys_local_message` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `biz_type`        VARCHAR(32)     NOT NULL COMMENT '业务类型: ORDER/PAYMENT/STOCK/PRODUCT...',
  `biz_no`          VARCHAR(64)     NOT NULL COMMENT '业务单号(幂等键)',
  `topic`           VARCHAR(64)     NOT NULL COMMENT '目标Kafka topic',
  `payload`         JSON            NOT NULL COMMENT '消息体',
  `status`          TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待投递 1已投递 2投递失败(超重试上限)',
  `retry_count`     INT             NOT NULL DEFAULT 0,
  `next_retry_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下次重试时间(指数退避)',
  `error_msg`       VARCHAR(500)             DEFAULT NULL,
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_biz_topic` (`biz_type`, `biz_no`, `topic`) COMMENT '同一业务事件的幂等键',
  KEY `idx_status_retry` (`status`, `next_retry_time`) COMMENT '投递任务扫描'
) ENGINE=InnoDB COMMENT='本地消息表';

-- 死信表 (Kafka 消费失败 / 延迟任务失败统一落库, 支持人工重放)
CREATE TABLE IF NOT EXISTS `sys_dead_letter` (
  `id`           BIGINT UNSIGNED NOT NULL,
  `source`       VARCHAR(32)     NOT NULL COMMENT '来源: KAFKA/DELAY_TASK/LOCAL_MSG',
  `ref_key`      VARCHAR(128)    NOT NULL COMMENT '业务唯一键(如 order_no)',
  `topic`        VARCHAR(64)              DEFAULT NULL COMMENT '来源topic',
  `payload`      JSON                     COMMENT '原始消息体',
  `error_msg`    VARCHAR(1000)            DEFAULT NULL,
  `retry_count`  INT             NOT NULL DEFAULT 0,
  `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待处理 1已重放 2已忽略',
  `handle_time`  DATETIME                 DEFAULT NULL,
  `handler_id`   BIGINT UNSIGNED          DEFAULT NULL COMMENT '处理人',
  `create_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_source_status` (`source`, `status`),
  KEY `idx_ref_key` (`ref_key`)
) ENGINE=InnoDB COMMENT='死信表';

-- 系统动态配置表 (v1.3: 替代 Nacos 配置中心; 后台可改, 经 Redis 发布订阅热更新, 无需重启)
CREATE TABLE IF NOT EXISTS `sys_config` (
  `id`           BIGINT UNSIGNED NOT NULL COMMENT '雪花ID',
  `config_key`   VARCHAR(128)    NOT NULL COMMENT '配置键, 如 ai.retrieval_score_threshold',
  `config_value` VARCHAR(512)    NOT NULL COMMENT '配置值(字符串, 按 value_type 解析)',
  `value_type`   TINYINT         NOT NULL DEFAULT 0 COMMENT '值类型: 0 string 1 int 2 float 3 bool 4 json',
  `description`  VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '配置说明',
  `updated_by`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后修改人 sys_user.id',
  `create_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_config_key` (`config_key`)
) ENGINE=InnoDB COMMENT='系统动态配置表';

-- =============================================================
-- 八、通知域
-- =============================================================

-- 站内信表
CREATE TABLE IF NOT EXISTS `biz_message` (
  `id`          BIGINT UNSIGNED NOT NULL,
  `user_id`     BIGINT UNSIGNED NOT NULL,
  `type`        TINYINT         NOT NULL COMMENT '类型: 1订单 2售后 3系统 4营销',
  `title`       VARCHAR(64)     NOT NULL,
  `content`     VARCHAR(500)    NOT NULL,
  `biz_id`      BIGINT UNSIGNED          DEFAULT NULL COMMENT '关联业务ID(订单/售后单)',
  `is_read`     TINYINT         NOT NULL DEFAULT 0 COMMENT '已读: 0否 1是',
  `read_time`   DATETIME                 DEFAULT NULL,
  `create_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user_read_time` (`user_id`, `is_read`, `create_time`) COMMENT '未读列表 + 倒序'
) ENGINE=InnoDB COMMENT='站内信表';
