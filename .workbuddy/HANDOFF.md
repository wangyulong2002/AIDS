# AIDS 项目交接说明

> ⚠️ **本文档是 2026-09-22 的历史快照，请勿据此上手。**
> 当前有效的交接文档已迁移到 **`docs/HANDOFF.md`**（与工具无关，任何会话/开发者都读它）——
> 那里有最新的进度、上手命令、本机环境坑与下一步任务。
> 本文档保留仅为追溯当时的问题清单与背景。

> 生成时间：2026-09-22 22:2x ｜ 生成者：上一会话（WorkBuddy）
> 位置：`F:\AIDS`（已从 WSL `/home/heart/vibocoding/AIDS` 迁出，**不再使用 WSL**）
> 本文档替代之前所有口头上下文。新任务请先完整读完第 0 节。

---

## 0. 【必须先处理】仓库当前跑不起来

`pytest tests -q` 会直接报错：

```
tests/api/test_route_contract.py:20: from aids_backend.api import MODULE_ROUTERS
E   ModuleNotFoundError: No module named 'aids_backend'
355 tests collected, 2 errors（tests/api 两个文件无法收集）
```

### 根因

提交历史里有一次**半途而废的重构**，导致「新增的代码」和「配置」互相矛盾：

| 提交 | 内容 | 是否自洽 |
|------|------|----------|
| `0d198a0` refactor: 拆分服务包命名并统一扫描面 | 服务包独立命名 `aids_backend` + pyproject/Dockerfile/CI 全部配套改好 | ✅ 自洽 |
| `f309978` refactor: 服务包统一命名为 app | **提交了新增文件**，却把 pyproject/Dockerfile/CI/pre-commit **改回了回退前的旧状态**；且**并未真正重命名目录** | ❌ 矛盾 |

结果：代码目录是 `aids-backend/aids_backend/`，但配置全部指向 `aids-backend/app/`。

### 5 处不一致（逐条已核实）

| # | 文件 | 现状（错） | 应为 |
|---|------|-----------|------|
| 1 | `deploy/app/{backend,ai,mock}.Dockerfile` | `COPY aids-backend/app ./app` + `CMD gunicorn app.main:app` | `COPY aids-backend/aids_backend ./aids_backend` + `CMD gunicorn aids_backend.main:app`（Docker build 现在**必失败**：源目录不存在） |
| 2 | `pyproject.toml` | `packages=["app"]`、`include=["app","tests"]`，**无** `pythonpath`/`extraPaths`/`known-first-party` | 需补 `pythonpath=["aids-backend"]`、pyright `include`+`extraPaths`、ruff `isort.known-first-party` |
| 3 | `.github/workflows/ci.yml` | `ruff check app tests`、`scan_* app`、`pytest tests/contract -m contract` | 扫描面加 `aids-*`；`pytest tests -m contract`（否则 tests/api 永不执行） |
| 4 | `.pre-commit-config.yaml` | 扫描器 `files: ^app/.*\.py$` | `^(app|aids-[a-z]+)/.*\.py$` |
| 5 | `tests/contract/scan_{error_codes,enum_magic_numbers}.py` | `_default_targets() -> [PROJECT_ROOT/"app"]`，但仍残留 `_targets`/`scan_targets` 引用（疑似未使用 import，ruff F401 会报） | 恢复为 `return scan_targets()` |

> `tests/contract/_targets.py` 目前是**孤儿文件**（无有效引用）。

### 修复（一条命令）

「服务包独立命名」是经过实测验证的正确方案（见 §8.1），`f309978` 想回退到的「都叫 app」方案**在本地开发时行不通**。直接恢复到 `0d198a0` 的配置状态：

```powershell
cd F:\AIDS
git checkout 0d198a0 -- pyproject.toml .github/workflows/ci.yml .pre-commit-config.yaml `
    deploy/app/backend.Dockerfile deploy/app/ai.Dockerfile deploy/app/mock.Dockerfile `
    tests/contract/scan_error_codes.py tests/contract/scan_enum_magic_numbers.py

# 重建 Windows venv（现有 .venv 是 Linux 的，见 §1）
Remove-Item -Recurse -Force .venv
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 验证
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\ruff check app tests aids-backend
.venv\Scripts\python -m tests.contract.scan_error_codes
```

预期：`pytest` 全绿（约 400 项）、ruff 通过、扫描器静默通过。

修好后建议**补一个契约测试**：断言 Dockerfile 的 COPY 源目录真实存在——这类「配置指向不存在的目录」不该靠人肉发现。

