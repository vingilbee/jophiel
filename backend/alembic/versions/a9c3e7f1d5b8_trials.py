"""Learning step 4, predictions and bounded game trials (docs/learning-system/04-implementation.md).

- ``learning_trials``: one experiment, learning's authoritative record: the problem, its current proposal
  revision, cognition's selection, deferrals, limits and counted costs, execution and how it ended.
- ``learning_trial_revisions``: every proposal revision, whole, as learning made it before cognition saw it.
  Immutable, and none is added once a trial was selected (triggers).
- ``learning_trial_events``: what happened to a trial, in order; append-only (trigger).
- ``learning_trial_assessments``: each reading of a trial's evidence (measurements and prediction checks by code,
  the checked interpretation); later readings keep the earlier ones.
- ``learning_trial_triggers``: what learning may investigate (struggling / stuck steps, her hypotheses, an approach
  changed mid-trial); one pending per problem.
- ``game_skill_attempts.origin`` gains ``trial``: the execution of a selected trial.

Everything cascades with its user; revisions, events and assessments with their trial; triggers with their session.
A trial keeps its record when its session, attempt or memory items go (SET NULL). Downgrade drops all of it (the
data is gone) and refuses while trial attempts exist.

Revision ID: a9c3e7f1d5b8
Revises: f4a8c2e6b0d3
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9c3e7f1d5b8"
down_revision = "f4a8c2e6b0d3"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()

STATUSES = ("proposed", "presented", "deferred", "rejected", "expired", "selected", "preparing", "executing",
            "paused", "observing", "assessing", "assessed")
LIVE = ("selected", "preparing", "executing", "paused", "observing", "assessing")
CLOSED = ("rejected", "expired", "assessed")
HYPOTHESIS = ("supported", "contradicted", "unresolved")
TRIGGER_KINDS = ("struggling", "stuck", "hypothesis", "approach_changed")
TRIGGER_STATES = ("pending", "running", "done", "failed")

FUNCTIONS = """
CREATE OR REPLACE FUNCTION learning_trial_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%: rows are never rewritten', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION learning_trial_revision_open() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM learning_trials WHERE id = NEW.trial_id
               AND (selected_at IS NOT NULL OR user_id <> NEW.user_id)) THEN
        RAISE EXCEPTION 'learning_trial_revisions: the trial was selected (or is not this user''s)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
REVISION_TRIGGERS = """
CREATE TRIGGER learning_trial_revisions_immutable BEFORE UPDATE ON learning_trial_revisions
    FOR EACH ROW EXECUTE FUNCTION learning_trial_append_only();
CREATE TRIGGER learning_trial_revisions_open BEFORE INSERT ON learning_trial_revisions
    FOR EACH ROW EXECUTE FUNCTION learning_trial_revision_open();
"""
EVENT_TRIGGER = """
CREATE TRIGGER learning_trial_events_append_only BEFORE UPDATE ON learning_trial_events
    FOR EACH ROW WHEN (OLD.skill_attempt_id IS NOT DISTINCT FROM NEW.skill_attempt_id
                       AND OLD.experience_id IS NOT DISTINCT FROM NEW.experience_id)
    EXECUTE FUNCTION learning_trial_append_only();
"""


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _user():
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _trial():
    return sa.Column("trial_id", UUID, sa.ForeignKey("learning_trials.id", ondelete="CASCADE"), nullable=False)


def _ts(name, *, null=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=null, server_default=None if null else sa.func.now())


def _json(name, empty="'{}'::jsonb"):
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text(empty))


