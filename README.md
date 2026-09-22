# AIDS — AI Driven Store

企业级电商平台 + AI 智能客服。毕设项目，按商用标准开发。

> **当前阶段**：T0 设计冻结 + 工程门禁基线
> **代码量**：0 行业务代码（本阶段只搭骨架与护栏）

---

## ⚠️ 三条铁律

> **1. 每写一个约束性文档章节（§2.2 数据权限 / §7 库存防线 / §9.2 Milvus 陷阱），旁边立刻配一个会失败的测试。**
>
> **2. 文档管「为什么」，测试管「必须」。凡是能用代码检查的，绝不用文档要求。**
>
> **3. 任何"约定"若无对应测试或 CI 检查，视为不存在。**

这三条是为了根治 [bysj](../bysj/) 的问题：有 12 万字设计文档和 skill 门禁，
工程化仍然薄弱——因为门禁装在**输入端**（读没读文档），而"读过"无法验证，
必然沦为形式合规。

---

## 快速开始

```bash
# 1. Python 环境（基准解释器 3.11，与 pyright/pre-commit 的固定版本一致）
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
# 注意：venv 不入库。Windows 上创建的 venv 在 Linux/WSL 里是废的
#（只有 Include/ Lib/ Scripts/，没有 bin/），换系统须重建。

# 2. 本地配置
cp .env.example .env                    # 按需修改

# 3. pre-commit（本地门禁 L1）
.venv/bin/pre-commit install

# 4. 跑测试（门禁核心）
.venv/bin/pytest tests -q

# 5. 中间件（Docker Compose，分档）
cd deploy && docker compose --profile minimal up -d
```

---

## 仓库结构

```
AIDS/
├── app/                        # 主业务服务（FastAPI）
│   ├── core/
│   │   ├── errors.py           # ★ SSOT：错误码（对齐 API.md §1.2/§1.3）
│   │   ├── response.py         # ★ SSOT：统一响应体（对齐 API.md §1.1）
│   │   └── config.py           # ★ 结构性防御 S1：启动断言
│   └── domain/
│       └── enums.py            # ★ SSOT：状态枚举（对齐 DATA-DICTIONARY §一）
├── tests/
│   ├── contract/               # ★ 契约测试：文档 ↔ 代码 一致性（CI 门禁核心）
│   │   ├── _doc_parser.py      #   直接解析 docs/*.md，不抄文档内容
│   │   ├── test_enum_mapping.py        # C2
│   │   ├── test_error_codes.py         # C4
│   │   ├── test_schema_matches_dict.py # C1
│   │   ├── test_response_envelope.py   # C7
│   │   ├── test_unique_indexes.py      # C8
│   │   ├── scan_error_codes.py         # 扫描器：禁止硬编码错误码
│   │   └── scan_enum_magic_numbers.py  # 扫描器：禁止状态魔法数字
│   └── invariants/             # ★ 业务不变量
│       ├── test_stock_invariants.py    # C3/C5/C6 库存恒等式、幂等键、退款边界
│       ├── test_idor_guard.py          # S4 IDOR 防护
│       └── test_startup_assertions.py  # S1 启动断言
├── docs/                       # 设计文档（权威来源）
│   ├── PRD.md                  # 需求 + 状态机 + 库存防线 + AI 客服设计
│   ├── TASKS.md                # 7 梯次 97 项任务
│   ├── DATA-DICTIONARY.md      # 38 表 419 字段 + 状态枚举映射 + 12 条不变量
│   ├── API.md                  # 接口契约 + 错误码分段
│   ├── 工程化门禁方案.md        # ★ 本门禁体系的设计说明
│   └── sql/                    # schema.sql / mock_schema.sql / seed.sql
├── aids-backend/               # 三服务镜像的依赖清单（由 pyproject.toml 生成）
├── aids-ai/                    #   └ 对应 deploy/app/{backend,ai,mock}.Dockerfile
├── aids-mock/                  #      的构建上下文
├── deploy/                     # Docker Compose（9 服务分档）
├── scripts/hooks/              # pre-commit hook 脚本
├── scripts/gen_requirements.py # 依赖清单生成器 + 一致性门禁
├── .pre-commit-config.yaml     # 门禁 L1
└── .github/workflows/ci.yml    # 门禁 L2/L3
```

---

## 门禁体系（三层防线）

| 层 | 触发 | 内容 | 时长 |
|---|---|---|---|
| **L1** | pre-commit（本地） | 文档一致性门禁、ruff、敏感文件、残留文件、错误码扫描、枚举扫描 | < 5s |
| **L2** | PR（GitHub Actions） | **文档一致性门禁（第一步）**、契约测试（真实 MySQL 8）、不变量测试、pyright | < 5min |
| **L3** | merge 到 main | compose 最小档冒烟 | < 15min |

