"""ORM 模型汇总（**生成物**）。

导入本包即让全部表注册进 `Base.metadata` —— Alembic autogenerate
与应用启动都依赖这一点（少导入一个模块，生成的迁移就会缺表）。
"""

from __future__ import annotations

from app.models.biz import *  # noqa: F401,F403
from app.models.ai import *  # noqa: F401,F403
from app.models.sys import *  # noqa: F401,F403
