"""C2 扫描器：禁止状态魔法数字。

pre-commit hook 入口。用法：
    python -m tests.contract.scan_enum_magic_numbers [files...]

为什么重要：
    bysj 的教训——规格里写"4 种报表"，实现里硬编码 `== 20`
    这类数字，改需求时必然漏改。状态判定尤其致命：
    `if order.status == 20` 读起来完全不知道 20 是什么。

规则：
    ❌ if order.status == 20:              ← 状态魔法数字
    ❌ order.status = 30
    ❌ status in (10, 20)
    ✅ if order.status == OrderStatus.PAID:
    ✅ order.status = OrderStatus.SHIPPED

豁免：
    - app/domain/enums.py（枚举定义处）
    - 与状态字段无关的普通数字比较（通过字段名白名单识别状态字段）
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ALLOWED_FILENAMES = {"enums.py"}

# 状态类字段名特征（命中即认为该字段的比较/赋值需用枚举）
_STATUS_FIELD_HINTS = (
    "status",
    "_status",
    "state",
    "change_type",
    "operator_type",
    "queue_status",
    "role",
    "reason",
)

# 允许的裸数字（非状态语义）
_TRIVIAL_VALUES = {0, 1, -1}  # 0/1 太常见（布尔/计数），-1 哨兵值不拦


def _is_status_field(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _STATUS_FIELD_HINTS)


def _attr_name(node: ast.AST) -> str | None:
    """取 `obj.field` 的字段名。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan_file(path: Path) -> list[str]:
    """扫描单文件，返回违规描述列表。"""
    if path.name in _ALLOWED_FILENAMES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[str] = []

    for node in ast.walk(tree):
        # 追加：翻译/错误/成功响应中的 code 字段不算状态
        if isinstance(node, ast.Compare):
            violations.extend(_check_compare(node, path))
        elif isinstance(node, ast.Assign):
            violations.extend(_check_assign(node, path))

    return violations


def _check_compare(node: ast.Compare, path: Path) -> list[str]:
    """检查 `obj.status == 20` 这类比较。"""
    out: list[str] = []
    lhs = node.left
    field = _attr_name(lhs)
    if field and _is_status_field(field):
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, int):
                if comparator.value not in _TRIVIAL_VALUES:
                    out.append(
                        f"{path}:{node.lineno}: 状态字段 `{field}` 与魔法数字 "
                        f"{comparator.value} 比较，请改用枚举成员（如 OrderStatus.PAID）"
                    )
            elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                for elt in comparator.elts:
                    if (
                        isinstance(elt, ast.Constant)
                        and isinstance(elt.value, int)
                        and elt.value not in _TRIVIAL_VALUES
                    ):
                        out.append(
                            f"{path}:{node.lineno}: 状态字段 `{field}` 与魔法数字 "
                            f"{elt.value} 比较，请改用枚举成员"
                        )
    return out


def _check_assign(node: ast.Assign, path: Path) -> list[str]:
    """检查 `order.status = 30` 这类赋值。"""
    out: list[str] = []
    for target in node.targets:
        field = _attr_name(target)
        if (
            field
            and _is_status_field(field)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
            and node.value.value not in _TRIVIAL_VALUES
        ):
            out.append(
                f"{path}:{node.lineno}: 状态字段 `{field}` 被赋魔法数字 "
                f"{node.value.value}，请改用枚举成员"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    targets = [Path(p) for p in args] if args else _default_targets()

    violations: list[str] = []
    for target in targets:
        if target.is_dir():
            for py in sorted(target.rglob("*.py")):
                violations.extend(scan_file(py))
        elif target.suffix == ".py" and target.exists():
            violations.extend(scan_file(target))

    # 去重（同一行可能被多个 walk 路径命中）
    violations = sorted(set(violations))

    if violations:
        print("[C2 状态枚举扫描] 发现魔法数字：", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "  提示：改用 app/domain/enums.py 的枚举成员；"
            "状态枚举以 DATA-DICTIONARY.md §一 为唯一权威",
            file=sys.stderr,
        )
        return 1

    return 0


def _default_targets() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    return [project_root / "app"]


if __name__ == "__main__":
    raise SystemExit(main())
