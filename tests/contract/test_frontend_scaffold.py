"""前端工程脚手架契约测试（FE-01 / FE-02）。

为什么需要：
    FE-01（Vite + Vue3 + TS + Pinia + Router 双工程脚手架）与 FE-02（axios 拦截器 +
    JWT 无感刷新并发队列）是**纯前端**交付物，Python 侧的三道门禁（ruff / pyright /
    pytest）天然照不到它们。若不固化，最容易发生的退化是：

        ① 两个工程被悄悄并成一个 —— 「商城 / 管理后台是两个独立工程」这条约束消失；
        ② ESLint / Prettier 配置被删掉或换成空壳，`npm run lint` 退化成 no-op；
        ③ 无感刷新的**单飞（single-flight）**语义被改坏 —— 它是并发不变量，
           坏了的表现是"偶发被登出"，只在 N 个请求同时 401 时复现（BE-03 是
           "刷新即轮换"，并发刷新会把彼此的 Refresh Token 互相作废）。

    所以本文件断言两件事：**结构存在**（工程、依赖、配置）与**关键不变量在源码里
    成立**（拦截器装配、单飞、401 重放、业务错误解包）。

与「抄文档」的区别：
    每条断言都对应一个会真实出错的形态（工程合并、缺依赖、缺配置、刷新并发失控），
    而不是把 `docs/TASKS.md` 的措辞再誊一遍。真正的**并发**语义由工程自带的
    `vitest` 用例（`src/api/refreshQueue.spec.ts`）承载 —— 本文件在 `node_modules`
    可用时会真的把它跑起来（不可用则 skip；CI 由独立的 frontend job 承担）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DOCKERFILE = PROJECT_ROOT / "deploy" / "app" / "frontend.Dockerfile"

# 工程名 → 开发端口（FE-01 要求两个**独立**工程，端口必须错开，否则本地同时起会撞）
PROJECTS: dict[str, int] = {"aids-mall": 5173, "aids-admin": 5174}
PROJECT_IDS = list(PROJECTS)

REQUIRED_DEPENDENCIES = {"vue", "vue-router", "pinia", "axios"}

REQUIRED_DEV_DEPENDENCIES = {
    "vite",
    "typescript",
    "@vitejs/plugin-vue",
    "vue-tsc",
    "eslint",
    "prettier",
    "vitest",
    "@eslint/js",
    "eslint-plugin-vue",
    "typescript-eslint",
    "eslint-config-prettier",
}

REQUIRED_SCRIPTS = {"dev", "build", "lint", "format", "test"}

# 脚手架必须存在的文件；缺任何一个都意味着"这条约束已经没人守着了"。
REQUIRED_FILES = (
    "package.json",
    "package-lock.json",
    "index.html",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
    "eslint.config.js",
    ".prettierrc.json",
    ".prettierignore",
    ".gitignore",
    "src/main.ts",
    "src/App.vue",
    "src/router/index.ts",
    "src/env.d.ts",
)

# FE-02 的交付文件
REQUEST_FILES = (
    "src/api/http.ts",
    "src/api/refreshQueue.ts",
    "src/api/refreshQueue.spec.ts",
    "src/api/tokenStore.ts",
    "src/api/notify.ts",
    "src/api/auth.ts",
)


def _read(project: str, rel: str) -> str:
    return (PROJECT_ROOT / project / rel).read_text(encoding="utf-8")


def _search(pattern: str, text: str) -> re.Match[str]:
    """匹配不到即断言失败。

    不用 `re.search(...).group(...)` 直连：返回类型是 `Match | None`，
    类型检查器会报 `reportOptionalMemberAccess`，而 `# type: ignore` 的
    规则名在不同工具间不一致（pyright 认 `reportOptionalMemberAccess`，
    mypy 认 `union-attr`），写了也会被另一个工具当噪音。断言既消掉可选性，
    又让"配置里找不到这一行"变成一个**带消息的失败**而不是 AttributeError。
    """
    match = re.search(pattern, text)
    assert match is not None, f"未匹配到 {pattern!r}"
    return match


def _package(project: str) -> dict:
    return json.loads(_read(project, "package.json"))


# =====================================================================
# 一、FE-01 工程脚手架
# =====================================================================


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_required_scaffold_files_present(project: str) -> None:
    """脚手架文件齐全（工程 + 构建 + Lint/格式化配置 + 入口）。"""
    missing = [f for f in REQUIRED_FILES if not (PROJECT_ROOT / project / f).is_file()]
    assert not missing, (
        f"{project} 缺少脚手架文件：{missing}\n"
        f"（ESLint/Prettier 配置缺失时 `npm run lint` 会变成 no-op，门禁形同虚设）"
    )


@pytest.mark.task("FE-01")
def test_two_independent_projects_not_one() -> None:
    """商城与管理后台必须是**两个独立工程**（FE-01），不是同一工程的两种模式。

    判据取三处、互相独立：包名不同、开发端口不同、各自持有自己的路由与首页视图。
    只断言"两个目录存在"是不够的 —— 复制出来的空壳也满足，故必须有内容差异。
    """
    names = {_package(p)["name"] for p in PROJECT_IDS}
    assert names == set(PROJECT_IDS), (
        f"两个工程的 package.json name 应互不相同，实际 {sorted(names)}"
    )

    ports = [
        int(_search(r"port:\s*(\d+)", _read(p, "vite.config.ts")).group(1)) for p in PROJECT_IDS
    ]
    assert len(set(ports)) == len(PROJECTS), f"两个工程的 dev 端口重复：{ports}"

    # 各自持有独立的首页视图：商城 HomeView / 后台 DashboardView
    assert (PROJECT_ROOT / "aids-mall" / "src/views/HomeView.vue").is_file()
    assert (PROJECT_ROOT / "aids-admin" / "src/views/DashboardView.vue").is_file()
    assert "HomeView" in _read("aids-mall", "src/router/index.ts")
    assert "DashboardView" in _read("aids-admin", "src/router/index.ts")


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_dependencies_declare_documented_stack(project: str) -> None:
    """运行期依赖必须覆盖 Vite + Vue3 + TS + Pinia + Router 全栈。"""
    declared = set(_package(project)["dependencies"])
    missing = sorted(REQUIRED_DEPENDENCIES - declared)
    assert not missing, f"{project} 缺少运行期依赖：{missing}（缺一个就会在运行期 import 失败）"


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_eslint_and_prettier_toolchain_declared(project: str) -> None:
    """ESLint + Prettier 工具链齐备（FE-01 的显式验收项）。"""
    declared = set(_package(project)["devDependencies"])
    missing = sorted(REQUIRED_DEV_DEPENDENCIES - declared)
    assert not missing, f"{project} 缺少开发期工具链：{missing}"


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_npm_scripts_cover_full_gate(project: str) -> None:
    """`npm run <script>` 覆盖 开发/构建/类型检查/Lint/格式化/单测。"""
    scripts = _package(project)["scripts"]
    missing = sorted(REQUIRED_SCRIPTS - set(scripts))
    assert not missing, f"{project} 缺少 npm script：{missing}"
    assert "type-check" in scripts, f"{project} 缺少 type-check（vue-tsc 是 TS 的唯一静态关卡）"


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_vite_config_has_alias_and_api_proxy(project: str) -> None:
    """`vite.config.ts` 必须配 `@` 别名与 `/api` 代理，且端口与约定一致。

    代理口径必须与 `deploy/nginx/conf.d/default.conf` 对齐：`/api/ai` 先匹配（AI 侧
    路由自带该前缀，不剥离），`/api` 后匹配并**剥离**前缀。写反会让前端本地开发
    与生产行为不一致 —— 这类偏差只在部署后才暴露。
    """
    text = _read(project, "vite.config.ts")
    assert "'@'" in text or '"@"' in text, f"{project} 的 vite 配置缺少 `@` → src 别名"
    assert "'/api/ai'" in text, f"{project} 的 vite 配置缺少 `/api/ai` 代理（AI SSE 走它）"
    assert "'/api'" in text, f"{project} 的 vite 配置缺少 `/api` 代理"
    assert "rewrite" in text, f"{project} 的 `/api` 代理必须 rewrite 掉前缀（与 Nginx 对齐）"
    actual = int(_search(r"port:\s*(\d+)", text).group(1))
    assert actual == PROJECTS[project], (
        f"{project} 的 dev 端口应为 {PROJECTS[project]}，实际 {actual}"
    )


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_typescript_strict_mode_enabled(project: str) -> None:
    """`strict: true` —— 关掉它，类型错误会静默变成运行期错误。"""
    config = json.loads(_read(project, "tsconfig.json"))
    compiler = config["compilerOptions"]
    assert compiler.get("strict") is True, f"{project} 的 tsconfig 未开启 strict"
    assert compiler.get("noEmit") is True, f"{project} 不应产出 .js（由 vite 打包）"
    assert config["include"], f"{project} 的 tsconfig include 为空，等于不检查任何文件"


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_eslint_flat_config_defers_formatting_to_prettier(project: str) -> None:
    """ESLint 9 flat config：TypeScript + Vue 插件齐备，且**排版交回 Prettier**。

    两条配置同时管格式（缩进/换行/属性顺序）时，`npm run lint` 与 `npm run format`
    会互相打架，最后两个都没人跑 —— 故必须显式关掉一批排版类规则并接 `prettier`。
    """
    text = _read(project, "eslint.config.js")
    for token in ("typescript-eslint", "eslint-plugin-vue", "eslint-config-prettier"):
        assert token in text, f"{project} 的 eslint.config.js 未引用 {token}"
    assert "prettier" in text.split("rules")[-1], (
        f"{project} 的 prettier 配置必须放在 rules 之后（否则关掉的排版规则会被重新打开）"
    )


@pytest.mark.task("FE-01")
def test_frontend_dockerfile_app_dir_matches_real_projects() -> None:
    """`frontend.Dockerfile` 是**参数化**的，且它写死的 `APP_DIR` 必须真实存在。

    这是"配置 ↔ 真实文件系统"这一维度的断言（同类事故见 test_deploy_artifacts.py
    的 f309978）：Dockerfile 里出现 `APP_DIR=aids-mall` 而磁盘上没有该目录时，
    `docker build` 必然失败，而 `docker compose config` 与文档门禁都照不到。
    """
    text = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG APP_DIR" in text, "frontend.Dockerfile 应由 APP_DIR 构建参数区分商城/后台"
    assert "${APP_DIR}" in text, "frontend.Dockerfile 必须用 ${APP_DIR} 引用源码，而不是写死目录"

    referenced = set(re.findall(r"APP_DIR=([A-Za-z0-9_-]+)", text))
    assert referenced, "frontend.Dockerfile 未给出任何 APP_DIR 示例，测试无法校验（疑似被改写）"
    missing = sorted(d for d in referenced if not (PROJECT_ROOT / d).is_dir())
    assert not missing, (
        f"frontend.Dockerfile 引用了不存在的工程目录：{missing}\n"
        f"（`docker build` 会因 COPY 源缺失而失败）"
    )


@pytest.mark.task("FE-01")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_build_artifacts_are_gitignored(project: str) -> None:
    """构建产物与依赖目录不得入库（否则每次构建都产生一堆待提交变更）。"""
    ignore = _read(project, ".gitignore")
    for pattern in ("node_modules", "dist"):
        assert pattern in ignore, f"{project}/.gitignore 未忽略 {pattern}"

    if shutil.which("git") is None:
        pytest.skip("git 不可用，跳过 check-ignore 实测")

    for probe in (f"{project}/dist/index.html", f"{project}/node_modules/.package-lock.json"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, f"{probe} 未被任何 .gitignore 命中 —— 构建产物会被提交进仓库"


# =====================================================================
# 二、FE-02 请求封装
# =====================================================================


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_request_files_present(project: str) -> None:
    missing = [f for f in REQUEST_FILES if not (PROJECT_ROOT / project / f).is_file()]
    assert not missing, f"{project} 缺少 FE-02 交付文件：{missing}"


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_http_client_registers_both_interceptors(project: str) -> None:
    """请求拦截器注入 `Bearer`；响应拦截器负责 401 与错误规整。"""
    text = _read(project, "src/api/http.ts")
    assert "interceptors.request.use" in text, f"{project} 未注册请求拦截器（无法注入 Token）"
    assert "interceptors.response.use" in text, f"{project} 未注册响应拦截器（401 无法处理）"
    assert "Bearer" in text, f"{project} 请求头未按 `Bearer <token>` 格式注入"
    assert "getAccessToken" in text, f"{project} 未从 tokenStore 取 Access Token"


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_401_triggers_single_flight_refresh_then_replay(project: str) -> None:
    """401 → 走单飞刷新 → 用新 Token **原样重放**，且只重放一次。

    三个关键点缺一不可：
        - 用 `createRefreshCoordinator`（单飞）而不是各自 `axios.post('/auth/refresh')`；
        - 有"已重放"标记，避免"刷新成功但接口仍 401"时无限重试打爆后端；
        - 刷新请求走**不带拦截器**的裸客户端，否则刷新自身 401 会递归触发刷新。
    """
    text = _read(project, "src/api/http.ts")
    assert "createRefreshCoordinator" in text, f"{project} 未使用单飞协调器做刷新"
    assert re.search(r"401", text), f"{project} 未判断 HTTP 401"
    assert "_aidsRetried" in text, f"{project} 缺少重放标记（会无限重试）"
    assert "rawClient" in text, f"{project} 缺少裸客户端（刷新会因递归触发而失控）"
    # 裸客户端的定义必须先于使用，且不得挂拦截器
    raw_def = text.index("const rawClient")
    assert "interceptors" not in text[raw_def : raw_def + 200], (
        f"{project} 的 rawClient 挂了拦截器 —— 刷新自身 401 时会递归"
    )


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_business_failure_is_unwrapped_to_api_error(project: str) -> None:
    """后端「HTTP 200 + code != 0」也是失败，必须转成异常而不是当成功返回。

    后端契约（docs/API.md §1.1）：业务失败用 HTTP 200 + 非零 code 表达。
    只判 HTTP 状态码的封装会把**业务失败当成功**返回给调用方 —— 漏判即资损。
    """
    text = _read(project, "src/api/http.ts")
    assert "code !== 0" in text, f"{project} 未把 `code !== 0` 判为失败"
    assert "class ApiError" in text, f"{project} 未定义统一业务错误类型"
    assert "notifyError" in text, f"{project} 未接统一错误提示（用户看不到失败原因）"


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_refresh_coordinator_is_single_flight(project: str) -> None:
    """单飞语义：并发调用只发起一次刷新，且失败后必须释放，不能把队列锁死。"""
    text = _read(project, "src/api/refreshQueue.ts")
    assert "inFlight" in text, f"{project} 的刷新协调器未共享在飞 Promise（会并发刷新）"
    assert "finally" in text, f"{project} 的刷新未在 finally 中释放 —— 一次失败会永久挂起"
    assert "onFailure" in text, f"{project} 缺少 onFailure 挂点（失败时无法清 Token / 跳登录）"
    assert "export function createRefreshCoordinator" in text, f"{project} 未导出协调器工厂"


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_token_storage_is_single_source_of_truth(project: str) -> None:
    """Token 读写只能经 `tokenStore`，散落的 `localStorage.getItem('refreshToken')`
    在改名时必然漏改，症状是"刷新总是失败"且只在浏览器里复现。"""
    src = PROJECT_ROOT / project / "src"
    offenders = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in src.rglob("*.ts")
        if path.name != "tokenStore.ts" and "localStorage" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{project} 直接访问 localStorage 的文件（应统一走 tokenStore）：{offenders}"
    )


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_unauthorized_redirects_to_login_route(project: str) -> None:
    """会话不可恢复时必须清 Token 并跳登录页 —— 且该路由真实存在（否则跳 404）。"""
    http = _read(project, "src/api/http.ts")
    assert "clearTokens" in http, f"{project} 会话失效时未清理本地 Token"
    assert "/login" in http, f"{project} 会话失效时未跳转登录页"

    login_path = "/login"
    assert login_path in _read(project, "src/router/index.ts"), (
        f"{project} 的 401 跳转目标是 {login_path}，但路由表里没有它 —— 用户会被甩到 404"
    )


# =====================================================================
# 三、真跑 vitest（并发语义的唯一有效验证；无 node_modules 则 skip）
# =====================================================================


@pytest.mark.task("FE-02")
@pytest.mark.parametrize("project", PROJECT_IDS)
def test_refresh_concurrency_specs_pass(project: str) -> None:
    """把 `refreshQueue.spec.ts` 真正跑起来 —— 静态断言证明不了并发语义。

    `node_modules` 不存在时 skip（CI 的 contract job 不装 Node 依赖，
    前端单测由独立的 frontend job 承担；本断言在本地与任何装了依赖的环境里生效）。
    """
    root = PROJECT_ROOT / project
    if not (root / "node_modules" / ".bin").is_dir():
        pytest.skip(f"{project} 未安装 node_modules，跳过（结构断言与 CI frontend job 仍覆盖）")
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm 不可用")

    result = subprocess.run(
        [npm, "test", "--silent"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        shell=False,
    )
    assert result.returncode == 0, (
        f"{project} 的 vitest 未通过 —— 无感刷新的并发不变量已被改坏：\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
    assert "refreshQueue.spec.ts" in result.stdout, (
        f"{project} 的 vitest 跑到了别处（未覆盖 refreshQueue.spec.ts）"
    )
