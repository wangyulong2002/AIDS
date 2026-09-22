-- =============================================================
-- AIDS 企业级电商平台 - 初始化种子数据
-- 版本: v1.1 | 依赖: schema.sql (需先执行) | MySQL 8.0.16+
--
-- 用途: M1 搭环境后一键灌入可演示数据, 免去手工造数
-- 特性: 全量幂等 —— 所有 INSERT 均使用固定主键 + ON DUPLICATE KEY UPDATE,
--       可重复执行而不产生重复数据, 也不会因主键冲突中断
--
-- 【重要】本文件中的密码哈希与手机号密文为真实算法生成, 配套密钥如下:
--
--   BCrypt           : 明文 Admin@123456 (管理员) / Agent@123456 (客服)
--   AES-256-GCM 密钥 : a1d7f3c9e5b2048168cafe11d3b7e94f5a2c8d610e3b47f9a0c5d2e8b4f16a73
--   HMAC-SHA256 密钥 : 3f8a1c6e9d24b7058af3e1c07d6b92548e0f3a7c1d5b84629e7f0a3c8d1b5e46
--   密文格式          : Base64( nonce[12字节] || ciphertext+GCM tag )
--
--   ⚠️ 以上密钥仅供本地开发/演示。生产环境必须通过环境变量注入并另行生成,
--      绝不可将本文件的密钥用于任何真实环境。数据库泄露 + 密钥泄露 = 手机号全量泄露,
--      故密钥必须与数据库分开保管(本文件只是为了让毕设能一键跑起来)。
-- =============================================================
USE `aids_shop`;
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
-- 一、系统域 (RBAC 基础数据)
-- =============================================================

-- 后台用户: 管理员 + 客服坐席
-- 注意: 两个账号密码不同, 均为真实 BCrypt 哈希, 可直接用于登录
INSERT INTO `sys_user` (`id`,`username`,`password`,`real_name`,`mobile`,`mobile_hash`,`status`) VALUES
(1001,'admin','$2b$10$5dZObJ1.m.1C4WEEKO45VOnuWl.k06Ld/7xw9vp/vIAWpihXKmMjC','超级管理员','aMOXXy7mp+PHJDGEy0jFqTT6ninvlvyPhc0T4r3gA4K51hQ2YZv2','15c7dbebf135e360e3408b0ee9a92e57d3bb97fbc52b2b86379297f6c4f36581',1),
(1002,'agent01','$2b$10$eKfD987CBsIsTT0OQrAGtuFa2pSq5UIGjT59wLe/GSAbq2mYD.6Qa','客服小美','NHIVZgBQoEhh8LRsn3np0DEy7GwN0AKPYuVyKfklG0IUnfuAD/70','b308a35303ccd3c1277db87d4a6e65857cc5f72eb53bfb0c976782795252ebd4',1)
ON DUPLICATE KEY UPDATE `password`=VALUES(`password`),`real_name`=VALUES(`real_name`),`status`=VALUES(`status`);

