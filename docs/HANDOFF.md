# 开发交接（HANDOFF）

> **这份文档与工具无关。** 面向任何接手本仓库的开发者或 AI 会话。
> 上一个会话用的是 WorkBuddy，其工作日志留在 `.workbuddy/memory/`（内容已提炼到本文件，可仅作参考）。
>
> 最后更新：2026-09-24（**T1 已收官：14/14 ✅，`task_runner verify` 10/10 PASS；全文已移除工期/工作量内容**）· 各轮提交见 `git log`，现状只看 §0

---

## 0. 30 秒读懂现状

| 项 | 值 |
|---|---|
| 阶段 | **T1 完成（14/14）→ 可进 T2** |
| 已勾选任务 | DOC-01~03、DEP-01~04、**BE-00~BE-06**、**MOCK-01~03**、**AI-01**、**FE-01~02**、**BE-37**（行尾口径统一） |
| **下一个任务** | **T2 起**：从 `BE-07`（注册 / 登录）或 `AI-02`（Ark 客户端）挑一条；**开工前先跑 `scripts/task_runner.py card <编号>`**。范围与协作口径见 `docs/TASKS.md §范围与协作口径` |
| 测试基线 | **全绿**：全量 `830 passed / 11 skipped`（2026-09-24 实测）。11 个 skip = 5 个需 MySQL + 6 个需 Redis —— **本机 AIDS 中间件容器未启动**（`docker ps` 只有一个无关的 `campus-mysql`），这是**预期态、不是失败**；要跑带库/带 Redis 的用例先执行 `cd deploy && docker compose --profile minimal up -d`（或 `--profile search` / `all`） |
| 门禁 | `scripts/task_runner.py verify` **10/10 PASS**（四项 `--check` + `ruff format/check` + C2/C4 扫描器 + `pyright` 0 errors + 全量测试）；文档一致性 **34 项 PASS** |
| 镜像 | 三个**服务**镜像已端到端验证：build 成功 + 容器起得来 + `/health` 返回 `code=0`（见 §4.1）。**前端镜像未端到端构建**：两个前端工程 `npm run build` 已通过，但 `deploy/app/frontend.Dockerfile` 的 `docker build` 在本机没跑过（见 §4.7 遗留） |
| 仓库规模 | **205** 个受跟踪文件（T1 全部改动已提交，工作区干净）；服务包 3 个（backend / ai / mock）+ 前端工程 2 个（aids-mall / aids-admin） |
| 远端 | `main` 比 `origin/main` **ahead 9**（未推送，推送见 §3.0） |

**业务代码量：T1 全线收官（14/14）。** 鉴权（BE-03）/ 数据权限（BE-04）/ 通用组件（BE-05）/
内部服务接口（BE-06）均已落地；Mock 三服务（MOCK-01~03）实现与测试全绿；AI 服务脚手架（AI-01，
结构化日志 + 配置隔离显式化）与两个独立前端工程 + axios 请求封装（FE-01/02）已完成。完整收官记录见 §4.7。

> **关于两个报告文档**：`docs/地基测评报告.md`、`docs/AI自动生成可行性评估报告.md` **已被项目所有者有意删除**
> （经确认：没用，不再恢复）。本会话已清理正文对它们的路径引用（§0.1 与 §6 改为叙述式），
> 并把删除一并纳入 T1 收官提交。

### 0.1 上一轮修复的三个前置条件（B1/B2/B3）

> 背景：上一轮的可行性评估报告（**该文档已被项目所有者有意删除**，见 §0）判定本项目适合
> 「门禁闭环下的逐任务生成」，但列出 5 个前置条件。B1/B2/B3 已落地，B4（Ark Key）按用户决定暂缓，B5（范围裁决）**已裁决**：1 人 + AI 协作、范围暂不裁剪（见 `TASKS.md §范围与协作口径`）。

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

# 中间件：**当前未启动**（2026-09-24 实测 docker ps 只有一个无关容器）→ 需要时先起：
cd deploy && docker compose --profile minimal up -d      # MySQL + Redis + Nginx
# 全量档（含 Milvus，AI/RAG 用）：docker compose --profile search up -d
# 端口：aids-mysql 13306 / aids-redis 6379 / aids-kafka 29092 / aids-minio 9000-9001
#       aids-nginx 18080+443 / aids-milvus 19530+9091 / aids-milvus-minio 9100 / aids-milvus-etcd
docker ps --format "{{.Names}}: {{.Status}}"
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
<python> docs/tools/gen_data_dictionary.py --check      # 文档 ↔ DDL/枚举/常量，34 项
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
| `docs/*.md` + `schema.sql` | 34 项一致性断言 | `gen_data_dictionary.py` | pre-commit + CI |

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

