<%
# 让 `Revises:` 在「无父修订」时也有内容。
# 为什么要这层处理：直接用 `${down_revision | comma,n}` 时，down_revision 为 None
# 会渲染成 `Revises: ` —— **带一个行尾空格**，被 ruff 的 W291 判红（2026-09-24 实测，
# 首次 autogenerate 就撞上）。`(base)` 同时也是 Alembic 自己的惯例写法。
_parents = down_revision
if isinstance(_parents, (list, tuple)):
    _parents = ", ".join(_parents)
%>"""${message}

Revision ID: ${up_revision}
Revises: ${_parents or "(base)"}
Create Date: ${create_date}

=====================================================================
S5（见 docs/工程化门禁方案.md §5）：本文件的 downgrade() **必须真正实现**。

    CI 会实跑 `upgrade → downgrade → upgrade`。空的 `pass` 会让回滚失效，
    而"回滚不了"只会在真出事时才被发现 —— 那时已经晚了。

    写迁移时先自问：这条 DDL 反过来执行是什么？
        加列   → 删列（**数据会丢**，downgrade 里要写清楚这个代价）
        建索引 → 删索引
        改类型 → 改回去（窄化可能截断，需注释风险）

    模板刻意把未填写的 downgrade 生成为 `raise NotImplementedError`，
    而不是 Alembic 默认的 `pass` —— 让"忘了写回滚"在第一次执行时就暴露。
=====================================================================
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else 'raise NotImplementedError("回滚未实现 —— S5 要求每个 revision 都能 downgrade")'}