-- 角色
INSERT INTO `sys_role` (`id`,`name`,`code`,`data_scope`,`remark`) VALUES
(2001,'超级管理员','admin',3,'全部权限'),
(2002,'运营','ops',3,'商品/订单/营销管理'),
(2003,'客服','agent',2,'会话接待, 仅本人接待的会话'),
(2004,'财务','finance',3,'退款审核与对账')
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`),`data_scope`=VALUES(`data_scope`);

-- 权限 (菜单/按钮/接口)
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`type`,`perm_code`,`path`,`sort`) VALUES
(3001,0,'商品管理',1,'product','/product',10),
(3002,3001,'商品列表',1,'product:list','/product/list',11),
(3003,3002,'商品新增',2,'product:add',NULL,12),
(3004,3002,'商品编辑',2,'product:edit',NULL,13),
(3005,3002,'商品上下架',2,'product:shelf',NULL,14),
(3006,3002,'商品审核',2,'product:audit',NULL,15),
(3011,0,'订单管理',1,'order','/order',20),
(3012,3011,'订单列表',1,'order:list','/order/list',21),
(3013,3012,'订单发货',2,'order:ship',NULL,22),
(3014,3012,'订单详情',2,'order:detail',NULL,23),
(3015,3012,'退款审核',2,'order:refund:audit',NULL,24),
(3021,0,'库存管理',1,'stock','/stock',30),
(3022,3021,'库存查询',1,'stock:list','/stock/list',31),
(3023,3022,'库存调整',2,'stock:adjust',NULL,32),
(3024,3022,'库存流水',2,'stock:log',NULL,33),
(3031,0,'营销管理',1,'marketing','/marketing',40),
(3032,3031,'优惠券模板',1,'coupon:list','/coupon/list',41),
(3033,3032,'优惠券发放',2,'coupon:issue',NULL,42),
(3034,3032,'运费模板',1,'freight:list','/freight/list',43),
(3041,0,'客服工作台',1,'cs','/cs',50),
(3042,3041,'会话列表',1,'cs:conversation','/cs/conversation',51),
(3043,3042,'接管会话',2,'cs:takeover',NULL,52),
(3044,3042,'结束会话',2,'cs:close',NULL,53),
(3051,0,'知识库管理',1,'kb','/kb',60),
(3052,3051,'文档列表',1,'kb:list','/kb/list',61),
(3053,3052,'文档上传',2,'kb:upload',NULL,62),
(3054,3052,'索引重建',2,'kb:rebuild',NULL,63),
(3061,0,'数据看板',1,'dashboard','/dashboard',70),
(3071,0,'系统管理',1,'system','/system',80),
(3072,3071,'用户管理',1,'system:user','/system/user',81),
(3073,3071,'角色管理',1,'system:role','/system/role',82),
(3074,3071,'权限管理',1,'system:permission','/system/permission',83),
(3075,3071,'审计日志',1,'system:audit','/system/audit',84)
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`),`path`=VALUES(`path`),`sort`=VALUES(`sort`);

-- 用户-角色
INSERT INTO `sys_user_role` (`id`,`user_id`,`role_id`) VALUES
(4001,1001,2001),
(4002,1002,2003)
ON DUPLICATE KEY UPDATE `role_id`=VALUES(`role_id`);

-- 角色-权限: 管理员全量
INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`)
SELECT 5000 + p.`id`, 2001, p.`id` FROM `sys_permission` p
ON DUPLICATE KEY UPDATE `permission_id`=VALUES(`permission_id`);

-- 客服: 仅工作台 + 知识库查看
INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`) VALUES
(5101,2003,3041),(5102,2003,3042),(5103,2003,3043),(5104,2003,3044),
(5105,2003,3051),(5106,2003,3052)
ON DUPLICATE KEY UPDATE `permission_id`=VALUES(`permission_id`);

-- 系统动态配置 (v1.3: 替代 Nacos; 默认值与 PRD §9.4/§9.5 一致)
-- value_type: 0 string 1 int 2 float 3 bool 4 json
INSERT INTO `sys_config` (`id`,`config_key`,`config_value`,`value_type`,`description`,`updated_by`) VALUES
(7001,'ai.retrieval_score_threshold','0.65',2,'RAG 检索 Top1 相似度阈值, 低于则判低置信(转人工信号 S1)',1001),
(7002,'ai.intent_confidence_threshold','0.75',2,'意图识别五分类置信度阈值, 低于则判低置信(转人工信号 S3)',1001),
(7003,'ai.context_support_threshold','0.5',2,'上下文支撑度判定阈值(转人工信号 S2)',1001),
(7004,'cs.agent_max_sessions','5',1,'每坐席默认并发接待会话上限',1001),
(7005,'cs.queue_timeout_seconds','60',1,'转人工排队超时秒数, 超时无坐席接入则提示留言',1001)
ON DUPLICATE KEY UPDATE `config_value`=VALUES(`config_value`),`value_type`=VALUES(`value_type`),`description`=VALUES(`description`);

