"""控制台输出兼容（Windows 中文控制台是 GBK/cp936）。

为什么单独一个模块：
    `dev_env_check.py` 与 `task_runner.py` 都要打印中文与状态符号，而
    `✅`（U+2705）、`↔`（U+2194）这类字符不在 GBK 码表内，`print` 会直接抛
    `UnicodeEncodeError` —— **辅助脚本因为自己的一行提示语而崩掉**，是最没用的
    失败形态（实测踩过：`dev_env_check.py` 首发即崩在 `↔` 上）。
    两份脚本各写一遍就是又一处会漂移的重复，故集中一处。

文件名为什么没有下划线前缀：
    `scripts/hooks/forbid_temp_files.sh` 把 `_*.py` 视作临时残留文件，
    白名单里只有 `__init__.py` 与两个契约测试共享模块。命名成 `_console.py`
    会被自家 pre-commit 拦下 —— 这正是 HANDOFF §8.2 记的那类"被 .gitignore /
    hook 静默吞掉"的坑。
"""

from __future__ import annotations

import sys


def tolerate_console_encoding() -> None:
    """把编码错误降级为替换符，而不是让脚本崩在 print 上。

    保留原编码（不乱码），只把编不出的字符退化成 `?`：
    「输出里有个问号」比「脚本崩掉、什么也没输出」好得多。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
