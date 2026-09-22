# 方案：移除 Java，后端全栈切换 FastAPI

## Context（背景与目标）

AIDS（AI Driven Store，毕设电商 + AI 客服）目前处于 **T0 设计冻结阶段**：只有文档（PRD / TASKS / API / DATA-DICTIONARY / SQL）与部署配置（deploy/），**尚无任何实际代码**。原设计主业务后端为 Spring Boot 3.x (Java 17) + MyBatis-Plus，Mock 渠道服务也是 Spring Boot 模块。

用户指令：**移除全部 Java，所有后端由 FastAPI 承载**。经确认的三个架构决策：

| 决策点 | 结论 |
|--------|------|
| Nacos（Java 应用、Spring 生态专属） | **移除**。静态配置走 pydantic-settings（.env）；动态配置（AI 阈值等）新增 `sys_config` 表 + Redis 发布订阅热更新 |
| Kafka（JVM 中间件，语言无关） | **保留**。acks=all / 幂等 / DLQ / 本地消息表设计全部不变 |
| 服务拆分 | **保持 3 个独立 FastAPI 服务**：主业务 aids-backend(:8080) / AI 客服 aids-ai(:8000) / Mock 渠道 aids-mock(:8081)，保持 PRD 故障隔离设计 |

**接口契约零变化**：API 路径、错误码、SSE 协议、端口、Kafka topic、全部 SQL 业务表均不动，前端无感知。

## 技术栈映射表（文档改写的统一依据）

| 原（Java 系） | 新（Python 系） |
|---------------|-----------------|
| Spring Boot 3.x (Java 17) | FastAPI (Python 3.11+) |
| MyBatis-Plus | SQLAlchemy 2.0（async） |
| Flyway（TASKS 中 BE-12 提及） | Alembic |
| MyBatis-Plus 拦截器（数据权限） | SQLAlchemy 统一查询过滤器 / Repository 基类强制注入 `user_id` 条件 |
| MyBatis TypeHandler（手机号加解密） | SQLAlchemy TypeDecorator |
| jjwt / Spring Security JWT（RS256） | PyJWT + cryptography（RS256 不变，主业务持私钥签发，AI 服务持公钥验签） |
| BCrypt（Spring Security） | bcrypt（passlib 已弃维护，直接用 bcrypt 库） |
| AOP 审计切面 | FastAPI 依赖注入 + 中间件/装饰器 |
| Nacos 配置中心 | pydantic-settings（.env）+ `sys_config` 动态配置表 + Redis pub/sub 热更新 |
| Spring Cloud Gateway（二期网关） | APISIX / Kong（语言无关，二期） |
| `/actuator/health` | `/health`（统一，AI 服务已是此约定） |
| JVM 监控指标 | Python 运行时指标（uvicorn worker 存活、事件循环延迟、内存 RSS） |
| Spring Bean（PaymentChannel 抽象） | Python Protocol/ABC 适配器（语义不变） |

## 逐文件改动

### 1. docs/PRD.md（v1.2 → v1.3）
- 头部表：版本 v1.3、更新日期 2026-09-21、新增「v1.3 修订」行：后端全栈切换 FastAPI、移除 Nacos、网关备选改 APISIX/Kong（工期估算不因此调整）
- §2.2：「MyBatis-Plus 拦截器统一注入数据权限条件」→ SQLAlchemy 过滤器方案
- §4.4：JVM 指标 → Python 运行时指标；「Nacos（配置+服务注册），本地开发用 application-local.yml」→「pydantic-settings（.env 覆盖）+ sys_config 动态配置（Redis 发布订阅热更新）；单机 Compose 静态编排，无需注册中心」；健康检查统一 `/health`
- §5.1 技术栈表：主业务行、Mock 服务行、网关行按映射表改写；**删除 Nacos 行**，新增「配置管理」行（pydantic-settings + sys_config）
- §5.2 架构图：框内文字 Spring Boot → FastAPI
- §5.3：通信约定四处 `SpringBoot` 改「主业务服务」；JWT 表「SpringBoot 持私钥签发，FastAPI 持公钥验签」→「主业务服务持私钥签发，AI 服务持公钥本地验签」；「静态 Token（Nacos 下发）」→「（环境变量下发）」
- §8.4：「更换 Spring Bean 与配置」→「更换适配器实现与配置」
- §9.4：「阈值全部落 Nacos 配置」→「阈值全部落 sys_config 动态配置，后台可视化调整，Redis 发布订阅热更新，无需重启」；§9.5 接待上限同理
- §11 M1：「多模块工程」→「FastAPI 三服务工程（backend/ai/mock）」
- §12.7：「从 Nginx 到 FastAPI 日志」→「从 Nginx 贯通全部后端服务」
- §14：「本期 SpringBoot 单体模块化」→「本期 FastAPI 单体模块化」

