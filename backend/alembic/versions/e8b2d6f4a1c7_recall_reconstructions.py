"""Source-backed history (docs/COLUMBINA_SOURCE_BACKED_RECALL.md): each history her recall_history tool
rebuilt for a reply, and the passages it was shown, as real references with their versions. No source
text is copied; a trigger checks each source row belongs to the owner.

Revision ID: e8b2d6f4a1c7
Revises: d3f7b1e9a5c2
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8b2d6f4a1c7"
down_revision = "d3f7b1e9a5c2"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
EMPTY = sa.text("'{}'::jsonb")

OWNER_CHECK = """
CREATE OR REPLACE FUNCTION recall_reconstruction_sources_owner_check() RETURNS trigger AS $$
BEGIN
    IF (NEW.message_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = NEW.message_id AND c.user_id = NEW.user_id))
       OR (NEW.observation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM observations o WHERE o.id = NEW.observation_id AND o.user_id = NEW.user_id))
       OR (NEW.experience_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM game_experiences g WHERE g.id = NEW.experience_id AND g.user_id = NEW.user_id))
       OR (NEW.thought_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM mind_thoughts t WHERE t.id = NEW.thought_id AND t.user_id = NEW.user_id))
       OR (NEW.session_event_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM desktop_session_events e JOIN desktop_sessions s ON s.id = e.session_id
            WHERE e.id = NEW.session_event_id AND s.owner_user_id = NEW.user_id)) THEN
        RAISE EXCEPTION 'a reconstruction source must belong to its owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_reconstruction_sources_owner_check BEFORE INSERT OR UPDATE ON recall_reconstruction_sources
    FOR EACH ROW EXECUTE FUNCTION recall_reconstruction_sources_owner_check();
"""


def upgrade() -> None:
    op.create_table(
        "recall_reconstructions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("about", sa.Text(), nullable=True),
        sa.Column("scope", JSONB, nullable=False, server_default=EMPTY),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("reasoning_effort", sa.String(16), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("account", JSONB, nullable=False, server_default=EMPTY),
        sa.Column("coverage", JSONB, nullable=False, server_default=EMPTY),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('complete', 'partial', 'no_history', 'failed')",
                           name="ck_recall_reconstructions_status"),
        sa.UniqueConstraint("id", "user_id", name="uq_recall_reconstructions_id_user"),
        sa.ForeignKeyConstraint(["receipt_id", "user_id"], ["recall_receipts.id", "recall_receipts.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstructions_receipt"),
    )
    op.create_index("ix_recall_reconstructions_receipt", "recall_reconstructions", ["receipt_id"])

    op.create_table(
        "recall_reconstruction_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("reconstruction_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(8), nullable=False),
        sa.Column("speaker", sa.String(16), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("cited", sa.Boolean(), nullable=False),
        sa.Column("in_scope", sa.Boolean(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fact_id", UUID, nullable=True),
        sa.Column("episode_id", UUID, nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("observation_id", sa.BigInteger(), sa.ForeignKey("observations.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("experience_id", sa.BigInteger(), sa.ForeignKey("game_experiences.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("session_event_id", sa.BigInteger(), sa.ForeignKey("desktop_session_events.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("thought_id", UUID, sa.ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True),
        sa.CheckConstraint("speaker IN ('user', 'columbina', 'source', 'memory')",
                           name="ck_recall_reconstruction_sources_speaker"),
        sa.CheckConstraint("num_nonnulls(fact_id, episode_id, event_id, item_id, message_id, observation_id, "
                           "experience_id, session_event_id, thought_id) = 1",
                           name="ck_recall_reconstruction_sources_target"),
        sa.UniqueConstraint("reconstruction_id", "label", name="uq_recall_reconstruction_sources_label"),
        sa.ForeignKeyConstraint(["reconstruction_id", "user_id"],
                                ["recall_reconstructions.id", "recall_reconstructions.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstruction_sources_reconstruction"),
        sa.ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstruction_sources_fact"),
        sa.ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstruction_sources_episode"),
        sa.ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstruction_sources_event"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="CASCADE", name="fk_recall_reconstruction_sources_item"),
    )
    op.create_index("ix_recall_reconstruction_sources_reconstruction", "recall_reconstruction_sources",
                    ["reconstruction_id"])
    for column in ("message_id", "event_id", "item_id", "fact_id", "episode_id", "observation_id", "experience_id",
                   "session_event_id", "thought_id"):
        op.create_index(f"ix_recall_reconstruction_sources_{column.removesuffix('_id')}",
                        "recall_reconstruction_sources", [column], postgresql_where=sa.text(f"{column} IS NOT NULL"))
    op.execute(OWNER_CHECK)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS recall_reconstruction_sources_owner_check ON recall_reconstruction_sources")
    op.drop_table("recall_reconstruction_sources")
    op.execute("DROP FUNCTION IF EXISTS recall_reconstruction_sources_owner_check()")
    op.drop_table("recall_reconstructions")