-- =============================================================
-- 二、AI 客服域 (坐席)
-- =============================================================
INSERT INTO `ai_agent` (`id`,`user_id`,`agent_no`,`nickname`,`max_concurrent`,`skill_tags`,`status`) VALUES
(6001,1002,'A001','客服小美',5,'售前,售后,订单',1)
ON DUPLICATE KEY UPDATE `nickname`=VALUES(`nickname`),`status`=VALUES(`status`);

-- =============================================================
-- 三、用户域
-- =============================================================

-- 测试消费者 (密码同 Admin@123456)
INSERT INTO `biz_user` (`id`,`mobile`,`mobile_hash`,`password`,`nickname`,`gender`,`status`,`register_channel`) VALUES
(7001,'aMOXXy7mp+PHJDGEy0jFqTT6ninvlvyPhc0T4r3gA4K51hQ2YZv2','15c7dbebf135e360e3408b0ee9a92e57d3bb97fbc52b2b86379297f6c4f36581','$2b$10$5dZObJ1.m.1C4WEEKO45VOnuWl.k06Ld/7xw9vp/vIAWpihXKmMjC','测试用户A',1,1,1),
(7002,'NHIVZgBQoEhh8LRsn3np0DEy7GwN0AKPYuVyKfklG0IUnfuAD/70','b308a35303ccd3c1277db87d4a6e65857cc5f72eb53bfb0c976782795252ebd4','$2b$10$5dZObJ1.m.1C4WEEKO45VOnuWl.k06Ld/7xw9vp/vIAWpihXKmMjC','测试用户B',2,1,1)
ON DUPLICATE KEY UPDATE `nickname`=VALUES(`nickname`),`status`=VALUES(`status`);

INSERT INTO `biz_address` (`id`,`user_id`,`receiver`,`phone`,`phone_hash`,`province`,`city`,`district`,`detail`,`is_default`) VALUES
(7101,7001,'张三','aMOXXy7mp+PHJDGEy0jFqTT6ninvlvyPhc0T4r3gA4K51hQ2YZv2','15c7dbebf135e360e3408b0ee9a92e57d3bb97fbc52b2b86379297f6c4f36581','浙江省','杭州市','西湖区','文三路 100 号 1 幢 201 室',1),
(7102,7001,'张三(公司)','aMOXXy7mp+PHJDGEy0jFqTT6ninvlvyPhc0T4r3gA4K51hQ2YZv2','15c7dbebf135e360e3408b0ee9a92e57d3bb97fbc52b2b86379297f6c4f36581','上海市','上海市','浦东新区','张江高科技园区博云路 2 号',0)
ON DUPLICATE KEY UPDATE `detail`=VALUES(`detail`),`is_default`=VALUES(`is_default`);

-- =============================================================
-- 四、商品域
-- =============================================================

