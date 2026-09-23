# 开发交接（HANDOFF）

> **这份文档与工具无关。** 面向任何接手本仓库的开发者或 AI 会话。
> 上一个会话用的是 WorkBuddy，其工作日志留在 `.workbuddy/memory/`（内容已提炼到本文件，可仅作参考）。
>
> 最后更新：2026-09-23（第三轮：B5 裁决落纸 + 协作治具 + BE-03 鉴权模块）· 各轮提交见 `git log`，现状只看 §0

---

## 0. 30 秒读懂现状

| 项 | 值 |
|---|---|
| 阶段 | **T0 完成 → T1 进行中** |
| 已勾选任务 | DOC-01~03、DEP-01~04、BE-00~**BE-05** |
| **下一个任务** | **BE-06 内部服务接口**（T1 最后一项；完成后 T1 出口对账） |
| 测试基线 | 无 DB：`705 passed / 5 skipped`；有 DB：**`710 passed / 0 skipped`** |
| 门禁 | 文档一致性 35 项 PASS；`ruff`（含 `scripts/`）+ `pyright` 0 errors；3 个生成器 `--check` 全绿 |
| 镜像 | **三个服务镜像已端到端验证**：build 成功 + 容器起得来 + `/health` 返回 `code=0`（见 §4.1） |
| 仓库规模 | ≈110 个受跟踪文件（含本次新增）；服务包 3 个（backend / ai / mock 骨架齐备） |
| 远端 | `main` 与 `origin/main` 一致（`df60f82`），无未推送 |

**业务代码量：T1 首块已落地。** 地基（门禁 + 契约测试 + ORM 基建 + 38 表模型 + 三服务骨架）之外，
已有 BE-03 鉴权内核（JWT 双 Token / RS256 / JWKS / Refresh 轮换与吊销 / 依赖注入拦截）。
其余仍是分组占位，BE-04 是下一块。

### 0.1 上一轮修复的三个前置条件（B1/B2/B3）

> 背景：`docs/AI自动生成可行性评估报告.md` 判定本项目适合「门禁闭环下的逐任务生成」，
> 但列出 5 个前置条件。B1/B2/B3 已落地，B4（Ark Key）按用户决定暂缓，B5（范围裁决）待定。

| 编号 | 问题 | 落地方式 |
|---|---|---|
| **B1** | 环境边界（跨系统 venv / WSL 跨盘）不产生报错，只让结果不可信 | 新增 `scripts/dev_env_check.py`：解释器版本、跨系统 venv、WSL→NTFS 边界、依赖、S1-d 库隔离、四项 `--check`、中间件容器，一条命令给出结论 |
| **B2** | 三个服务镜像**从未端到端构建过**；`ai`/`mock` 连服务包都没有；CI 不 build | ① 补齐 `aids_ai` / `aids_mock` 骨架（装配 + 探针 + 统一异常处理）；② CI 新增 `images` job（矩阵并行：build + 起容器打 `/health`）；③ CI smoke 升级为**真拉起 compose 最小档**（38 表自检） |
| **B3** | 门禁是「文档↔代码」形式门禁，**不判断业务对错** | 新增 C10 `tests/contract/test_task_coverage.py`：`TASKS.md` 中 ✅ 的代码类任务，必须有 `@pytest.mark.task("<任务号>")` 的测试；含 3 条防退化自检（解析器健康、标记指向真实任务、豁免清单反向校验） |

**顺带修掉的**（都是「门禁全绿但产出是错的」同类）：

1. `app/core/handlers.py`：统一异常处理上移到共享层，三个服务共用一份，`aids-*/handlers.py` 只做转出——此前只有主业务有实现，另外两个服务要么复制三份、要么格式不一致。
2. `tests/invariants/test_guard_selfcheck.py`：bash 解析从「`shutil.which()` 结果」改为**候选列表 + 同构探针**。原因：从 `cmd.exe` 启动时 `C:\Windows\System32\bash.exe`（WSL 启动器）在 PATH 里胜出，它能执行 `bash -c true` 却读不到 `F:\...`，导致 12 个护栏断言全以 rc=127 失败——看起来像"护栏坏了"，实际是选错了 shell。
3. `scripts/` 此前只在 pre-commit（扫全仓）里被 ruff 检查，CI 只扫 `app tests aids-*` → 出现「L1 红、L2 绿」的错位。现已把 `scripts/` 纳入 CI 扫描面并修掉其 `SIM103`。

