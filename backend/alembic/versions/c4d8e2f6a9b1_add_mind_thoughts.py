"""Her mind's thoughts (docs/COLUMBINA_MIND_DESIGN.md).

Revision ID: c4d8e2f6a9b1
Revises: e3a9c7b1d5f2
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4d8e2f6a9b1"
down_revision = "e3a9c7b1d5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mind_thoughts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("said", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="done"),
        sa.Column("topic", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("level IN ('react', 'light', 'hard')", name="ck_mind_thoughts_level"),
        sa.CheckConstraint("status IN ('done', 'stale', 'unfinished')", name="ck_mind_thoughts_status"),
    )
    op.create_index("ix_mind_thoughts_user_created", "mind_thoughts", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mind_thoughts_user_created", table_name="mind_thoughts")
    op.drop_table("mind_thoughts")
