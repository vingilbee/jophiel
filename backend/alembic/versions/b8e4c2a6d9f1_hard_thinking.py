"""Hard thinking with a conclusion (docs/COLUMBINA_HARD_THINKING_PLAN.md): what started it,
what kind of conclusion it reached, her private reasoning, a tip for her hands, a question
for the user, and why a thought was dropped (status ``dropped``).

Revision ID: b8e4c2a6d9f1
Revises: a3d7f1c9e5b8
Create Date: 2026-10-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e4c2a6d9f1"
down_revision = "a3d7f1c9e5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mind_thoughts", sa.Column("trigger", sa.String(12), nullable=True))
    op.add_column("mind_thoughts", sa.Column("conclusion_kind", sa.String(12), nullable=True))
    op.add_column("mind_thoughts", sa.Column("thinking", sa.Text(), nullable=True))
    op.add_column("mind_thoughts", sa.Column("for_hands", sa.Text(), nullable=True))
    op.add_column("mind_thoughts", sa.Column("question", sa.Text(), nullable=True))
    op.add_column("mind_thoughts", sa.Column("dropped_reason", sa.String(200), nullable=True))
    op.drop_constraint("ck_mind_thoughts_status", "mind_thoughts", type_="check")
    op.create_check_constraint("ck_mind_thoughts_status", "mind_thoughts",
                               "status IN ('done', 'stale', 'unfinished', 'dropped')")


def downgrade() -> None:
    op.execute("UPDATE mind_thoughts SET status = 'stale' WHERE status = 'dropped'")
    op.drop_constraint("ck_mind_thoughts_status", "mind_thoughts", type_="check")
    op.create_check_constraint("ck_mind_thoughts_status", "mind_thoughts", "status IN ('done', 'stale', 'unfinished')")
    for column in ("dropped_reason", "question", "for_hands", "thinking", "conclusion_kind", "trigger"):
        op.drop_column("mind_thoughts", column)
