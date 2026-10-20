"""General working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md §4): contexts, working items (goals,
commitments, intentions, questions), events, evidence links, the manager's state, and the goal a game
session serves.

Revision ID: a4c8e2f6b1d9
Revises: d7a3e5c9b2f4
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4c8e2f6b1d9"
down_revision = "d7a3e5c9b2f4"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
NOW = sa.func.now()

ITEM_STATUSES = (
    "(kind = 'goal' AND status IN ('active', 'suspended', 'completed', 'abandoned')) "
    "OR (kind = 'commitment' AND status IN ('open', 'needs_review', 'fulfilled', 'cancelled')) "
    "OR (kind = 'intention' AND status IN ('active', 'done', 'dropped', 'lapsed')) "
    "OR (kind = 'question' AND status IN ('open', 'answered', 'dropped'))"
)
ITEM_CLOSED = "'abandoned', 'answered', 'cancelled', 'completed', 'done', 'dropped', 'fulfilled', 'lapsed'"

PARENT_TRIGGERS = """
CREATE OR REPLACE FUNCTION working_items_parent_check() RETURNS trigger AS $$
DECLARE
    walker uuid;
    steps integer := 0;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.kind IS DISTINCT FROM OLD.kind
       AND EXISTS (SELECT 1 FROM working_items WHERE parent_id = NEW.id) THEN
        RAISE EXCEPTION 'working_items: a goal with sub-goals keeps its kind' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF (SELECT kind FROM working_items WHERE id = NEW.parent_id) IS DISTINCT FROM 'goal' THEN
        RAISE EXCEPTION 'working_items: a parent must be a goal' USING ERRCODE = 'check_violation';
    END IF;
    walker := NEW.parent_id;
    WHILE walker IS NOT NULL LOOP
        IF walker = NEW.id OR steps > 64 THEN
            RAISE EXCEPTION 'working_items: parents may not loop' USING ERRCODE = 'check_violation';
        END IF;
        steps := steps + 1;
        SELECT parent_id INTO walker FROM working_items WHERE id = walker;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER working_items_parent_check BEFORE INSERT OR UPDATE OF parent_id, kind, context_id ON working_items
    FOR EACH ROW EXECUTE FUNCTION working_items_parent_check();

CREATE OR REPLACE FUNCTION memory_contexts_parent_check() RETURNS trigger AS $$
DECLARE
    walker uuid;
    steps integer := 0;
BEGIN
    walker := NEW.parent_id;
    WHILE walker IS NOT NULL LOOP
        IF walker = NEW.id OR steps > 64 THEN
            RAISE EXCEPTION 'memory_contexts: parents may not loop' USING ERRCODE = 'check_violation';
        END IF;
        steps := steps + 1;
        SELECT parent_id INTO walker FROM memory_contexts WHERE id = walker;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER memory_contexts_parent_check BEFORE INSERT OR UPDATE OF parent_id ON memory_contexts
    FOR EACH ROW EXECUTE FUNCTION memory_contexts_parent_check();