### 顺手修的小事

恢复后的 `tests/contract/scan_{error_codes,enum_magic_numbers}.py` 里，注释仍写着
「扫描面由 `tests/contract/_scan_targets.py` 统一定义」，但该文件后来已改名为 `_targets.py`
（原名撞上 `.gitignore` 的 `**/_scan_*.py`，见 §8.2）。把注释里的文件名改对即可。

---

## 1. 环境（已切到 Windows，无 WSL）

| 项 | 值 |
|----|-----|
| 项目路径 | `F:\AIDS` |
| 操作系统 | Windows（**不再用 WSL**） |
| Docker | Docker Desktop 29.6.2（`docker` 在 PATH） |
| ⚠️ `.venv` | **是 Linux venv**（`.venv/bin/python`、`.venv/lib/python3.11/`），Windows 下不可用，**必须删除重建**（`.venv/Scripts/python.exe`） |
| Python | 本机 python 3.13（系统）、3.11 / 3.13 / 3.14（managed）；项目要求 **>=3.11**，CI 用 **3.11** |

### 为什么离开 WSL（重要，别再走回头路）

上一会话实测（Windows 侧工具访问 WSL 文件）：

| 项 | 耗时 |
|----|------|
| 读同一批 72 个文件（WSL 走 9P/UNC） | 203 ms |
| 同上（本地 NTFS） | **6 ms** → **慢 31.5 倍** |
| `docker exec <容器> true`（挂载 WSL 路径） | ~60 s |
| Bash 命令执行路径整体 | 被拖慢约 20 倍（`sleep 1` 实测 19932 ms） |

现在在 `F:\AIDS` 原生跑 pytest：**收集 355 项仅 2.14 秒**。

**约定：不要再出现 `\\wsl.localhost\...`、`/mnt/c/...` 这类跨边界路径；不要用 wsl.exe。**

---

## 2. 项目是什么

**毕设**：企业级电商平台 + AI 智能客服（代号 AIDS）。

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + Alembic，Python 3.11 |
| 前端 | Vite + Vue3 + TS + Pinia（商城 / 管理后台两个独立工程） |
| AI | FastAPI + Ark（火山方舟）+ Milvus RAG |
| Mock | 独立 FastAPI 服务，模拟第三方支付/短信/物流（**独立库独立端口**） |
| 中间件 | MySQL 8 / Redis / Kafka / MinIO / Milvus / Nginx |
| 部署 | Docker Compose，构建期 COPY 自建镜像 |

### 项目最大的特色：**门禁驱动开发**

不是「文档要求 AI 读设计报告」，而是「**代码必须过契约测试**」。设计理念见 `docs/工程化门禁方案.md`，三条铁律：

> 1. 每写一个约束性文档章节，旁边立刻配一个**会失败**的测试。
> 2. 文档管「为什么」，测试管「必须」。凡是能用代码检查的，绝不用文档要求。
> 3. 任何「约定」若无对应测试或 CI 检查，**视为不存在**。

**改代码前务必读：** `docs/TASKS.md`（任务清单与验收标准）、`docs/API.md`（接口契约）、`docs/DATA-DICTIONARY.md`（表/字段/枚举 SSOT）、`docs/项目设计报告.md`。

---

## 3. Git 状态

```
HEAD = f309978   工作区干净   main == origin/main（无待推送）
```

```
f309978  refactor: 服务包统一命名为 app，ORM/Docker/CI 契约层收敛   ← 有问题的那个，见 §0
0d198a0  refactor: 拆分服务包命名并统一扫描面，修复 CI 漏检新服务问题  ← 正确状态
4483f40  feat(deploy): 落地镜像布局方案 A
0a7f17d  fix(ci): 修复 contract 载入 DDL 冲突，并修掉反射层测试的 SQLAlchemy 2.0 兼容 bug
d8c344b  fix(gate): 修复门禁漏检与误伤，依赖清单与本地环境收口
9844d2f  fix(ci): 修复 contract 容器创建与 pyright 类型检查
974041a  fix(ci): 修复 contract/invariants/typecheck 三个 job 失败
bb8b99a  T0 阶段交付 — 设计文档 + 工程化门禁骨架
```

远端：`https://github.com/wangyulong2002/AIDS.git`
受跟踪文件 72 个。

---

## 4. 目录结构

