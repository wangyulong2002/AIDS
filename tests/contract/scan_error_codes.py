"""C4 扫描器：禁止在业务代码里硬编码错误码数字。

pre-commit hook 入口。用法：
    python -m tests.contract.scan_error_codes [files...]

为什么需要扫描而非仅靠测试：
    测试只能覆盖"被调用的代码路径"。硬编码的错误码可能藏在
    罕见的异常分支里，直到线上才暴露——那时前端拿到未知码，
    用户看到的是"系统繁忙"。

规则：
    ❌ raise BizError(code=10001, ...)          ← 关键字参数裸数字
    ❌ return {"code": 40002}                   ← 字典裸数字
    ❌ fail(10001, "参数错误")                   ← 位置参数裸数字
    ❌ BizError(40002)                          ← 构造式位置参数
    ❌ err_code = 50001                         ← 赋值给 code 类变量
    ✅ raise BizError(code=CommonError.PARAM_INVALID, ...)
    ✅ raise BizError(code=int(OrderError.ORDER_NOT_FOUND), ...)

豁免：
    - ``app/core/errors.py``（错误码定义处本身）——按**相对路径**豁免
    - HTTP 状态码（200/401/403/429/500）——不落在 5 位业务码区间，天然豁免

区间：``_CODE_MIN ~ _CODE_MAX`` 必须覆盖 ``app.core.errors.ERROR_SEGMENTS``
的全部段位（1xxxx ~ 9xxxx）。由 ``test_error_codes.py`` 断言两者一致——
历史教训：区间曾止于 89999，导致 9xxxx 段的裸数字**永远漏检**。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TypeGuard

from tests.contract._targets import scan_targets

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 允许出现裸错误码数字的文件——**按相对路径**豁免，不按文件名。
# 历史教训：曾写作 `path.name in {"errors.py"}`，导致任何
# `app/**/errors.py`（如 app/modules/order/errors.py）都自动免疫。
_ALLOWED_RELATIVE = "app/core/errors.py"

# 业务错误码区间（API.md §1.2：1xxxx ~ 9xxxx，覆盖 ERROR_SEGMENTS 全部段位）
_CODE_MIN = 10001
_CODE_MAX = 99999

# 被视为"错误码字段名"的关键字参数 / 字典键 / 变量名
_CODE_FIELD_NAMES = {"code", "err_code", "error_code"}

# 疑似"抛错/返回错误"的函数名特征——用于识别位置参数写法（fail(10001) / BizError(40002)）
_ERROR_CALL_HINTS = ("error", "fail", "exception", "raise")


def _is_allowed(path: Path) -> bool:
    """错误码定义处本身豁免（按相对路径判定）。"""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix() == _ALLOWED_RELATIVE
    except (ValueError, OSError):
        return path.as_posix().endswith(_ALLOWED_RELATIVE)


def _looks_like_error_code(value: object) -> TypeGuard[int]:
    """判断是否为业务错误码。

    用 ``TypeGuard`` 而非 ``bool``：判断通过后 pyright 会把
    ``ast.Constant.value`` 收窄为 ``int``，调用侧不必写 ``int(...)`` 强转
    ——强转正是此前 6 处类型报错的来源。
    """
    return (
        isinstance(value, int) and not isinstance(value, bool) and _CODE_MIN <= value <= _CODE_MAX
    )


def _is_code_field(name: str) -> bool:
    return name.lower() in _CODE_FIELD_NAMES


def _dotted_name(node: ast.AST) -> str | None:
    """取 `foo.bar` / `foo["bar"]` / `bar` 里的最末一段名字（用于字段名判定）。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _looks_like_error_call(node: ast.Call) -> bool:
    """调用名是否像"构造/抛出业务错误"：fail(...) / BizError(...) / XxxException(...)。"""
    low = _call_name(node).lower()
    return any(hint in low for hint in _ERROR_CALL_HINTS)


def _violation(path: Path, lineno: int, code: int, how: str) -> str:
    return (
        f"{path}:{lineno}: 硬编码错误码 {code}（{how}），"
        f"请改用 app.core.errors 中的枚举常量（如 CommonError.PARAM_INVALID）"
    )


def _check_constant_target(node: ast.AST, path: Path) -> list[str]:
    """赋值/注解赋值给 code 类变量：`err_code = 50001`。"""
    if isinstance(node, ast.Assign):
        targets = node.targets
        value = node.value
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
        value = node.value
    else:
        return []
    if value is None or not (
        isinstance(value, ast.Constant) and _looks_like_error_code(value.value)
    ):
        return []
    out: list[str] = []
    for target in targets:
        name = _dotted_name(target)
        if name and _is_code_field(name):
            out.append(_violation(path, node.lineno, int(value.value), f"赋值给 {name}"))
    return out


def scan_file(path: Path) -> list[str]:
    """扫描单个 Python 文件，返回违规描述列表。"""
    if _is_allowed(path):
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
    for node in ast.walk(tree):
        # ① 关键字参数：fail(code=10001)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg
                    and _is_code_field(kw.arg)
                    and isinstance(kw.value, ast.Constant)
                    and _looks_like_error_code(kw.value.value)
                ):
                    violations.append(
                        _violation(
                            path, kw.value.lineno, int(kw.value.value), f"{kw.arg}= 关键字参数"
                        )
                    )
            # ② 位置参数：fail(10001) / BizError(40002)（仅当调用名像错误构造/抛出）
            if _looks_like_error_call(node):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and _looks_like_error_code(arg.value):
                        violations.append(_violation(path, arg.lineno, int(arg.value), "位置参数"))
        # ③ 字典字面量：{"code": 40002}
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _is_code_field(key.value)
                    and isinstance(value, ast.Constant)
                    and _looks_like_error_code(value.value)
                ):
                    violations.append(
                        _violation(path, value.lineno, int(value.value), f'{{"{key.value}": ...}}')
                    )
        # ④ 赋值：err_code = 50001
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            violations.extend(_check_constant_target(node, path))

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

    if violations:
        print("[C4 错误码扫描] 发现硬编码错误码：", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("  提示：改用 app.core.errors 的枚举常量，新增码须同步 docs/API.md", file=sys.stderr)
        return 1

    return 0


def _default_targets() -> list[Path]:
    # 扫描面由 tests/contract/_targets.py 统一定义：共享层 + 全部服务包。
    # 不要在本地再写死目录列表——那正是"新增服务后无人检查"的成因。
    return scan_targets()


if __name__ == "__main__":
    raise SystemExit(main())