---

## 1. 上手三步

### 1.1 环境（本机已就绪）

```bash
# Python 解释器（Windows 布局的 venv，已装齐依赖）
.venv/Scripts/python.exe --version        # 3.11.9

# 纯标准库脚本（门禁）用系统 Python 即可
C:/Users/heart/AppData/Local/Programs/Python/Python313/python.exe

# 中间件：docker compose 已在跑（8 个容器 healthy）
docker ps --format '{{.Names}}\t{{.Status}}'
#   aids-mysql 13306 / aids-redis 6379 / aids-kafka 29092 / aids-minio 9000-9001
#   aids-nginx 18080+443 / aids-milvus 19530+9091 / aids-milvus-minio 9100 / aids-milvus-etcd
```

若 venv 丢失需重建：

```bash
C:/Users/heart/.workbuddy/binaries/python/versions/3.11.9/python.exe -m venv .venv
.venv/Scripts/python.exe -m pip install --no-cache-dir -e ".[dev]"
```

> `py` 启动器在本机不可用，用上面的绝对路径。

### 1.2 跑门禁（**改动后第一件事**）

```bash
# 0) 一键自检：解释器 / 跨系统 venv / WSL 跨盘 / 依赖 / 库隔离 / 四项门禁 / 容器
<python> scripts/dev_env_check.py

# 四项一致性门禁（对应 CI 的第一步）
<python> docs/tools/gen_data_dictionary.py --check      # 文档 ↔ DDL/枚举/常量，35 项
python3 scripts/gen_requirements.py --check             # aids-*/requirements.txt ← pyproject.toml
python3 scripts/gen_constraints.py --check              # constraints.txt 覆盖全部直接依赖
python3 scripts/gen_orm_models.py --check               # app/models/ ← docs/sql/schema.sql

# 静态检查
.venv/Scripts/ruff.exe format --check app tests aids-ai aids-backend aids-mock
.venv/Scripts/ruff.exe check        app tests aids-ai aids-backend aids-mock
.venv/Scripts/pyright
```

### 1.3 跑测试

```bash
# 无数据库（CI 的 invariants job 场景）：契约与不变量测试照样跑
.venv/Scripts/python.exe -m pytest tests -q --basetemp=/tmp/pt

# 带真实 MySQL（强烈建议——反射层与 CRUD 验收测试只在有 DB 时运行）
DATABASE_URL="mysql+asyncmy://root:aids_root_2026@127.0.0.1:13306/aids_shop_test?charset=utf8mb4" \
  .venv/Scripts/python.exe -m pytest tests -q --basetemp=/tmp/pt
```

**隔离测试库已建好**（`aids_shop_test`，38 表 / 419 字段）。重建方式与 CI 完全一致：

```bash
sed 's/`aids_shop`/`aids_shop_test`/g' docs/sql/schema.sql \
  | docker exec -i aids-mysql mysql --default-character-set=utf8mb4 -uroot -paids_root_2026
```

> 库名**必须含 `test`**：启动断言 S1-d（`app/core/config.py::assert_db_is_isolated`）
> 禁止 test/development 环境连 `aids_shop`。这是 Bysj 历史顽疾「测试连生产库」的代码化根治。
>
> `--basetemp` 不是可选项：pytest 结束时清理临时目录会触发宿主机的批量删除守卫，
> 导致进程被打断且**吞掉测试摘要**（你会看到 100% 但看不到 failed 列表）。

---

## 2. 不可动摇的项目约定

### 2.1 三条铁律（详见 `README.md`）

1. **契约先行** —— 文档里的约定必须变成会失败的测试，否则视为不存在。
2. **门禁可复现** —— 任何"我这跑通了"的结论必须能被别人用同一条命令复现。
3. **能用代码检查的，绝不靠文档要求。** —— 约定若无对应测试或 CI 检查，等于不存在。

### 2.2 单一来源 + 派生（改一处，跑生成器）

| 唯一来源 | 派生物 | 生成器 | 被谁强制 |
|---|---|---|---|
| `pyproject.toml` | `aids-*/requirements.txt` | `gen_requirements.py` | pre-commit + CI |
| 当前环境 | `constraints.txt` | `gen_constraints.py` | pre-commit + CI |
| `docs/sql/schema.sql` | `app/models/*.py` | `gen_orm_models.py` | pre-commit + CI |
| `docs/*.md` + `schema.sql` | 35 项一致性断言 | `gen_data_dictionary.py` | pre-commit + CI |