-- 三级类目树
INSERT INTO `biz_category` (`id`,`parent_id`,`name`,`level`,`sort`,`status`) VALUES
(8001,0,'数码电子',1,10,1),
(8002,0,'服饰鞋包',1,20,1),
(8011,8001,'手机通讯',2,11,1),
(8012,8001,'电脑办公',2,12,1),
(8021,8002,'男装',2,21,1),
(8101,8011,'智能手机',3,111,1),
(8102,8011,'手机配件',3,112,1),
(8103,8012,'笔记本电脑',3,121,1),
(8104,8012,'键鼠外设',3,122,1),
(8105,8021,'T恤',3,211,1)
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`),`parent_id`=VALUES(`parent_id`),`level`=VALUES(`level`);

-- 品牌
INSERT INTO `biz_brand` (`id`,`name`,`description`,`sort`,`status`) VALUES
(8201,'Apple','苹果公司',10,1),
(8202,'小米','小米科技',20,1),
(8203,'联想','联想集团',30,1),
(8204,'优衣库','日本迅销集团',40,1)
ON DUPLICATE KEY UPDATE `description`=VALUES(`description`);

-- 运费模板 (PRD §6.6)
INSERT INTO `biz_freight_template` (`id`,`name`,`free_threshold`,`base_freight`,`remote_extra`,`remote_regions`,`is_default`,`status`) VALUES
(8301,'默认模板',99.00,10.00,15.00,JSON_ARRAY('新疆','西藏','青海','内蒙古'),1,1),
(8302,'数码包邮',0.00,0.00,0.00,NULL,0,1)
ON DUPLICATE KEY UPDATE `free_threshold`=VALUES(`free_threshold`),`base_freight`=VALUES(`base_freight`),`is_default`=VALUES(`is_default`);

-- SPU
INSERT INTO `biz_spu` (`id`,`category_id`,`brand_id`,`freight_template_id`,`name`,`subtitle`,`main_pic`,`min_price`,`max_price`,`total_stock`,`sales`,`status`) VALUES
(8401,8101,8201,8302,'Apple iPhone 15 Pro','钛金属设计, A17 Pro 芯片','https://cdn.example.com/p/iphone15pro.jpg',7999.00,9999.00,300,120,2),
(8402,8101,8202,8302,'小米 14 Ultra','徕卡光学镜头, 骁龙 8 Gen3','https://cdn.example.com/p/mi14ultra.jpg',5999.00,6999.00,200,85,2),
(8403,8103,8203,8302,'联想 ThinkPad X1 Carbon','14 英寸商务轻薄本','https://cdn.example.com/p/x1carbon.jpg',9999.00,12999.00,80,30,2),
(8404,8104,8202,8301,'小米机械键盘','87 键红轴, 全键无冲','https://cdn.example.com/p/kb87.jpg',199.00,299.00,500,260,2),
(8405,8105,8204,8301,'优衣库 U 系列圆领 T 恤','纯棉舒适, 多色可选','https://cdn.example.com/p/utshirt.jpg',79.00,99.00,1000,640,2)
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`),`min_price`=VALUES(`min_price`),`max_price`=VALUES(`max_price`),`status`=VALUES(`status`);

-- SKU (与库存一一对应)
INSERT INTO `biz_sku` (`id`,`spu_id`,`sku_code`,`specs`,`price`,`original_price`,`status`) VALUES
(8501,8401,'IP15P-256-BLK',JSON_ARRAY(JSON_OBJECT('k','颜色','v','原色钛金属'),JSON_OBJECT('k','容量','v','256GB')),8999.00,9999.00,1),
(8502,8401,'IP15P-256-BLU',JSON_ARRAY(JSON_OBJECT('k','颜色','v','蓝色钛金属'),JSON_OBJECT('k','容量','v','256GB')),8999.00,9999.00,1),
(8503,8401,'IP15P-512-BLK',JSON_ARRAY(JSON_OBJECT('k','颜色','v','原色钛金属'),JSON_OBJECT('k','容量','v','512GB')),9999.00,10999.00,1),
(8511,8402,'MI14U-512-BLK',JSON_ARRAY(JSON_OBJECT('k','颜色','v','黑色'),JSON_OBJECT('k','容量','v','512GB')),6499.00,6999.00,1),
(8512,8402,'MI14U-512-WHT',JSON_ARRAY(JSON_OBJECT('k','颜色','v','白色'),JSON_OBJECT('k','容量','v','512GB')),6499.00,6999.00,1),
(8521,8403,'X1C-16-512',JSON_ARRAY(JSON_OBJECT('k','配置','v','i7/16G/512G')),9999.00,12999.00,1),
(8531,8404,'KB87-RED',JSON_ARRAY(JSON_OBJECT('k','轴体','v','红轴')),199.00,299.00,1),
(8532,8404,'KB87-BROWN',JSON_ARRAY(JSON_OBJECT('k','轴体','v','茶轴')),229.00,329.00,1),
(8541,8405,'UT-M-WHT',JSON_ARRAY(JSON_OBJECT('k','尺码','v','M'),JSON_OBJECT('k','颜色','v','白色')),79.00,99.00,1),
(8542,8405,'UT-L-WHT',JSON_ARRAY(JSON_OBJECT('k','尺码','v','L'),JSON_OBJECT('k','颜色','v','白色')),79.00,99.00,1)
ON DUPLICATE KEY UPDATE `price`=VALUES(`price`),`specs`=VALUES(`specs`),`status`=VALUES(`status`);