```
F:\AIDS\
├── app/                      ★ 跨服务共享契约层（SSOT，三个服务都依赖）
│   ├── core/
│   │   ├── errors.py         错误码唯一定义（1xxxx~9xxxx 九段位）
│   │   ├── response.py       统一响应体 + HTTP_STATUS_EXCEPTIONS
│   │   ├── exceptions.py     BusinessError（统一业务异常）
│   │   └── config.py         配置 + 启动断言 S1（4 个硬校验）
│   └── domain/enums.py       11 组状态枚举 SSOT（IntEnum）
├── aids-backend/             ★ 主业务服务工程
│   ├── requirements.txt      由 pyproject.toml 生成（勿手改）
│   └── aids_backend/
│       ├── main.py           gunicorn 入口：启动断言 → app
│       ├── app_factory.py    create_app()（无副作用，供测试）
│       ├── handlers.py       全局异常处理器
│       └── api/              health + user/product/order/pay/marketing/admin
├── aids-ai/                  仅 requirements.txt（AI 服务未开工）
├── aids-mock/                仅 requirements.txt（Mock 服务未开工）
├── tests/
│   ├── contract/             文档↔代码一致性（C1~C8）+ 两个 AST 扫描器
│   │   ├── _doc_parser.py    解析 markdown 取「文档事实」
│   │   ├── _targets.py       扫描面枚举（当前是孤儿，见 §0）
│   │   └── scan_*.py         错误码 / 状态枚举 硬编码扫描器
│   ├── invariants/           业务不变量 + 启动断言 + 护栏自检
│   └── api/                  BE-01 新增：响应体/异常映射/路由前缀/启动防御
├── docs/                     PRD / API / DATA-DICTIONARY / TASKS / 设计报告 …
├── deploy/                   docker-compose + 自建镜像（mysql/nginx/app）
└── scripts/                  gen_requirements.py + pre-commit hooks
```

---

## 5. 门禁体系（工作方式）

### 三层防线

| 层 | 触发 | 内容 |
|----|------|------|
| **L1** | pre-commit（本地，<5s） | ruff format/check、残留文件、敏感文件、错误码扫描、文档一致性 |
| **L2** | PR（CI，<5min） | ruff、契约测试、不变量测试、pyright basic |
| **L3** | merge 到 main | compose 最小档冒烟（当前是占位） |

CI 文件：`.github/workflows/ci.yml`，7 个 job：`docs` / `lint` / `contract` / `invariants` / `typecheck` / `smoke` / `gate`。

### 契约测试清单（C1~C8，见 `docs/工程化门禁方案.md`）

| # | 约束 | 手段 |
|---|------|------|
| C1 | 38 表 / 419 字段 | 反射真实 MySQL 比对 |
| C2 | 11 组状态枚举 | 代码 IntEnum ↔ DATA-DICTIONARY §一 逐项比对 + AST 扫魔法数字 |
| C3/C5/C6 | 12 条 DB 不变量、库存恒等式、幂等键 | hypothesis 属性测试 |
| C4 | 错误码分段 | AST 扫裸错误码数字 |
| C7 | 统一响应体 `{code,message,data}` | 遍历路由断言结构 |
| C8 | UNIQUE 索引列清单 | 反射 information_schema |

### 启动断言 S1（`app/core/config.py`，`main.py` 导入时执行）

- S1-a 生产环境 DB URL 含 test/dev → 拒绝启动
- S1-b 生产环境 SECRET_KEY 为默认值或 <32 位 → 拒绝启动
- S1-c 生产环境缺 `ARK_API_KEY`/`FIELD_ENCRYPT_KEY`/`FIELD_HMAC_KEY` → 拒绝启动
- S1-d test/development 连生产库名 `aids_shop` → 拒绝启动

`StartupAssertionError` **故意继承 `SystemExit`**，业务层 `except Exception` 吞不掉。

### 本地跑法（Windows）

```powershell
cd F:\AIDS
.venv\Scripts\python -m pytest tests -q                        # 全量
.venv\Scripts\python -m pytest tests -m contract               # 契约
.venv\Scripts\python -m pytest tests -m requires_mysql         # 反射层（需 DATABASE_URL）
.venv\Scripts\ruff format --check app tests aids-backend
.venv\Scripts\ruff check app tests aids-backend
.venv\Scripts\python -m tests.contract.scan_error_codes
.venv\Scripts\python -m tests.contract.scan_enum_magic_numbers
.venv\Scripts\pyright
.venv\Scripts\pre-commit run --all-files
python docs/tools/gen_data_dictionary.py --check -v             # 文档 33 项门禁
python scripts/gen_requirements.py --check                      # 依赖清单一致性
```