"""


def _times(*names: str) -> list[sa.Column]:
    return [sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False) for name in names]


def upgrade() -> None:
    op.create_table(
        "memory_contexts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("parent_id", UUID, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        *_times("created_at", "last_active_at"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('general', 'activity', 'project', 'world')", name="ck_memory_contexts_kind"),
        sa.CheckConstraint("status IN ('active', 'paused', 'closed')", name="ck_memory_contexts_status"),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_memory_contexts_label"),
        sa.CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="ck_memory_contexts_closed"),
        sa.CheckConstraint("kind <> 'general' OR (source IS NULL AND external_ref IS NULL AND parent_id IS NULL "
                           "AND status <> 'closed')", name="ck_memory_contexts_general"),
        sa.CheckConstraint("external_ref IS NULL OR source IS NOT NULL", name="ck_memory_contexts_ref"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_memory_contexts_parent_self"),
        sa.UniqueConstraint("id", "user_id", name="uq_memory_contexts_id_user"),
        sa.ForeignKeyConstraint(["parent_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                name="fk_memory_contexts_parent"),
    )
    op.create_index("uq_memory_contexts_general", "memory_contexts", ["user_id"], unique=True,
                    postgresql_where=sa.text("kind = 'general'"))
    op.create_index("uq_memory_contexts_ref", "memory_contexts", ["user_id", "source", "external_ref"], unique=True,
                    postgresql_where=sa.text("external_ref IS NOT NULL"))
    op.create_index("ix_memory_contexts_user_status", "memory_contexts",
                    ["user_id", "status", sa.text("last_active_at DESC")])

    op.create_table(
        "working_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_id", UUID, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_id", UUID, nullable=True),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("owed_to", sa.String(16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("done_when", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("epistemic", sa.String(16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        *_times("created_at", "updated_at"),
        sa.CheckConstraint("kind IN ('goal', 'intention', 'commitment', 'question')", name="ck_working_items_kind"),
        sa.CheckConstraint(ITEM_STATUSES, name="ck_working_items_status"),
        sa.CheckConstraint("owner IN ('user', 'columbina', 'shared')", name="ck_working_items_owner"),
        sa.CheckConstraint("epistemic IN ('reported', 'inferred')", name="ck_working_items_epistemic"),
        sa.CheckConstraint("btrim(text) <> ''", name="ck_working_items_text"),
        sa.CheckConstraint(f"(closed_at IS NOT NULL) = (status IN ({ITEM_CLOSED}))", name="ck_working_items_closed"),
        sa.CheckConstraint("parent_id IS NULL OR (kind = 'goal' AND parent_id <> id)", name="ck_working_items_parent"),
        sa.CheckConstraint("(kind = 'commitment') = (owed_to IS NOT NULL)", name="ck_working_items_owed_to"),
        sa.CheckConstraint("kind <> 'commitment' OR (owner IN ('user', 'columbina') AND owed_to IN "
                           "('user', 'columbina') AND owner <> owed_to)", name="ck_working_items_commitment_parties"),
        sa.CheckConstraint("kind <> 'intention' OR due_at IS NOT NULL", name="ck_working_items_intention_due"),
        sa.CheckConstraint("revision >= 1", name="ck_working_items_revision"),
        sa.UniqueConstraint("id", "user_id", name="uq_working_items_id_user"),
        sa.UniqueConstraint("id", "context_id", name="uq_working_items_id_context"),
        sa.ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                name="fk_working_items_context"),
        sa.ForeignKeyConstraint(["parent_id", "context_id"], ["working_items.id", "working_items.context_id"],
                                name="fk_working_items_parent"),
    )
    op.create_index("ix_working_items_user_status", "working_items", ["user_id", "status"])
    op.create_index("ix_working_items_context_kind_status", "working_items", ["context_id", "kind", "status"])
    op.create_index("ix_working_items_parent", "working_items", ["parent_id"],
                    postgresql_where=sa.text("parent_id IS NOT NULL"))
    op.create_index("ix_working_items_due", "working_items", ["due_at"],
                    postgresql_where=sa.text("due_at IS NOT NULL AND closed_at IS NULL"))

    op.create_table(
        "memory_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("epistemic", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_times("recorded_at"),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.CheckConstraint("epistemic IN ('observed', 'reported', 'inferred')", name="ck_memory_events_epistemic"),
        sa.CheckConstraint("btrim(text) <> ''", name="ck_memory_events_text"),
        sa.UniqueConstraint("id", "user_id", name="uq_memory_events_id_user"),
        sa.ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                name="fk_memory_events_context"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="SET NULL (item_id)", name="fk_memory_events_item"),
    )
    op.create_index("uq_memory_events_dedupe", "memory_events", ["user_id", "dedupe_key"], unique=True,
                    postgresql_where=sa.text("dedupe_key IS NOT NULL"))
    op.create_index("ix_memory_events_context", "memory_events", ["context_id", sa.text("id DESC")])
    op.create_index("ix_memory_events_user", "memory_events", ["user_id", "id"])

    op.create_table(
        "memory_manager_state",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("focus_context_id", UUID, nullable=True),
        sa.Column("last_event_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=True),
        *_times("next_check_at"),
        sa.Column("last_decision", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        *_times("updated_at"),
        sa.ForeignKeyConstraint(["focus_context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                                ondelete="SET NULL (focus_context_id)", name="fk_memory_manager_state_focus"),
    )
    op.create_index("ix_memory_manager_state_next_check", "memory_manager_state", ["next_check_at"])

    op.create_table(
        "memory_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("session_event_id", sa.BigInteger(),
                  sa.ForeignKey("desktop_session_events.id", ondelete="CASCADE"), nullable=True),
        sa.Column("thought_id", UUID, sa.ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("experience_id", sa.BigInteger(),
                  sa.ForeignKey("game_experiences.id", ondelete="CASCADE"), nullable=True),
        *_times("created_at"),
        sa.CheckConstraint("num_nonnulls(item_id, event_id) = 1", name="ck_memory_evidence_target"),
        sa.CheckConstraint("num_nonnulls(message_id, session_event_id, thought_id, experience_id) = 1",
                           name="ck_memory_evidence_source"),
        sa.CheckConstraint("role IN ('origin', 'support', 'resolution', 'correction') "
                           "AND (role <> 'resolution' OR item_id IS NOT NULL)", name="ck_memory_evidence_role"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="CASCADE", name="fk_memory_evidence_item"),
        sa.ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                                ondelete="CASCADE", name="fk_memory_evidence_event"),
        sa.UniqueConstraint("item_id", "event_id", "role", "message_id", "session_event_id", "thought_id",
                            "experience_id", name="uq_memory_evidence", postgresql_nulls_not_distinct=True),
    )
    for column in ("item_id", "event_id", "message_id", "session_event_id", "thought_id", "experience_id"):
        op.create_index(f"ix_memory_evidence_{column.removesuffix('_id')}", "memory_evidence", [column])

    op.execute(PARENT_TRIGGERS)

    op.add_column("desktop_sessions", sa.Column("goal_item_id", UUID, nullable=True))
    op.create_foreign_key("fk_desktop_sessions_goal_item", "desktop_sessions", "working_items",
                          ["goal_item_id", "owner_user_id"], ["id", "user_id"], ondelete="SET NULL (goal_item_id)")
    op.create_index("ix_desktop_sessions_goal_item", "desktop_sessions", ["goal_item_id"],
                    postgresql_where=sa.text("goal_item_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_desktop_sessions_goal_item", table_name="desktop_sessions")
    op.drop_constraint("fk_desktop_sessions_goal_item", "desktop_sessions", type_="foreignkey")
    op.drop_column("desktop_sessions", "goal_item_id")
    op.drop_table("memory_evidence")
    op.drop_table("memory_manager_state")
    op.drop_table("memory_events")
    op.execute("DROP TRIGGER IF EXISTS working_items_parent_check ON working_items")
    op.drop_table("working_items")
    op.execute("DROP TRIGGER IF EXISTS memory_contexts_parent_check ON memory_contexts")
    op.drop_table("memory_contexts")
    op.execute("DROP FUNCTION IF EXISTS working_items_parent_check()")
    op.execute("DROP FUNCTION IF EXISTS memory_contexts_parent_check()")