> 文档一致性门禁（`docs/tools/gen_data_dictionary.py --check`，33 项）**必须是第一步**：
> 它拦的是"文档与代码已经互相矛盾"这类结构性漂移，一旦漂移，后面的测试全绿也没有意义。
> 它纯标准库实现，不需要装任何依赖，因此不会因为依赖问题被跳过。

### 八类契约测试

| # | 文档事实 | 测试文件 | 失败即 |
|---|---|---|---|
| C1 | 38 表 / 419 字段 | `test_schema_matches_dict.py` | DDL 与字典漂移 |
| C2 | 11 组状态枚举映射 | `test_enum_mapping.py` | 状态机错乱 |
| C3 | 12 条不变量 | `test_stock_invariants.py` | 库存账不平 |
| C4 | 错误码分段 1xxxx-9xxxx | `test_error_codes.py` | 错误码野生 |
| C5 | 库存恒等式 | `test_stock_invariants.py` | 超卖 |
| C6 | 幂等键 6 种规则 | `test_stock_invariants.py` | 重复扣减 |
| C7 | 统一响应 `{code,message,data}` | `test_response_envelope.py` | 前端拦截器失配 |
| C8 | 4 类 UNIQUE 索引 | `test_unique_indexes.py` | 重复发货/刷评价 |

### 核心设计：单一定义 + 反向校验

状态枚举**不在** DDL / PRD / 代码 / 前端各写一遍：

```
app/domain/enums.py          ← 唯一权威定义（IntEnum）
        ↕  test_enum_mapping.py 逐项比对
docs/DATA-DICTIONARY.md §一   ← 唯一权威映射表
```

两侧任何一侧漂移，CI 立刻变红——**双向都拦得住**。

---

## 常用命令

```bash
# 全部测试
pytest tests -q

# 只跑契约测试（门禁核心，无需 DB）
pytest tests/contract -q -m contract

# 需要真实 MySQL 的反射测试
DATABASE_URL=mysql+asyncmy://... pytest tests/contract -q -m requires_mysql

# 只跑不变量
pytest tests/invariants -q

# 代码风格
ruff format app tests
ruff check app tests --fix

# 扫描器（也可单独跑）
python -m tests.contract.scan_error_codes app
python -m tests.contract.scan_enum_magic_numbers app

# 启动断言（模拟生产启动）
APP_ENV=production SECRET_KEY=xxx python -m app.core.config

# 依赖清单（唯一来源是 pyproject.toml，三份清单是派生物）
python3 scripts/gen_requirements.py --write     # 改完 pyproject.toml 后重新生成
python3 scripts/gen_requirements.py --check     # 校验（pre-commit 与 CI 跑的就是这条）
```

---

## 权威文档索引

改动以下内容时，**必须同步对应文档，否则 CI 会失败**：

| 改什么 | 同步文档 | 受哪个测试约束 |
|---|---|---|
| 状态枚举值/名称 | `DATA-DICTIONARY.md` §一 | C2 |
| 新增错误码 | `API.md` §1.3 或对应模块 | C4 |
| 新增表/字段 | `DATA-DICTIONARY.md` §二/§三 + `schema.sql` | C1 |
| 新增 UNIQUE 索引 | `DATA-DICTIONARY.md` §四 | C8 |
| 改统一响应结构 | `API.md` §1.1 | C7 |
| 改不变量规则 | `DATA-DICTIONARY.md` §四 | C3/C5/C6 |
| 依赖增删/升级 | 只改 `pyproject.toml`，再跑 `scripts/gen_requirements.py --write` | pre-commit `requirements-sync` + CI consistency |

> **依赖只有一处来源**：`pyproject.toml`。`aids-backend/`、`aids-ai/`、`aids-mock/`
> 三个目录里的 `requirements.txt` 是**生成物**（Dockerfile 构建时要拷的文件），
> 手工编辑会被 pre-commit 与 CI 同时拦下。

---

## 不要做的事

- ❌ 在业务代码里硬编码错误码（用 `app.core.errors`）→ CI 拦截
- ❌ 用 `order.status == 20` 判断状态（用 `OrderStatus`）→ CI 拦截
- ❌ 提交 `.env` / 密钥文件 → pre-commit 拦截
- ❌ 提交 `_*` 临时文件 → pre-commit 拦截
- ❌ 生产环境连测试库 / 测试环境连生产库 → 启动断言拦截

---

## 相关文档

- [工程化门禁方案](docs/工程化门禁方案.md) — 本门禁体系为什么这样设计
- [项目设计报告](docs/项目设计报告.md) — 逐文件职责说明 + 两轮核查发现的优化清单（§11）
- [PRD](docs/PRD.md) — 需求与技术架构
- [TASKS](docs/TASKS.md) — 7 梯次任务清单
- [DATA-DICTIONARY](docs/DATA-DICTIONARY.md) — 数据字典与不变量
- [API](docs/API.md) — 接口契约
