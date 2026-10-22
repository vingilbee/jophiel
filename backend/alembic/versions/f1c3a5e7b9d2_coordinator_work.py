"""Coordinator work and outputs (docs/SHARED_STATE_STEP4_SPEC.md): background recall/mind work for a request,
leased, with dependencies, cancellation generation and an idempotency key; and one output decision per
request phase (immediate, follow_up), so a retry never repeats a phase and silence is recorded.

Revision ID: f1c3a5e7b9d2
Revises: e8b2d6f4a1c7
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1c3a5e7b9d2"
down_revision = "e8b2d6f4a1c7"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "coordinator_work",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", UUID, nullable=True),
        sa.Column("trigger_message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("dependency_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("input", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("input_versions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("cancellation_generation", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("idempotency_key", name="uq_coordinator_work_idempotency"),
        sa.CheckConstraint("kind IN ('recall', 'mind')", name="ck_coordinator_work_kind"),
        sa.CheckConstraint("status IN ('queued', 'waiting_for_memory', 'running', 'completed', 'failed', "
                           "'cancelled', 'expired', 'superseded')", name="ck_coordinator_work_status"),
    )
    op.create_index("ix_coordinator_work_request_id", "coordinator_work", ["request_id"])
    op.create_index("ix_coordinator_work_user_id", "coordinator_work", ["user_id"])
    op.create_index("ix_coordinator_work_claim", "coordinator_work", ["status", "lease_expires_at"])
    op.create_index("ix_coordinator_work_conversation", "coordinator_work", ["conversation_id", "status"])
    op.create_table(
        "coordinator_outputs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", UUID, sa.ForeignKey("coordinator_work.id", ondelete="SET NULL"), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decision_origin", sa.String(32), nullable=False),
        sa.Column("missing_capability", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="decided"),
        sa.Column("chat_job_id", UUID, sa.ForeignKey("chat_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assistant_message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("request_id", "phase", name="uq_coordinator_outputs_phase"),
        sa.CheckConstraint("phase IN ('immediate', 'follow_up')", name="ck_coordinator_outputs_phase"),
    )
    op.create_index("ix_coordinator_outputs_request_id", "coordinator_outputs", ["request_id"])
    op.create_index("ix_coordinator_outputs_user_id", "coordinator_outputs", ["user_id"])


def downgrade() -> None:
    op.drop_table("coordinator_outputs")
    op.drop_table("coordinator_work")
