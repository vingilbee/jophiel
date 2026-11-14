"""Her mind owns what cognition did: retire the cognition stores (docs/MIND_ARCHITECTURE.md).

Upgrade:

- Open conversation notes the retired notebook kept (``working_memories.cognitive_state.simple_live.notes``) move to
  working memory, so no open state is lost: an unanswered question of theirs or hers becomes an open ``question``,
  a promise of hers an open ``commitment``, an offer of hers an ``intention`` (lapsing after a day, as intentions
  do). Each goes into the user's focused context. Notes that only described the exchange ("they corrected you",
  "they shared something") are memory's already (facts, corrections) and are not copied. An identical open item is
  not duplicated.
- ``working_memories`` (the per-conversation cognitive snapshot) is dropped.
- ``training_examples`` and their sources and tombstones (derivatives of Student 2's decisions) are dropped.
- Retired capture: Student 1 / Student 2 attempts (their sources cascade), ``effect`` executions, lesson uses by
  ``student_1`` / ``student_2``, assessment uses by ``training_examples``, and ``cognition`` coordinator work.
  Assessments, lessons and memory evidence stay.
- Constraints: learners ``mind`` / ``hands`` / ``planner`` / ``hard_thought``; execution kinds without ``effect``;
  coordinator work kinds ``recall`` / ``mind`` / ``wake`` / ``memory`` / ``observation``.

Downgrade recreates empty tables and the old constraints; deleted rows and moved notes are not restored, and her
mind's own records (``mind`` attempts, ``wake`` work) are deleted so the old constraints hold.

Revision ID: b7e2c9f4a1d6
Revises: a6d2f8c4e9b1
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e2c9f4a1d6"
down_revision = "a6d2f8c4e9b1"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()

LEARNERS = "('mind', 'hands', 'planner', 'hard_thought')"
OLD_LEARNERS = "('student_1', 'student_2', 'hands', 'planner', 'hard_thought')"
KINDS = ("('reply', 'heard', 'channel_delivery', 'skill_run', 'plan_apply', 'thought_route', 'step_check', "
         "'desktop_action')")
OLD_KINDS = ("('effect', 'reply', 'heard', 'channel_delivery', 'skill_run', 'plan_apply', 'thought_route', "
             "'step_check', 'desktop_action')")
WORK_KINDS = "('recall', 'mind', 'wake', 'memory', 'observation')"
OLD_WORK_KINDS = "('recall', 'mind', 'memory', 'observation', 'cognition')"

# note kind -> (item kind, owner, owed_to, epistemic, status, due)
NOTES = {
    "their_open_question": ("question", "user", None, "reported", "open", None),
    "her_open_question": ("question", "columbina", None, "inferred", "open", None),
    "her_commitment": ("commitment", "columbina", "user", "reported", "open", None),
    "her_offer": ("intention", "columbina", None, "inferred", "active", "1 day"),
}


def _check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def _move_notes() -> None:
    for note_kind, (kind, owner, owed_to, epistemic, status, due) in NOTES.items():
        op.execute(sa.text(f"""
            INSERT INTO working_items (id, user_id, context_id, kind, owner, owed_to, text, status, epistemic,
                                       reason, due_at, revision, checked_facts, time_inferred,
                                       created_at, updated_at)
            SELECT gen_random_uuid(), wm.user_id, st.focus_context_id, :kind, :owner, :owed_to,
                   left(btrim(note->>'text'), 2000), :status, :epistemic,
                   'kept from the retired conversation notes', {"now() + interval '" + due + "'" if due else "NULL"},
                   1, '{{}}'::jsonb, false, now(), now()
            FROM working_memories wm
            JOIN memory_manager_state st ON st.user_id = wm.user_id
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(wm.cognitive_state->'simple_live'->'notes') = 'array'
                     THEN wm.cognitive_state->'simple_live'->'notes' ELSE '[]'::jsonb END) AS note
            WHERE note->>'kind' = :note_kind
              AND coalesce(btrim(note->>'text'), '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM working_items wi
                WHERE wi.user_id = wm.user_id AND wi.kind = :kind AND wi.closed_at IS NULL
                  AND wi.text = left(btrim(note->>'text'), 2000))
        """).bindparams(kind=kind, owner=owner, owed_to=owed_to, status=status, epistemic=epistemic,
                        note_kind=note_kind))


def upgrade() -> None:
    _move_notes()
    op.drop_table("working_memories")
    for table in ("training_example_sources", "training_example_deletions", "training_examples"):
        op.drop_table(table)
    op.execute("DELETE FROM decision_attempts WHERE learner IN ('student_1', 'student_2')")
    op.execute("DELETE FROM decision_executions WHERE kind = 'effect'")
    op.execute("DELETE FROM memory_lesson_uses WHERE consumer IN ('student_1', 'student_2')")
    op.execute("DELETE FROM learning_assessment_uses WHERE consumer = 'training_examples'")
    op.execute("UPDATE coordinator_outputs SET work_id = NULL WHERE work_id IN "
               "(SELECT id FROM coordinator_work WHERE kind = 'cognition')")
    op.execute("DELETE FROM coordinator_work WHERE kind = 'cognition'")
    _check("decision_attempts", "ck_decision_attempts_learner", f"learner IN {LEARNERS}")
    _check("decision_executions", "ck_decision_executions_kind", f"kind IN {KINDS}")
    _check("coordinator_work", "ck_coordinator_work_kind", f"kind IN {WORK_KINDS}")


def _ts(name: str, *, default: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False,
                     **({"server_default": sa.func.now()} if default else {}))


def _user() -> sa.Column:
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def downgrade() -> None:
    op.execute("DELETE FROM decision_attempts WHERE learner = 'mind'")
    op.execute("UPDATE coordinator_outputs SET work_id = NULL WHERE work_id IN "
               "(SELECT id FROM coordinator_work WHERE kind = 'wake')")
    op.execute("DELETE FROM coordinator_work WHERE kind = 'wake'")
    _check("coordinator_work", "ck_coordinator_work_kind", f"kind IN {OLD_WORK_KINDS}")
    _check("decision_executions", "ck_decision_executions_kind", f"kind IN {OLD_KINDS}")
    _check("decision_attempts", "ck_decision_attempts_learner", f"learner IN {OLD_LEARNERS}")
    op.create_table(
        "training_examples",
        sa.Column("id", UUID, primary_key=True), _user(), sa.Column("learner", sa.String(24), nullable=False),
        sa.Column("attempt_id", UUID, nullable=False), sa.Column("label_source", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(12), nullable=False, server_default="real"),
        sa.Column("input", JSONB, nullable=False), sa.Column("target", JSONB, nullable=False),
        sa.Column("versions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="unreviewed"),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ineligible_reason", sa.Text()), _ts("decided_at", default=False), _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(f"learner IN {OLD_LEARNERS}", name="ck_training_examples_learner"),
        sa.CheckConstraint("review_status IN ('unreviewed', 'needs_review', 'accepted', 'rejected')",
                           name="ck_training_examples_review"),
        sa.CheckConstraint("origin IN ('real', 'synthetic')", name="ck_training_examples_origin"),
        sa.UniqueConstraint("attempt_id", "learner", "label_source", name="uq_training_examples_derivation"))
    op.create_index("ix_training_examples_user", "training_examples", ["user_id", "learner", "eligible"])
    op.create_table(
        "training_example_sources",
        sa.Column("example_id", UUID, sa.ForeignKey("training_examples.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ref", sa.String(80), nullable=False), _user(), sa.PrimaryKeyConstraint("example_id", "ref"))
    op.create_index("ix_training_example_sources_ref", "training_example_sources", ["user_id", "ref"])
    op.create_table(
        "training_example_deletions",
        sa.Column("example_id", UUID, primary_key=True), _user(), sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("learner", sa.String(24), nullable=False), sa.Column("label_source", sa.String(32), nullable=False),
        _ts("deleted_at"))
    op.create_index("ix_training_example_deletions_attempt", "training_example_deletions", ["attempt_id"])
    op.create_table(
        "working_memories",
        sa.Column("id", UUID, nullable=False), sa.Column("user_id", UUID, nullable=False),
        sa.Column("conversation_id", UUID, nullable=False),
        sa.Column("mode", sa.String(16), server_default="agent", nullable=False),
        sa.Column("context_window_size", sa.Integer(), server_default="30", nullable=False),
        sa.Column("current_topic", sa.Text(), nullable=True),
        sa.Column("active_goals", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("unresolved_questions", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("cognitive_state", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("retrieved_memories", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("last_message_id", UUID, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("mode IN ('agent', 'cognitive')", name="ck_working_memory_mode"),
        sa.CheckConstraint("context_window_size BETWEEN 1 AND 200", name="ck_working_memory_context_window"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("conversation_id"))
    op.create_index("ix_working_memories_user_id", "working_memories", ["user_id"])
