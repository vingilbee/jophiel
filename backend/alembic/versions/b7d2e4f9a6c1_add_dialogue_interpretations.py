"""Interpreted dialogue relationships (Call 1 registry v1.7).

One immutable row per user message: the option sets, same-referent links and
corrections its understanding call read, as exact user spans.

Revision ID: b7d2e4f9a6c1
Revises: d8f3a1c5e7b2
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7d2e4f9a6c1"
down_revision = "d8f3a1c5e7b2"
branch_labels = None
depends_on = None

_GUARD = """
CREATE OR REPLACE FUNCTION cognitive_dialogue_interpretations_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'cognitive_dialogue_interpretations rows are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER cognitive_dialogue_interpretations_immutable
    BEFORE UPDATE ON cognitive_dialogue_interpretations
    FOR EACH ROW EXECUTE FUNCTION cognitive_dialogue_interpretations_guard();
"""


def upgrade() -> None:
    op.create_table(
        "cognitive_dialogue_interpretations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_conversation_sequence", sa.BigInteger, nullable=False),
        sa.Column("shadow_trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_message_id", name="uq_dialogue_interpretations_source_message"),
        sa.CheckConstraint("source_conversation_sequence >= 1", name="ck_dialogue_interpretations_sequence"),
        sa.CheckConstraint("record_digest ~ '^[0-9a-f]{64}$'", name="ck_dialogue_interpretations_digest"),
    )
    op.create_index("ix_cognitive_dialogue_interpretations_user_id", "cognitive_dialogue_interpretations", ["user_id"])
    op.create_index("ix_dialogue_interpretations_conversation_sequence", "cognitive_dialogue_interpretations",
                    ["conversation_id", "source_conversation_sequence"])
    op.execute(_GUARD)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS cognitive_dialogue_interpretations_immutable ON cognitive_dialogue_interpretations")
    op.drop_table("cognitive_dialogue_interpretations")
    op.execute("DROP FUNCTION IF EXISTS cognitive_dialogue_interpretations_guard()")