def upgrade() -> None:
    op.drop_constraint("ck_game_skill_attempts_origin", "game_skill_attempts", type_="check")
    op.create_check_constraint("ck_game_skill_attempts_origin", "game_skill_attempts",
                               "origin IN ('offer', 'user', 'self', 'trial')")

    op.create_table(
        "learning_trials",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("session_id", UUID, sa.ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("problem_key", sa.String(160), nullable=False),
        sa.Column("problem", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("hypothesis_item_id", UUID, nullable=True),
        sa.Column("reminder_item_id", UUID, nullable=True),
        sa.Column("linked_trial_id", UUID, sa.ForeignKey("learning_trials.id", ondelete="SET NULL"), nullable=True),
        sa.Column("selection", JSONB, nullable=True),
        sa.Column("selected_alternative", sa.String(8), nullable=True),
        sa.Column("selected_revision", sa.Integer, nullable=True),
        sa.Column("deferral", JSONB, nullable=True),
        _json("deferrals", "'[]'::jsonb"),
        _json("limits"),
        _json("costs"),
        sa.Column("attempt_id", UUID, sa.ForeignKey("game_skill_attempts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ending", sa.String(24), nullable=True),
        sa.Column("stop_reason", sa.Text, nullable=True),
        sa.Column("presentations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("decide_failures", sa.Integer, nullable=False, server_default="0"),
        _ts("next_check_at"),
        _ts("lease_expires_at", null=True),
        _ts("active_since", null=True),
        _ts("presented_at", null=True),
        _ts("selected_at", null=True),
        _ts("exec_started_at", null=True),
        _ts("exec_ended_at", null=True),
        _ts("window_ends_at", null=True),
        _ts("closed_at", null=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_learning_trials_status"),
        sa.CheckConstraint("revision >= 1", name="ck_learning_trials_revision"),
        sa.CheckConstraint(f"(closed_at IS NOT NULL) = ({_in('status', CLOSED)})", name="ck_learning_trials_closed"),
        sa.CheckConstraint(f"NOT ({_in('status', LIVE)} OR status = 'assessed') OR "
                           "(selection IS NOT NULL AND selected_alternative IS NOT NULL AND selected_revision IS NOT NULL)",
                           name="ck_learning_trials_selection"),
        sa.UniqueConstraint("id", "user_id", name="uq_learning_trials_id_user"),
        sa.ForeignKeyConstraint(["hypothesis_item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="SET NULL (hypothesis_item_id)", name="fk_learning_trials_hypothesis"),
        sa.ForeignKeyConstraint(["reminder_item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="SET NULL (reminder_item_id)", name="fk_learning_trials_reminder"),
    )
    op.create_index("uq_learning_trials_pending_problem", "learning_trials", ["user_id", "problem_key"], unique=True,
                    postgresql_where=sa.text("status IN ('proposed', 'presented', 'deferred')"))
    op.create_index("uq_learning_trials_live_session", "learning_trials", ["session_id"], unique=True,
                    postgresql_where=sa.text("status IN ('selected', 'preparing', 'executing', 'paused', 'observing', "
                                             "'assessing')"))
    op.create_index("ix_learning_trials_due", "learning_trials", ["next_check_at"],
                    postgresql_where=sa.text("closed_at IS NULL"))
    op.create_index("ix_learning_trials_user", "learning_trials", ["user_id", "created_at"])
    op.execute(FUNCTIONS)

    op.create_table(
        "learning_trial_revisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _trial(),
        _user(),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("proposal", JSONB, nullable=False),
        _json("dropped", "'[]'::jsonb"),
        sa.Column("reason", sa.Text, nullable=False),
        _json("evidence", "'[]'::jsonb"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("trial_id", "revision", name="uq_learning_trial_revisions"),
    )
    op.execute(REVISION_TRIGGERS)

    op.create_table(
        "learning_trial_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _trial(),
        _user(),
        sa.Column("kind", sa.String(24), nullable=False),
        _json("payload"),
        sa.Column("decision_attempt_id", UUID, nullable=True),
        sa.Column("skill_attempt_id", UUID, sa.ForeignKey("game_skill_attempts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("experience_id", sa.BigInteger, sa.ForeignKey("game_experiences.id", ondelete="SET NULL"),
                  nullable=True),
        _ts("at"),
    )
    op.create_index("ix_learning_trial_events_trial", "learning_trial_events", ["trial_id", "id"])
    op.execute(EVENT_TRIGGER)

    op.create_table(
        "learning_trial_assessments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _trial(),
        _user(),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("hypothesis", sa.String(16), nullable=False),
        sa.Column("test_started", sa.Boolean, nullable=False),
        sa.Column("measurements", JSONB, nullable=False),
        sa.Column("prediction_checks", JSONB, nullable=False),
        _json("missing", "'[]'::jsonb"),
        _json("result"),
        _json("dropped", "'[]'::jsonb"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("current", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        _ts("created_at"),
        sa.CheckConstraint(_in("hypothesis", HYPOTHESIS), name="ck_learning_trial_assessments_hypothesis"),
        sa.UniqueConstraint("trial_id", "n", name="uq_learning_trial_assessments"),
    )
    op.create_index("uq_learning_trial_assessments_current", "learning_trial_assessments", ["trial_id"], unique=True,
                    postgresql_where=sa.text("current"))

    op.create_table(
        "learning_trial_triggers",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("session_id", UUID, sa.ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("problem_key", sa.String(160), nullable=False),
        _json("payload"),
        sa.Column("state", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _ts("next_attempt_at"),
        _ts("lease_expires_at", null=True),
        sa.Column("last_error", sa.Text, nullable=True),
        _json("result"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(_in("kind", TRIGGER_KINDS), name="ck_learning_trial_triggers_kind"),
        sa.CheckConstraint(_in("state", TRIGGER_STATES), name="ck_learning_trial_triggers_state"),
    )
    op.create_index("uq_learning_trial_triggers_pending", "learning_trial_triggers", ["user_id", "problem_key"],
                    unique=True, postgresql_where=sa.text("state IN ('pending', 'running')"))
    op.create_index("ix_learning_trial_triggers_due", "learning_trial_triggers", ["next_attempt_at"],
                    postgresql_where=sa.text("state IN ('pending', 'running')"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM game_skill_attempts WHERE origin = 'trial' LIMIT 1")).scalar():
        raise RuntimeError("game_skill_attempts has trial attempts: delete them (scope trials/game) before downgrading")
    op.drop_table("learning_trial_triggers")
    op.drop_table("learning_trial_assessments")
    op.drop_table("learning_trial_events")
    op.drop_table("learning_trial_revisions")
    op.drop_table("learning_trials")
    op.execute("DROP FUNCTION IF EXISTS learning_trial_revision_open()")
    op.execute("DROP FUNCTION IF EXISTS learning_trial_append_only()")
    op.drop_constraint("ck_game_skill_attempts_origin", "game_skill_attempts", type_="check")
    op.create_check_constraint("ck_game_skill_attempts_origin", "game_skill_attempts",
                               "origin IN ('offer', 'user', 'self')")