-- 库存 (满足恒等式 total = available + locked + sold)
INSERT INTO `biz_sku_stock` (`id`,`sku_id`,`total_stock`,`available_stock`,`locked_stock`,`sold_stock`,`version`) VALUES
(8601,8501,100,70,0,30,0),
(8602,8502,80,55,0,25,0),
(8603,8503,60,45,0,15,0),
(8611,8511,120,80,0,40,0),
(8612,8512,80,55,0,25,0),
(8621,8521,80,50,0,30,0),
(8631,8531,300,160,0,140,0),
(8632,8532,200,140,0,60,0),
(8641,8541,600,360,0,240,0),
(8642,8542,400,240,0,160,0)
ON DUPLICATE KEY UPDATE `total_stock`=VALUES(`total_stock`),`available_stock`=VALUES(`available_stock`),`locked_stock`=VALUES(`locked_stock`),`sold_stock`=VALUES(`sold_stock`);

-- SPU 图集
INSERT INTO `biz_spu_image` (`id`,`spu_id`,`url`,`sort`) VALUES
(8701,8401,'https://cdn.example.com/p/iphone15pro-1.jpg',1),
(8702,8401,'https://cdn.example.com/p/iphone15pro-2.jpg',2),
(8703,8402,'https://cdn.example.com/p/mi14ultra-1.jpg',1),
(8704,8403,'https://cdn.example.com/p/x1carbon-1.jpg',1),
(8705,8404,'https://cdn.example.com/p/kb87-1.jpg',1),
(8706,8405,'https://cdn.example.com/p/utshirt-1.jpg',1)
ON DUPLICATE KEY UPDATE `url`=VALUES(`url`),`sort`=VALUES(`sort`);

