"""Observation (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §5): one immutable stream of what sources reported,
per-consumer processing receipts, after-the-fact context bindings, source health/progress, adapter run
state, and observations as a memory evidence source.

Revision ID: b5d9f3a7c2e8
Revises: a4c8e2f6b1d9
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b5d9f3a7c2e8"
down_revision = "a4c8e2f6b1d9"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.func.now()

IMMUTABLE = """
CREATE OR REPLACE FUNCTION observations_immutable() RETURNS trigger AS $$
BEGIN
    IF (NEW.context_id IS DISTINCT FROM OLD.context_id AND NEW.context_id IS NOT NULL)
       OR (NEW.caused_by_id IS DISTINCT FROM OLD.caused_by_id AND NEW.caused_by_id IS NOT NULL)
       OR (NEW.outcome_of_id IS DISTINCT FROM OLD.outcome_of_id AND NEW.outcome_of_id IS NOT NULL)
       OR (NEW.session_id IS DISTINCT FROM OLD.session_id AND NEW.session_id IS NOT NULL)
       OR (to_jsonb(NEW) - ARRAY['context_id', 'caused_by_id', 'outcome_of_id', 'session_id'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['context_id', 'caused_by_id', 'outcome_of_id', 'session_id']) THEN
        RAISE EXCEPTION 'observations are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER observations_immutable BEFORE UPDATE ON observations
    FOR EACH ROW EXECUTE FUNCTION observations_immutable();
"""

OLD_SOURCES = "message_id, session_event_id, thought_id, experience_id"


def _now(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_id", UUID, nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_instance", sa.String(160), nullable=False),
        sa.Column("source_event_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("epistemic", sa.String(16), nullable=False),
        sa.Column("content_origin", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        _now("received_at"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("salience", sa.String(16), nullable=False),
        sa.Column("caused_by_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome_of_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("delivery_id", UUID, sa.ForeignKey("message_deliveries.id", ondelete="CASCADE"), nullable=True),
        sa.Column("experience_id", sa.BigInteger(), sa.ForeignKey("game_experiences.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("session_id", UUID, sa.ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("actor IN ('user', 'columbina', 'environment', 'tool', 'system')",
                           name="ck_observations_actor"),
        sa.CheckConstraint("epistemic IN ('observed', 'reported')", name="ck_observations_epistemic"),
        sa.CheckConstraint("content_origin IN ('user', 'external', 'system', 'model')", name="ck_observations_origin"),
        sa.CheckConstraint("salience IN ('routine', 'notable', 'critical')", name="ck_observations_salience"),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 500 AND btrim(summary) <> ''",
                           name="ck_observations_summary"),
        sa.CheckConstraint("schema_version >= 1", name="ck_observations_schema_version"),
        sa.CheckConstraint("btrim(source) <> '' AND btrim(source_instance) <> '' AND btrim(source_event_key) <> ''",
                           name="ck_observations_identity"),
        sa.CheckConstraint("caused_by_id IS NULL OR caused_by_id <> id", name="ck_observations_caused_by_self"),
        sa.CheckConstraint("outcome_of_id IS NULL OR outcome_of_id <> id", name="ck_observations_outcome_self"),
        sa.UniqueConstraint("user_id", "source", "source_instance", "source_event_key",
                            name="uq_observations_source_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_observations_id_user"),
        sa.ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                name="fk_observations_context"),
        sa.ForeignKeyConstraint(["caused_by_id", "user_id"], ["observations.id", "observations.user_id"],
                                ondelete="SET NULL (caused_by_id)", name="fk_observations_caused_by"),
        sa.ForeignKeyConstraint(["outcome_of_id", "user_id"], ["observations.id", "observations.user_id"],
                                ondelete="SET NULL (outcome_of_id)", name="fk_observations_outcome_of"),
    )
    op.create_index("ix_observations_user_received", "observations",
                    ["user_id", sa.text("received_at DESC"), sa.text("id DESC")])
    op.create_index("ix_observations_context_received", "observations",
                    ["context_id", sa.text("received_at DESC"), sa.text("id DESC")],
                    postgresql_where=sa.text("context_id IS NOT NULL"))
    for column in ("message", "delivery", "experience", "session", "caused_by", "outcome_of"):
        op.create_index(f"ix_observations_{column}", "observations", [f"{column}_id"],
                        postgresql_where=sa.text(f"{column}_id IS NOT NULL"))
    op.execute(IMMUTABLE)

    op.create_table(
        "observation_processing",
        sa.Column("observation_id", sa.BigInteger(), sa.ForeignKey("observations.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("consumer", sa.String(16), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _now("next_attempt_at"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("rule_version", sa.String(32), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'retry', 'applied', 'skipped', 'failed')",
                           name="ck_observation_processing_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_observation_processing_attempts"),
        sa.CheckConstraint("(status IN ('applied', 'skipped', 'failed')) = (processed_at IS NOT NULL)",
                           name="ck_observation_processing_done"),
        sa.ForeignKeyConstraint(["observation_id", "user_id"], ["observations.id", "observations.user_id"],
                                ondelete="CASCADE", name="fk_observation_processing_observation"),
    )
    op.create_index("ix_observation_processing_due", "observation_processing",
                    ["consumer", "next_attempt_at", "observation_id"],
                    postgresql_where=sa.text("status IN ('pending', 'retry')"))
    op.create_index("ix_observation_processing_user", "observation_processing", ["user_id", "consumer", "status"])

    op.create_table(
        "observation_bindings",
        sa.Column("observation_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_id", UUID, nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        _now("bound_at"),
        sa.ForeignKeyConstraint(["observation_id", "user_id"], ["observations.id", "observations.user_id"],
                                ondelete="CASCADE", name="fk_observation_bindings_observation"),
        sa.ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                name="fk_observation_bindings_context"),
    )
    op.create_index("ix_observation_bindings_context", "observation_bindings", ["context_id"])

    op.create_table(
        "observation_sources",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("source_instance", sa.String(160), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("availability", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("position", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_decision", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        _now("updated_at"),
        sa.CheckConstraint("availability IN ('unknown', 'online', 'offline')",
                           name="ck_observation_sources_availability"),
        sa.CheckConstraint("revision >= 1", name="ck_observation_sources_revision"),
    )

    op.create_table(
        "observation_adapters",
        sa.Column("name", sa.String(64), primary_key=True),
        _now("started_at"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_decision", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.add_column("memory_evidence", sa.Column(
        "observation_id", sa.BigInteger(), sa.ForeignKey("observations.id", ondelete="CASCADE"), nullable=True))
    op.drop_constraint("ck_memory_evidence_source", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_source", "memory_evidence",
                               f"num_nonnulls({OLD_SOURCES}, observation_id) = 1")
    op.drop_constraint("uq_memory_evidence", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "uq_memory_evidence", "memory_evidence",
        ["item_id", "event_id", "role", "message_id", "session_event_id", "thought_id", "experience_id",
         "observation_id"], postgresql_nulls_not_distinct=True)
    op.create_index("ix_memory_evidence_observation", "memory_evidence", ["observation_id"])
    op.create_index("ix_chat_messages_heard", "chat_messages", ["created_at"],
                    postgresql_where=sa.text("heard_chars IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_chat_messages_heard", table_name="chat_messages")
    op.drop_index("ix_memory_evidence_observation", table_name="memory_evidence")
    op.execute("DELETE FROM memory_evidence WHERE observation_id IS NOT NULL")
    op.drop_constraint("uq_memory_evidence", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "uq_memory_evidence", "memory_evidence",
        ["item_id", "event_id", "role", "message_id", "session_event_id", "thought_id", "experience_id"],
        postgresql_nulls_not_distinct=True)
    op.drop_constraint("ck_memory_evidence_source", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_source", "memory_evidence", f"num_nonnulls({OLD_SOURCES}) = 1")
    op.drop_column("memory_evidence", "observation_id")
    op.drop_table("observation_adapters")
    op.drop_table("observation_sources")
    op.drop_table("observation_bindings")
    op.drop_table("observation_processing")
    op.execute("DROP TRIGGER IF EXISTS observations_immutable ON observations")
    op.drop_table("observations")
    op.execute("DROP FUNCTION IF EXISTS observations_immutable()")