> **执行环境（2026-09-23 起）**：IDE 默认终端已切为 **Command Prompt（cmd）**，AI 会话的命令
> 直接在 Windows 侧原生执行（`git config core.autocrlf=true`）。此前 BE-05 提交时的行尾翻转
> 反复拦截（§6）即源于 WSL git（未设 autocrlf）与 Windows git 混用 —— 新会话**禁止**再经 WSL
> （`/mnt/f`、`wsl.exe`）执行任何仓库操作；脚本内确需调 bash 时用 `shutil.which("bash")` 的
> 绝对路径（见 `tests/invariants/test_guard_selfcheck.py` 的处理）。

### 3.0 cmd 会话的四个坑（2026-09-24 实测，会浪费大量时间）

| 坑 | 现象 | 对策 |
|---|---|---|
| **Unix 工具不在 PATH** | `tail` / `grep` / `head` / `sed` 报 `invalid trailing option` 或直接失败；`git commit ... \| tail -2` 会把提交**打断在半途**（实测：钩子跑完但提交没落地） | 输出过滤用 `findstr`（原生）或 `.venv\Scripts\python.exe -c "..."` 里跑 `subprocess` 再筛；**提交时不要接管道** |
| **多行 `python -c` 被截断** | `python -c "` 里带换行时，cmd 只执行第一行，**无任何报错、输出为空**（极难察觉） | 单行 `-c`（分号连接）或写临时脚本文件到 `C:\tmp\` 再跑 |
| **推送必须带代理** | 裸 `git push` → `SSL_ERROR_SYSCALL`（直连被掐）；WSL 侧 `127.0.0.1:7890` 也到不了 Clash | 用：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main`（一次成功；亦可 `git config` 写进本仓库配置） |
| **`findstr` 语法** | `findstr /C:"a" /C:"b" \| head -5` 这类混用会报 `Cannot open \|` | 一条命令只用一种工具；需要复杂过滤就交给 python |


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

### 3.4 前端工具链（Node / Vite，2026-09-24 实测）

本机没有独立安装 Node：`node` / `npm` 来自 WorkBuddy 的**托管运行时**
（`C:\Users\heart\.workbuddy\binaries\node\versions\22.22.2-3\`，Node 22.22.2 / npm 10.9.7）。

| 现象 | 原因 / 对策 |
|---|---|
| `npm install` 最后一步报 `spawnSync … node.exe EBUSY`（栈指向 `esbuild/install.js: validateBinaryVersion`） | 沙箱拦住了 esbuild 安装脚本对 `node.exe` 的 spawn。**安装其实已经成功** —— 包已解压，二进制就在 `node_modules/@esbuild/win32-x64/esbuild.exe`，失败的只是那条版本自检。→ 用 `npm install --ignore-scripts`（平台二进制来自 optionalDependency，不跑安装脚本也能用）；**CI（ubuntu）不受影响** |
| `npm ci` 报 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]` | `npm ci` 会整目录删除 `node_modules/.bin`（>50 项），触发宿主机的批量删除守卫。→ 改用 `npm install --ignore-scripts`；**CI 上 `npm ci` 正常**（CI 的 frontend job 就用它） |
| 同一工程**第二次** `npm run build` 报 `emptyDir` 失败 | vite 构建前要清空 `dist/`，整目录删除同样撞守卫。→ 先 `rm -rf dist` 再 build；**CI 上正常** |
| 工程根出现 `vite.config.ts.timestamp-*.mjs` | vite / vitest 加载 TS 配置时的临时文件（正常退出自删，被强杀时残留）。→ 已加入两个工程的 `.gitignore` / `.prettierignore` 与根 `.gitignore` / `.dockerignore`（`*.timestamp-*.mjs`） |
| `task_runner verify` 出现**随机 34 个** `tmp_path` 用例 ERROR，重跑又全绿 | 固定 `--basetemp` 在 pytest 首次使用时被 `rm_rf`，删除**部分失败**后残留 `test_xxx0` / `test_xxxcurrent`，随后 mktemp 报 `FileExistsError`。→ **已修**：`task_runner.py` 改为每轮唯一 basetemp（`_pytest_basetemp()`）—— 一个"跑第二次才绿"的门禁等于没有门禁 |
| `ruff … aids-*` 扫到 `aids-mall` / `aids-admin`（无 Python 文件） | ruff 只**告警**（`No Python files found`）并返回 0，不影响 CI；但要注意 `aids-*` 这个 glob 现在同时匹配到**前端工程**，新增基于该 glob 的工具时需显式挑服务包（用 `aids-*/aids_*`） |

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

