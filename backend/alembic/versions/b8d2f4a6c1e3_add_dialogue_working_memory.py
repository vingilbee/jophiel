"""Add the conversation message ledger and Dialogue Working Memory V0 storage.

1. ``chat_messages.conversation_sequence``: a durable per-conversation
   position (dialogue-message-order-v0.1). Existing rows are backfilled within
   each conversation by ``coalesce(ChatJob.created_at, ChatMessage.created_at)``,
   then turn id, then user before assistant, then ``ChatMessage.created_at``,
   then ``ChatMessage.id``; every conversation is verified contiguous 1..N
   before the column becomes NOT NULL. New positions are allocated by a
   trigger under the conversation row lock and are never reused.
2. ``cognitive_dialogue_memory_snapshots`` (immutable), ``..._heads`` (the one
   mutable compare-and-set pointer), and ``..._jobs`` (durable update queue).

Revision ID: b8d2f4a6c1e3
Revises: a7c4e9b2d1f3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b8d2f4a6c1e3"
down_revision = "a7c4e9b2d1f3"
branch_labels = None
depends_on = None

ORDER_VERSION = "dialogue-message-order-v0.1"
_DIGEST = "~ '^[0-9a-f]{64}$'"

_SEQUENCE_FUNCTIONS = """
CREATE OR REPLACE FUNCTION chat_messages_assign_sequence() RETURNS trigger AS $$
DECLARE
    allocated BIGINT;
BEGIN
    IF NEW.conversation_sequence IS NOT NULL THEN
        RAISE EXCEPTION 'conversation_sequence is allocated by the database' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE conversations SET last_message_sequence = last_message_sequence + 1
        WHERE id = NEW.conversation_id
        RETURNING last_message_sequence INTO allocated;
    IF allocated IS NULL THEN
        RAISE EXCEPTION 'chat message conversation does not exist' USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.conversation_sequence := allocated;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_POSITION_GUARD = """
CREATE OR REPLACE FUNCTION chat_messages_position_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.conversation_sequence IS DISTINCT FROM OLD.conversation_sequence
       OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id THEN
        RAISE EXCEPTION 'a chat message keeps its conversation position' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_SNAPSHOT_GUARD = """
CREATE OR REPLACE FUNCTION cognitive_dialogue_memory_snapshots_guard() RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - 'through_message_id') IS DISTINCT FROM (to_jsonb(OLD) - 'through_message_id')
       OR (NEW.through_message_id IS DISTINCT FROM OLD.through_message_id AND NEW.through_message_id IS NOT NULL) THEN
        RAISE EXCEPTION 'dialogue memory snapshots are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# The documented backfill order. Text comparisons use the "C" collation so the
# order is bytewise and matches app/ai/Cognitive/system/v0/dialogue_memory_v0/ordering.py.
_BACKFILL = """
WITH ordered AS (
    SELECT m.id,
           ROW_NUMBER() OVER (
               PARTITION BY m.conversation_id
               ORDER BY coalesce(j.created_at, m.created_at),
                        coalesce(m.turn_id::text, '') COLLATE "C",
                        CASE WHEN m.role = 'user' THEN 0 ELSE 1 END,
                        m.created_at,
                        m.id
           ) AS position
    FROM chat_messages AS m
    LEFT JOIN chat_jobs AS j ON j.turn_id = m.turn_id
)
UPDATE chat_messages AS c
SET conversation_sequence = ordered.position
FROM ordered
WHERE c.id = ordered.id
"""

_NON_CONTIGUOUS = """
SELECT count(*) FROM (
    SELECT conversation_id,
           count(*) AS n,
           min(conversation_sequence) AS low,
           max(conversation_sequence) AS high,
           count(DISTINCT conversation_sequence) AS distinct_positions,
           count(conversation_sequence) AS assigned
    FROM chat_messages
    GROUP BY conversation_id
) AS positions
WHERE NOT (low = 1 AND high = n AND distinct_positions = n AND assigned = n)
"""


