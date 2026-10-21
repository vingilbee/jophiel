"""Memory corrections (docs/COLUMBINA_MEMORY_CORRECTIONS.md): explicit corrections read by semantic
consolidation, applied by the memory manager to the current memories they affect.

Revision ID: d3f7b1e9a5c2
Revises: c9e4a2f6d8b1
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3f7b1e9a5c2"
down_revision = "c9e4a2f6d8b1"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.func.now()

OWNER_CHECK = """
CREATE OR REPLACE FUNCTION memory_corrections_owner_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = NEW.source_message_id AND c.user_id = NEW.user_id AND m.role = 'user') THEN
        RAISE EXCEPTION 'a correction must come from its owner''s own message' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER memory_corrections_owner_check BEFORE INSERT OR UPDATE OF source_message_id, user_id
    ON memory_corrections FOR EACH ROW EXECUTE FUNCTION memory_corrections_owner_check();
"""


def upgrade() -> None:
    op.create_table(
        "memory_corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_job_id", UUID, sa.ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("attribute", sa.Text(), nullable=True),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("user_words", sa.Text(), nullable=False),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('changed', 'mistaken', 'unspecified')", name="ck_memory_corrections_kind"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'applied', 'failed')",
                           name="ck_memory_corrections_status"),
        sa.CheckConstraint("btrim(subject) <> '' AND btrim(new_value) <> '' AND btrim(user_words) <> ''",
                           name="ck_memory_corrections_text"),
        sa.CheckConstraint("(status = 'applied') = (applied_at IS NOT NULL)", name="ck_memory_corrections_applied"),
        sa.UniqueConstraint("user_id", "source_message_id", "fingerprint", name="uq_memory_corrections_once"),
        sa.ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                                ondelete="SET NULL (event_id)", name="fk_memory_corrections_event"),
    )
    op.create_index("ix_memory_corrections_due", "memory_corrections", ["status", "next_attempt_at"])
    op.create_index("ix_memory_corrections_user", "memory_corrections", ["user_id", "said_at"])
    op.create_index("ix_memory_corrections_message", "memory_corrections", ["source_message_id"])
    op.execute(OWNER_CHECK)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memory_corrections_owner_check ON memory_corrections")
    op.execute("DROP FUNCTION IF EXISTS memory_corrections_owner_check()")
    op.drop_table("memory_corrections")
