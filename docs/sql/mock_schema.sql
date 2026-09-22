-- =============================================================
-- AIDS Mock 服务 - 数据库 DDL
-- 版本: v1.1 | 数据库: MySQL 8.0.16+
--
-- 为什么独立库:
--   Mock 服务模拟的是「第三方渠道」，与商户系统是不同主体、不同数据库。
--   物理隔离才能真实模拟渠道故障（Mock 库挂了不能影响主业务库）。
--   PRD §8.4 要求切换真实渠道时业务代码零改动，接口形态必须与真实渠道一致。
--
-- 覆盖:
--   1. Mock 支付网关 (统一下单 / 异步回调 / 主动查询 / 退款 / T+1 对账)
--   2. Mock 短信服务 (验证码 / 通知)
--   3. Mock 物流服务 (运单 / 轨迹)
-- =============================================================
CREATE DATABASE IF NOT EXISTS `aids_mock`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `aids_mock`;

-- =============================================================
-- 一、Mock 支付网关
-- =============================================================

-- 渠道支付单表 (对应真实渠道的商户订单记录)
-- 状态机与商户侧 biz_payment 独立: 渠道有自己的生命周期
CREATE TABLE IF NOT EXISTS `mock_payment_order` (
  `id`                BIGINT UNSIGNED NOT NULL,
  `out_trade_no`      VARCHAR(32)     NOT NULL COMMENT '商户订单号(商户传入)',
  `trade_no`          VARCHAR(64)     NOT NULL COMMENT '渠道交易号(渠道生成, 对应真实支付宝 trade_no)',
  `merchant_id`       VARCHAR(32)     NOT NULL COMMENT '商户号',
  `pay_type`          TINYINT         NOT NULL COMMENT '支付方式: 1支付宝 2微信',
  `amount`            DECIMAL(12,2)   NOT NULL COMMENT '订单金额',
  `subject`           VARCHAR(128)    NOT NULL COMMENT '订单标题',
  `notify_url`        VARCHAR(255)    NOT NULL COMMENT '异步通知地址',
  `return_url`        VARCHAR(255)             DEFAULT NULL COMMENT '同步跳转地址',
  `status`            TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待支付 1已支付 2已关闭 3支付失败',
  `refunded_amount`   DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '累计已退金额',
  `pay_time`          DATETIME                 DEFAULT NULL COMMENT '支付成功时间',
  `close_time`        DATETIME                 DEFAULT NULL,
  `expire_time`       DATETIME        NOT NULL COMMENT '支付超时时间(超过则渠道关单)',
  `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_out_trade_no` (`out_trade_no`) COMMENT '商户订单号唯一, 重复下单返回原单',
  UNIQUE KEY `uk_trade_no` (`trade_no`),
  KEY `idx_status_expire` (`status`, `expire_time`) COMMENT '渠道侧超时关单扫描'
) ENGINE=InnoDB COMMENT='Mock渠道支付单表';

-- 渠道退款单表
CREATE TABLE IF NOT EXISTS `mock_refund_order` (
  `id`              BIGINT UNSIGNED NOT NULL,
  `out_refund_no`   VARCHAR(32)     NOT NULL COMMENT '商户退款单号',
  `refund_no`       VARCHAR(64)     NOT NULL COMMENT '渠道退款单号',
  `out_trade_no`    VARCHAR(32)     NOT NULL COMMENT '关联商户订单号',
  `trade_no`        VARCHAR(64)     NOT NULL COMMENT '关联渠道交易号',
  `refund_amount`   DECIMAL(12,2)   NOT NULL COMMENT '退款金额',
  `reason`          VARCHAR(255)             DEFAULT NULL,
  `status`          TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0退款中 1退款成功 2退款失败',
  `refund_time`     DATETIME                 DEFAULT NULL,
  `error_msg`       VARCHAR(500)             DEFAULT NULL,
  `create_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_out_refund_no` (`out_refund_no`) COMMENT '商户退款单号幂等',
  UNIQUE KEY `uk_refund_no` (`refund_no`),
  KEY `idx_out_trade_no` (`out_trade_no`)
) ENGINE=InnoDB COMMENT='Mock渠道退款单表';