## 4.5 本轮（2026-09-23 第六轮）：BE-06 内部服务接口

| 文件 | 职责 |
|---|---|
| `app/core/service_auth.py` | 服务间鉴权内核：四件套请求头（`X-Internal-Token/X-Timestamp/X-Nonce/X-Sign`）、**HMAC-SHA256 签名**（规范化串 `timestamp\n + nonce`，换行分隔防拼接歧义——此前 API.md 未定义，本轮补入文档）、±300s 时间窗、**nonce 原子登记**（Redis `SET NX EX` / 进程内降级，防并发重放）、内网网段限制（私有网段白名单；解析不出 IP 视为不可信，测试可单独覆盖该依赖） |
| `aids-backend/aids_backend/api/internal.py` | 5 个查询接口：`/internal/order/list`（含 items 聚合）、`/internal/order/{orderNo}`、`/internal/order/{orderNo}/trace`（未发货返回**空轨迹**——对 AI 是正常答案）、`/internal/refund/{refundNo}`、`/internal/product/{spuId}`。**脱敏**：`receiver_phone` 密文永不返回、`receiver_addr` 只回省市区 + 掩码 |
| `tests/api/test_internal_service.py` | 16 条：四件套失败路径全 403/10003、**nonce 重放必拒**、时间窗、签名绑定 secret/timestamp/nonce、订单列表与详情契约、脱敏断言、内网限制单测 |

**信任边界（本任务最重要的文档化）**：`userId` 在内部接口里是**业务入参**（AI 服务从其用户的
JWT 解出后传入，PRD §9.3「禁止从对话内容中提取」），不是凭据——信任边界是服务间鉴权。
用户 JWT（`app/core/security.py`）与服务间鉴权（`app/core/service_auth.py`）**两套正交，不得混用**。

**验证**：`task_runner verify` 8/8 PASS；pytest `722/5`（无 DB）、`727/0`（带库）；pyright 0 errors。

---

## 4.6 ✅ 已完成（2026-09-24）：T1 剩余项 —— Mock 三服务

> **本节保留当时的诊断过程**（6 个失败用例的逐条根因是有效知识，不要删）。**结论已达成**：
> 6 个用例全部修复、Mock 测试 **26/26 绿**；`MOCK-01~03` 已勾 ✅。完整收官记录见 **§4.7**。

**已落地文件**：

| 文件 | 职责 |
|---|---|
| `aids-mock/aids_mock/models.py` | Mock 库 7 张表 ORM（独立 `MockBase`，对应 `docs/sql/mock_schema.sql`；`TINYINT/MEDIUMTEXT` 取自 `sqlalchemy.dialects.mysql`，数值默认走 `server_default=text(...)`） |
| `aids-mock/aids_mock/db.py` | Mock 库会话工厂（`MOCK_DATABASE_URL`，惰性建 engine，语义同 `app/orm/session.py`） |
| `aids-mock/aids_mock/constants.py` | 全部状态取值命名常量 + 故障注入 env 键名（C2 禁 status 配魔法数字） |
| `aids-mock/aids_mock/rsa.py` | RSA2 签名/验签：`SHA256(body) + "\n" + ts + "\n" + nonce`；开发引导：密钥缺失现场生成落盘 |
| `aids-mock/aids_mock/payment_channel.py` | `PaymentChannel` Protocol + `MockPaymentChannel`（下单/查询/关单/退款/对账 CSV）+ 回调登记与投递状态机（成功=商户回 `success`；失败退避；超限放弃）+ 故障注入（延迟/重复/丢失/渠道失败率） |
| `aids-mock/aids_mock/routes_payment.py` | 渠道 HTTP 面：`/payment/uniorder`·`query`·`refund`、`/recon/daily`、`/callbacks/dispatch`、**沙箱收银台** `/cashier/{no}`（确认/取消/超时关单） |
| `aids-mock/aids_mock/routes_sms.py` | `/sms/send`（60s 频控 → **10006/429**、开发环境验证码回显、落库）、`/sms/record/{mobile}` |
| `aids-mock/aids_mock/routes_logistics.py` | `/logistics/waybill`、`/logistics/trace/{no}`（**按经过时间补齐轨迹节点**，`uk(delivery_no,trace_time)` 幂等） |
| `aids-mock/aids_mock/api/__init__.py` | 聚合路由 + `MODULE_ROUTERS` |

