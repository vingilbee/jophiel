"""Desktop agent environments, sessions, and session events.

Revision ID: e5b8c2d4f6a1
Revises: d4f7a1c3e9b2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5b8c2d4f6a1"
down_revision = "d4f7a1c3e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "desktop_environments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("hosting_kind", sa.String(16), nullable=False, server_default="local_vm"),
        sa.Column("guest_os", sa.String(32), nullable=True),
        sa.Column("allowed_profile_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("policy_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="offline"),
        sa.Column("controller_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("hosting_kind IN ('local_vm', 'cloud_vm')", name="ck_desktop_environments_hosting_kind"),
        sa.CheckConstraint("status IN ('offline', 'online')", name="ck_desktop_environments_status"),
    )
    op.create_index("ix_desktop_environments_owner_user_id", "desktop_environments", ["owner_user_id"])

    op.create_table(
        "desktop_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("profile_id", sa.String(32), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason_code", sa.String(48), nullable=True),
        sa.Column("control_owner", sa.String(16), nullable=False, server_default="none"),
        sa.Column("execution_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("plan", postgresql.JSONB(), nullable=True),
        sa.Column("plan_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_step_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_step_id", sa.String(16), nullable=True),
        sa.Column("driver_status", sa.String(16), nullable=True),
        sa.Column("commentary_mode", sa.String(16), nullable=False, server_default="occasional"),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('starting', 'running', 'paused', 'manual', 'disconnected', 'ended', 'failed')",
            name="ck_desktop_sessions_state",
        ),
        sa.CheckConstraint("control_owner IN ('none', 'gameplay', 'manual')", name="ck_desktop_sessions_control_owner"),
        sa.CheckConstraint(
            "commentary_mode IN ('quiet', 'occasional', 'commentary')", name="ck_desktop_sessions_commentary_mode"
        ),
    )
    op.create_index("ix_desktop_sessions_owner_user_id", "desktop_sessions", ["owner_user_id"])
    op.create_index("ix_desktop_sessions_environment_id", "desktop_sessions", ["environment_id"])
    op.create_index(
        "uq_desktop_sessions_active_environment", "desktop_sessions", ["environment_id"],
        unique=True, postgresql_where=sa.text("state NOT IN ('ended', 'failed')"),
    )
    op.create_index(
        "uq_desktop_sessions_owner_idempotency", "desktop_sessions", ["owner_user_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "desktop_session_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_desktop_session_events_session_id", "desktop_session_events", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_desktop_session_events_session_id", table_name="desktop_session_events")
    op.drop_table("desktop_session_events")
    op.drop_index("uq_desktop_sessions_owner_idempotency", table_name="desktop_sessions")
    op.drop_index("uq_desktop_sessions_active_environment", table_name="desktop_sessions")
    op.drop_index("ix_desktop_sessions_environment_id", table_name="desktop_sessions")
    op.drop_index("ix_desktop_sessions_owner_user_id", table_name="desktop_sessions")
    op.drop_table("desktop_sessions")
    op.drop_index("ix_desktop_environments_owner_user_id", table_name="desktop_environments")
    op.drop_table("desktop_environments")