**派生文件一律不手改** —— 改了会被 `--check` 拦下。

> 两层防漂移的教训（BE-02 实测）：`app/models/` 的防漂移需要**两层**——
> `gen_orm_models.py --check` 只能抓「有人手改生成物」；
> 「生成器自己解析错了」要靠 `tests/contract/test_orm_matches_schema.py` 用**独立解析器**比对。
> 写生成器时曾因正则写成 `[^,]*` 让 10 个注释含逗号的列整体消失（419→409），而 `--check` 全程绿灯。

### 2.3 加一个新的门禁/测试时

- 契约测试 → `tests/contract/`（可用 `_doc_parser.py` 解析文档，**不要手抄文档内容**）
- 不变量测试 → `tests/invariants/`
- 接口测试 → `tests/api/`
- 纯结构类断言尽量**不依赖数据库**（读 metadata / 编译 SQL / Fake session），
  需要真实库的标 `@pytest.mark.requires_mysql` 并提供跳过条件。

---

## 3. 本机环境特有的坑（新会话必读，可省数小时）

### 3.1 git

| 现象 | 原因 / 对策 |
|---|---|
| `git push` 卡住或 `CONNECT tunnel failed, response 502` | 环境变量 `http_proxy=http://127.0.0.1:<随机端口>` 是**沙箱自用代理**，git 会走它而它连不上 GitHub。用户的 Clash 在 **7890**，直连也不通。→ 网络不通时先怀疑网络，别怀疑凭据；重试常能成功 |
| `git push \| tail` 后 `$?` 是 0 却实际失败 | 拿到的是 `tail` 的退出码。**判断真实结果请用 `git status -sb` 看有无 `[ahead N]`**，或 `git ls-remote origin main` |
| 需要交互认证 | 本环境命令行无 tty；凭据已由凭据管理器缓存。若失效，让用户在**自己的终端**跑一次 `git push` 即可恢复 |

### 3.2 Python / 工具链

