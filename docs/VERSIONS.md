# AIDS 文档版本矩阵（常驻核对表）

| 项 | 内容 |
|------|------|
| 版本 | v1.0 |
| 建立日期 | 2026-09-21 |
| 用途 | **DOC-03 配套资产**。固化「哪几个版本是一套」以及「跨文档必须相等的数字」，把每次修订时的一次性人工核对升级为常驻可查 + 机器可校验 |
| 维护规则 | **任何文档版本或下表常量变化，必须同一次提交内更新本文件**；`docs/tools/gen_data_dictionary.py --check` 会校验下表「可脚本化」的行，不一致即非零退出 |

> **为什么有这个文件**：v1.2（移除 ES）与 v1.3（去 Java）两次跨文档修订，都在个别位置留下了未同步的残留
> （服务数、内存口径、工期、未 bump 的版本号）。根因不是不认真，而是**没有一处权威地方写着「这套版本长什么样」**。
> 本文件就是这个锚点。

---

## 一、当前配套版本（一套，缺一不可）

| 文件 | 当前版本 | 上次修订 | 本轮修订原因 |
|------|----------|----------|--------------|
| [PRD.md](PRD.md) | **v1.4** | 2026-09-23 | §15 #6 开发人力由「待裁决」转「已决策」：1 人 + AI 作为第二执行者；范围暂不裁剪，改由 T1 出口实测触发分级裁剪 |
| [TASKS.md](TASKS.md) | **v2.2** | 2026-09-23 | §工作量对账 增补「决策记录」（人力口径与触发条件）；任务编号与总数仍是 97 |
| [API.md](API.md) | **v1.1** | 2026-09-21 | §四 标题与内部调用方改「主业务 ↔ AI 服务」；Token 来源改环境变量 |
| [DATA-DICTIONARY.md](DATA-DICTIONARY.md) | **v1.1** | 2026-09-21 | 新增 `sys_config`；37→38 表、411→419 字段；Flyway→Alembic；Java 枚举→Python str Enum |
| [sql/schema.sql](sql/schema.sql) | **v1.2** | 2026-09-21 | 新增 `sys_config` 建表；TypeHandler→TypeDecorator |
| [sql/mock_schema.sql](sql/mock_schema.sql) | **v1.1** | 2026-09-21 | `mock_sms_record.mobile` 注释补「真实渠道接入后本表废弃」 |
| [sql/seed.sql](sql/seed.sql) | **v1.1** | 2026-09-21 | 灌入 `sys_config` 5 条默认配置 + 自检计数行 |
| [deploy/docker-compose.yml](../deploy/docker-compose.yml) | 无版本号，以 `9 服务` 与内存口径为准 | 2026-09-21 | 移除 nacos 服务与 `nacos-data`/`nacos-logs` 卷 |
| [deploy/README.md](../deploy/README.md) | 无版本号，以踩坑条数为准 | 2026-09-21 | 分档表去 Nacos；踩坑 #6 改写、新增 #9/#10 |
| [deploy/app/*.Dockerfile](../deploy/app/) | 无版本号 | 2026-09-21 | backend / mock 由 Maven 分层构建改为 `python:3.11-slim` 两阶段 |

> `mock_schema.sql` / `seed.sql` / `DATA-DICTIONARY.md` 在本轮已实际变更但仍停留在 v1.0，本次一并补齐为 v1.1。
> **这正是本文件要防的那类问题**：改动落在正文，版本号却没人记得 bump。

---

## 二、跨文档必须相等的常量（核对命令可直接跑）

| # | 常量 | 期望值 | 声明处 | 核对命令 | 脚本化 |
|---|------|--------|--------|----------|--------|
| 1 | 主库表数 | **38** | schema.sql / DATA-DICTIONARY 表头 / TASKS DOC-02 / init 脚本注释 | `grep -c '^CREATE TABLE' docs/sql/schema.sql` | ✅ |
| 2 | 主库字段数 | **419** | DATA-DICTIONARY 表头 | `grep -cE '^\| \`[a-z_]+\` \|' docs/DATA-DICTIONARY.md` | ✅ |
| 3 | Mock 库表数 | **7** | mock_schema.sql / DATA-DICTIONARY / TASKS DOC-02 | `grep -c '^CREATE TABLE' docs/sql/mock_schema.sql` | ✅ |
| 4 | 表清单 ↔ 字段明细 | **38 = 38** | DATA-DICTIONARY §二 / §三 | `grep -c '^#### \`' docs/DATA-DICTIONARY.md` | ✅ |
| 5 | 状态枚举映射组 | **11** | DATA-DICTIONARY §一 | 取 `## 一、` 至 `## 二、` 间 `^### ` 计数 | ✅ |
| 6 | 不变量条数 | **12** | DATA-DICTIONARY §四 | `awk '/^## 四、/,0' docs/DATA-DICTIONARY.md \| grep -cE '^\| *[0-9]+ *\|'` | ✅ |
| 7 | 中间件服务数 | **9** | compose / README 分档 / TASKS DEP-01 | `docker compose -f deploy/docker-compose.yml --profile search config --services \| wc -l` | ✅ |
| 8 | 任务总数 | **97** | TASKS §梯次概览「总任务数」行 | `awk -F'\|' '/^\| (BE\|FE\|AI\|MOCK\|DEP\|DOC)-[0-9]+ \|/{c++} END{print c}' docs/TASKS.md` | ✅ |
| 9 | 总工期 | **17 周** | PRD §11 / PRD §15 #6 / TASKS 概览 | `grep -c '17 周' docs/PRD.md docs/TASKS.md` 且不得出现「16 周」结论 | ✅ |
| 10 | 全量档内存口径 | **宿主可用 ≥6GB（8GB 舒适）**；容器空载实测 ≈1.5GB（默认档 ≈0.9GB） | PRD §13 / §15 / README 分档表 / compose 头部 | `grep -rn '≥ 6GB\|≥6GB' docs/PRD.md deploy/README.md deploy/docker-compose.yml` | ⬜ 人工 |
| 11 | 三服务端口 | 主业务 **8080** / AI **8000** / Mock **8081**（前端另有 80，不计入） | PRD §5.3 / API 头部 / 各 Dockerfile EXPOSE | `grep -h EXPOSE deploy/app/backend.Dockerfile deploy/app/ai.Dockerfile deploy/app/mock.Dockerfile` | ✅ |
| 12 | 部署踩坑条数 | **11** | deploy/README.md | `grep -cE '^\*\*[0-9]+\.' deploy/README.md` | ✅ |
| 13 | Java 残留 | **0 处活跃引用**（仅允许带 v1.3 注记的历史/对照说明） | 全仓 | `grep -rniE '\bjava\b\|spring\|mybatis\|jvm\|nacos\|flyway\|actuator' docs/ deploy/ \| grep -v 'application/javascript'` 后人工判定 | ⬜ 人工 |

> **常量 5 说明**：状态枚举映射共 **11 组**
> （订单/支付/售后/优惠券/库存变更/操作者/会话/转人工原因/排队/消息角色/知识库状态），以本表 #5 为唯一锚点。
> 历史遗留的「10 组」旧写法曾出现在 `README.md` C2 行、`工程化门禁方案.md` §3.1 与附录 A，
> 现已全部订正为 11 组；此后新增枚举时，除本表 #5 外无需再改其它文档的计数。

---

## 三、修订沿革（主线）

| 版本节点 | 日期 | 动了什么 | 连带数字变化 |
|----------|------|----------|--------------|
| v1.0 / v1.0 | 2026-09-19 前 | 初始设计冻结 | 11 服务、37 表、16 周（表头误值） |
| **v1.2 / v2.0** | 2026-09-19 | **移除 Elasticsearch**，商品搜索改 MySQL 全文索引（`ngram`） | 服务 11→10；撤销 `product.changed` topic；总工期校正为 **17 周** |
| **v1.3 / v2.1 / API v1.1 / schema v1.2** | 2026-09-21 | **移除全部 Java**，后端全栈 FastAPI；**移除 Nacos**，动态配置改 `sys_config` + Redis 热更新 | 服务 10→**9**；表 37→**38**；字段 411→**419**；Flyway→Alembic；`/actuator/health`→`/health`；接口契约/端口/业务表 **零变化** |
| v1.3 收尾 | 2026-09-21 | 按 `文档质量锐评.md` 修复：内存口径统一、工期残留、`data_scope` 退化定义、成本上界、本版本矩阵 | 无表/服务数变化；DD·seed·mock_schema 版本补齐 v1.1 |
| 门禁常量订正 | 2026-09-22 | ① `README.md` / `工程化门禁方案.md` 的「10 组」旧写法统一为 **11 组**；② TASKS 计数订正（增补 `BE-00` 后锚点未同步）；③ 移除审校快照文件及其全部悬空引用 | TASKS 总数 96→**97**（板块 BE 36→**37**、T1 梯次 12→**13**）；枚举组数 / 表数 / 字段数 / 服务数无变化 |
| **v1.4 / v2.2** | 2026-09-23 | **开发人力口径定稿**（PRD §15 #6）：采纳「1 人 + AI 作为第二执行者」，范围暂不裁剪，改由 T1 出口的实测压缩率触发分级裁剪（判据见 TASKS §工作量对账「决策记录」）；同批落地 C9~C12 契约测试与三服务骨架（详见 HANDOFF §0.1） | 无表 / 字段 / 枚举 / 服务数变化；任务总数仍为 **97**；工期头条不变 |

---

## 四、改动同步规则（改 X 必须同时动 Y）

**门禁怎么跑**（不修改任何文件；非零退出即有漂移）：

```bash
python3 docs/tools/gen_data_dictionary.py --check          # 静默，只报失败项
python3 docs/tools/gen_data_dictionary.py --check -v       # 逐条打印全部断言（条数随门禁增补而变，以输出为准）
python3 docs/tools/gen_data_dictionary.py --gen biz_order  # 从 DDL 重生成该表明细（只打印，不写盘）
```

> 门禁覆盖：DDL ↔ 数据字典的表集合/字段名序/类型/可空/默认/说明逐行比对、字段总数、枚举映射与不变量计数、
> 跨文档常量（服务数/任务数/踩坑条数/端口/内存口径/工期残留）、`.env` 重复键与 compose 变量覆盖、
> 以及 VERSIONS.md 与脚本期望值的**双向互认**。
> 已验证门禁**能红**：临时把某字段类型 `VARCHAR(32)` 改成 `VARCHAR(64)`、把表头 `419` 改成 `418`，
> 均立即 `exit=1` 并指出具体差异；还原后恢复全绿。当前状态：**全部断言一致**。
> 仓库尚未 `git init`，因此先以 CLI + CI 步骤形式执行；建库后由 DEP-05 挂 pre-commit 钩子跑同一脚本。

| 你改了 | 必须同步 |
|--------|----------|
| `docs/sql/schema.sql`（任何表/字段/COMMENT） | 跑 `python3 docs/tools/gen_data_dictionary.py` 重新生成数据字典 → 更新本表 #1/#2/#4 期望值 → 若涉及表数，改 TASKS DOC-02 与 `deploy/mysql/init/00-init-databases.sh` 注释 |
| `docs/sql/seed.sql` | 重跑容器验证（注意 compose 镜像是构建期 COPY，**必须 `docker compose build mysql` 且重建数据卷**才生效，见 deploy/README 踩坑 #6/#9） |
| `docs/API.md` 错误码或接口 | 核对 PRD §5.4 错误码分段；本表 #8~#11 无变化也要过一遍 |
| `deploy/docker-compose.yml` 服务增删 | 本表 #7 期望值 + README 分档表 + TASKS DEP-01 **三处一起改**（历史上正是这三处各说一套） |
| 任一文档版本号 bump | 本文件 §一 表格 |
