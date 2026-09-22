"""护栏自检（S7 结构性防御的反向测试）。

为什么需要这个文件：
    "护栏误伤"与"护栏失效"是同一件事的两面——都会让门禁不再可信。
    bysj 的教训是护栏没生效；AIDS 第一轮则撞到另一面：
    `.gitignore` 的 `*/_*.py` 与 `forbid_temp_files.sh` 的 `_*`，都把
    `__init__.py` 和 `_doc_parser.py` 判成了"残留/临时文件"——
    于是**这两个文件根本无法提交**（或只能靠 `--no-verify` 绕过，
    而一旦养成绕过的习惯，整套门禁就都废了）。

本文件断言四件事：
    ① 残留文件 hook 对**当前受跟踪的全部文件**放行（不误伤自己的仓库）；
    ② 它对真正的残留形态**仍然拦得住**（不是被削成了空壳）；
    ③ 敏感文件 hook 拦 `.env`、放行 `*.example`；
    ④ **没有任何受跟踪文件命中忽略规则**（用 `git check-ignore --no-index`）。

关于 ④ 为什么必须加 `--no-index`：
    `git check-ignore` 默认**不报告已被跟踪的文件**，所以当 `*/_*.py`
    把 `app/__init__.py` 挡在版本库外时，check-ignore 看起来一切正常——
    这正是那个 bug 能长期潜伏的原因。要复现它，必须查"索引之外的世界"。

依赖：git 与 bash；非 git 环境（源码 tarball）自动 skip。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.invariant,
    pytest.mark.skipif(
        shutil.which("bash") is None, reason="护栏 hook 是 bash 脚本，本环境无 bash"
    ),
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PROJECT_ROOT / "scripts" / "hooks"


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip("当前环境不是 git 仓库（或 git 不可用），跳过护栏自检")
    files = [line for line in proc.stdout.splitlines() if line.strip()]
    if not files:
        pytest.skip("受跟踪文件为空，跳过护栏自检")
    return files


def _run_hook(hook: str, paths: list[str]) -> int:
    """按 pre-commit 的方式调用 hook（把文件名作为参数传入）。"""
    proc = subprocess.run(
        ["bash", str(HOOKS_DIR / hook), *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


def _is_ignored(path: str) -> bool:
    """该路径是否命中忽略规则（--no-index：连已跟踪文件也检查）。"""
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", path],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


class TestTempFileHook:
    """残留文件 hook：不能误伤，也不能削成空壳。"""

    def test_does_not_block_any_tracked_file(self) -> None:
        files = _tracked_files()
        assert _run_hook("forbid_temp_files.sh", files) == 0, (
            "残留文件 hook 拦下了仓库里**已受跟踪**的文件——护栏误伤。\n"
            "修复：在 scripts/hooks/forbid_temp_files.sh 的白名单里显式登记该文件"
            "（并按 docs/项目设计报告.md 的说明补理由）。"
        )

    @pytest.mark.parametrize(
        "residue",
        ["app/_probe.py", "docs/_PRDfix.md", "notes_wtest2", "old.bak", "scratch.tmp"],
    )
    def test_still_blocks_residue(self, residue: str) -> None:
        """反向用例：真正的残留必须仍然被拦（防止白名单把规则改死）。"""
        assert _run_hook("forbid_temp_files.sh", [residue]) == 1, f"残留文件未被拦下：{residue}"


class TestSensitiveFileHook:
    """敏感文件 hook：拦真密钥、放模板。"""

    @pytest.mark.parametrize("secret", [".env", "deploy/.env", "cert.pem", "app.key"])
    def test_blocks_sensitive_files(self, secret: str) -> None:
        assert _run_hook("forbid_sensitive_files.sh", [secret]) == 1, f"敏感文件未被拦下：{secret}"

    @pytest.mark.parametrize("template", [".env.example", "deploy/.env.example"])
    def test_allows_example_templates(self, template: str) -> None:
        assert _run_hook("forbid_sensitive_files.sh", [template]) == 0, (
            f"模板文件被误拦：{template}（S7 防御必须区分模板与真实密钥）"
        )


class TestNoTrackedFileIsIgnored:
    """核心回归：受跟踪的文件绝不能被忽略规则命中。

    历史事故：`.gitignore` 的 `*/_*.py` 把 `app/__init__.py` 一直挡在版本库外，
    而 `git check-ignore`（不带 --no-index）**看不到这个问题**，
    因为"已被跟踪的文件"被默认跳过。
    """

    def test_no_tracked_file_is_ignored(self) -> None:
        files = _tracked_files()
        offenders = [f for f in files if _is_ignored(f)]
        assert not offenders, (
            "以下受跟踪文件命中忽略规则——它们一旦被删除就无法重新入库：\n"
            + "\n".join(f"  {f}" for f in offenders)
            + "\n  修复：把宽泛的忽略规则（如 `_*` / `*/_*.py`）收窄为具体残留形态。"
        )