### 2. docs/TASKS.md（v2.0 → v2.1）
- 头部版本 v2.1 + 新增「v1.3 修订」说明段（对应 PRD v1.3）
- **BE 任务改写**（内容与编号不变，措辞换栈）：
  - BE-01：SpringBoot+MyBatis-Plus 脚手架 → FastAPI + Pydantic v2 + SQLAlchemy 2.0 async，APIRouter 模块化分包（user/product/order/pay/marketing/admin）
  - BE-02：雪花 ID、时间自动填充（Mapper 事件）、逻辑删除（查询过滤器）、分页、乐观锁（version 条件更新）；**Alembic** 迁移基建
  - BE-03：JWT 双 Token——PyJWT + cryptography，RS256 + JWKS 端点
  - BE-04：MyBatis-Plus 拦截器 → SQLAlchemy 统一查询过滤器（验收标准不变：A 的 Token 访问 B 的数据全 403）
  - BE-05：追加「sys_config 动态配置读取 + Redis 发布订阅热更新」
  - BE-07：「MyBatis TypeHandler」→「SQLAlchemy TypeDecorator」
  - BE-12：「纳入 Flyway 迁移」→「纳入 Alembic 迁移」
  - BE-31：「AOP 切面」→「FastAPI 中间件/装饰器」
- **MOCK-01/02/03**：SpringBoot 独立模块 → FastAPI 独立服务（aids-mock 工程独立库独立端口不变）；PaymentChannel 抽象注明 Protocol/ABC
- T1 出口：「后端 `/actuator/health`」→「主业务 `/health`」
- DEP-01：10 服务 → **9 服务**（移除 Nacos），默认档内存估算 ~1.7GB → ~1.2GB；T0 完成记录中 Nacos 相关句子加「v1.3 已移除」注记（保留历史实测记录本身）
- DOC-02：37 表 → **38 表**（新增 sys_config）
- DEP-08：「JVM」→「Python 运行时（uvicorn worker、事件循环延迟、RSS）」
- DEP-10：traceId 链路「Nginx→SpringBoot→FastAPI」→「Nginx→backend→ai/mock」

### 3. docs/API.md（v1.0 → v1.1）
- §四 标题「内部服务接口（SpringBoot ↔ FastAPI）」→「（主业务 ↔ AI 服务）」
- 「Nacos 下发的静态 Token」→「环境变量下发的静态 Token」
- 其余（错误码、SSE 协议、路径）零变化

### 4. docs/DATA-DICTIONARY.md
- 「Java 枚举与前端映射」→「Python 枚举（str Enum）与前端映射」
- 「BE-12 经 Flyway 迁移落地」→「经 Alembic 迁移落地」
- 头部统计 37 表 → 38 表；系统域新增 `sys_config` 表条目（字段说明见下）

### 5. docs/sql/schema.sql（v1.1 → v1.2）+ seed.sql + init 脚本
- schema.sql L17 注释：「MyBatis TypeHandler」→「SQLAlchemy TypeDecorator」
- 系统域新增表（遵循现有约定：BIGINT UNSIGNED 雪花 ID、DATETIME、无物理外键、utf8mb4）：

```sql
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
```

- seed.sql：灌入默认配置（与 PRD §9.4/§9.5 默认值一致）：`ai.retrieval_score_threshold=0.65`、`ai.intent_confidence_threshold=0.75`、`ai.context_support_threshold`、`cs.agent_max_sessions=5`、`cs.queue_timeout_seconds=60` 等；自检段补一行 sys_config 计数
- deploy/mysql/init/00-init-databases.sh：注释「37 表」→「38 表」