> 反射层契约测试需真实 MySQL：设 `DATABASE_URL=mysql+pymysql://.../aids_shop_test`（库名必须含 `test`，否则 S1-d 拦截）。本地用 `deploy/docker-compose.yml` 起 MySQL 即可。

---

## 6. 待办：T1 阶段任务

`docs/TASKS.md` 中 T1 共 12 项，`BE-01` 已基本完成（§0 修好后即完成）。

| 编号 | 任务 | 依赖 |
|------|------|------|
| BE-01 | 项目脚手架 ★基本完成 | BE-00 |
| BE-02 | ORM 基建（雪花 ID、自动时间戳、逻辑删除、分页、乐观锁、Alembic） | BE-01 |
| BE-03 | 鉴权模块（JWT 双 Token、RS256 + JWKS、Refresh 存 Redis 吊销） | BE-01 |
| BE-04 | **数据权限 / IDOR 防护**（Repository 基类统一注入 `WHERE user_id=?`） | BE-03 |
| BE-05 | 通用组件（Redis 封装、Kafka 生产者、本地消息表、traceId 全链路、sys_config 热更新） | BE-01 |
| BE-06 | 内部服务接口（服务间签名 + 脱敏） | BE-03, BE-04 |
| MOCK-01/02/03 | Mock 支付网关 / 短信 / 物流 | DEP-01 |
| FE-01/02 | 前端脚手架、axios 封装（JWT 无感刷新） | DEP-02 |
| AI-01 | AI 服务脚手架 | DEP-02 |

**建议顺序**：修 §0 → BE-02 → BE-03 → BE-04（最高风险，验收标准很硬：用 A 的 Token 访问 B 的订单/地址/优惠券/会话**全部 403**，批量递增 ID 扫描无越权泄漏）。

---

## 7. 关键约定（改动前必看）

### 7.1 统一响应体（`docs/API.md` §1.1）

```json
{ "code": 0, "message": "success", "data": {} }
```

**业务失败也返回 HTTP 200**，用 `code` 区分。只有 5 个例外，定义在 `app/core/response.py::HTTP_STATUS_EXCEPTIONS`：

| code | HTTP |
|------|------|
| 10002 未登录 / Token 失效 | 401 |
| 10003 无权限 | 403 |
| 10005 数据越权（IDOR） | 403 |
| 10006 请求过于频繁 | 429 |
| 10008 系统繁忙 | 500 |

其余业务码一律 200。**`10004 资源不存在` → HTTP 200**（API.md §1.3 明确）。
路由层错误（路径不存在/方法不允许）保留 404/405，但仍返回统一响应体——这不违反上表，因为该表约束的是**业务异常码**。

统一分页：请求 `{pageNum(从1), pageSize(≤100)}`，响应 `{total, list}`。

### 7.2 错误码

唯一来源 `app/core/errors.py`（`CommonError`/`UserError`/`ProductError`/`OrderError`/`PaymentError`/`AiError`/`MarketingError`/`AdminError`/`FileSystemError`）。

**禁止裸数字**，必须 `int(XxxError.YYY)`。新增错误码必须同步 `docs/API.md`。

### 7.3 状态枚举

唯一来源 `app/domain/enums.py`。DDL 的数字、PRD 的名称、前端映射全部以它为准，由 `tests/contract/test_enum_mapping.py` 逐项校验。**禁止 `if order.status == 20`**。

### 7.4 敏感字段（`docs/sql/schema.sql` 头部约定 9）

手机号 AES-256-GCM 加密存 `mobile`，另设 HMAC-SHA256 的 `mobile_hash` 用于查询与唯一约束。**必须用 HMAC（带密钥），裸 SHA-256 可被彩虹表秒破**（手机号只有 11 位）。

### 7.5 时间

一律 UTC 存储。连接串需 `connectionTimeZone=UTC` 或连后 `SET time_zone='+00:00'`。CI 的 MySQL 服务已设 `--default-time-zone=+00:00`。

### 7.6 依赖清单

唯一来源 `pyproject.toml`。三份 `aids-*/requirements.txt` 由 `scripts/gen_requirements.py` **生成**，手改任意一份都会被 pre-commit 与 CI 拦下。

---

## 8. 已知陷阱（都踩过）

### 8.1 服务包名不能与共享层同名（决定独立命名的原因）

共享层占用 `app`。若服务包也叫 `app`：

- 两者都是普通包（有 `__init__.py`）→ `app.__path__` 只含**第一个**目录，`app.main` 直接 ModuleNotFoundError（实测）
- 去掉 `__init__.py` 走 PEP 420 命名空间包 → 能合并，但**多个服务目录同时在 `sys.path` 上时 `app.main` 会静默解析到错误的服务**
- 靠 Dockerfile 把两者 COPY 进同一目录只在镜像里成立，本地跑不起来

