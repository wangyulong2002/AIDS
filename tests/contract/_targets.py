"""扫描目标枚举（C2 / C4 两个扫描器共用）。

为什么独立成模块：
    两个扫描器各写一份"默认扫描面"时，"新增服务目录"这件事要改两处，
    漏一处就出现「C4 扫得到、C2 扫不到」的静默缺口——扫描器仍然全绿，
    但一半代码从未被检查。

历史坑：本函数（原分别写在两个扫描器里）长期只返回 `PROJECT_ROOT / "app"`，
    BE-01 新增的 `aids-backend/aids_backend/` 因此**完全不在扫描范围内**。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 服务包命名约定（与 deploy/app/*.Dockerfile 的 COPY 目标一致）
SERVICE_PACKAGE_GLOB: str = "aids-*/aids_*"


def scan_targets() -> list[Path]:
    """默认扫描面：共享契约层 + 全部服务包（存在即纳入，无需手工登记）。"""
    candidates: list[Path] = [PROJECT_ROOT / "app"]
    candidates.extend(sorted(PROJECT_ROOT.glob(SERVICE_PACKAGE_GLOB)))
    return [p for p in candidates if p.is_dir()]