测试：`tests/api/test_mock_{payment,sms,logistics}.py`（均带 `task("MOCK-0x")` 标记）。替身用**按实体分发**的 `_SmartSession`（select 谁返回谁），比"结果队列"更能暴露查错表/漏条件。

**6 个失败用例与根因（照单修即可）**：

| 用例 | 现象 | 根因与修法 |
|---|---|---|
| `test_mock_payment.py::test_confirm_pays_and_schedules_callback` | 500 | 用例**没先播种订单**（替身会话为空）就 POST `/cashier/ORD100/confirm` → 修：先 `client.post("/payment/uniorder", json=_uniorder_body())` |
| `test_mock_payment.py::test_cancel_closes_without_callback` | `IndexError: payment_rows[0]` | 同上：未建单 |
| `test_mock_payment.py::test_cashier_page_renders_for_pending_order` | 500（`_Result` 无 `one_or_none`） | 路由 `_load_order` 用 `.one_or_none()`，payment 测试的 `_Result` 只实现了 `scalar_one_or_none` → 补 `one_or_none()`（另两个测试文件的替身已补） |
| `test_mock_payment.py::test_dispatch_failure_schedules_backoff` | `assert 2 == 0` | **测试期望写错**：失败时实现置 `CALLBACK_FAILED(2)` + 退避（正确），用例却断言 `CALLBACK_WAITING` → 改断言 `CALLBACK_FAILED` + `retry_count==1` + `next_retry_time > now` |
| `test_mock_logistics.py::test_create_waybill_rejects_duplicate_no` | `_waybill() got an unexpected keyword 'delivery_no'` | 测试辅助未支持覆盖参数 → 签名改 `_waybill(created_minutes_ago=0, **overrides)` |
| `test_mock_logistics.py::test_trace_partial_advancement` | `assert 2 == 3` | **测试期望算错**：创建于 5 分钟前 → 已发生 0/2 分钟两个节点（10 分钟节点在将来）→ 断言改 `== 2` |

> 另有一类"看着像 bug、实为替身局限"：`dispatch` 的 `next_retry_time` 过滤是 SQL WHERE，实体分发替身不模拟 WHERE（相关用例已改为断言投递报文与 `X-Channel-Sign` 头）。

**当时仍需补的配套（现状）**：

1. ~~新增 env 键尚未写入两个 `.env.example`~~ → **已补**：4 个 `MOCK_*` 键已进两份 `.env.example`
   （`MOCK_DATABASE_URL`、`MOCK_CHANNEL_RSA_PRIVATE_KEY_PATH`、`MOCK_CHANNEL_RSA_PUBLIC_KEY_PATH`、
   `MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH`），`check_env_hygiene` 过。
2. **商户侧签名未实现**（**未变，仍待 T3**）：`_require_merchant_signed` 目前**显式降级**
   （未配置商户公钥即跳过并告警，标 v0-draft）→ T3 做 BE-23 时补，并同步关掉降级。
3. Mock 渠道报文是 **v0-draft**（**未变**；TASKS 允许：先按「RSA2 + 统一响应体」出最小可用版，
   T3 只许向后兼容加字段）；若要进 `docs/API.md`，新增小节并标 v0-draft。

**当时未开工的工作**（同会话内已完成，见 §4.7）：`AI-01`（差量：结构化日志 + 配置隔离显式化）、
`FE-01`（Vite+Vue3 双工程脚手架）、`FE-02`（axios 拦截器 + JWT 无感刷新并发队列，BE-03 已解锁）。

---

## 4.7 ✅ 本轮（2026-09-24）：T1 收官 —— Mock 测试修复 / AI-01 / FE-01 / FE-02

**验证（全部实跑，可复现）**：

