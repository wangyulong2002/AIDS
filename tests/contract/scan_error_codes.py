"""C4 扫描器：禁止在业务代码里硬编码错误码数字。

pre-commit hook 入口。用法：
    python -m tests.contract.scan_error_codes [files...]

为什么需要扫描而非仅靠测试：
    测试只能覆盖"被调用的代码路径"。硬编码的错误码可能藏在
    罕见的异常分支里，直到线上才暴露——那时前端拿到未知码，
    用户看到的是"系统繁忙"。

规则：
    ❌ raise BizError(code=10001, ...)          ← 裸数字
    ❌ return {"code": 40002}                   ← 裸数字
    ✅ raise BizError(code=CommonError.PARAM_INVALID, ...)
    ✅ raise BizError(code=int(OrderError.ORDER_NOT_FOUND), ...)

豁免：
    - app/core/errors.py（错误码定义处本身）
    - HTTP 状态码（200/401/403/429/500）——不落在 5 位业务码区间，天然豁免
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# 允许出现裸错误码数字的文件（错误码定义处本身）
_ALLOWED_FILENAMES = {"errors.py"}

# 业务错误码区间（API.md §1.2：1xxxx ~ 8xxxx）
_CODE_MIN = 10001
_CODE_MAX = 89999

# 被视为"错误码字段名"的关键字参数 / 字典键
_CODE_FIELD_NAMES = {"code", "err_code", "error_code"}


def _looks_like_error_code(value: int) -> bool:
    return _CODE_MIN <= value <= _CODE_MAX


def _in_error_context(parent: ast.AST, child: ast.AST) -> bool:
    """判断常量 child 是否作为错误码字段值出现在 parent 下。"""
    if isinstance(parent, ast.keyword):
        return parent.arg in _CODE_FIELD_NAMES
    if isinstance(parent, ast.Dict):
        return any(
            value is child and isinstance(key, ast.Constant) and key.value in _CODE_FIELD_NAMES
            for key, value in zip(parent.keys, parent.values, strict=False)
        )
    return False


def scan_file(path: Path) -> list[str]:
    """扫描单个 Python 文件，返回违规描述列表。"""
    if path.name in _ALLOWED_FILENAMES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []  # 语法错误由 ruff / 编译器负责报告

    violations: list[str] = []
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if not (isinstance(child, ast.Constant) and isinstance(child.value, int)):
                continue
            if not _looks_like_error_code(child.value):
                continue
            if _in_error_context(parent, child):
                violations.append(
                    f"{path}:{child.lineno}: 硬编码错误码 {child.value}，"
                    f"请改用 app.core.errors 中的枚举常量（如 CommonError.PARAM_INVALID）"
                )
    return violations


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

    if violations:
        print("[C4 错误码扫描] 发现硬编码错误码：", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("  提示：改用 app.core.errors 的枚举常量，新增码须同步 docs/API.md", file=sys.stderr)
        return 1

    return 0


def _default_targets() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    return [project_root / "app"]


if __name__ == "__main__":
    raise SystemExit(main())
