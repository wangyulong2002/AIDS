"""任务验收门禁 —— 「任务已完成」必须对应一个真实存在的测试。

为什么需要（B3 前置条件）：
    本项目最容易被误读的一点：现有门禁是「文档 ↔ 代码」**形式一致性**门禁
    （表数/字段/枚举/响应体/错误码），它**不判断业务对错**。
    真实教训：BE-02 的模型生成器曾因正则写成 `[^,]*` 漏掉 10 个含逗号的列
    （419 → 409），而 `gen_orm_models.py --check` 全程绿灯——门禁全绿，产物是错的。
    所以「铁律 1：每写一个约束性文档章节，旁边立刻配一个会失败的测试」
    必须机械化，否则它只是一句口号。

本门禁的规则（单向且极简）：
    `docs/TASKS.md` 中**状态为 ✅ 且属代码类板块（BE/FE/AI/MOCK）**的任务，
    必须至少存在一个 `@pytest.mark.task("<任务号>")` 的测试。

    反过来不成立（刻意）：先写带标记的测试、再把任务勾成 ✅ 是被**鼓励**的
    ——那正是 TDD 的方向。故不要求「标记必须已 ✅」。

三个防退化断言（门禁自身也会腐烂，必须自检）：
    ① 解析器健康自检 —— 解析不到任务表时，主断言会空转全绿；
    ② 标记必须指向真实存在的任务号 —— 否则 `BE-003` 这类笔误能骗过一切；
    ③ 豁免清单反向校验 —— 登记为豁免的任务若已经有测试，必须从清单里删掉。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASKS_DOC = PROJECT_ROOT / "docs" / "TASKS.md"
TESTS_DIR = PROJECT_ROOT / "tests"

# 代码类板块：有代码交付就必须有测试。
# DOC-* 是文档交付物，DEP-* 是部署产物（由文档门禁 / CI 的镜像 job 覆盖），
# 二者不适用本规则，故在此排除而不是逐个登记豁免。
CODE_BOARDS = frozenset({"BE", "FE", "AI", "MOCK"})

DONE = "✅"

# 已验收但确实无法用测试覆盖的任务：{任务号: 理由}
# 登记项必须**真的**没有 task 标记，否则 test_exemptions_are_not_stale 会红。
EXEMPT: dict[str, str] = {}

_ROW = re.compile(
    r"^\|\s*(?P<id>[A-Z]+-\d+)\s*\|[^|]*\|\s*(?P<status>✅|⏳|☐|🚫)\s*\|",
)


def _task_status() -> dict[str, str]:
    """从 `docs/TASKS.md` 解析 {任务号: 状态}。不抄文档内容，直接读权威来源。"""
    status: dict[str, str] = {}
    for line in TASKS_DOC.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            status[match.group("id")] = match.group("status")
    return status


def _is_pytest_mark_task(func: ast.expr) -> bool:
    """判断调用目标是否为 `pytest.mark.task`。"""
    parts: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return parts == ["task", "mark", "pytest"]


def _marked_task_ids() -> set[str]:
    """扫描 tests/ 收集全部 `pytest.mark.task("...")` 的任务号（AST，非文本匹配）。"""
    found: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_pytest_mark_task(node.func):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    found.add(value)
    return found


# =====================================================================
# 一、主断言
# =====================================================================


def test_every_completed_code_task_has_a_tagged_test() -> None:
    """✅ 的代码类任务必须被 `@pytest.mark.task("<任务号>")` 覆盖。

    出现失败时的正确修法：给该任务的验收标准补一个测试并打上标记
    （**不要**把任务状态改回去，也不要往 EXEMPT 里塞——那正是本门禁要防的）。
    """
    status = _task_status()
    marked = _marked_task_ids()

    missing = sorted(
        f"{task_id}（{status[task_id]}）"
        for task_id in status
        if task_id.split("-")[0] in CODE_BOARDS
        and status[task_id] == DONE
        and task_id not in marked
        and task_id not in EXEMPT
    )

    assert not missing, (
        f"以下任务在 TASKS.md 已标 ✅，但没有任何带 task 标记的测试：{missing}\n"
        f'修法：在覆盖其验收标准的测试上加 @pytest.mark.task("<任务号>")；'
        f"若确实无法测试，登记到 tests/contract/test_task_coverage.py 的 EXEMPT 并写明理由。"
    )


# =====================================================================
# 二、门禁自检（防「门禁自己在空转」）
# =====================================================================


def test_parser_sees_the_task_table() -> None:
    """解析器健康自检。

    没有它，一旦 TASKS.md 的表格格式变化（比如多了个空列），`_task_status()`
    会返回空字典，主断言随即**空转通过**——门禁变成装饰。
    期望值锚点是 VERSIONS.md §二 #8（任务总数），此处只做下界校验，
    精确的 97 与文档门禁（gen_data_dictionary.py）保持一致，不在此重复维护。
    """
    status = _task_status()
    assert len(status) >= 80, (
        f"只从 docs/TASKS.md 解析到 {len(status)} 条任务，表格格式可能已变化 —— 本门禁正在空转"
    )
    assert DONE in status.values(), "解析结果里一个 ✅ 都没有，解析规则疑似失效"


def test_markers_point_to_real_tasks() -> None:
    """标记里的任务号必须真实存在（防 `BE-003` 这类笔误骗过一切）。"""
    status = _task_status()
    unknown = sorted(t for t in _marked_task_ids() if t not in status)
    assert not unknown, f"以下 task 标记在 docs/TASKS.md 中找不到对应任务（多半是笔误）：{unknown}"


def test_exemptions_are_not_stale() -> None:
    """豁免清单反向校验：已有测试的任务不得继续挂在豁免里。"""
    marked = _marked_task_ids()
    stale = sorted(t for t in EXEMPT if t in marked)
    assert not stale, f"以下任务已有 task 标记，请从 EXEMPT 中移除：{stale}"