| 项 | 结果 |
|---|---|
| `scripts/task_runner.py verify` | **10/10 PASS** |
| 全量测试（无 DB） | `830 passed / 11 skipped` |
| 文档一致性门禁 | `PASS 34 项` |
| 三个生成器 `--check` | 全绿（清单 / 快照 / ORM） |
| `ruff format --check` + `ruff check`（`app tests aids-* scripts`） | 全绿 |
| `pyright`（basic） | `0 errors, 0 warnings` |
| `aids-mall` / `aids-admin` 各跑 `build` / `test` / `lint` / `format:check` | 全绿（vitest 各 5/5） |

### ① Mock 三服务（§4.6 的 6 个失败用例）

按 §4.6 的根因表逐条修复。其中 5 个是**用例自身**的问题（未播种订单 ×2、替身缺 `one_or_none()`、
两处期望值算错、辅助函数签名），**1 个是真实缺陷**：

> `routes_payment.py::daily_recon` 写成 `_Ch(session, self_private_pem()).build_daily_recon(...)`
> —— **把 `session` 当成了渠道私钥参数**（构造参数错位），对账单接口此前必然 500。
> 已改为 `_channel().build_daily_recon(session, bill_date=..., pay_type=...)`，并把
> `ValueError`（日期非法）转成 `BusinessError.not_found`。该缺陷由新增用例
> `test_daily_recon_returns_csv` / `test_daily_recon_rejects_bad_date` 固化。
>
> 顺带发现 `build_daily_recon` 把金额返回成字符串 `"100.00"` → 改为 `float(...)`，并补
> `FaultConfig(TypedDict)`（消除 pyright 对 `range(int|float)` 的报错）。

**测试数**：`tests/api/test_mock_payment.py` 14 + `test_mock_sms.py` 5 + `test_mock_logistics.py` 7 = **26/26 绿**。

另补齐 **`tests/contract/test_mock_schema.py`（28 条）**：`aids_mock/models.py` 的 docstring 早已引用它，
但文件不存在（**悬空引用**）。现覆盖 7 表、逐列比对、雪花主键、唯一索引、`MockBase` 与主库 `Base`
隔离、可空性抽查。

### ② env 键

4 个 `MOCK_*` 键（`MOCK_DATABASE_URL`、`MOCK_CHANNEL_RSA_PRIVATE_KEY_PATH`、
`MOCK_CHANNEL_RSA_PUBLIC_KEY_PATH`、`MOCK_MERCHANT_RSA_PUBLIC_KEY_PATH`）+ `LOG_LEVEL`
写入**根与 `deploy/` 两份 `.env.example`**，`check_env_hygiene` 过。

### ③ AI-01（差量）：结构化日志 + 配置隔离显式化

| 文件 | 职责 |
|---|---|
| `app/core/logging.py`（新） | `JsonFormatter`（字段 `ts/level/logger/service/msg/traceId/exc`；**无 traceId 时不输出该键** —— 与 `app/core/trace.py` 的 ContextVar 打通）、`configure_structured_logging()`（幂等，靠 handler marker 去重）、`log_fields()` |
| `app/core/config.py` | 拆出 `REQUIRED_KEYS_AI` 与 `run_ai_startup_assertions()`：**AI 服务只断言自己需要的那部分**（Ark Key + 字段加密密钥），不再断言 DB / JWT 密钥 —— 否则 AI 服务会因"没有 JWT 私钥"拒绝启动 |
| `aids-ai/aids_ai/main.py` | `bootstrap()`：先装结构化日志 → 再跑 AI 作用域断言 → 最后装配 |
| `tests/contract/test_service_config_isolation.py`（新，**14 条**） | AST 扫描：AI 包**不得**直接 `os.getenv`、不得读别家服务的 env 键、只允许 import `app.core.*`；`run_ai_startup_assertions` 的作用域；Ark 取值默认值 |
| `tests/core/test_logging.py`（新，**12 条**） | JSON 形状、traceId 有无两种形态、幂等装配、`log_fields` 合并 |

### ④ FE-01 / FE-02：两个独立前端工程

| 工程 | 端口 | 页面骨架 |
|---|---|---|
| `aids-mall`（商城 C 端） | 5173 | `HomeView` / 登录 / 404 |
| `aids-admin`（管理后台 B 端） | 5174 | `DashboardView` / 登录 / 404 |

各 **24 个文件**（含 `package-lock.json`）：Vite 5 + Vue 3 + TS 5（`strict`）+ Pinia + Router；
ESLint 9 flat config（**排版交回 Prettier**，避免两个工具互相打架）+ Prettier；vitest（`environment: node`）。

