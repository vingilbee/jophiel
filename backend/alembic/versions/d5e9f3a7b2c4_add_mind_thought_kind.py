"""The kind of thinking behind a thought (why, next, you, judgement, feeling, memory),
and quiet thoughts: the ones she has when nothing is happening.

Revision ID: d5e9f3a7b2c4
Revises: c4d8e2f6a9b1
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5e9f3a7b2c4"
down_revision = "c4d8e2f6a9b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mind_thoughts", sa.Column("kind", sa.String(12), nullable=True))
    op.drop_constraint("ck_mind_thoughts_level", "mind_thoughts", type_="check")
    op.create_check_constraint("ck_mind_thoughts_level", "mind_thoughts", "level IN ('react', 'light', 'quiet', 'hard')")


def downgrade() -> None:
    op.execute("DELETE FROM mind_thoughts WHERE level = 'quiet'")
    op.drop_constraint("ck_mind_thoughts_level", "mind_thoughts", type_="check")
    op.create_check_constraint("ck_mind_thoughts_level", "mind_thoughts", "level IN ('react', 'light', 'hard')")
    op.drop_column("mind_thoughts", "kind")