**故：共享包 `app`（仓库根）+ 服务包 `aids_backend`（`aids-backend/` 下），两个顶层包零冲突。**

### 8.2 `.gitignore` 的 `**/_scan_*.py` 会吞掉新文件

该规则用于防残留探针文件，但任何 `_scan_xxx.py` 都会被忽略 → **永远不会被提交**，且不会有任何报错。
**新增文件后必须验：** `git check-ignore --no-index --quiet <path>`（rc=0 表示被忽略）。
`tests/invariants/test_guard_selfcheck.py::TestNoTrackedFileIsIgnored` 正是为此设计。

### 8.3 C2 扫描器按字段名子串判定

`_STATUS_FIELD_HINTS` 含 `"status"`（子串匹配）。写 `status >= 500` 会被当成业务状态魔法数字误报，`http_status` 同样中招。
**修法：把变量改名为 `http_code`**，不要加 `# enum-ok`——到处加豁免会让门禁名存实亡。

### 8.4 mysql 客户端不展开 SQL 文件里的 `${VAR:-default}`

看着像 shell 变量，实际是普通文本。实测会建出一个**字面名为 `${AIDS_MAIN_DB:-aids_shop}` 的库**且不报错。
CI 载入 DDL 用的是 `sed 's/`aids_shop`/`aids_shop_test`/g'` + 表数自检（38 表）。

### 8.5 SQLAlchemy 2.0 不接受裸字符串

`Connection.execute("SELECT ...")` 会抛 `ObjectNotExecutableError`，必须 `text("SELECT ...")`。

### 8.6 其他

- **CRLF**：仓库 blob 一直是 CRLF（初始提交即如此）。GitHub Actions 解析 YAML 时块标量会归一化，对 `run: |` 无影响。
- **pyright 用 UNC 路径会 `filesAnalyzed: 0`**（CMD.EXE 不支持 UNC 工作目录）——已在 F 盘原生路径，不受影响。
- **`test_guard_selfcheck.py` 在无 git 的环境会 `FileNotFoundError` 而非 skip**（`subprocess.run(["git",...])` 未捕获）。CI（ubuntu-latest）有 git，不受影响。
- 该文件另有一处在 **Windows Git Bash** 下必挂的问题（`subprocess` 用 cp936 解码含中文的 bash 输出 → `UnicodeDecodeError: 0xd2`）。若在 Windows 原生跑全量测试遇到，属已知环境问题，非代码缺陷。

---

## 9. 门禁的当前覆盖缺口（§0 修好后解决）

修复前，**新增的服务代码完全不受任何门禁约束**：

| 门禁 | 修复前扫描面 |
|------|-------------|
| ruff format / check（CI） | `app tests` |
| C4 / C2 扫描器 | `app` |
| pre-commit 扫描器 files 正则 | `^app/.*\.py$` |
| pyright include | `["app","tests"]` |
| pytest 契约测试 | `tests/contract`（漏掉 `tests/api`） |

修复后统一为 `app + aids-*`，且扫描器改为**无参数调用**（用 `_targets.scan_targets()` 的默认扫描面），避免「新增服务后忘了登记」。

> 这次修复同时带了一个**门禁有效性验证**：往服务目录注入一个含裸错误码的文件，确认扫描器真的抓得到。建议保留这种「证明门禁生效」的验证习惯。

---

## 10. 快速上手（新会话直接照做）

```powershell
cd F:\AIDS

# 1) 修 §0 的仓库不一致
git checkout 0d198a0 -- pyproject.toml .github/workflows/ci.yml .pre-commit-config.yaml `
    deploy/app/backend.Dockerfile deploy/app/ai.Dockerfile deploy/app/mock.Dockerfile `
    tests/contract/scan_error_codes.py tests/contract/scan_enum_magic_numbers.py

# 2) 重建 Windows venv
Remove-Item -Recurse -Force .venv
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 3) 全量验证
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\ruff check app tests aids-backend
.venv\Scripts\python -m tests.contract.scan_error_codes
python docs/tools/gen_data_dictionary.py --check

# 4) 通过后提交
git add -A && git commit -m "fix: 修复 f309978 半途回退导致的包名与配置不一致"
```

然后开 **BE-02（ORM 基建）**。

---

*本文档由上一会话生成，若与实际不符，以仓库现状为准。*
