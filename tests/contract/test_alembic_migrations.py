"""Alembic 迁移目录的静态卫生（BE-02 / DEP-04）。

为什么需要这个门禁（它守的是"迁移链"而不是"某条迁移"）：
    迁移是**唯一一类"写错了要等真出事才发现"的代码** —— 建表 SQL 写错，
    第一次 `upgrade` 就报错；但"忘了写 downgrade""两个 revision 互相指向"
    这类问题，平时完全无感，只在需要回滚的那天才爆出来（而那天通常已经很忙）。

    故这里把三件**可静态判定**的事固化成断言，全部不连数据库：

      ① 迁移脚本能解析、且 `revision` / `down_revision` 是可判定的字面量；
      ② 迁移链**有且只有一个 head**（两个 head = 分支未合并，Alembic 会拒绝升级）、
         且不存在环；
      ③ 每个 revision 的 `downgrade()` **真的实现了** ——
         不是 `pass`，也不是模板留下的 `raise NotImplementedError`。
         这正是 docs/工程化门禁方案.md §5（S5）的要求。

    不在此处断言的部分：`upgrade → downgrade → upgrade` 的**实跑**需要数据库，
    由 CI 的 contract job（带 MySQL）覆盖；本文件是它在无库环境下的下限保障。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.contract, pytest.mark.task("BE-02")]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = PROJECT_ROOT / "aids-backend" / "alembic" / "versions"

# 模板 script.py.mako 对未填写的 downgrade 会生成这一句（而不是 Alembic 默认的 pass），
# 目的就是"让忘了写回滚在第一次执行时暴露"。此处按同一标记判它没写完。
_UNIMPLEMENTED = "NotImplementedError"


def _revision_files() -> list[Path]:
    """迁移脚本（排除 README 等非 .py）。"""
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")


def _assignments(tree: ast.Module) -> dict[str, object]:
    """取出模块级 `revision` / `down_revision` 等字面量赋值。"""
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        value = node.value
        if value is None or not isinstance(value, ast.Constant):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _downgrade_body(tree: ast.Module) -> list[ast.stmt] | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            return node.body
    return None


class TestMigrationChain:
    def test_versions_dir_exists(self) -> None:
        """目录必须在。它一旦被删，下面每一条都会"空转通过"（门禁变装饰）。"""
        assert VERSIONS_DIR.is_dir(), f"迁移目录不存在：{VERSIONS_DIR}"

    def test_has_at_least_one_revision(self) -> None:
        """DEP-04 的纳管已落地 —— 没有迁移时本文件应当提醒，而不是静默全绿。"""
        files = _revision_files()
        assert files, (
            "aids-backend/alembic/versions/ 下没有任何迁移脚本。\n"
            "若是有意清空（例如尚未纳管 DDL），请连同本测试与 DEP-04 的说明一起改；\n"
            "不要让它静静地退化成空跑。"
        )

    def test_single_head_and_acyclic(self) -> None:
        """有且只有一个 head，且迁移链无环。"""
        revisions: dict[str, str | None] = {}
        for path in _revision_files():
            data = _assignments(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            rev = data.get("revision")
            assert isinstance(rev, str) and rev, f"{path.name} 缺少字符串常量 revision"
            down = data.get("down_revision")
            assert down is None or isinstance(down, str), (
                f"{path.name} 的 down_revision 不是字面量字符串（无法静态判定）"
            )
            revisions[rev] = down

        parents = {p for p in revisions.values() if p}
        unknown = sorted(parents - set(revisions))
        assert not unknown, (
            f"down_revision 指向不存在的 revision（多半是删了文件没改链）：{unknown}"
        )

        heads = sorted(set(revisions) - parents)
        assert len(heads) == 1, (
            f"期望恰好 1 个 head，实际 {len(heads)} 个：{heads}\n"
            "两个 head 意味着有未合并的迁移分支 —— Alembic 会直接拒绝 upgrade。"
        )

        # 环检测：从 head 沿 down_revision 走
        seen: set[str] = set()
        cursor: str | None = heads[0]
        while cursor is not None:
            assert cursor not in seen, f"迁移链存在环，重复经过 {cursor}"
            seen.add(cursor)
            cursor = revisions.get(cursor)
        assert seen == set(revisions), (
            f"有 revision 不在 head 的祖先链上（游离节点）：{sorted(set(revisions) - seen)}"
        )


class TestDowngradeImplemented:
    """S5：每个 revision 都必须能回滚。"""

    @pytest.mark.parametrize("path", _revision_files(), ids=lambda p: p.name)
    def test_downgrade_is_implemented(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        body = _downgrade_body(tree)
        assert body is not None, f"{path.name} 没有 downgrade() 函数"

        # 剔除 docstring —— 只有 docstring 的函数等价于空实现
        effective = [
            stmt
            for stmt in body
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        ]
        assert effective, f"{path.name} 的 downgrade() 是空实现（只有 pass 或只有 docstring）"

        placeholder = [
            stmt
            for stmt in effective
            if isinstance(stmt, ast.Raise)
            and isinstance(stmt.exc, ast.Call)
            and isinstance(stmt.exc.func, ast.Name)
            and stmt.exc.func.id == _UNIMPLEMENTED
        ]
        assert not placeholder, (
            f"{path.name} 的 downgrade() 仍是模板占位（raise {_UNIMPLEMENTED}）"
            "—— 请按 docs/工程化门禁方案.md §5 真正实现回滚"
        )
