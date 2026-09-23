"""C2 扫描器：禁止状态魔法数字。

pre-commit hook 入口。用法：
    python -m tests.contract.scan_enum_magic_numbers [files...]

为什么重要：
    bysj 的教训——规格里写"4 种报表"，实现里硬编码 `== 20`
    这类数字，改需求时必然漏改。状态判定尤其致命：
    `if order.status == 20` 读起来完全不知道 20 是什么。

规则（覆盖 6 种写法）：
    ❌ if order.status == 20:              ← 属性比较
    ❌ if payment.status == 0:             ← 0/1 也是枚举值，不再豁免
    ❌ order.status = 30                   ← 属性赋值
    ❌ status == 30 / d["status"] == 30    ← 裸名 / 下标
    ❌ Order(status=20) / q.filter(status=20)  ← 关键字参数
    ❌ d["status"] = 30 / order.status += 10   ← 下标赋值 / 增量
    ✅ if order.status == OrderStatus.PAID:
    ✅ order.status = OrderStatus.SHIPPED

豁免：
    - ``app/domain/enums.py``（枚举定义处）——按**相对路径**豁免，不按文件名
    - 无语义哨兵值 ``-1``（见 ``_TRIVIAL_VALUES``）
    - 行尾显式标注 ``# enum-ok``（用于确实与状态无关的同名场景，
      必须逐行标注，不接受文件级豁免）

为什么 0/1 不再豁免：
    历史实现把 0/1 当作"布尔/计数"一律放行，但 11 组枚举中有 10 组
    存在值为 0 或 1 的成员（PaymentStatus.INIT=0、RefundStatus.APPLIED=0、
    MessageRole.USER=1 …）。放行 0/1 等于让这些状态判定完全脱离门禁。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from tests.contract._targets import scan_targets

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 枚举定义处——按相对路径豁免（与 scan_error_codes.py 同款修正）
_ALLOWED_RELATIVE = "app/domain/enums.py"

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

# 允许的裸数字：只剩无语义哨兵值
_TRIVIAL_VALUES = {-1}

# 行尾显式豁免标记
_EXEMPT_MARK = "# enum-ok"


def _is_allowed(path: Path) -> bool:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix() == _ALLOWED_RELATIVE
    except (ValueError, OSError):
        return path.as_posix().endswith(_ALLOWED_RELATIVE)


def _is_status_field(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _STATUS_FIELD_HINTS)


def _field_name(node: ast.AST) -> str | None:
    """取字段名：`obj.status` / `status` / `d["status"]`。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return None


def _status_field(node: ast.AST) -> str | None:
    """节点若表示状态字段则返回字段名，否则 None。

    全大写名字按"常量命名约定"排除：`STATUS_NOT_PAYABLE = 40003`、
    `ROLE_ADMIN = 1` 这类是**常量定义处**（枚举成员/模块常量），
    不是状态字段的读写。若不做这个排除，错误码枚举类自身会被大面积误报，
    逼得所有人往行尾加豁免标记——那等于门禁失效。
    """
    name = _field_name(node)
    if not name or name.isupper():
        return None
    if _is_status_field(name):
        return name
    return None


def _magic_constants(node: ast.AST) -> list[int]:
    """取出节点里的魔法数字（标量或元组/列表/集合元素）。"""
    if isinstance(node, ast.Constant):
        if (
            isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and node.value not in _TRIVIAL_VALUES
        ):
            return [node.value]
        return []
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[int] = []
        for elt in node.elts:
            if (
                isinstance(elt, ast.Constant)
                and isinstance(elt.value, int)
                and not isinstance(elt.value, bool)
                and elt.value not in _TRIVIAL_VALUES
            ):
                out.append(elt.value)
        return out
    return []


def _collect(tree: ast.AST) -> list[tuple[int, str]]:
    """返回 [(行号, 描述), ...]，尚未做行尾豁免过滤。"""
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # ① 比较：order.status == 20 / status == 30 / d["status"] in (10, 20)
        if isinstance(node, ast.Compare):
            others: list[ast.AST] = list(node.comparators)
            field = _status_field(node.left)
            if field is None:
                for comparator in node.comparators:
                    if (field := _status_field(comparator)) is not None:
                        others = [node.left]
                        break
            if field:
                for other in others:
                    for value in _magic_constants(other):
                        found.append(
                            (
                                node.lineno,
                                f"状态字段 `{field}` 与魔法数字 {value} 比较，"
                                f"请改用枚举成员（如 OrderStatus.PAID）",
                            )
                        )

        # ② 赋值：order.status = 30 / d["status"] = 30
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                field = _status_field(target)
                if field:
                    for value in _magic_constants(node.value):
                        found.append(
                            (
                                node.lineno,
                                f"状态字段 `{field}` 被赋魔法数字 {value}，请改用枚举成员",
                            )
                        )

        # ③ 注解赋值：status: int = 30
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            field = _status_field(node.target)
            if field:
                for value in _magic_constants(node.value):
                    found.append(
                        (
                            node.lineno,
                            f"状态字段 `{field}` 被赋魔法数字 {value}，请改用枚举成员",
                        )
                    )

        # ④ 增量赋值：order.status += 10
        elif isinstance(node, ast.AugAssign):
            field = _status_field(node.target)
            if field:
                for value in _magic_constants(node.value):
                    found.append(
                        (
                            node.lineno,
                            f"状态字段 `{field}` 与魔法数字 {value} 运算，请改用枚举成员",
                        )
                    )

        # ⑤ 海象赋值：(status := 30)
        elif isinstance(node, ast.NamedExpr):
            field = _status_field(node.target)
            if field:
                for value in _magic_constants(node.value):
                    found.append(
                        (
                            node.lineno,
                            f"状态字段 `{field}` 被赋魔法数字 {value}，请改用枚举成员",
                        )
                    )

        # ⑥ 关键字参数：Order(status=20) / query.filter(queue_status=3)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and _is_status_field(kw.arg):
                    for value in _magic_constants(kw.value):
                        found.append(
                            (
                                kw.value.lineno,
                                f"状态字段 `{kw.arg}=` 传入魔法数字 {value}，请改用枚举成员",
                            )
                        )

    return found


def scan_file(path: Path) -> list[str]:
    """扫描单文件，返回违规描述列表。"""
    if _is_allowed(path):
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    lines = source.splitlines()
    violations: list[str] = []
    for lineno, message in _collect(tree):
        # 行尾 `# enum-ok` 显式豁免（逐行生效，可用注释说明理由）
        if 1 <= lineno <= len(lines) and _EXEMPT_MARK in lines[lineno - 1]:
            continue
        violations.append(f"{path}:{lineno}: {message}")

    # 去重（同一行可能被多条规则命中）
    return sorted(set(violations))


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

    violations = sorted(set(violations))

    if violations:
        print("[C2 状态枚举扫描] 发现魔法数字：", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "  提示：改用 app/domain/enums.py 的枚举成员；"
            "状态枚举以 DATA-DICTIONARY.md §一 为唯一权威；"
            f"确属无关的同名场景可在行尾加 {_EXEMPT_MARK}",
            file=sys.stderr,
        )
        return 1

    return 0


def _default_targets() -> list[Path]:
    # 扫描面由 tests/contract/_targets.py 统一定义：共享层 + 全部服务包。
    # 不要在本地再写死目录列表——那正是"新增服务后无人检查"的成因。
    return scan_targets()


if __name__ == "__main__":
    raise SystemExit(main())