### 6. deploy/docker-compose.yml
- **删除整个 nacos 服务块**及 `nacos-data`、`nacos-logs` 两个卷
- 头部注释：10 服务 → 9；默认档「+ Kafka + MinIO + Nacos ≈ 1.7GB」→「+ Kafka + MinIO ≈ 1.2GB」
- 其余（MySQL/Redis/Kafka/MinIO/Milvus/Nginx）不动

### 7. deploy/app/backend.Dockerfile、mock.Dockerfile（整体重写）
以现有 [ai.Dockerfile](../../deploy/app/ai.Dockerfile) 为模板（python:3.11-slim 两阶段：builder 阶段 `pip install --user`，runtime 阶段仅 COPY 依赖与代码，非 root，TZ=UTC）：
- **backend.Dockerfile**：构建上下文 `aids-backend/`（requirements.txt + app/），EXPOSE 8080，HEALTHCHECK 打 `/health`，CMD gunicorn + UvicornWorker（workers 4，timeout 120）
- **mock.Dockerfile**：`aids-mock/`，EXPOSE 8081，HEALTHCHECK `/health`，gunicorn workers 2
- ai.Dockerfile 与 frontend.Dockerfile 不动（frontend 中 `application/javascript` 为 MIME 类型，非 Java 引用，保留）

### 8. deploy/nginx/（三处小改）
- conf.d/default.conf 头部注释：「`/api/**` → SpringBoot」→「→ FastAPI 主业务」
- nginx/Dockerfile L13 注释同上
- html/index.html：删除「Nacos 控制台」入口条目；「SpringBoot」→「FastAPI」

### 9. deploy/.env.example 与 deploy/.env（同步）
- 删除全部 NACOS_* 变量（端口 / IDENTITY_KEY / IDENTITY_VALUE / AUTH_TOKEN / SERVER_ADDR / USERNAME / PASSWORD 共 9 行 + 2 段注释）
- JWT 注释「供 FastAPI 验签」→「供 AI 服务验签」；INTERNAL_TOKEN 注释「（SpringBoot ↔ FastAPI）」→「（主业务 ↔ AI 服务）」

### 10. deploy/README.md
- 分档表：默认档服务列去 Nacos、内存 ≥4GB → ≥3.5GB；服务入口表删 Nacos 行
- 目录结构：backend.Dockerfile 注释「SpringBoot（分层构建）」→「FastAPI 主业务」；mock 同理
- 踩坑清单 #7（Nacos AUTH_TOKEN）：删除该条并重新编号，README 头部加一条 v1.3 变更说明

## 明确不改的部分
- Kafka 及全部 topic 契约、Redis、MySQL、MinIO、Milvus 配置
- 全部业务表 DDL、错误码、API 路径、SSE 协议、三个服务端口（8080/8000/8081）
- RS256 JWT 设计（仅签发方描述文字变化）、`deploy/app/ai.Dockerfile`、`frontend.Dockerfile`
- 工期估算（17 周）与任务总数（96 项）

## 验证

1. **残留扫描**（核心验收）：
   ```bash
   grep -rniE '\bjava\b|spring|mybatis|jvm|maven|mvnw|pom\.xml|actuator|nacos|flyway' docs/ deploy/ \
     | grep -v 'application/javascript' | grep -v 'text/javascript'
   ```
   预期输出为空（frontend.Dockerfile 的 MIME 类型 `application/javascript` 与 html `<script>` 属误报，排除）
2. **Compose 语法与结构**：`docker compose -f deploy/docker-compose.yml config` 通过，且输出含 9 个服务、无 nacos 卷
3. **一致性核对**：文档间相互引用的数字一致——表数 38（schema 注释 / init 脚本 / DATA-DICTIONARY / TASKS DOC-02）、服务数 9（compose 注释 / README / TASKS DEP-01）、版本号 PRD v1.3 / TASKS v2.1 / API v1.1 / schema v1.2
4. **可选（若本机 docker 可用）**：`docker compose build mysql` 后 `docker compose up -d mysql`，初始化日志应显示 aids_shop 38 表且 sys_config 自检行出现
