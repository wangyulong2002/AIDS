#!/usr/bin/env python3
"""依赖版本约束生成器 / 一致性门禁（constraints.txt）。

=====================================================================
与 gen_requirements.py 的分工：
    pyproject.toml        声明      兼容范围（如 fastapi>=0.115）
    aids-*/requirements.txt  派生声明  三个镜像的安装清单（由前者生成）
    constraints.txt       快照      已验证过的精确版本（==）
=====================================================================

为什么需要快照：
    只有前两者时，每次 CI / docker build 都会装到"当时最新的满足版本"。
    代价已经真实发生过：fastapi 0.14x 改了 include_router 的返回结构
    （惰性 `_IncludedRouter`，无 `.path`），直接让两处依赖 `app.routes`
    的契约测试假阴性 —— 失败信号出现在与被改代码毫无关系的地方，
    且本地绿、CI 红，排查成本极高。

用法：
    python3 scripts/gen_constraints.py            # = --check，只校验不写盘
    python3 scripts/gen_constraints.py --write    # 按当前环境重新生成
    python3 scripts/gen_constraints.py --check -v

    更新时机：升级/新增依赖后，在**已跑通 pytest 的环境**里重跑 --write。

消费方式（关键：用 -c，不要用 -r）：
    CI:     pip install -c constraints.txt -e ".[dev]"
    Docker: pip install -r requirements.txt -c constraints.txt

    constraints 只**约束版本**、不**限定安装集合**，这是刻意选择：
    快照可能生成自 Windows，而 CI 与镜像是 Linux；若用 -r，
    平台专属依赖（如 Linux 上的 uvloop）会被漏装。

--check 的三项断言（都不做"与当前环境逐项比对"——CI 是 Linux、快照可能源自
Windows，逐项比对只会产生假阳性）：
    1. constraints.txt 存在；
    2. 每行都是 `name==version` 形态（防止被手改成 `>=` 或塞入 -e / 本地路径）；
    3. pyproject.toml 的**全部直接依赖**（含 dev extra）都在快照里 ——
       这一条防的是"新增了依赖却忘了更新快照"，且与平台无关。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CONSTRAINTS = ROOT / "constraints.txt"

HEADER = """\
# =============================================================
# 自动生成 —— 请勿手工编辑
#
# 依赖版本快照（已验证过的精确组合）
# 生成器：python3 scripts/gen_constraints.py --write
# 消费：  pip install -c constraints.txt -e ".[dev]"        （CI）
#         pip install -r requirements.txt -c constraints.txt （Docker）
#
# 用 -c 而不是 -r：constraints 只约束版本、不限定安装集合，
# 因此平台专属依赖（如 Linux 的 uvloop）不会被 Windows 快照误伤。
#
# 升级依赖后，请在**已跑通 pytest 的环境**里重跑 --write。
# =============================================================
"""

_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>\S+)$")


def _canonical(name: str) -> str:
    """PEP 503 规范化包名（大小写与 _ / . 归一）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_requirements() -> set[str]:
    """pyproject.toml 的全部直接依赖（含 dev extra），返回规范化包名集合。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)

    names: set[str] = set()
    for spec in specs:
        # "uvicorn[standard]>=0.32" → "uvicorn"
        name = re.split(r"[<>=!~\[; ]", str(spec).strip(), maxsplit=1)[0]
        if name:
            names.add(_canonical(name))
    return names


def _parse(text: str) -> tuple[dict[str, str], list[str]]:
    """解析快照，返回 ({规范化包名: 版本}, [格式非法的行])。"""
    pinned: dict[str, str] = {}
    bad: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            bad.append(line)
            continue
        pinned[_canonical(m.group("name"))] = m.group("version")
    return pinned, bad


def freeze() -> list[str]:
    """当前环境的依赖快照（排除本仓库自己的 editable 安装）。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"pip freeze 失败：{exc}") from exc

    lines = [
        ln.strip()
        for ln in proc.stdout.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("-e ")
    ]
    return sorted(lines, key=str.lower)


def check() -> int:
    failures: list[str] = []

    if not CONSTRAINTS.exists():
        print(
            "[依赖约束] 缺少 constraints.txt\n  修复：python3 scripts/gen_constraints.py --write",
            file=sys.stderr,
        )
        return 1

    pinned, bad = _parse(CONSTRAINTS.read_text(encoding="utf-8"))
    if bad:
        failures.append(
            "以下行不是 `name==version`（快照必须精确锁定）：\n"
            + "\n".join(f"    {line}" for line in bad)
        )

    missing = sorted(_direct_requirements() - set(pinned))
    if missing:
        failures.append(
            f"pyproject.toml 的直接依赖未出现在快照中：{missing}\n"
            "    （新增/升级依赖后需重跑 --write，否则 CI 与本地装的版本可能不同）"
        )

    if failures:
        print("[依赖约束] 发现问题：", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1

    print(f"[依赖约束] ok：快照 {len(pinned)} 个包，覆盖 pyproject.toml 全部直接依赖")
    return 0


def write() -> int:
    lines = freeze()
    # newline="\n" 必须保留：Windows 上 write_text 默认落 CRLF，与
    # .gitattributes 的 eol=lf 冲突，每次生成都会让 mixed-line-ending 钩子变红。
    CONSTRAINTS.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"  ✓ 已写入 {CONSTRAINTS.relative_to(ROOT)}（{len(lines)} 个包）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="依赖版本快照生成器 / 一致性门禁")
    ap.add_argument("--check", action="store_true", help="校验快照存在且覆盖直接依赖（默认）")
    ap.add_argument("--write", action="store_true", help="按当前环境重新生成快照")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印快照内容")
    args = ap.parse_args(argv)

    if args.verbose and CONSTRAINTS.exists():
        for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.strip().startswith("#"):
                print("   ", line)

    if args.write:
        return write()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