def upgrade() -> None:
    bind = op.get_bind()
    # No message may be appended between the backfill and the trigger.
    op.execute("LOCK TABLE chat_messages IN EXCLUSIVE MODE")
    op.add_column(
        "conversations",
        sa.Column("last_message_sequence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("chat_messages", sa.Column("conversation_sequence", sa.BigInteger(), nullable=True))
    op.execute(_BACKFILL)
    broken = bind.execute(sa.text(_NON_CONTIGUOUS)).scalar_one()
    if broken:
        raise RuntimeError(f"{ORDER_VERSION} backfill left {broken} conversation(s) without contiguous positions")
    op.execute(
        "UPDATE conversations SET last_message_sequence = positions.high "
        "FROM (SELECT conversation_id, max(conversation_sequence) AS high FROM chat_messages "
        "GROUP BY conversation_id) AS positions WHERE conversations.id = positions.conversation_id"
    )
    op.alter_column("chat_messages", "conversation_sequence", nullable=False)
    op.create_unique_constraint(
        "uq_chat_messages_conversation_sequence", "chat_messages", ["conversation_id", "conversation_sequence"]
    )
    op.create_check_constraint(
        "ck_chat_messages_conversation_sequence", "chat_messages", "conversation_sequence >= 1"
    )
    op.execute(_SEQUENCE_FUNCTIONS)
    op.execute(_POSITION_GUARD)
    op.execute(
        "CREATE TRIGGER chat_messages_sequence BEFORE INSERT ON chat_messages "
        "FOR EACH ROW EXECUTE FUNCTION chat_messages_assign_sequence()"
    )
    op.execute(
        "CREATE TRIGGER chat_messages_position_immutable BEFORE UPDATE ON chat_messages "
        "FOR EACH ROW EXECUTE FUNCTION chat_messages_position_guard()"
    )

    op.create_table(
        "cognitive_dialogue_memory_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("predecessor_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reset_reason", sa.String(length=64), nullable=True),
        sa.Column("through_conversation_sequence", sa.BigInteger(), nullable=False),
        sa.Column("through_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_event_count", sa.BigInteger(), nullable=False),
        sa.Column("source_prefix_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("semantic_digest", sa.String(length=64), nullable=False),
        sa.Column("versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["through_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "revision", name="uq_dialogue_memory_snapshots_revision"),
        sa.UniqueConstraint(
            "conversation_id", "through_conversation_sequence", "semantic_digest",
            name="uq_dialogue_memory_snapshots_position_digest",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_dialogue_memory_snapshots_revision"),
        sa.CheckConstraint("through_conversation_sequence >= 0", name="ck_dialogue_memory_snapshots_through"),
        sa.CheckConstraint("source_event_count >= 0", name="ck_dialogue_memory_snapshots_event_count"),
        sa.CheckConstraint(f"source_prefix_digest {_DIGEST}", name="ck_dialogue_memory_snapshots_prefix_digest"),
        sa.CheckConstraint(f"semantic_digest {_DIGEST}", name="ck_dialogue_memory_snapshots_semantic_digest"),
    )
    op.create_index(
        "ix_cognitive_dialogue_memory_snapshots_user_id", "cognitive_dialogue_memory_snapshots", ["user_id"]
    )
    op.create_index(
        "ix_dialogue_memory_snapshots_conversation_through", "cognitive_dialogue_memory_snapshots",
        ["conversation_id", "through_conversation_sequence"],
    )
    op.execute(_SNAPSHOT_GUARD)
    op.execute(
        "CREATE TRIGGER cognitive_dialogue_memory_snapshots_immutable "
        "BEFORE UPDATE ON cognitive_dialogue_memory_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION cognitive_dialogue_memory_snapshots_guard()"
    )

    op.create_table(
        "cognitive_dialogue_memory_heads",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("through_conversation_sequence", sa.BigInteger(), nullable=False),
        sa.Column("semantic_digest", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["cognitive_dialogue_memory_snapshots.id"]),
        sa.PrimaryKeyConstraint("conversation_id"),
        sa.CheckConstraint("revision >= 1", name="ck_dialogue_memory_heads_revision"),
        sa.CheckConstraint("through_conversation_sequence >= 0", name="ck_dialogue_memory_heads_through"),
        sa.CheckConstraint(f"semantic_digest {_DIGEST}", name="ck_dialogue_memory_heads_semantic_digest"),
    )
    op.create_index("ix_cognitive_dialogue_memory_heads_user_id", "cognitive_dialogue_memory_heads", ["user_id"])

    op.create_table(
        "cognitive_dialogue_memory_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_kind", sa.String(length=32), nullable=False),
        sa.Column("target_conversation_sequence", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_safe", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trigger_message_id", "trigger_kind", name="uq_dialogue_memory_jobs_trigger"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'skipped')",
            name="ck_dialogue_memory_jobs_status",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('message_committed', 'delivery_settled')", name="ck_dialogue_memory_jobs_trigger_kind"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_dialogue_memory_jobs_attempts"),
        sa.CheckConstraint("target_conversation_sequence >= 1", name="ck_dialogue_memory_jobs_target"),
        sa.CheckConstraint(
            "status <> 'processing' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_dialogue_memory_jobs_processing_lease",
        ),
    )
    op.create_index("ix_cognitive_dialogue_memory_jobs_user_id", "cognitive_dialogue_memory_jobs", ["user_id"])
    op.create_index(
        "ix_dialogue_memory_jobs_status_created", "cognitive_dialogue_memory_jobs", ["status", "created_at"]
    )
    op.create_index(
        "ix_dialogue_memory_jobs_conversation", "cognitive_dialogue_memory_jobs",
        ["conversation_id", "target_conversation_sequence"],
    )


def downgrade() -> None:
    op.drop_table("cognitive_dialogue_memory_jobs")
    op.drop_table("cognitive_dialogue_memory_heads")
    op.execute("DROP TRIGGER IF EXISTS cognitive_dialogue_memory_snapshots_immutable ON cognitive_dialogue_memory_snapshots")
    op.drop_table("cognitive_dialogue_memory_snapshots")
    op.execute("DROP FUNCTION IF EXISTS cognitive_dialogue_memory_snapshots_guard()")
    op.execute("DROP TRIGGER IF EXISTS chat_messages_position_immutable ON chat_messages")
    op.execute("DROP TRIGGER IF EXISTS chat_messages_sequence ON chat_messages")
    op.execute("DROP FUNCTION IF EXISTS chat_messages_position_guard()")
    op.execute("DROP FUNCTION IF EXISTS chat_messages_assign_sequence()")
    op.drop_constraint("ck_chat_messages_conversation_sequence", "chat_messages", type_="check")
    op.drop_constraint("uq_chat_messages_conversation_sequence", "chat_messages", type_="unique")
    op.drop_column("chat_messages", "conversation_sequence")
    op.drop_column("conversations", "last_message_sequence")