| 现象 | 原因 / 对策 |
|---|---|
| `pip install` 报安全策略拦截（清 HTTP 缓存触发批量删除守卫） | 加 `--no-cache-dir` |
| `alembic` 报 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80` | **`aids-backend/alembic.ini` 必须纯 ASCII**：Alembic 用 `configparser.read(encoding="locale")`，中文 Windows 的 `locale.getencoding()` 是 cp936。`PYTHONUTF8=1` **救不了**（它只影响 `getpreferredencoding`）。中文说明写在 `aids-backend/alembic/README.md` |
| Python `subprocess` 调 `bash` 得到一段乱码或 `UnicodeDecodeError` | 裸名 `bash` 被 Windows `CreateProcess` 先解析到 `System32\bash.exe`（WSL 启动器），被安全策略拦成一段 UTF-16 的「拒绝访问」。→ 用 `shutil.which("bash")` 的**绝对路径**调用 |
| pytest 看不到失败摘要 | 见 §1.3 的 `--basetemp` 说明 |
| `app/models/` 的改动没被 ruff 格式化 | 刻意如此：它在 `[tool.ruff].exclude` 里。生成物若被格式化，`gen_orm_models.py --check`（文本比对）必然红 |
| PowerShell 工具不返回 stdout | 脚本先写文件再读；`.ps1` 只用 ASCII（5.1 会按 GBK 误读 UTF-8） |

### 3.3 数据库

- 测试库 `aids_shop_test` 已建；跑带 DB 的测试前**不要**忘记 `DATABASE_URL` 指它。
- `schema.sql` 里**不能**写 `${VAR}` 形式的库名 —— mysql 客户端不展开它，
  会静默把表建进一个名为 `${VAR}` 的垃圾库（实测踩过，CI 因此加了表数自检）。
- 部署用 `aids_shop`（compose 的 mysql 镜像初始化建）；**测试绝不允许连它**。

---

## 4. 本会话（2026-09-23）做了什么

从「地基审核」一路做到 BE-02 完成，共 8 个提交：

| 提交 | 内容 |
|---|---|
| `3efe50b` | 修复 `f309978` 的**半途回退**：包名/配置不一致（Dockerfile 指向不存在的目录、扫描门禁漏掉整个服务层、`_targets.py` 被残留 hook 误伤）。恢复到 `0d198a0` 的「服务包独立命名」方案 |
| `16a31f2` | 统一两份 `.env.example` 命名（以根文件为权威）；引入 `constraints.txt` 依赖快照；补 `.dockerignore`（此前没有，198MB 的 `.venv` 会进构建上下文） |
| `4343285` | 生产维度项（Nginx TLS / 中间件口令护栏）**定级为"影响不大"并登记**到 `项目设计报告.md §9.10`；`deploy/.env` 切到统一命名 |
| `ca5dfbd` / `07f9cc4` / `311b11f` | 入库审核报告与交接文档；`.workbuddy/memory/` 移出版本控制 |
| `a9bc526` | **BE-02 ORM 基建**：`app/orm/`（雪花ID / Mixin / 会话 / 软删除 / 分页 / 乐观锁）+ 38 表模型（生成物）+ Alembic 基建 + 4 个测试文件 |
| `df60f82` | 补打勾 BE-01（交付物早在 `f309978`，只是状态位没同步） |

**顺带修掉的一个潜伏 bug**：`app/core/config.py` 用 `db_url or os.getenv(...)` 读连接串，
导致**显式传入空串会被环境变量覆盖** —— "生产未配 DATABASE_URL 则拒绝启动"这条断言，
在环境里恰好设了 `DATABASE_URL` 时就失效。已改用 `is not None`。
（只有带 `DATABASE_URL` 跑全量测试才会暴露，CI 的 invariants job 不设它。）

---

## 4.1 本轮（2026-09-23 第二轮）：修复自动生成的前置条件

按 `docs/AI自动生成可行性评估报告.md` §4，先修 B1/B2/B3（B4 Ark Key 暂缓、B5 范围裁决待定）。

**验证（全部实跑，可复现）**：

| 项 | 结果 |
|---|---|
| 全量测试（无 DB） | `603 passed / 6 skipped` |
| 全量测试（带 `DATABASE_URL` 指向 `aids_shop_test`） | `608 passed / 1 skipped` |
| 文档一致性门禁 | `PASS 35 项` |
| 三个生成器 `--check` | 全绿（清单 / 快照 / ORM） |
| `ruff format --check` + `ruff check`（`app tests aids-* scripts`） | 全绿 |
| `pyright` | `0 errors, 0 warnings` |
| **三镜像端到端** | `docker build` ×3 成功；`docker run` ×3 后 `/health` 均返回 `{"code":0,...,"status":"up"}`；未知路径返回 `{"code":10004,...}` + HTTP 404 |

**新增文件**：`app/core/handlers.py`、`scripts/dev_env_check.py`、`tests/contract/test_task_coverage.py`、
`tests/contract/test_service_skeletons.py`、`aids-ai/aids_ai/**`（5 个）、`aids-mock/aids_mock/**`（5 个）。

**新增/加强的门禁**：C10（任务验收覆盖）、C11 扩展（`_PENDING_SERVICES` 清空，三个服务不再有"未开工豁免"）、
C12（三服务骨架 + 工具链登记）、CI `images` job、CI smoke 真拉起最小档、`scripts/` 纳入 ruff 扫描面。

---

## 4.2 本轮（2026-09-23 第三轮）：BE-03 鉴权模块

**交付**（严格按 `TASKS.md` 验收原文：「过期/伪造/刷新用例全部通过单测」）：

| 文件 | 职责 |
|---|---|
| `app/core/jwt.py` | Token 内核：RS256 签发/验签、双 Token 带 `typ`、`sid` 会话标识、RFC 7638 kid、JWKS 文档 |
| `app/core/refresh_store.py` | Refresh 一次性存储：`Protocol` + Redis（`GETDEL` 原子） + 进程内实现（测试/无 Redis 显式降级） |
| `app/core/config.py` | 新增 JWT/Redis 配置读取 + **S1-e 启动断言**（生产缺密钥文件直接拒绝启动） |
| `aids-backend/aids_backend/deps.py` | 依赖注入：`CurrentUser` / `get_current_user` / `require_roles`（越权防线唯一入口，BE-04 依赖它） |
| `aids-backend/aids_backend/api/auth.py` | `POST /auth/refresh`（轮换）、`POST /auth/logout`（吊销会话） |
| `aids-backend/aids_backend/api/jwks.py` | `GET /.well-known/jwks.json`（供 AI/Mock 服务验签） |
| `tests/api/test_auth_tokens.py` | **27 条验收用例**（标记 `task("BE-03")`）：过期 / 伪造 / `alg=none` / 篡改 / 跨类型 / 重放 / 吊销 / JWKS 只含公钥 / AI 服务独立验签 |
| `tests/invariants/test_startup_assertions.py` | 新增 S1-e 的 5 条用例 |

**几个刻意的设计取舍**（都写进了代码注释）：

1. **Access 与 Refresh 共享 `sid`**：`/auth/logout` 因此只需 Access Token。
   若要求回传 Refresh，「前端忘传」会让退出登录静默变成没退出。
2. **刷新即轮换**（旧 Refresh 立刻作废）：被盗凭证只剩一次机会，用户侧下次刷新失败即暴露异常。
3. **JWKS 不套统一响应体**（API.md §1.1 的唯一例外，已注明）：它是被 `PyJWKClient` 消费的标准发现文档。
4. **BE-03 不碰数据库**：鉴权内核必须能在没有 DB 的情况下被完整测试，读用户表属 BE-07。

**验证**：`scripts/task_runner.py verify` 8 项全绿（含 630/640 测试、pyright 0 error）。

---

## 4.3 本轮（2026-09-23 第四轮）：BE-04 数据权限（IDOR 防护）

**交付**（验收原文：「用 A 的 Token 访问 B 的订单/地址/优惠券/会话全部 403，批量递增 ID 扫描无一条越权数据泄露」）：

| 文件 | 职责 |
|---|---|
| `app/orm/repository.py` | **`OwnedRepository` 基类**：`scoped()` 是行级条件唯一注入点（`owner = user_id` + 有 `deleted` 列的表自动加 `alive()`）；`require()` 查不到即 403/10005，**不区分**"不存在"与"无权"；无 owner 列的模型**构造即 TypeError**。事实表（订单/支付，无 deleted 列）只注入 owner 条件 |
| `app/core/security.py` | 用户上下文从 `aids_backend/deps.py` **上移到共享层**：S4 扫描器豁免的 `app/core/security.py` 从"悬空路径"变成真实实现（AI 服务 T5 直接复用，不必重写一份） |
| `aids-backend/aids_backend/deps.py` | 薄转出（对象身份不变，`dependency_overrides` 行为不受影响） |
| `tests/invariants/test_idor_guard.py` | ① S4 扫描面从只扫 `app/` 扩到 `scan_targets()`（**此前整个服务层不在 IDOR 扫描范围**，与 HANDOFF §9 同型缺口）；② 删除 skip，端到端落地：探针路由 + 按编译参数模拟行级过滤的会话替身，50 连发递增 ID 扫描全 403/10005、响应体无 B 的任何字段、"不存在"与"无权"逐字节一致 |
| `tests/invariants/test_owned_repository.py` | 机制级：编译 SQL 断言（主数据表 = owner + alive；事实表 = 仅 owner；公共表构造即炸）；`get()` 必须**单条查询**同时带 id 与 user_id（防"先查后比"的多查询窗口） |

**两个要记住的点**：

1. **越权防线现在的完整链路**：`get_current_user`（JWT → userId）→ `OwnedRepository`（SQL 注入 `WHERE user_id=?`）→ `require()`（查不到 = 10005/403）。业务代码**不允许**出现第三种取 userId 的方式，也**不允许**绕过 `scoped()` 手写查询（S4 扫描器扫全部服务包，code review 把关）。
2. **新发现并登记的集成缺口**（非 BE-04 范围，见 §6）：nginx `location /api/` 无 rewrite，而后端路由挂在 `/user`、`/auth` 等无 `/api` 前缀下 → 经网关访问会 404。

**验证**：`task_runner verify` 8/8 PASS；pytest `647/5`（无 DB）、`652/0`（带库，原 IDOR skip 已消除）；pyright 0 errors。

---

## 4.4 本轮（2026-09-23 第五轮）：BE-05 通用组件

六个子组件全部落在共享层 `app/core/`，**无新表**（`sys_local_message` / `sys_dead_letter` / `sys_config` 均已在 DDL）：

| 模块 | 职责与关键决定 |
|---|---|
| `app/core/redis.py` | 客户端单例 + `RedisLock`：`SET NX PX` 加锁带随机 token，释放/续期用 **Lua 做原子校验**（防"锁过期换主后误删他人锁"）；无 TTL 参数直接拒绝（Redis 锁靠过期兜底） |
| `app/core/trace.py` | traceId 全链路：**纯 ASGI 中间件**（不包装响应体——AI 服务的 SSE 依赖此点）+ `ContextVar`（投递循环等无 request 上下文处可取）；外部 traceId 只接受 8~64 位十六进制/连字符，不合规格重新生成（注入面） |
| `app/core/sms.py` | `SmsSender` Protocol + `LoggingSmsSender`（日志**脱敏** `138****8000`）；场景白名单外的调用直接拒；Mock 通道是 MOCK-02 的接入点，BE-07 调用方零改动 |
| `app/core/kafka_producer.py` | `acks="all"` + `enable_idempotence`（生产端幂等，消费端幂等另算）；**key = 业务单号**（同单同分区保序）；headers 自动带 traceId；未配置 `KAFKA_BOOTSTRAP_SERVERS` → 抛错，由 outbox 捕获转为"保持待投递" |
| `app/core/outbox.py` | **事务性发件箱**：`enqueue()` 与业务行同事务登记（uk 三键幂等，先查后插避免污染业务事务）；`dispatch_once()` 状态机——成功置 1 / 失败指数退避（10,20,40…封顶 600s）/ 超 8 次转 `sys_dead_letter`（可人工重放，不是丢弃）；`OutboxRelay` 循环可测试驱动 |
| `app/core/sys_config.py` | `sys_config` 读取：`value_type` 解析（失败**响**不串型）、单条损坏跳过整缓存不倒、未加载返回 default 并告警（宁可保守不阻塞请求）；Redis pub/sub 失效广播 → **全量重载**（配置行少，避免半新半旧） |

**测试**：新增 `tests/core/` 目录 + `unit` 标记（CI 的 invariants job 增加一步执行）；Redis 依赖用例在本机真实 Redis 上验证、不可达自动 skip；共 **58 条**新测试。

**两个边界（刻意留白，不是遗漏）**：
1. 投递循环与配置失效订阅的 **lifespan 接线**留到 T3 —— 交易链路起才有消息可投；
2. `app/core/refresh_store.py`（BE-03）暂保留自持客户端，并入通用单例列为小任务。

**验证**：`task_runner verify` 8/8 PASS；pytest `705/5`（无 DB）、`710/0`（带库）；pyright 0 errors。

---

## 5. 下一步：BE-06 内部服务接口

**验收原文**（`docs/TASKS.md`）：

> 供 AI 服务调用的订单/商品/用户查询接口；服务间静态 Token + 请求签名
> （timestamp+nonce 防重放）+ 内网网段限制；**返回数据须脱敏**（手机号、详细地址）。
> 依赖 BE-03, BE-04

开工前建议先读：

| 事实 | 说明 |
|---|---|
| `docs/API.md` §四 | 内部服务接口的路径、鉴权与脱敏要求**已定稿**（主业务 ↔ AI 服务） |
| `docs/PRD.md` §5.3 | 服务间通信约定：静态 Token + 签名算法（timestamp+nonce 防重放）+ 内网网段限制 |
| `app/core/security.py` | 那是**用户 JWT** 的依赖注入；内部接口是另一套（服务间静态 Token + 签名），**不要混用** |
| `app/core/jwt.py` / JWKS | AI 服务验用户 JWT 走 JWKS 端点，与服务间鉴权正交 |
| `app/core/trace.py` | 内部调用的消息头同样要透传 traceId |
| `.env.example` | 已有 `INTERNAL_SERVICE_TOKEN` / `INTERNAL_SIGN_SECRET` —— 配置名有权威来源，直接复用 |

**T1 出口对账**：BE-06 完成 = T1 收官。按 `TASKS.md §工作量对账「决策记录」` 回来对一次账
（T1 实测人天 vs 估算 21.5 人天），裁决是否触发分级裁剪。

**收尾动作（每个任务都一样，C10 门禁会拦）**：把 `TASKS.md` 里该任务勾成 ✅ 之前，
先给覆盖其验收标准的测试加 `pytestmark = [..., pytest.mark.task("<任务号>")]`。
没有 `task` 标记时 `tests/contract/test_task_coverage.py` 会红——这条门禁存在的意义
就是让「任务已完成」必须对应一个真实存在的测试。推荐顺序：**先写测试并打标记，再勾选任务**。

---

## 6. 未决 / 已知遗留

| 项 | 状态 |
|---|---|
| **B4 · Ark API Key 与真实模型调用** | 按用户决定**暂缓**（本轮不动）。影响 AI-02 起的任务：本地用桩推进实现，`AI-01` 脚手架不依赖它 |
| **pre-commit 的 ruff 版本与 CI 偏斜** | pre-commit 钉 `v0.8.4`，CI 经 constraints 装的是 0.16.x —— 同一份代码两边判定不同（实测 v0.8.4 报 `UP038`、0.16.x 不报；BE-04 提交时被 L1 拦下，见 `test_idor_guard.py:149` 的修改）。**处置（单独小任务，勿与功能提交混做）**：先在 `constraints.txt` 给 ruff 上锁，再把 `.pre-commit-config.yaml` 的 ruff `rev` 对齐到该版本，最后 `pre-commit run --all-files` 清一次全仓噪音 |
| **双 git 环境的行尾翻转**（BE-05 提交期间确认） | Windows git `core.autocrlf=true`，WSL git 未设 —— 同一仓库两套提交环境对行尾各说各话：cmd git 的 pre-commit（patch 恢复）会把文件写成 CRLF，混合行尾随即被 `mixed-line-ending` 钩子拦下，提交反复中止。**处置建议**：加 `.gitattributes`（`* text=auto eol=lf`）统一口径 + 一次性 renormalize；在此之前，若提交被 `mixed-line-ending` 反复拦，对工作区跑一轮 `sed -i 's/\r$//'`（只处理受跟踪文本文件）再 add |
| **B5 · 范围与工期裁决** | **已裁决（2026-09-23）**：采纳「1 人 + AI 作为第二执行者」，**范围暂不裁剪**，改由 T1 出口的实测压缩率触发分级裁剪。判据、裁剪顺序与硬底线见 `TASKS.md §工作量对账` 的「决策记录」。**T1 出口时要回来对一次账**（实测人天 vs 估算 21.5 人天） |
| **Nginx `/api/` 前缀与后端路由不一致** | **已修复（2026-09-23，用户裁决取"nginx 剥离"方案）**：`location /api/` 加 `rewrite ^/api/(.*)$ /$1 break;`，后端路由保持无 `/api` 前缀；`/api/ai/**` 走更长前缀的独立 location，不受影响（AI 侧自带 `/api/ai` 前缀）。已实测：重建 `aids/nginx` 镜像并替换容器后，`GET /api/health` 经网关 → 后端日志为 `/health`（200），`/healthz` 200。教训已写进 `deploy/nginx/conf.d/default.conf` 的 location 注释；`API.md §1.1` 已注明剥离行为 |
| **S1-c 与 S1-e 的口径** | `assert_required_keys_present`（Ark/字段加密密钥）与 `assert_jwt_keys_configured`（JWT 密钥文件）分列两条断言；若后续把密钥统一收敛到 KeyProvider，应合并并同步本表 |
| **`.env.example` 与 compose 的中间件口令护栏** | 已登记为「未落地的约束」（`项目设计报告.md §9.10`），触发条件：首次部署到可被外网访问的环境前 |
| **Nginx TLS** | 同上（443 目前是空映射） |
| **Alembic 纳管既有 DDL** | `DEP-04` 明确留待 T1；基建已就位，`aids-backend/alembic/versions/README.md` 写了正确的纳管步骤 |
| **S5 迁移可回退检查** | `script.py.mako` 已把未实现的 downgrade 生成为 `raise`；完整 CI 检查按计划在 T6 |
| **前端 / AI 服务** | `aids-ai`、`aids-mock` 目前只有 `requirements.txt`，未开工 |
| `docs/项目设计报告.md §1` 的状态表 | 是**第二轮核查的历史快照**（里面的测试数、文件数已过时），按项目惯例保留不改 |

---

## 7. 一句话给接手的你

这个仓库的价值**全押在"门禁可复现"上**：它的文档不是说明，是**会失败的断言**。
所以改动后的正确姿势永远是——先跑 §1.2 的四项 `--check` 与 §1.3 的测试，
红了就修，绿了再提交。别绕过门禁（`--no-verify` 之类），
