"""Shared recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §10, §12): reply recall receipts, the
memories each reply was given or referred to, and background embeddings for memory events. Semantic and
episodic memories get an (id, user_id) key so a reference's owner is checked by the database.

Revision ID: c9e4a2f6d8b1
Revises: b5d9f3a7c2e8
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9e4a2f6d8b1"
down_revision = "b5d9f3a7c2e8"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.func.now()

OWNER_CHECK = """
CREATE OR REPLACE FUNCTION recall_receipts_owner_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = NEW.assistant_message_id AND c.id = NEW.conversation_id
                         AND c.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'a recall receipt must belong to the reply''s owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_receipts_owner_check BEFORE INSERT OR UPDATE ON recall_receipts
    FOR EACH ROW EXECUTE FUNCTION recall_receipts_owner_check();
"""


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def upgrade() -> None:
    op.create_unique_constraint("uq_semantic_memories_id_user", "semantic_memories", ["id", "user_id"])
    op.create_unique_constraint("uq_episodic_memories_id_user", "episodic_memories", ["id", "user_id"])

    op.create_table(
        "recall_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("conversation_id", UUID, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("coverage", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("counts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("association_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("association_note", sa.Text(), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("status IN ('not_searched', 'matched', 'no_match', 'partial', 'failed')",
                           name="ck_recall_receipts_status"),
        sa.CheckConstraint("association_status IN ('none', 'pending', 'done', 'ambiguous', 'failed', 'skipped')",
                           name="ck_recall_receipts_association"),
        sa.UniqueConstraint("id", "user_id", name="uq_recall_receipts_id_user"),
    )
    op.create_index("ix_recall_receipts_conversation", "recall_receipts", ["conversation_id", "created_at"])
    op.create_index("ix_recall_receipts_user", "recall_receipts", ["user_id", "created_at"])
    op.execute(OWNER_CHECK)

    op.create_table(
        "recall_references",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("fact_id", UUID, nullable=True),
        sa.Column("episode_id", UUID, nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("target_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(24), nullable=False),
        _now("created_at"),
        sa.CheckConstraint("relation IN ('supplied', 'referenced')", name="ck_recall_references_relation"),
        sa.CheckConstraint("num_nonnulls(fact_id, episode_id, event_id, item_id) = 1",
                           name="ck_recall_references_target"),
        sa.CheckConstraint("(span_start IS NULL) = (span_end IS NULL) AND (span_start IS NULL OR "
                           "(span_start >= 0 AND span_end > span_start))", name="ck_recall_references_span"),
        sa.CheckConstraint("relation = 'referenced' OR span_start IS NULL", name="ck_recall_references_supplied_span"),
        sa.ForeignKeyConstraint(["receipt_id", "user_id"], ["recall_receipts.id", "recall_receipts.user_id"],
                                ondelete="CASCADE", name="fk_recall_references_receipt"),
        sa.ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                                ondelete="CASCADE", name="fk_recall_references_fact"),
        sa.ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                                ondelete="CASCADE", name="fk_recall_references_episode"),
        sa.ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                                ondelete="CASCADE", name="fk_recall_references_event"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="CASCADE", name="fk_recall_references_item"),
    )
    op.create_index("uq_recall_references_supplied", "recall_references",
                    ["receipt_id", "fact_id", "episode_id", "event_id", "item_id"], unique=True,
                    postgresql_where=sa.text("relation = 'supplied'"), postgresql_nulls_not_distinct=True)
    op.create_index("ix_recall_references_receipt", "recall_references", ["receipt_id"])
    for target in ("event", "item", "fact", "episode"):
        op.create_index(f"ix_recall_references_{target}", "recall_references", [f"{target}_id"],
                        postgresql_where=sa.text(f"{target}_id IS NOT NULL"))

    op.create_table(
        "memory_event_embeddings",
        sa.Column("event_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("text_version", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("vector", JSONB, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _now("next_attempt_at"),
        _now("updated_at"),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_memory_event_embeddings_status"),
        sa.CheckConstraint("(status = 'done') = (vector IS NOT NULL)", name="ck_memory_event_embeddings_vector"),
        sa.ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                                ondelete="CASCADE", name="fk_memory_event_embeddings_event"),
    )
    op.create_index("ix_memory_event_embeddings_due", "memory_event_embeddings", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("memory_event_embeddings")
    op.drop_table("recall_references")
    op.execute("DROP TRIGGER IF EXISTS recall_receipts_owner_check ON recall_receipts")
    op.drop_table("recall_receipts")
    op.execute("DROP FUNCTION IF EXISTS recall_receipts_owner_check()")
    op.drop_constraint("uq_episodic_memories_id_user", "episodic_memories", type_="unique")
    op.drop_constraint("uq_semantic_memories_id_user", "semantic_memories", type_="unique")
