"""Stage 5 (docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): transcript search, forgetting by span, duplicate-fact upkeep.

- ``chat_messages.search_tsv``: a generated full-text column over what was said, as far as it was heard
  (``heard_chars``), with a GIN index. Postgres keeps it; nothing is backfilled by hand.
- ``recall_references.message_id``: a transcript passage a reply was given, one more exactly-one target; a trigger
  checks the message is the owner's (messages have no user column).
- ``memory_forgotten_sources.spans``: which characters of a message are about what was forgotten (null: all of it).
  Existing tombstones stay whole-message, exactly as before.
- ``memory_forgets.fence`` (hashed word stems, kept once done) and status ``partial``.
- ``memory_upkeep_pairs``: fact pairs upkeep judged, by fingerprint.
- ``chat_message_embeddings``: what was said, embedded in the background, for transcript search by meaning.

Downgrade drops them; tombstones lose their spans and become whole-message again (never less protective).

Revision ID: c8e4a6f2d9b3
Revises: b6f2d8a4c1e7
Create Date: 2026-10-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8e4a6f2d9b3"
down_revision = "b6f2d8a4c1e7"
branch_labels = None
depends_on = None

SEARCH = ("to_tsvector('english'::regconfig, CASE WHEN heard_chars IS NULL THEN content "
          "ELSE left(content, heard_chars) END)")

OWNER_CHECK = """
CREATE OR REPLACE FUNCTION recall_references_message_owner_check() RETURNS trigger AS $$
BEGIN
    IF NEW.message_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = NEW.message_id AND c.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'a recalled message must belong to its owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_references_message_owner_check BEFORE INSERT OR UPDATE ON recall_references
    FOR EACH ROW EXECUTE FUNCTION recall_references_message_owner_check();
"""


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("search_tsv", postgresql.TSVECTOR(),
                                             sa.Computed(SEARCH, persisted=True), nullable=True))
    op.create_index("ix_chat_messages_search", "chat_messages", ["search_tsv"], postgresql_using="gin")

    op.add_column("recall_references", sa.Column("message_id", postgresql.UUID(as_uuid=True),
                                                 sa.ForeignKey("chat_messages.id", ondelete="CASCADE",
                                                               name="recall_references_message_id_fkey"),
                                                 nullable=True))
    op.drop_constraint("ck_recall_references_target", "recall_references", type_="check")
    op.create_check_constraint("ck_recall_references_target", "recall_references",
                               "num_nonnulls(fact_id, episode_id, event_id, item_id, message_id) = 1")
    op.drop_index("uq_recall_references_supplied", table_name="recall_references")
    op.create_index("uq_recall_references_supplied", "recall_references",
                    ["receipt_id", "fact_id", "episode_id", "event_id", "item_id", "message_id"], unique=True,
                    postgresql_where=sa.text("relation = 'supplied'"), postgresql_nulls_not_distinct=True)
    op.create_index("ix_recall_references_message", "recall_references", ["message_id"],
                    postgresql_where=sa.text("message_id IS NOT NULL"))
    op.execute(OWNER_CHECK)

    op.add_column("memory_forgotten_sources", sa.Column("spans", postgresql.JSONB(), nullable=True))
    op.add_column("memory_forgets", sa.Column("fence", postgresql.JSONB(), nullable=False,
                                              server_default=sa.text("'[]'::jsonb")))
    op.drop_constraint("ck_memory_forgets_status", "memory_forgets", type_="check")
    op.create_check_constraint("ck_memory_forgets_status", "memory_forgets",
                               "status IN ('pending', 'done', 'partial', 'nothing_found', 'failed')")

    op.create_table(
        "memory_upkeep_pairs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("fact_a", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_b", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint_a", sa.String(32), nullable=False),
        sa.Column("fingerprint_b", sa.String(32), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("verdict IN ('different', 'merged', 'skipped')", name="ck_memory_upkeep_pairs_verdict"),
        sa.CheckConstraint("fact_a < fact_b", name="ck_memory_upkeep_pairs_order"),
        sa.ForeignKeyConstraint(["fact_a", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                                ondelete="CASCADE", name="fk_memory_upkeep_pairs_a"),
        sa.ForeignKeyConstraint(["fact_b", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                                ondelete="CASCADE", name="fk_memory_upkeep_pairs_b"),
        sa.UniqueConstraint("fact_a", "fact_b", name="uq_memory_upkeep_pairs"),
    )
    op.create_index("ix_memory_upkeep_pairs_user", "memory_upkeep_pairs", ["user_id", "judged_at"])

    op.create_table(
        "chat_message_embeddings",
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("vector", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_chat_message_embeddings_status"),
        sa.CheckConstraint("(status = 'done') = (vector IS NOT NULL)", name="ck_chat_message_embeddings_vector"),
    )
    op.create_index("ix_chat_message_embeddings_user", "chat_message_embeddings", ["user_id"],
                    postgresql_where=sa.text("status = 'done'"))
    op.create_index("ix_chat_message_embeddings_due", "chat_message_embeddings", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("chat_message_embeddings")
    op.drop_index("ix_memory_upkeep_pairs_user", table_name="memory_upkeep_pairs")
    op.drop_table("memory_upkeep_pairs")
    op.execute("UPDATE memory_forgets SET status = 'done' WHERE status = 'partial'")
    op.drop_constraint("ck_memory_forgets_status", "memory_forgets", type_="check")
    op.create_check_constraint("ck_memory_forgets_status", "memory_forgets",
                               "status IN ('pending', 'done', 'nothing_found', 'failed')")
    op.drop_column("memory_forgets", "fence")
    op.drop_column("memory_forgotten_sources", "spans")

    op.execute("DROP TRIGGER IF EXISTS recall_references_message_owner_check ON recall_references")
    op.execute("DROP FUNCTION IF EXISTS recall_references_message_owner_check()")
    op.execute("DELETE FROM recall_references WHERE message_id IS NOT NULL")
    op.drop_index("ix_recall_references_message", table_name="recall_references")
    op.drop_index("uq_recall_references_supplied", table_name="recall_references")
    op.create_index("uq_recall_references_supplied", "recall_references",
                    ["receipt_id", "fact_id", "episode_id", "event_id", "item_id"], unique=True,
                    postgresql_where=sa.text("relation = 'supplied'"), postgresql_nulls_not_distinct=True)
    op.drop_constraint("ck_recall_references_target", "recall_references", type_="check")
    op.create_check_constraint("ck_recall_references_target", "recall_references",
                               "num_nonnulls(fact_id, episode_id, event_id, item_id) = 1")
    op.drop_column("recall_references", "message_id")

    op.drop_index("ix_chat_messages_search", table_name="chat_messages")
    op.drop_column("chat_messages", "search_tsv")