FE-02 的关键不变量（`src/api/`）：

| 文件 | 职责 |
|---|---|
| `http.ts` | 请求拦截器注入 `Bearer`；响应拦截器处理 401 → **单飞刷新** → 原样重放（`_aidsRetried` 防无限重试）；**业务失败（HTTP 200 + `code !== 0`）也转成 `ApiError`**；刷新走不带拦截器的 `rawClient`（否则刷新自身 401 会递归）；会话不可恢复时清 Token + 跳 `/login` |
| `refreshQueue.ts` | `createRefreshCoordinator()`：**单飞 + 并发排队**（N 个 401 只刷新一次、共享同一 Promise；`finally` 释放，一次失败不锁死队列；`onFailure` 是清 Token / 跳登录的挂点）。为什么必须：BE-03 是**刷新即轮换**，并发刷新会把彼此的 Refresh Token 互相作废 —— 症状是用户"被误登出" |
| `refreshQueue.spec.ts` | **5 条** vitest：并发只刷一次且共享结果 / 结束后可再刷 / 失败全拒且不锁死 / `onFailure` 只调一次 / 结果一致性 |
| `tokenStore.ts` | Token 读写的**唯一入口**（键名集中；无 `window` 时降级内存态） |
| `notify.ts` | 统一错误提示（无 DOM 时降级 `console` —— 提示失败绝不反过来打断请求链） |

### ⑤ BE-37：行尾口径统一（根治"提交反复被拦"）

`core.autocrlf=true` 让文件以 CRLF 检出，而编辑器 / AI 写入 LF → 同一文件同时含 CRLF 与 LF。
链式后果：`mixed-line-ending` 每次"修正" → diff 变成「所有行都改了」→ `git add` 时 autocrlf 又把
CRLF 压成 LF，与库内 blob 不一致（"改一行" = "整文件重写"）→ **提交被反复拦下（实测连拦 3 轮，
且 pre-commit 会报 Passed 却已改文件）**。

修法：把口径写进仓库、与开发者本机配置解耦 —— 新增 `.gitattributes`（`* text=auto eol=lf` +
`*.sh` 显式 `eol=lf` + 二进制声明），并做一次性 `git add --renormalize .`
（实测对库内 blob 是 no-op：库内本就是 LF；真正的作用是让 git 自此刻起对工作区按同一口径归一）。

### 新增门禁

1. **C13** `tests/contract/test_frontend_scaffold.py`（**34 条**，`task("FE-01")` / `task("FE-02")`）：
   双工程**独立**（包名 / 端口 / 视图三处互证）、依赖与配置齐全、TS `strict`、ESLint 关掉排版类规则、
   `frontend.Dockerfile` 的 `${APP_DIR}` 指向真实目录、构建产物已被忽略（`git check-ignore` 实测）、
   请求封装与单飞刷新不变量，**并在有 `node_modules` 时真跑 vitest**（无则 skip —— CI 的 contract job
   不装 Node 依赖，前端单测由下面的 frontend job 承担）。
2. **CI `frontend` job**（矩阵 `aids-mall` / `aids-admin`：`npm ci` → lint → format:check → test → build），
   已并入 `gate` 汇总。理由与 `images` job 同：纯前端交付物 Python 侧三道门禁照不到，**并发语义更必须真跑**。
3. **`tests/contract/test_line_endings.py`**（5 条，`task("BE-37")`）：`.gitattributes` 存在性、
   对 `*` 生效的 `eol=lf` 规则（根治的唯一承重点）、`*.sh` 钉 LF、**无受跟踪文本文件混用行尾**、
   `*.sh` 不含 CRLF。为什么必须常驻：口径文件有被删/改的可能，一旦消失顽疾会原样复发
   而**没有任何测试会发现** —— 只会表现为"这次提交又卡住了"。

### 顺带修掉的两处门禁自身缺陷（都是"门禁会给人错误的安全感"）

1. **`verify` 漏跑 C2/C4 扫描器 → 会给出骗人的绿灯**。`task_runner verify` 自称"与 CI 的 L2 同序"，
   其步骤清单里却**没有** `scan_error_codes` / `scan_enum_magic_numbers`（它们只在 CI 的 lint job 与
   pre-commit 里跑）。实测后果：`aids-mock/aids_mock/payment_channel.py` 的 `status_code == 200`
   被 C2 判为业务状态魔法数字 —— **verify 全绿、CI 必红**。→ 已把两个扫描器补进 `verify`
   （现 **10 项**），顺序与 CI 的 lint job 一致。该 C2 命中按 `app/core/handlers.py` 的先例
   **改名**（`status_code` → `http_code`）修掉，**不加** `# enum-ok` 豁免 —— 到处加豁免会让门禁名存实亡。
