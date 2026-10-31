"""Claims, entities and time (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md stage 3).

- ``memory_entities``: who or what memories are about (the user's sister, their dog), owner-keyed.
- ``semantic_memories``: a fact can be one claim (``entity_id``, ``attribute``, ``value``, ``qualifier``,
  ``historical``). Existing facts keep their ids and content and get none of these: they become claims only
  when the user next changes them (no bulk rewrite, no guessed entities).
- Time fields (``time_words``, ``time_start``/``time_end``, ``time_precision``, ``time_inferred``,
  ``time_zone``, ``time_repeats``, ``time_said_at``) on facts, episodes and working items: the user's words, and the interval
  read from them on the user's calendar when said. Existing rows have none (nothing is guessed).
- ``working_items``: a new kind ``plan`` (planned / needs_review / happened / cancelled) with ``user_words``.
- ``memory_entity_links``: the entities a plan or an episode involves.
- ``memory_evidence``: two more owner-checked targets, ``fact_id`` and ``entity_id``. Each existing fact gets
  an ``origin`` link to its source message, only where that message is the owner's own (others are left
  unlinked, never granted).

Downgrade drops the new tables and columns; it refuses while plans exist, since they can't be kept as any
older kind.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4d8f2c6e9b1"
down_revision = "f7c3a9e1b5d2"
branch_labels = None
depends_on = None

TIME_PRECISIONS = ("day", "days", "week", "month", "season", "year", "unknown", "repeating")
OLD_KINDS = ("goal", "intention", "commitment", "question", "hypothesis", "decision")
OLD_STATUSES = {
    "goal": ("active", "suspended", "completed", "abandoned"),
    "commitment": ("open", "needs_review", "fulfilled", "cancelled"),
    "intention": ("active", "done", "dropped", "lapsed"),
    "question": ("open", "answered", "dropped"),
    "hypothesis": ("open", "supported", "disproved", "unresolved"),
    "decision": ("active", "superseded", "withdrawn"),
}
NEW_STATUSES = {**OLD_STATUSES, "plan": ("planned", "needs_review", "happened", "cancelled")}


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _status_check(statuses: dict) -> str:
    return " OR ".join(f"(kind = '{kind}' AND status IN ({_in(values)}))" for kind, values in statuses.items())


def _time_columns(table: str, short: str) -> None:
    op.add_column(table, sa.Column("time_words", sa.Text(), nullable=True))
    op.add_column(table, sa.Column("time_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("time_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("time_precision", sa.String(16), nullable=True))
    op.add_column(table, sa.Column("time_inferred", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column(table, sa.Column("time_zone", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("time_repeats", postgresql.JSONB(), nullable=True))
    op.add_column(table, sa.Column("time_said_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        f"ck_{short}_time_bounds", table,
        "(time_start IS NULL) = (time_end IS NULL) AND (time_start IS NULL OR time_start < time_end)")
    op.create_check_constraint(f"ck_{short}_time_precision", table,
                               f"time_precision IS NULL OR time_precision IN ({_in(TIME_PRECISIONS)})")
    op.create_check_constraint(
        f"ck_{short}_time_words", table,
        "(time_words IS NULL) = (time_precision IS NULL) AND (time_words IS NULL) = (time_said_at IS NULL) "
        "AND (time_words IS NULL OR btrim(time_words) <> '')")
    op.create_check_constraint(f"ck_{short}_time_unknown", table,
                               "time_start IS NULL OR time_precision <> 'unknown'")
    op.create_check_constraint(f"ck_{short}_time_repeats", table,
                               "(time_repeats IS NOT NULL) = (time_precision IS NOT DISTINCT FROM 'repeating')")


def _drop_time_columns(table: str, short: str) -> None:
    for name in ("bounds", "precision", "words", "unknown", "repeats"):
        op.drop_constraint(f"ck_{short}_time_{name}", table, type_="check")
    for column in ("time_said_at", "time_repeats", "time_zone", "time_inferred", "time_precision", "time_end", "time_start",
                   "time_words"):
        op.drop_column(table, column)


def upgrade() -> None:
    op.create_table(
        "memory_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("relation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('person', 'pet', 'place', 'organization', 'activity', 'thing', 'other')",
                           name="ck_memory_entities_kind"),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_memory_entities_status"),
        sa.CheckConstraint("coalesce(btrim(name), '') <> '' OR coalesce(btrim(relation), '') <> ''",
                           name="ck_memory_entities_named"),
        sa.UniqueConstraint("id", "user_id", name="uq_memory_entities_id_user"),
    )
    op.create_index("ix_memory_entities_user", "memory_entities", ["user_id", "status"])

    # Claims on facts.
    op.add_column("semantic_memories", sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("semantic_memories", sa.Column("attribute", sa.String(120), nullable=True))
    op.add_column("semantic_memories", sa.Column("value", sa.Text(), nullable=True))
    op.add_column("semantic_memories", sa.Column("qualifier", sa.Text(), nullable=True))
    op.add_column("semantic_memories", sa.Column("historical", sa.Boolean(), nullable=False,
                                                 server_default="false"))
    op.create_foreign_key("fk_semantic_memories_entity", "semantic_memories", "memory_entities",
                          ["entity_id", "user_id"], ["id", "user_id"], ondelete="SET NULL (entity_id)")
    op.create_index("ix_semantic_memories_entity", "semantic_memories", ["entity_id"],
                    postgresql_where=sa.text("entity_id IS NOT NULL"))
    op.create_check_constraint("ck_semantic_memory_attribute", "semantic_memories",
                               "attribute IS NULL OR btrim(attribute) <> ''")
    _time_columns("semantic_memories", "semantic_memory")
    _time_columns("episodic_memories", "episodic_memory")

    # Plans as working items.
    _time_columns("working_items", "working_items")
    op.add_column("working_items", sa.Column("user_words", sa.Text(), nullable=True))
    op.drop_constraint("ck_working_items_kind", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_kind", "working_items", f"kind IN ({_in((*OLD_KINDS, 'plan'))})")
    op.drop_constraint("ck_working_items_status", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_status", "working_items", _status_check(NEW_STATUSES))
    # The closed set as the model defines it (WORKING_ITEM_CLOSED), "happened" added.
    closed = ("abandoned", "answered", "cancelled", "completed", "disproved", "done", "dropped", "fulfilled",
              "happened", "lapsed", "superseded", "supported", "unresolved", "withdrawn")
    op.drop_constraint("ck_working_items_closed", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_closed", "working_items",
                               f"(closed_at IS NOT NULL) = (status IN ({_in(closed)}))")
    op.create_check_constraint("ck_working_items_plan_words", "working_items",
                               "kind <> 'plan' OR coalesce(btrim(user_words), '') <> ''")

    op.create_table(
        "memory_entity_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("num_nonnulls(item_id, episode_id) = 1", name="ck_memory_entity_links_target"),
        sa.CheckConstraint("role IN ('participant', 'place', 'about')", name="ck_memory_entity_links_role"),
        sa.ForeignKeyConstraint(["entity_id", "user_id"], ["memory_entities.id", "memory_entities.user_id"],
                                ondelete="CASCADE", name="fk_memory_entity_links_entity"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="CASCADE", name="fk_memory_entity_links_item"),
        sa.ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                                ondelete="CASCADE", name="fk_memory_entity_links_episode"),
        sa.UniqueConstraint("entity_id", "item_id", "episode_id", "role", name="uq_memory_entity_links",
                            postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_memory_entity_links_entity", "memory_entity_links", ["entity_id"])
    op.create_index("ix_memory_entity_links_item", "memory_entity_links", ["item_id"],
                    postgresql_where=sa.text("item_id IS NOT NULL"))
    op.create_index("ix_memory_entity_links_episode", "memory_entity_links", ["episode_id"],
                    postgresql_where=sa.text("episode_id IS NOT NULL"))

    # Evidence for facts and entities.
    op.add_column("memory_evidence", sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("memory_evidence", sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.drop_constraint("ck_memory_evidence_target", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_target", "memory_evidence",
                               "num_nonnulls(item_id, event_id, fact_id, entity_id) = 1")
    op.create_foreign_key("fk_memory_evidence_fact", "memory_evidence", "semantic_memories",
                          ["fact_id", "user_id"], ["id", "user_id"], ondelete="CASCADE")
    op.create_foreign_key("fk_memory_evidence_entity", "memory_evidence", "memory_entities",
                          ["entity_id", "user_id"], ["id", "user_id"], ondelete="CASCADE")
    op.drop_constraint("uq_memory_evidence", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "uq_memory_evidence", "memory_evidence",
        ["item_id", "event_id", "fact_id", "entity_id", "role", "message_id", "session_event_id", "thought_id",
         "experience_id", "observation_id"], postgresql_nulls_not_distinct=True)
    op.create_index("ix_memory_evidence_fact", "memory_evidence", ["fact_id"],
                    postgresql_where=sa.text("fact_id IS NOT NULL"))
    op.create_index("ix_memory_evidence_entity", "memory_evidence", ["entity_id"],
                    postgresql_where=sa.text("entity_id IS NOT NULL"))
    # Where each existing fact came from, only from the owner's own message.
    op.execute("""
        INSERT INTO memory_evidence (user_id, fact_id, role, message_id)
        SELECT f.user_id, f.id, 'origin', f.source_message_id
        FROM semantic_memories f
        JOIN chat_messages m ON m.id = f.source_message_id
        JOIN conversations c ON c.id = m.conversation_id AND c.user_id = f.user_id
        WHERE f.source_message_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM working_items WHERE kind = 'plan'")).scalar():
        raise RuntimeError("working_items holds plans: they have no older kind to become; remove them first")
    op.drop_index("ix_memory_evidence_entity", table_name="memory_evidence")
    op.drop_index("ix_memory_evidence_fact", table_name="memory_evidence")
    op.execute("DELETE FROM memory_evidence WHERE fact_id IS NOT NULL OR entity_id IS NOT NULL")
    op.drop_constraint("uq_memory_evidence", "memory_evidence", type_="unique")
    op.create_unique_constraint(
        "uq_memory_evidence", "memory_evidence",
        ["item_id", "event_id", "role", "message_id", "session_event_id", "thought_id", "experience_id",
         "observation_id"], postgresql_nulls_not_distinct=True)
    op.drop_constraint("fk_memory_evidence_entity", "memory_evidence", type_="foreignkey")
    op.drop_constraint("fk_memory_evidence_fact", "memory_evidence", type_="foreignkey")
    op.drop_constraint("ck_memory_evidence_target", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_target", "memory_evidence", "num_nonnulls(item_id, event_id) = 1")
    op.drop_column("memory_evidence", "entity_id")
    op.drop_column("memory_evidence", "fact_id")

    op.drop_table("memory_entity_links")
    op.drop_constraint("ck_working_items_plan_words", "working_items", type_="check")
    closed = ("abandoned", "answered", "cancelled", "completed", "disproved", "done", "dropped", "fulfilled",
              "lapsed", "superseded", "supported", "unresolved", "withdrawn")
    op.drop_constraint("ck_working_items_closed", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_closed", "working_items",
                               f"(closed_at IS NOT NULL) = (status IN ({_in(closed)}))")
    op.drop_constraint("ck_working_items_status", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_status", "working_items", _status_check(OLD_STATUSES))
    op.drop_constraint("ck_working_items_kind", "working_items", type_="check")
    op.create_check_constraint("ck_working_items_kind", "working_items", f"kind IN ({_in(OLD_KINDS)})")
    op.drop_column("working_items", "user_words")
    _drop_time_columns("working_items", "working_items")
    _drop_time_columns("episodic_memories", "episodic_memory")
    _drop_time_columns("semantic_memories", "semantic_memory")
    op.drop_constraint("ck_semantic_memory_attribute", "semantic_memories", type_="check")
    op.drop_index("ix_semantic_memories_entity", table_name="semantic_memories")
    op.drop_constraint("fk_semantic_memories_entity", "semantic_memories", type_="foreignkey")
    for column in ("historical", "qualifier", "value", "attribute", "entity_id"):
        op.drop_column("semantic_memories", column)
    op.drop_index("ix_memory_entities_user", table_name="memory_entities")
    op.drop_table("memory_entities")
