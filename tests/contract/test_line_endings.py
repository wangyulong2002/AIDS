"""行尾口径契约测试（BE-37）—— 「口径存在」与「现状干净」都要能被机器检查。

为什么需要（真实顽疾，症状链）：
    Windows 侧 git 默认 `core.autocrlf=true` —— 文件以 CRLF 检出，而编辑器 / AI 写入
    的是 LF，于是**同一个文件同时含 CRLF 与 LF**。后果是链式的：

        ① pre-commit 的 `mixed-line-ending` 每次都要"修正"，而它修的就是行尾；
        ② 每次修正都把 diff 变成「所有行都改了」，功能改动被行尾噪音淹没；
        ③ `git add` 时 autocrlf 又把 CRLF 压成 LF，与库内既有 blob 不一致 ——
           于是"改一行"表现为"整个文件重写"；
        ④ 提交因此反复被拦下（实测能连拦 3 轮，见 HANDOFF §6）。

    根治手段是**把口径写进仓库**：`.gitattributes` 显式 `* text=auto eol=lf`，
    与开发者本机的 `core.autocrlf` 解耦。

为什么不能只靠 `.gitattributes`：
    它有被删、被改、被"临时注释掉试试"的可能。一旦消失，顽疾会以完全相同的方式
    复发，而且**不会有任何测试或门禁发现** —— 只是"这次提交又卡住了"，然后人再花
    一小时重新定位。所以本文件把两件事都固化成断言：
      - 口径文件本身还在、且规则正确（防删改）；
      - 当前受跟踪文本文件的行尾确实干净（防"已经混进来了"）。

为什么不直接查 git 里的 blob：
    `git cat-file --batch` 逐 blob 校验需要额外解析、且要处理 `-z` 路径编码，收益不高；
    而 `eol=lf` 生效后 checkout 与 index 本来就是同一口径，**扫磁盘等价于扫 index**，
    且更快、更少依赖。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GITATTRIBUTES = PROJECT_ROOT / ".gitattributes"

# 与 .gitattributes 的 binary 声明保持一致：这些扩展名不做行尾检查
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".jar",
        ".zip",
        ".gz",
    }  # fmt: skip
)


def _tracked_files() -> list[str]:
    """`git ls-files`（-z 避免非 ASCII 路径被转义加引号）。"""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


def _text_files() -> list[str]:
    return [p for p in _tracked_files() if Path(p).suffix.lower() not in _BINARY_SUFFIXES]


def _line_endings(raw: bytes) -> tuple[int, int]:
    """(CRLF 数, 裸 LF 数)。裸 LF = 前面不是 CR 的 LF。"""
    crlf = raw.count(b"\r\n")
    return crlf, raw.count(b"\n") - crlf


# =====================================================================
# 一、口径本身：.gitattributes 必须存在且规则正确（防被删/被改）
# =====================================================================


@pytest.mark.task("BE-37")
def test_gitattributes_exists() -> None:
    assert GITATTRIBUTES.is_file(), (
        ".gitattributes 不存在 —— 行尾口径退回「依赖开发者本机 core.autocrlf」的状态，"
        "mixed-line-ending 与整文件 diff 的顽疾会原样复发（HANDOFF §6 / §3.4）"
    )


@pytest.mark.task("BE-37")
def test_default_rule_pins_lf_for_text_files() -> None:
    """必须有一条对 `*` 生效、且 `eol=lf` 的规则。

    这是整个根治方案的**唯一承重点**：没有它，工作区是 CRLF、diff 是全行改写、
    提交被反复拦下。
    """
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    rules = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    star_rules = [r for r in rules if r.split()[0] == "*"]
    assert star_rules, ".gitattributes 里没有对 `*` 生效的规则 —— 文本文件的行尾无人管"
    assert any("eol=lf" in r for r in star_rules), (
        f"`*` 规则未声明 eol=lf：{star_rules} —— 这一步不做，其余断言都只是巧合"
    )
    assert any("text=auto" in r or re.search(r"\btext\b(?!\s*=)", r) for r in star_rules), (
        f"`*` 规则缺少 text 属性（text=auto 或 text）：{star_rules} —— 没有 text 属性时 eol 不生效"
    )


@pytest.mark.task("BE-37")
def test_shell_scripts_are_pinned_to_lf() -> None:
    """`*.sh` 必须显式 eol=lf：它们要在 Linux 容器里执行，CRLF 会 bad interpreter。"""
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    assert re.search(r"^\s*\*\.sh\s+.*eol=lf", text, re.M), (
        "`.gitattributes` 未把 *.sh 钉为 eol=lf —— 脚本进容器会因 CRLF 报 bad interpreter"
    )


# =====================================================================
# 二、现状：受跟踪文本文件不得混用行尾
# =====================================================================


@pytest.mark.task("BE-37")
def test_no_tracked_text_file_has_mixed_line_endings() -> None:
    """核心断言：**不允许任何受跟踪文本文件同时含 CRLF 与 LF**。

    混合行尾正是顽疾的物理形态（hook 要"修正"、diff 要整文件重写）。
    纯 CRLF 或纯 LF 都放过 —— 前者在 eol=lf 生效后不该再出现，但即使出现也不会
    触发 hook；只有**混合**才会让工具链互相打架。
    """
    mixed: list[str] = []
    for rel in _text_files():
        path = PROJECT_ROOT / rel
        if not path.is_file():
            continue
        crlf, lf = _line_endings(path.read_bytes())
        if crlf and lf:
            mixed.append(f"{rel}（CRLF {crlf} 行 / LF {lf} 行）")
    assert not mixed, (
        "以下受跟踪文件混用了行尾，会触发 mixed-line-ending → 整文件 diff → 提交被拦：\n  "
        + "\n  ".join(mixed[:20])
        + f"\n共 {len(mixed)} 个。修法：`pre-commit run --all-files` 后重新 `git add`。"
    )


@pytest.mark.task("BE-37")
def test_shell_scripts_contain_no_crlf() -> None:
    """脚本文件连"纯 CRLF"也不允许 —— 容器里 shebang 为 `#!/bin/sh\\r` 会直接失败。"""
    broken: list[str] = []
    for rel in _tracked_files():
        if not rel.endswith(".sh"):
            continue
        crlf, _ = _line_endings((PROJECT_ROOT / rel).read_bytes())
        if crlf:
            broken.append(f"{rel}（{crlf} 处 CRLF）")
    assert not broken, f"以下脚本含 CRLF，进容器会 bad interpreter：{broken}"
