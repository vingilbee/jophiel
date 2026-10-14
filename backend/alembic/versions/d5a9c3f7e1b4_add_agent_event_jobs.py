"""Agent event jobs (Columbina acting on events) and desktop session autonomy.

Revision ID: d5a9c3f7e1b4
Revises: c4f8a2e6b9d1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d5a9c3f7e1b4"
down_revision = "c4f8a2e6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_event_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="ck_agent_event_jobs_status"),
    )
    op.create_index("ix_agent_event_jobs_user_id", "agent_event_jobs", ["user_id"])
    op.create_index("ix_agent_event_jobs_claim", "agent_event_jobs", ["status", "not_before"])
    op.create_index(
        "uq_agent_event_jobs_pending", "agent_event_jobs", ["source", "dedupe_key"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )
    op.add_column("desktop_sessions", sa.Column("autonomy", sa.String(16), nullable=False, server_default="continue"))
    op.create_check_constraint("ck_desktop_sessions_autonomy", "desktop_sessions", "autonomy IN ('continue', 'stop')")


def downgrade() -> None:
    op.drop_constraint("ck_desktop_sessions_autonomy", "desktop_sessions", type_="check")
    op.drop_column("desktop_sessions", "autonomy")
    op.drop_index("uq_agent_event_jobs_pending", table_name="agent_event_jobs")
    op.drop_index("ix_agent_event_jobs_claim", table_name="agent_event_jobs")
    op.drop_index("ix_agent_event_jobs_user_id", table_name="agent_event_jobs")
    op.drop_table("agent_event_jobs")