2. **`--basetemp` 用固定路径导致 `tmp_path` 用例随机 ERROR**。固定 `--basetemp` 在宿主机批量删除守卫下会
   **部分删除失败**，残留的 `test_xxx0` / `test_xxxcurrent` 让 **34 个 `tmp_path` 用例随机 ERROR、
   重跑又变绿**（上一会话把它当成了"偶发噪音"，见 §6）。→ 已改为**每轮唯一 basetemp**
   （`_pytest_basetemp()`）：既不删除、也不复用。一个"跑第二次才绿"的门禁等于没有门禁。详见 §3.4。

### T1 结论

**范围维持全量、直接进入 T2**（不触发任何裁剪）。裁剪顺序与硬底线见 `docs/TASKS.md §范围与协作口径`。

> 2026-09-24 起，PRD / TASKS / HANDOFF **不再记录任何工作量与工期数字** —— 这类数字既无法验收，
> 又容易把讨论引到「估得准不准」而不是「东西对不对」。进度改以「梯次出口判据是否达成」为准。

### 遗留（都不影响 T1 出口判据）

| 项 | 说明 |
|---|---|
| **前端镜像未端到端构建** | 两个工程 `npm run build` 已过，但 `deploy/app/frontend.Dockerfile` 的 `docker build` 本机没跑过；CI 的 `images` job 目前只覆盖 backend / ai / mock，**前端两个镜像待补入 CI 矩阵** |
| 商户侧验签降级 | 见 §4.6 第 2 条（T3 的 BE-23 补） |
| pre-commit ruff 版本偏斜 | **已修**（`ce2cf47`：rev 对齐 constraints 的 0.16.8） |
| 行尾（CRLF/LF） | **已根治**（`c6333a8` / BE-37：`.gitattributes` 统一 `eol=lf` + renormalize + 常驻契约测试） |

---

## 5. 下一步（精确顺序）

> **原 T1 的 6 步已全部完成**（记录见 §4.7）。以下替换为 **T2 的开局清单**。

1. **挑一条 T2 任务**：`BE-07`（注册 / 登录：手机号 AES-GCM + HMAC 查询、BCrypt、验证码 Redis 5min + 60s 防重发）
   或 `AI-02`（Ark 客户端 —— 依赖 B4 的 Key，见 §6；Key 仍缺则先用桩推进实现）。
2. 跑 `python scripts/task_runner.py card <编号>` 拿任务卡（依赖检查 + 测试落位 + 必跑门禁 + 提交模板）。
3. **先写会失败的测试并打 `task("<编号>")` 标记，再实现** —— C10 门禁会校验；顺序反了 CI 必红（红得有道理）。
4. 改完跑 `python scripts/task_runner.py verify`（当前 10/10），全绿再提交。
5. `BE-07` 开工前先起中间件（注册 / 登录要用 Redis 存验证码与频控）：
   `cd deploy && docker compose --profile minimal up -d`。
6. 提交时**按任务分包**（每包评审面建议 ≤ 30 文件，见 `TASKS.md §范围与协作口径` 的「评审面约束」）。

**收尾动作（每个任务都一样，C10 门禁会拦）**：把 `TASKS.md` 里该任务勾成 ✅ 之前，
先给覆盖其验收标准的测试加 `pytestmark = [..., pytest.mark.task("<任务号>")]`。
没有 `task` 标记时 `tests/contract/test_task_coverage.py` 会红——这条门禁存在的意义
就是让「任务已完成」必须对应一个真实存在的测试。推荐顺序：**先写测试并打标记，再勾选任务**。

---

## 6. 未决 / 已知遗留

