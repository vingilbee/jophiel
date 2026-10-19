"""Her learned moves look after themselves: three misses in a row switch one off,
and a learned program is tidied, keeping the original until the tidy has worked.

Revision ID: c2f6a8d4e1b7
Revises: b8e4c2a6d9f1
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2f6a8d4e1b7"
down_revision = "b8e4c2a6d9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("game_learned_skills", sa.Column("misses_in_row", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("game_learned_skills", sa.Column("off_reason", sa.Text(), nullable=True))
    op.add_column("game_learned_skills", sa.Column("original_code", sa.Text(), nullable=True))
    op.add_column("game_learned_skills", sa.Column("tidy_note", sa.Text(), nullable=True))
    op.add_column("game_learned_skills", sa.Column("tidy_proven", sa.Boolean(), nullable=False,
                                                   server_default=sa.text("false")))


def downgrade() -> None:
    for column in ("tidy_proven", "tidy_note", "original_code", "off_reason", "misses_in_row"):
        op.drop_column("game_learned_skills", column)
