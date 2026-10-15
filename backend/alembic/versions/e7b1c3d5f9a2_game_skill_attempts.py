"""Her attempts at teaching herself a move in a game (stage H1): the task, every
program she wrote with what it did, and how it ended.

Revision ID: e7b1c3d5f9a2
Revises: d5e9f3a7b2c4
Create Date: 2026-10-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7b1c3d5f9a2"
down_revision = "d5e9f3a7b2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_skill_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("tries", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'learned', 'failed', 'stopped')",
                           name="ck_game_skill_attempts_status"),
        sa.CheckConstraint("origin IN ('offer', 'user')", name="ck_game_skill_attempts_origin"),
    )
    op.create_index("uq_game_skill_attempts_active_session", "game_skill_attempts", ["session_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("ix_game_skill_attempts_user_created", "game_skill_attempts", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_game_skill_attempts_user_created", table_name="game_skill_attempts")
    op.drop_index("uq_game_skill_attempts_active_session", table_name="game_skill_attempts")
    op.drop_table("game_skill_attempts")