| 项 | 状态 |
|---|---|
| **B4 · Ark API Key 与真实模型调用** | 按用户决定**暂缓**（本轮不动）。影响 AI-02 起的任务：本地用桩推进实现，`AI-01` 脚手架不依赖它 |
| **pre-commit 的 ruff 版本与 CI 偏斜** | **已修（2026-09-24，`ce2cf47`）**：`.pre-commit-config.yaml` 的 rev 由 `v0.8.4` 对齐到 `v0.16.8`（= `constraints.txt` 的锁）。根因是**中文字符宽度算法不同**导致长中文 `assert` 折行位置不一致，症状极隐蔽（pre-commit 报 Passed 却改了文件）。今后升级 ruff 必须**同时**改这两处 |
| **行尾翻转（CRLF/LF）** | **已根治（2026-09-24，`c6333a8` / BE-37）**：新增 `.gitattributes`（`* text=auto eol=lf` + `*.sh` 显式 + 二进制声明）并做了一次性 renormalize —— 工作区与库内统一为 LF，与开发者本机 `core.autocrlf` 解耦。配套常驻门禁 `tests/contract/test_line_endings.py`（5 条）：口径文件存在性 + 规则正确性 + 无受跟踪文本文件混用行尾。此前「改一行 = 整文件重写」、提交连拦 3 轮的顽疾不再复发 |
| **B5 · 范围裁决** | **已裁决（2026-09-23；2026-09-24 修订去工期）**：**1 人 + AI 协作**，**范围暂不裁剪**；裁剪顺序与硬底线见 `TASKS.md §范围与协作口径`。**原「按实测压缩率触发分级裁剪」的规则已删除** —— 进度不再以工期衡量，改以「梯次出口判据是否达成」为准 |
| **Nginx `/api/` 前缀与后端路由不一致** | **已修复（2026-09-23，用户裁决取"nginx 剥离"方案）**：`location /api/` 加 `rewrite ^/api/(.*)$ /$1 break;`，后端路由保持无 `/api` 前缀；`/api/ai/**` 走更长前缀的独立 location，不受影响（AI 侧自带 `/api/ai` 前缀）。已实测：重建 `aids/nginx` 镜像并替换容器后，`GET /api/health` 经网关 → 后端日志为 `/health`（200），`/healthz` 200。教训已写进 `deploy/nginx/conf.d/default.conf` 的 location 注释；`API.md §1.1` 已注明剥离行为 |
| **S1-c 与 S1-e 的口径** | `assert_required_keys_present`（Ark/字段加密密钥）与 `assert_jwt_keys_configured`（JWT 密钥文件）分列两条断言；若后续把密钥统一收敛到 KeyProvider，应合并并同步本表 |
| **`.env.example` 与 compose 的中间件口令护栏** | 已登记为「未落地的约束」（`项目设计报告.md §9.10`），触发条件：首次部署到可被外网访问的环境前 |
| **Nginx TLS** | 同上（443 目前是空映射） |
| **Alembic 纳管既有 DDL** | `DEP-04` 曾说「留待 T1」；**T1 已收官但此事未做** → 顺延，建议在 T2 首个业务表之前处置。基建已就位（`alembic -c aids-backend/alembic.ini heads` 可跑），`aids-backend/alembic/versions/README.md` 写了正确步骤。**注意**：这是「存量库首次纳管」的一次性手工操作，不适合无监督执行 |
| **S5 迁移可回退检查** | `script.py.mako` 已把未实现的 downgrade 生成为 `raise`；完整 CI 检查按计划在 T6 |
| **前端 / AI 服务** | **三条线均已收官（见 §4.7）**：`aids-ai` 完成 AI-01（结构化日志 + 配置隔离显式化）；`aids-mock` 完成 MOCK-01~03（26/26 测试绿）；前端完成 FE-01/02（两个独立工程 + axios 请求封装）。**遗留**：① 前端两个镜像**未端到端构建**、也**未进 CI `images` 矩阵**（两个工程 `npm run build` 已通过，缺的是 `docker build`）；② 商户侧验签仍为显式降级（T3 的 BE-23 补）；③ Mock 渠道报文仍为 v0-draft |
| **两个报告文档已删除** | `docs/地基测评报告.md`、`docs/AI自动生成可行性评估报告.md` **由项目所有者有意删除**（确认无用，不恢复），并已纳入 T1 收官提交。正文对它们的路径引用已清理（§0 / §0.1 / §6 改为叙述式；`docs/VERSIONS.md` 未涉及这两份，无需改口径） |


---

## 7. 一句话给接手的你

这个仓库的价值**全押在"门禁可复现"上**：它的文档不是说明，是**会失败的断言**。
所以改动后的正确姿势永远是——先跑 §1.2 的四项 `--check` 与 §1.3 的测试，
红了就修，绿了再提交。别绕过门禁（`--no-verify` 之类），