-- =============================================================
-- 五、营销域 (优惠券)
-- =============================================================
INSERT INTO `biz_coupon_template` (`id`,`name`,`type`,`threshold_amount`,`discount_amount`,`discount_rate`,`max_discount`,`total_count`,`remain_count`,`per_limit`,`start_time`,`end_time`,`valid_days`,`scope_type`,`status`) VALUES
(8801,'新人专享 50 元券',1,299.00,50.00,NULL,NULL,10000,9950,1,'2026-01-01 00:00:00','2027-12-31 23:59:59',30,1,1),
(8802,'满 2000 减 200',1,2000.00,200.00,NULL,NULL,5000,5000,2,'2026-01-01 00:00:00','2027-12-31 23:59:59',15,1,1),
(8803,'数码专享 95 折',2,1000.00,0.00,0.95,300.00,2000,2000,1,'2026-01-01 00:00:00','2027-12-31 23:59:59',7,2,1),
(8804,'无门槛 10 元券',3,0.00,10.00,NULL,NULL,20000,20000,1,'2026-01-01 00:00:00','2027-12-31 23:59:59',7,1,1)
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`),`threshold_amount`=VALUES(`threshold_amount`),`discount_amount`=VALUES(`discount_amount`),`status`=VALUES(`status`);

-- 给测试用户发几张券 (券3的 scope_type=2 指定类目)
UPDATE `biz_coupon_template` SET `scope_ids`=JSON_ARRAY(8001) WHERE `id`=8803;

INSERT INTO `biz_user_coupon` (`id`,`user_id`,`coupon_id`,`status`,`receive_time`,`expire_time`) VALUES
(8901,7001,8801,1,'2026-09-01 10:00:00','2027-12-31 23:59:59'),
(8902,7001,8804,1,'2026-09-01 10:00:00','2027-12-31 23:59:59'),
(8903,7002,8801,1,'2026-09-01 10:00:00','2027-12-31 23:59:59')
ON DUPLICATE KEY UPDATE `status`=VALUES(`status`),`expire_time`=VALUES(`expire_time`);

-- =============================================================
-- 六、AI 知识库 (让 RAG 开箱可测)
-- =============================================================
INSERT INTO `ai_kb_document` (`id`,`title`,`domain`,`file_type`,`content`,`enabled`,`status`) VALUES
(9001,'七天无理由退货政策','policy','md','消费者自收到商品之日起 7 日内, 在商品完好、不影响二次销售的前提下, 可申请无理由退货。定制类商品、拆封的数码产品、贴身衣物不支持无理由退货。退货产生的运费由消费者承担, 若为商品质量问题则由商家承担。',1,2),
(9002,'退款到账时间说明','policy','md','退款申请审核通过后, 款项将原路退回。支付宝渠道一般 1-3 个工作日到账, 微信支付渠道一般 1-5 个工作日到账。若超过 7 个工作日仍未到账, 请联系人工客服并提供订单号核查。',1,2),
(9003,'配送与物流时效','faq','md','现货商品在支付成功后 24 小时内发货, 大促期间可能延迟至 48 小时。默认合作快递为中通、圆通、顺丰。江浙沪皖地区一般 1-2 天送达, 其他地区 2-4 天, 新疆西藏等偏远地区 5-7 天。满 99 元包邮, 偏远地区需补 15 元运费。',1,2),
(9004,'发票开具说明','faq','md','本店支持开具电子普通发票与增值税专用发票。电子普票在订单完成后可自助申请, 开具后发送至预留邮箱。增值税专票需提供公司名称、税号、开户行及账号、注册地址及电话, 审核通过后 3 个工作日内开具。',1,2),
(9005,'账号安全与密码找回','faq','md','若忘记密码, 可在登录页点击"忘记密码", 通过手机号验证码重置。连续 5 次输入错误密码, 账号将被锁定 30 分钟。为保障账户安全, 请勿向任何人透露验证码, 客服不会主动索要验证码。',1,2),
(9006,'优惠券使用规则','policy','md','优惠券仅可在有效期内使用, 过期自动失效。单笔订单限用 1 张优惠券, 不可叠加。优惠券按商品实付金额判断是否达到使用门槛, 运费不计入门槛。订单取消或全额退款时, 未过期的优惠券将原路返还至账户。',1,2),
(9007,'会员等级与权益','policy','md','会员等级根据近 12 个月累计消费金额计算: 普通会员(0 元)、银卡会员(5000 元)、金卡会员(20000 元)、钻石会员(50000 元)。等级越高享受的折扣与专属客服权益越多, 等级每月 1 日更新。',1,2)
ON DUPLICATE KEY UPDATE `title`=VALUES(`title`),`content`=VALUES(`content`),`enabled`=VALUES(`enabled`),`status`=VALUES(`status`);

-- 切片 (id 同时作为 Milvus 向量记录主键; 此处按段落粗切, 真实切片由 FastAPI 服务生成)
INSERT INTO `ai_kb_chunk` (`id`,`document_id`,`domain`,`content`,`token_count`,`status`) VALUES
(9101,9001,'policy','消费者自收到商品之日起 7 日内, 在商品完好、不影响二次销售的前提下, 可申请无理由退货。',42,1),
(9102,9001,'policy','定制类商品、拆封的数码产品、贴身衣物不支持无理由退货。',28,1),
(9103,9001,'policy','退货产生的运费由消费者承担, 若为商品质量问题则由商家承担。',30,1),
(9104,9002,'policy','退款申请审核通过后, 款项将原路退回。支付宝渠道一般 1-3 个工作日到账, 微信支付渠道一般 1-5 个工作日到账。',48,1),
(9105,9003,'faq','现货商品在支付成功后 24 小时内发货, 大促期间可能延迟至 48 小时。默认合作快递为中通、圆通、顺丰。',44,1),
(9106,9003,'faq','江浙沪皖地区一般 1-2 天送达, 其他地区 2-4 天, 新疆西藏等偏远地区 5-7 天。满 99 元包邮, 偏远地区需补 15 元运费。',52,1),
(9107,9004,'faq','本店支持开具电子普通发票与增值税专用发票。电子普票在订单完成后可自助申请。',38,1),
(9108,9005,'faq','连续 5 次输入错误密码, 账号将被锁定 30 分钟。客服不会主动索要验证码。',35,1),
(9109,9006,'policy','单笔订单限用 1 张优惠券, 不可叠加。优惠券按商品实付金额判断是否达到使用门槛, 运费不计入门槛。',44,1),
(9110,9006,'policy','订单取消或全额退款时, 未过期的优惠券将原路返还至账户。',28,1),
(9111,9007,'policy','会员等级根据近 12 个月累计消费金额计算: 普通会员(0 元)、银卡会员(5000 元)、金卡会员(20000 元)、钻石会员(50000 元)。',52,1)
ON DUPLICATE KEY UPDATE `content`=VALUES(`content`),`status`=VALUES(`status`);

-- 更新文档切片数
UPDATE `ai_kb_document` d
   SET d.`chunk_count` = (SELECT COUNT(*) FROM `ai_kb_chunk` c WHERE c.`document_id` = d.`id` AND c.`status` = 1)
 WHERE d.`id` BETWEEN 9001 AND 9007;

-- =============================================================
-- 七、自检
-- =============================================================
SELECT '=== 种子数据自检 ===' AS ``;
SELECT '后台用户'   AS `表`, COUNT(*) AS `行数` FROM `sys_user`
UNION ALL SELECT '角色',       COUNT(*) FROM `sys_role`
UNION ALL SELECT '权限',       COUNT(*) FROM `sys_permission`
UNION ALL SELECT '用户-角色',  COUNT(*) FROM `sys_user_role`
UNION ALL SELECT '角色-权限',  COUNT(*) FROM `sys_role_permission`
UNION ALL SELECT '客服坐席',   COUNT(*) FROM `ai_agent`
UNION ALL SELECT '消费者',     COUNT(*) FROM `biz_user`
UNION ALL SELECT '收货地址',   COUNT(*) FROM `biz_address`
UNION ALL SELECT '类目',       COUNT(*) FROM `biz_category`
UNION ALL SELECT '品牌',       COUNT(*) FROM `biz_brand`
UNION ALL SELECT '运费模板',   COUNT(*) FROM `biz_freight_template`
UNION ALL SELECT 'SPU',        COUNT(*) FROM `biz_spu`
UNION ALL SELECT 'SKU',        COUNT(*) FROM `biz_sku`
UNION ALL SELECT '库存',       COUNT(*) FROM `biz_sku_stock`
UNION ALL SELECT '优惠券模板', COUNT(*) FROM `biz_coupon_template`
UNION ALL SELECT '用户券',     COUNT(*) FROM `biz_user_coupon`
UNION ALL SELECT '知识库文档', COUNT(*) FROM `ai_kb_document`
UNION ALL SELECT '知识库切片', COUNT(*) FROM `ai_kb_chunk`
UNION ALL SELECT '系统配置',   COUNT(*) FROM `sys_config`;

-- 库存恒等式校验 (PRD §6.2)
SELECT IF(COUNT(*)=0,'✅ 全部 SKU 库存恒等式成立',
          CONCAT('❌ ',COUNT(*),' 个 SKU 库存失衡')) AS `库存一致性`
  FROM `biz_sku_stock`
 WHERE `total_stock` <> `available_stock` + `locked_stock` + `sold_stock`;

-- 运费模板默认唯一性
SELECT IF(SUM(`is_default`)=1,'✅ 默认运费模板唯一',
          CONCAT('❌ 默认模板数量异常: ',SUM(`is_default`))) AS `运费模板`
  FROM `biz_freight_template`;
