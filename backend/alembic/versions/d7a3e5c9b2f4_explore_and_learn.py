"""Explore and learn (stage H3): challenges she sets herself, the session switch, and
lessons she starts herself (origin 'self').

Revision ID: d7a3e5c9b2f4
Revises: c2f6a8d4e1b7
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d7a3e5c9b2f4"
down_revision = "c2f6a8d4e1b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_sessions", sa.Column("explore", sa.Boolean(), nullable=False,
                                                server_default=sa.text("true")))
    op.create_table(
        "game_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("aim", sa.String(16), nullable=False),
        sa.Column("check", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="proposed"),
        sa.Column("plan_revision", sa.Integer(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("learned", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('proposed','active','done','given_up','replaced','expired')",
                           name="ck_game_challenges_status"),
    )
    op.create_index("ix_game_challenges_user_game", "game_challenges", ["user_id", "game", "created_at"])
    op.drop_constraint("ck_game_skill_attempts_origin", "game_skill_attempts", type_="check")
    op.create_check_constraint("ck_game_skill_attempts_origin", "game_skill_attempts",
                               "origin IN ('offer', 'user', 'self')")


def downgrade() -> None:
    op.execute("UPDATE game_skill_attempts SET origin = 'user' WHERE origin = 'self'")
    op.drop_constraint("ck_game_skill_attempts_origin", "game_skill_attempts", type_="check")
    op.create_check_constraint("ck_game_skill_attempts_origin", "game_skill_attempts", "origin IN ('offer', 'user')")
    op.drop_index("ix_game_challenges_user_game", table_name="game_challenges")
    op.drop_table("game_challenges")
    op.drop_column("desktop_sessions", "explore")
