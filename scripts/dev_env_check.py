#!/usr/bin/env python3
"""开发环境自检 —— 一条命令回答「本机现在能不能干活」。

为什么需要它（B1 前置条件）：
    本项目的环境坑有相当一部分**不产生任何错误信息**，只是让结果不可信：

      1. 跨系统 venv：Windows 上建的 `.venv` 在 WSL（Linux）里是废的，反之亦然
         ——只有 `Include/ Lib/ Scripts/` 或 `bin/` 的差别，`python` 直接不存在。
      2. WSL 访问 `/mnt/f`（NTFS）：不是不能跑，而是读文件慢约 31 倍、命令慢约 20 倍
         （HANDOFF §1 实测）。表现是"测试很慢但能过"，没人会去查原因。
      3. 测试连生产库：由启动断言 S1-d 兜底，但那只在进程启动时才触发；
         自检把它提前到"开工前"，省一次莫名其妙的 SystemExit。

    这些都属于"能用代码检查，就不该靠文档要求"（README 三条铁律之三）。

用法（在仓库根，用仓库自己的解释器）：

    # Windows
    .venv/Scripts/python.exe scripts/dev_env_check.py
    # Linux / macOS / WSL（必须是用本系统建的 venv）
    .venv/bin/python scripts/dev_env_check.py

退出码：0 = 全部通过（警告不算失败）；1 = 存在阻断项。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV = PROJECT_ROOT / ".venv"

# 一致性门禁（CI 的第一步就是它们；本地也应该先跑这几条再看测试结果）
#
# 标签刻意只用 ASCII 箭头（`->`）：Windows 中文控制台是 GBK(cp936)，
# `↔`（U+2194）不在其码表内，print 会直接抛 UnicodeEncodeError
# ——自检脚本因为自己的一行提示语而崩掉，是最没用的失败形态（实测踩过）。
GENERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("文档 <-> DDL/枚举/跨文档常量", ("docs/tools/gen_data_dictionary.py", "--check")),
    ("aids-*/requirements.txt <- pyproject.toml", ("scripts/gen_requirements.py", "--check")),
    ("constraints.txt 覆盖全部直接依赖", ("scripts/gen_constraints.py", "--check")),
    ("app/models <- docs/sql/schema.sql", ("scripts/gen_orm_models.py", "--check")),
)

REQUIRED_MODULES = ("fastapi", "sqlalchemy", "pydantic", "pytest", "hypothesis", "pytest_asyncio")

EXPECTED_CONTAINERS = ("aids-mysql", "aids-redis", "aids-nginx")


class Report:
    def __init__(self) -> None:
        self.blocking: list[str] = []
        self.warnings: list[str] = []
        self.passed = 0

    def ok(self, name: str) -> None:
        self.passed += 1
        print(f"  [ OK ] {name}")

    def warn(self, name: str, detail: str) -> None:
        self.warnings.append(f"{name}：{detail}")
        print(f"  [WARN] {name} —— {detail}")

    def fail(self, name: str, detail: str) -> None:
        self.blocking.append(f"{name}：{detail}")
        print(f"  [FAIL] {name} —— {detail}")


def _required_python() -> tuple[int, int]:
    """从 pyproject.toml 读 `requires-python`（SSOT）。

    刻意不在这里写死 `(3, 11)`：写死会在项目抬高下限时静默失效，
    而「自检脚本自己过期」是最难发现的一类失效。
    """
    import tomllib

    spec = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    match = re.search(r"(\d+)\.(\d+)", str(spec))
    if not match:
        return (3, 11)
    return (int(match.group(1)), int(match.group(2)))


def check_interpreter(r: Report) -> None:
    """解释器版本与「跨系统 venv」检测。"""
    required = _required_python()
    current = sys.version_info[:2]
    if current < required:
        r.fail(
            "解释器版本",
            f"需要 >= {required[0]}.{required[1]}（pyproject requires-python），"
            f"当前 {sys.version.split()[0]}",
        )
    else:
        r.ok(f"解释器版本 {sys.version.split()[0]}（要求 >= {required[0]}.{required[1]}）")

    if os.name == "posix" and str(Path.cwd()).startswith("/mnt/"):
        r.warn(
            "跨文件系统边界（WSL → NTFS）",
            "当前在 WSL 下访问 Windows 盘，读文件约慢 31 倍、命令约慢 20 倍；"
            "请改用 Windows 侧解释器 .venv/Scripts/python.exe（HANDOFF §1）",
        )

    if VENV.is_dir():
        expected = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not expected.exists():
            r.fail(
                "跨系统 venv",
                f".venv 缺少 {expected.relative_to(VENV).as_posix()} —— "
                f"该 venv 是另一操作系统建的，本系统下不可用；删除后按 HANDOFF §1.1 重建",
            )
        else:
            r.ok("venv 布局与本机操作系统一致")
    else:
        r.warn("venv", "未发现 .venv —— 依赖检查将使用当前解释器")


def check_dependencies(r: Report) -> None:
    import importlib.util

    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        r.fail(
            "运行依赖",
            f'缺少 {missing}；安装：pip install -e ".[dev]"（Windows 加 --no-cache-dir）',
        )
    else:
        r.ok(f"运行依赖齐备（{len(REQUIRED_MODULES)} 项）")


def check_db_isolation(r: Report) -> None:
    """复用 S1-d 的判定逻辑（不另写一份），把「测试连生产库」提前到开工前。"""
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        r.warn("DATABASE_URL", "未设置 —— 反射层契约测试（requires_mysql）会被跳过")
        return

    sys.path.insert(0, str(PROJECT_ROOT))
    from app.core.config import assert_db_is_isolated  # noqa: PLC0415 - 需先注入 path

    env = os.getenv("APP_ENV", "development").strip().lower()
    try:
        assert_db_is_isolated(db_url=db_url, env=env)
    except SystemExit as exc:  # StartupAssertionError 继承 SystemExit
        r.fail("数据库隔离（S1-d）", str(exc))
        return
    if "test" not in db_url.lower():
        r.warn("DATABASE_URL", "连接串里没有 'test' 字样 —— 确认它不是生产库 aids_shop")
    else:
        r.ok("数据库隔离（连的是测试库）")


def check_generators(r: Report) -> None:
    """四项一致性门禁必须全绿——它们是 CI 的第一步，本地先跑能省一次 CI 往返。"""
    for name, argv in GENERATORS:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / argv[0]), *argv[1:]],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            r.ok(f"一致性门禁 · {name}")
        else:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
            r.fail(f"一致性门禁 · {name}", "\n         ".join(tail))


def check_containers(r: Report) -> None:
    """中间件容器：缺失只是警告（跑契约测试才需要，纯静态门禁不需要）。"""
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        r.warn("中间件容器", f"无法调用 docker（{exc.__class__.__name__}）—— 跳过")
        return

    if proc.returncode != 0:
        r.warn("中间件容器", f"docker ps 失败：{proc.stderr.strip()[:80]}")
        return

    running = {
        line.split("\t")[0]: line.split("\t")[-1] for line in proc.stdout.strip().splitlines()
    }
    unhealthy = [name for name in EXPECTED_CONTAINERS if "healthy" not in running.get(name, "")]
    if unhealthy:
        r.warn(
            "中间件容器",
            f"{unhealthy} 未运行或非 healthy；拉起：cd deploy && docker compose --profile minimal up -d",
        )
    else:
        r.ok(f"中间件容器 healthy（{len(EXPECTED_CONTAINERS)} 项抽查）")


def _tolerate_console_encoding() -> None:
    """把控制台编码错误降级为替换符，而不是让自检崩在 print 上。

    Windows 中文控制台默认 GBK(cp936)，并非所有符号都能编码。
    这里保留原编码（不乱码），只把编不出的字符退化成 `?`——
    「自检脚本自己崩掉」比「输出里有个问号」糟糕得多。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main() -> int:
    _tolerate_console_encoding()
    print("=" * 62)
    print("AIDS 开发环境自检")
    print(f"仓库：{PROJECT_ROOT}")
    print(f"解释器：{sys.executable}")
    print("=" * 62)

    r = Report()
    for section, fn in (
        ("解释器与 venv", check_interpreter),
        ("依赖", check_dependencies),
        ("数据库隔离", check_db_isolation),
        ("一致性门禁", check_generators),
        ("中间件容器", check_containers),
    ):
        print(f"\n[{section}]")
        fn(r)

    print("\n" + "=" * 62)
    if r.blocking:
        print(f"FAIL  {len(r.blocking)} 项阻断 / {r.passed} 项通过 / {len(r.warnings)} 项警告")
        for item in r.blocking:
            print(f"  x {item}")
        return 1
    print(f"PASS  {r.passed} 项通过 / {len(r.warnings)} 项警告")
    for item in r.warnings:
        print(f"  ! {item}")
    print("下一步：pytest tests -q --basetemp=<独立目录>（见 docs/HANDOFF.md §1.3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