-- 回调投递记录表 (故障注入 + 幂等验证的核心)
-- 支持配置: 回调延迟、重复推送次数、丢包概率 —— 用于 PRD §8.3 的故障注入测试
CREATE TABLE IF NOT EXISTS `mock_callback_log` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `biz_type`      TINYINT         NOT NULL COMMENT '类型: 1支付回调 2退款回调',
  `out_trade_no`  VARCHAR(32)     NOT NULL COMMENT '关联商户订单号',
  `notify_url`    VARCHAR(255)    NOT NULL,
  `payload`       JSON            NOT NULL COMMENT '回调报文(含签名)',
  `sign`          VARCHAR(512)    NOT NULL COMMENT 'RSA2签名',
  `attempt_no`    INT             NOT NULL DEFAULT 1 COMMENT '第几次推送(重复回调测试)',
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待推送 1推送成功 2推送失败 3已放弃',
  `http_status`   INT                      DEFAULT NULL COMMENT '商户返回HTTP状态',
  `resp_body`     VARCHAR(255)             DEFAULT NULL COMMENT '商户返回内容(期望"success")',
  `next_retry_time` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下次推送时间(退避)',
  `retry_count`   INT             NOT NULL DEFAULT 0,
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_status_retry` (`status`, `next_retry_time`) COMMENT '推送任务扫描',
  KEY `idx_out_trade_no` (`out_trade_no`)
) ENGINE=InnoDB COMMENT='Mock回调投递记录表';

-- 渠道对账单 (T+1 生成, 供商户对账任务拉取)
CREATE TABLE IF NOT EXISTS `mock_recon_file` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `bill_date`     DATE            NOT NULL COMMENT '账单日期(T-1)',
  `pay_type`      TINYINT         NOT NULL COMMENT '支付方式: 1支付宝 2微信',
  `file_url`      VARCHAR(255)             DEFAULT NULL COMMENT '对账文件URL(CSV, 存MinIO)',
  `file_content`  MEDIUMTEXT               COMMENT '对账明细(CSV文本, 便于本地演示直接返回)',
  `total_count`   INT             NOT NULL DEFAULT 0 COMMENT '总笔数',
  `total_amount`  DECIMAL(14,2)   NOT NULL DEFAULT 0.00 COMMENT '总金额',
  `refund_count`  INT             NOT NULL DEFAULT 0,
  `refund_amount` DECIMAL(14,2)   NOT NULL DEFAULT 0.00,
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0生成中 1已就绪',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_type` (`bill_date`, `pay_type`) COMMENT '同日同渠道仅一份'
) ENGINE=InnoDB COMMENT='Mock渠道对账单表';

-- =============================================================
-- 二、Mock 短信服务
-- =============================================================

CREATE TABLE IF NOT EXISTS `mock_sms_record` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `mobile`        VARCHAR(20)     NOT NULL COMMENT '接收手机号(明文, Mock库仅演示用; 接入真实渠道后本表整体废弃, 不迁移数据)',
  `template_code` VARCHAR(32)     NOT NULL COMMENT '模板编码: LOGIN_CODE/ORDER_SHIP/NOTIFY...',
  `content`       VARCHAR(500)    NOT NULL COMMENT '短信内容',
  `params`        JSON                     COMMENT '模板参数',
  `biz_no`        VARCHAR(64)              DEFAULT NULL COMMENT '关联业务号',
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待发送 1发送成功 2发送失败',
  `error_msg`     VARCHAR(255)             DEFAULT NULL,
  `send_time`     DATETIME                 DEFAULT NULL,
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_mobile_time` (`mobile`, `create_time`) COMMENT '限流校验: 60s内不可重复发送',
  KEY `idx_biz_no` (`biz_no`)
) ENGINE=InnoDB COMMENT='Mock短信记录表';

-- =============================================================
-- 三、Mock 物流服务
-- =============================================================

CREATE TABLE IF NOT EXISTS `mock_logistics_order` (
  `id`            BIGINT UNSIGNED NOT NULL,
  `delivery_no`   VARCHAR(64)     NOT NULL COMMENT '运单号',
  `company_code`  VARCHAR(32)     NOT NULL COMMENT '快递公司编码',
  `company_name`  VARCHAR(64)     NOT NULL,
  `sender_city`   VARCHAR(32)              DEFAULT NULL,
  `receiver_city` VARCHAR(32)              DEFAULT NULL,
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '状态: 0待揽收 1运输中 2派送中 3已签收 4异常',
  `create_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_delivery_no` (`delivery_no`)
) ENGINE=InnoDB COMMENT='Mock运单表';

-- 物流轨迹 (轨迹由 Mock 服务按时间自动推进生成, 模拟真实快递节点)
CREATE TABLE IF NOT EXISTS `mock_logistics_trace` (
  `id`           BIGINT UNSIGNED NOT NULL,
  `delivery_no`  VARCHAR(64)     NOT NULL,
  `trace_time`   DATETIME        NOT NULL COMMENT '轨迹发生时间',
  `status_desc`  VARCHAR(64)     NOT NULL COMMENT '轨迹描述',
  `location`     VARCHAR(128)             DEFAULT NULL COMMENT '所在地点',
  `create_time`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_delivery_time` (`delivery_no`, `trace_time`),
  KEY `idx_delivery_no` (`delivery_no`)
) ENGINE=InnoDB COMMENT='Mock物流轨迹表';
