#!/usr/bin/env python3
"""依赖清单生成器 / 一致性门禁。

=====================================================================
单一来源原则：pyproject.toml 是**唯一**的依赖声明处。
=====================================================================

本脚本把 ``pyproject.toml`` 的 ``[project].dependencies`` 同步到三个
服务镜像构建时需要的清单文件：

    aids-backend/requirements.txt
    aids-ai/requirements.txt
    aids-mock/requirements.txt

为什么需要它（而不是手工维护三份清单）：
    Dockerfile 会 ``COPY aids-*/requirements.txt``，如果三份清单靠手写，
    "改了一处忘了另外两处"是必然发生的——镜像里装什么版本将取决于
    谁最后动了哪个文件。这正是本项目反复要根治的漂移类问题。

用法：
    python3 scripts/gen_requirements.py            # = --check，只校验不写盘
    python3 scripts/gen_requirements.py --write    # 按 pyproject.toml 重新生成
    python3 scripts/gen_requirements.py --check -v # 逐项打印

门禁：
    - pre-commit 钩子 requirements-sync
    - CI 的 consistency job
    任何手工编辑 aids-*/requirements.txt 的行为都会让门禁变红。

已知边界（T0 现状，非缺陷）：
    三个服务目前共用同一份依赖清单（"项目所有服务都依赖这一个文件"）。
    T1 若需按服务裁剪（如 AI 服务不需要 aiokafka），应当在 pyproject.toml
    里拆成 optional-dependencies extra，再扩展本脚本按服务生成——
    届时仍然是"一个来源、多处派生"，不会退化成三份手写清单。
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

# 生成目标：与 deploy/app/*.Dockerfile 的构建上下文一一对应
TARGETS: tuple[str, ...] = ("aids-backend", "aids-ai", "aids-mock")

HEADER = """\
# =============================================================
# 自动生成 —— 请勿手工编辑
#
# 单一来源：pyproject.toml 的 [project].dependencies
# 生成器：  python3 scripts/gen_requirements.py --write
# 校验：    pre-commit 钩子 requirements-sync + CI consistency job
#          （手工改本文件 → 门禁立即变红）
#
# 三个服务共用同一份运行依赖（T0 约定：全项目依赖这一个来源）。
# T1 若需按服务裁剪，改 pyproject.toml 的 extra 后重新生成。
# =============================================================
"""


def read_dependencies() -> list[str]:
    """从 pyproject.toml 读取运行依赖（保序）。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    if not deps:
        raise SystemExit(f"{PYPROJECT} 里没有 [project].dependencies，无法生成依赖清单")
    return [str(d).strip() for d in deps]


def render(deps: list[str]) -> str:
    return HEADER + "\n" + "\n".join(deps) + "\n"


def check(expected: str) -> int:
    failures: list[str] = []
    for name in TARGETS:
        target = ROOT / name / "requirements.txt"
        if not target.exists():
            failures.append(f"缺少 {name}/requirements.txt（应为 pyproject.toml 生成）")
            continue
        actual = target.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(f"{name}/requirements.txt 与 pyproject.toml 不一致")

    if failures:
        print("[依赖清单一致性] 发现问题：", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
        print(
            "  修复：python3 scripts/gen_requirements.py --write"
            "（依赖只改 pyproject.toml，不要手改生成的清单）",
            file=sys.stderr,
        )
        return 1

    print(f"[依赖清单一致性] ok：{len(TARGETS)} 份清单与 pyproject.toml 一致")
    return 0


def write(expected: str) -> int:
    for name in TARGETS:
        target = ROOT / name / "requirements.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")
        print(f"  ✓ 已写入 {target.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="依赖清单生成器 / 一致性门禁")
    ap.add_argument("--check", action="store_true", help="校验清单与 pyproject.toml 一致（默认）")
    ap.add_argument("--write", action="store_true", help="按 pyproject.toml 重新生成清单")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印将生成的依赖列表")
    args = ap.parse_args(argv)

    expected = render(read_dependencies())
    if args.verbose:
        print(f"pyproject.toml → {len(expected.splitlines())} 行")
        for line in expected.splitlines():
            if line and not line.startswith("#"):
                print("   ", line)

    if args.write:
        return write(expected)
    return check(expected)


if __name__ == "__main__":
    raise SystemExit(main())
