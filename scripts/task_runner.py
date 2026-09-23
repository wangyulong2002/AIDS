#!/usr/bin/env python3
"""任务卡与门禁执行器 —— 「逐任务自动生成」的治具。

为什么需要它：
    `docs/TASKS.md` 有 97 项任务，每项的验收标准、依赖、测试该落在哪、要跑哪些
    门禁都不一样。每次都靠人（或靠 AI）重新读一遍文档再决定跑什么，等于把
    「可复现」交给了记忆——而本项目的立身之本正是**不依赖记忆**。
    本脚本只固定机械部分：

        card  <任务号>   任务卡：依赖检查 + 落测试位置 + 必须跑的门禁 + 提交信息模板
        verify           依次跑完整门禁链（四项 --check + ruff + pyright + 全量测试）
        list  [--tier]   按梯次列出任务与状态，便于挑下一个

    判断部分（写"会失败的测试"、实现、设计取舍）仍由人/AI 完成。
    脚本的价值是**不让它漏**，而不是替它想。

与 C10 门禁的配合（重要）：
    `tests/contract/test_task_coverage.py` 断言：`TASKS.md` 里 ✅ 的代码类任务，
    必须有 `@pytest.mark.task("<任务号>")` 的测试。故 `card` 的输出里把它列为
    第 1 步——**先写测试并打标记，再勾选任务**（顺序反了 CI 就会红，而且红得有道理）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from console_compat import tolerate_console_encoding  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_DOC = PROJECT_ROOT / "docs" / "TASKS.md"

# TASKS.md 的任务行：| 编号 | 板块 | 状态 | 任务 | 内容与验收标准 | 依赖 |
_ROW = re.compile(r"^\|\s*(?P<id>[A-Z]+-\d+)\s*\|(?P<rest>.+)\|\s*$")
_TIER = re.compile(r"^##\s+(?P<tier>T\d)\s+(?P<name>.+?)（")

# 状态符号 → ASCII（控制台可能是 GBK，符号打不出来就失去意义）
STATUS_LABEL = {"✅": "DONE", "⏳": "WIP", "☐": "TODO", "🚫": "BLOCKED"}
DONE = "✅"

# 板块 → 测试落位的默认位置（项目约定：契约测试 / 不变量 / 接口测试三类）
TEST_HOME = {
    "BE": "接口行为 -> tests/api/；业务不变量 -> tests/invariants/；文档代码一致性 -> tests/contract/",
    "MOCK": "渠道接口行为 → tests/api/（Mock 服务同样装配统一响应体，断言格式与主业务一致）",
    "AI": "AI 服务接口行为 → tests/api/；检索/切片类不变量 → tests/invariants/",
    "FE": "前端工程内（FE-01 起），E2E 可用 agent-browser 真浏览器验证",
    "DEP": "无单测：由 CI 的 images / smoke job 覆盖（构建 + 容器探针 + 最小档拉起）",
    "DOC": "无单测：由文档一致性门禁（docs/tools/gen_data_dictionary.py --check）覆盖",
}

# 「改 X 必须同步 Y」——与 README「权威文档索引」同源，改一处必须同时改这些
SSOT_HINTS = (
    "改状态枚举值/名称 → docs/DATA-DICTIONARY.md §一（受 C2 约束）",
    "新增错误码 → docs/API.md §1.3 或对应模块（受 C4 约束，且禁止裸数字）",
    "新增表/字段 → docs/sql/schema.sql + DATA-DICTIONARY §二/§三，再跑 gen_orm_models --write（C1/C9）",
    "改统一响应结构 → docs/API.md §1.1（C7）",
    "改不变量规则 → docs/DATA-DICTIONARY.md §四（C3/C5/C6）",
    "增删依赖 → 只改 pyproject.toml，再跑 gen_requirements --write 与 gen_constraints --write",
)

RUFF_TARGETS = ("app", "tests", "aids-ai", "aids-backend", "aids-mock", "scripts")


class Task:
    __slots__ = ("id", "board", "status", "title", "criteria", "deps", "tier")

    def __init__(
        self, id: str, board: str, status: str, title: str, criteria: str, deps: str, tier: str
    ) -> None:
        self.id = id
        self.board = board
        self.status = status
        self.title = title
        self.criteria = criteria
        self.deps = deps
        self.tier = tier

    @property
    def label(self) -> str:
        return STATUS_LABEL.get(self.status, "?")

    @property
    def board_code(self) -> str:
        """板块代码取自编号前缀（BE/FE/AI/MOCK/DEP/DOC）。

        TASKS.md 的「板块」列是中文（后端 / 前端 / 部署…），不能直接当 key 用
        ——曾因此把测试落位提示降级成「按板块约定落位」这句废话。
        """
        return self.id.split("-")[0]


def parse_tasks() -> list[Task]:
    """解析 TASKS.md。解析不到任务时抛错而不是静默返回空表。"""
    tasks: list[Task] = []
    tier = "T?"
    for line in TASKS_DOC.read_text(encoding="utf-8").splitlines():
        tier_match = _TIER.match(line)
        if tier_match:
            tier = tier_match.group("tier")
            continue
        row = _ROW.match(line)
        if not row:
            continue
        cells = [cell.strip() for cell in row.group("rest").split("|")]
        if len(cells) < 5:
            continue
        board, status, title, criteria, deps = cells[:5]  # noqa: PLW2901 - 解包即用
        tasks.append(Task(row.group("id"), board, status, title, criteria, deps, tier))
    if len(tasks) < 80:
        raise SystemExit(
            f"[task_runner] 只从 {TASKS_DOC} 解析到 {len(tasks)} 条任务，表格格式可能已变化"
        )
    return tasks


def _tool(name: str) -> str | None:
    """优先用仓库 venv 里的可执行文件（保证与 CI 逐字相同的版本）。"""
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).parent / f"{name}{suffix}"
    if candidate.is_file():
        return str(candidate)
    return shutil.which(name)


def _print_card(task: Task, all_tasks: dict[str, Task]) -> None:
    print("=" * 70)
    print(f"任务卡 · {task.id}  [{task.tier} · {task.board} · {task.label}]")
    print("=" * 70)
    print(f"标题：{task.title}")
    print(f"验收标准：{task.criteria}")

    deps = [d for d in re.split(r"[,，、\s]+", task.deps) if re.match(r"^[A-Z]+-\d+$", d)]
    print("\n[依赖]")
    if not deps:
        print("  无")
    for dep in deps:
        other = all_tasks.get(dep)
        if other is None:
            print(f"  ! {dep} —— TASKS.md 中找不到该任务（多半是笔误）")
        elif other.status == DONE:
            print(f"  OK {dep} 已完成（{other.title}）")
        else:
            print(f"  ! {dep} 尚未完成（{other.label}：{other.title}）—— 依赖未就绪，建议先做它")

    print("\n[第 1 步 · 先写会失败的测试]")
    print(f'  测试打标记：@pytest.mark.task("{task.id}")（或 pytestmark 列表里加一项）')
    print(f"  落位：{TEST_HOME.get(task.board_code, '按板块约定落位')}")
    print("  理由：C10 门禁（tests/contract/test_task_coverage.py）会断言 —— 被勾成 DONE")
    print("        的任务必须存在带该标记的测试，否则 CI 红。顺序：先测试 → 再实现 → 最后勾选。")

    print("\n[第 2 步 · 实现]  SSOT 与同步义务（改这些必须同批改文档，否则门禁红）")
    for hint in SSOT_HINTS:
        print(f"  - {hint}")

    print("\n[第 3 步 · 验证]")
    print("  python scripts/task_runner.py verify        # 完整门禁链，一条命令")
    print("  python -m pytest tests -q -m contract       # 只跑契约测试（快）")

    print("\n[第 4 步 · 提交]")
    print(f"  feat({task.board_code.lower()}): {task.id} {task.title} —— <一句话说清做了什么>")
    print("  禁止 --no-verify：绕过门禁正是本项目从上一次失败经历里学到的唯一教训。")


def _run_step(name: str, argv: list[str]) -> bool:
    started = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.monotonic() - started
    if proc.returncode == 0:
        print(f"  [ OK ] {name}（{elapsed:.1f}s）")
        return True
    print(f"  [FAIL] {name}（{elapsed:.1f}s）rc={proc.returncode}")
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
    for line in tail:
        print(f"         {line}")
    return False


def cmd_verify(_: argparse.Namespace) -> int:
    python = sys.executable
    ruff = _tool("ruff")
    pyright = _tool("pyright")
    missing = [name for name, tool in (("ruff", ruff), ("pyright", pyright)) if tool is None]
    if missing:
        print(f"[task_runner] 未找到 {missing} —— 请确认在仓库 venv 内运行（见 README 快速开始）")
        return 1

    basetemp = Path(tempfile.gettempdir()) / "aids-task-runner-pytest"
    steps: list[tuple[str, list[str]]] = [
        ("文档一致性门禁", [python, "docs/tools/gen_data_dictionary.py", "--check"]),
        ("依赖清单一致性", [python, "scripts/gen_requirements.py", "--check"]),
        ("依赖快照一致性", [python, "scripts/gen_constraints.py", "--check"]),
        ("ORM 模型一致性", [python, "scripts/gen_orm_models.py", "--check"]),
        ("ruff format", [str(ruff), "format", "--check", *RUFF_TARGETS]),
        ("ruff lint", [str(ruff), "check", *RUFF_TARGETS]),
        ("pyright（basic）", [str(pyright)]),
        ("全量测试", [python, "-m", "pytest", "tests", "-q", f"--basetemp={basetemp}"]),
    ]

    print("=" * 70)
    print("门禁链（与 CI 的 L2 同序；L3 的镜像/容器冒烟由 CI 执行）")
    print("=" * 70)
    failures = [name for name, argv in steps if not _run_step(name, argv)]

    print("-" * 70)
    if failures:
        print(f"FAIL  失败 {len(failures)}/{len(steps)}：{failures}")
        return 1
    print(f"PASS  {len(steps)} 项全部通过 —— 可以提交（提交前请确认 TASKS.md 状态与任务卡一致）")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    tasks = parse_tasks()
    if args.tier:
        tasks = [t for t in tasks if t.tier.upper() == args.tier.upper()]
    if args.todo_only:
        tasks = [t for t in tasks if t.status != DONE]
    for task in tasks:
        print(f"  {task.label:<7} {task.id:<9} [{task.tier}] {task.title}")
    print(f"\n共 {len(tasks)} 项")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    tasks = {task.id: task for task in parse_tasks()}
    task = tasks.get(args.task_id.upper())
    if task is None:
        print(f"[task_runner] TASKS.md 中不存在任务 {args.task_id}（注意大小写与连字符）")
        return 1
    _print_card(task, tasks)
    return 0


def main() -> int:
    tolerate_console_encoding()
    parser = argparse.ArgumentParser(description="AIDS 任务卡与门禁执行器")
    sub = parser.add_subparsers(dest="command", required=True)

    card = sub.add_parser("card", help="打印任务卡（依赖 + 落测试位置 + 门禁 + 提交模板）")
    card.add_argument("task_id", help="任务编号，例如 BE-03")
    card.set_defaults(func=cmd_card)

    verify = sub.add_parser("verify", help="依次跑完整门禁链")
    verify.set_defaults(func=cmd_verify)

    listing = sub.add_parser("list", help="列出任务与状态")
    listing.add_argument("--tier", help="只看某个梯次，例如 T1")
    listing.add_argument("--todo-only", action="store_true", help="只看未完成的")
    listing.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
