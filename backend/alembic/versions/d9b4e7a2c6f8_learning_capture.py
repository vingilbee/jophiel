"""Learning step 1, capture and ownership (docs/learning-system/01-implementation.md).

- ``capture_blobs``: exact unchanging content attempts refer to by hash (per user).
- ``decision_attempts`` + ``decision_attempt_sources``: what each learner was given and produced, and what it read.
- ``decision_executions``: what actually happened to a decision (effect, reply, heard, channel delivery, skill run).
- ``capture_gaps``: captures known to be lost.
- ``training_examples`` + ``training_example_sources`` + ``training_example_deletions``: learning's derivatives,
  independent of source deletion; opaque source ids, tombstones for explicit deletion.

Raw captures cascade with their user, conversation, message and game session; training examples only with their
user. Downgrade drops all of it (the data is gone).

Revision ID: d9b4e7a2c6f8
Revises: c8e4a6f2d9b3
Create Date: 2026-11-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9b4e7a2c6f8"
down_revision = "c8e4a6f2d9b3"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
LEARNERS = "('student_1', 'student_2', 'hands')"


def _user():
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _ts(name, *, null=False, default=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=null,
                     server_default=sa.func.now() if default and not null else None)


def _json(name, default="'{}'::jsonb", null=False):
    return sa.Column(name, JSONB, nullable=null, server_default=None if null else sa.text(default))


def _fk(name, table):
    return sa.Column(name, UUID, sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=True)


def upgrade() -> None:
    op.create_table(
        "capture_blobs", _user(), sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False), _ts("created_at"), _ts("last_used_at"),
        sa.PrimaryKeyConstraint("user_id", "sha256"))

    op.create_table(
        "decision_attempts",
        sa.Column("id", UUID, primary_key=True), _user(),
        sa.Column("learner", sa.String(24), nullable=False), sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(24)), sa.Column("model", sa.String(160)), sa.Column("turn_id", UUID),
        _fk("conversation_id", "conversations"), _fk("source_message_id", "chat_messages"),
        _fk("game_session_id", "desktop_sessions"), sa.Column("prior_attempt_id", UUID),
        _json("upstream"), _json("versions"), _json("settings"), sa.Column("input", JSONB, nullable=False),
        _json("input_refs", "'[]'::jsonb"), _json("output", null=True), _json("absent", "'[]'::jsonb"),
        sa.Column("prediction_ref", sa.String(80)), _json("selection", null=True),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("error", sa.Text()),
        _json("blob_hashes", "'[]'::jsonb"), _ts("started_at", default=False),
        _ts("produced_at", null=True), _ts("captured_at"), _ts("expires_at", default=False),
        sa.CheckConstraint(f"learner IN {LEARNERS}", name="ck_decision_attempts_learner"),
        sa.CheckConstraint("status IN ('ok', 'invalid', 'unavailable', 'error')", name="ck_decision_attempts_status"))
    op.create_index("ix_decision_attempts_user", "decision_attempts", ["user_id", "captured_at"])
    op.create_index("ix_decision_attempts_turn", "decision_attempts", ["turn_id"])
    op.create_index("ix_decision_attempts_session", "decision_attempts", ["game_session_id"])
    op.create_index("ix_decision_attempts_conversation", "decision_attempts", ["conversation_id", "started_at"])
    op.create_index("ix_decision_attempts_expires", "decision_attempts", ["expires_at"])
    op.create_index("ix_decision_attempts_blobs", "decision_attempts", ["blob_hashes"], postgresql_using="gin")

    op.create_table(
        "decision_attempt_sources",
        sa.Column("attempt_id", UUID, sa.ForeignKey("decision_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ref", sa.String(80), nullable=False), _user(), sa.PrimaryKeyConstraint("attempt_id", "ref"))
    op.create_index("ix_decision_attempt_sources_ref", "decision_attempt_sources", ["user_id", "ref"])

    op.create_table(
        "decision_executions",
        sa.Column("id", UUID, primary_key=True), _user(), sa.Column("attempt_id", UUID), sa.Column("turn_id", UUID),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("executor", sa.String(40), nullable=False),
        _fk("conversation_id", "conversations"), _fk("assistant_message_id", "chat_messages"),
        _fk("game_session_id", "desktop_sessions"), _json("intended"), _json("executed"),
        sa.Column("result", sa.String(16), nullable=False), sa.Column("deviation", sa.Text()),
        _ts("started_at", null=True), _ts("ended_at", null=True), _ts("captured_at"), _ts("expires_at", default=False),
        sa.CheckConstraint("kind IN ('effect', 'reply', 'heard', 'channel_delivery', 'skill_run')",
                           name="ck_decision_executions_kind"),
        sa.CheckConstraint("result IN ('succeeded', 'deviated', 'failed', 'unknown', 'not_executed', 'cancelled', "
                           "'handed_off', 'not_sent')", name="ck_decision_executions_result"))
    op.create_index("ix_decision_executions_attempt", "decision_executions", ["attempt_id"])
    op.create_index("ix_decision_executions_turn", "decision_executions", ["turn_id"])
    op.create_index("ix_decision_executions_message", "decision_executions", ["assistant_message_id"])
    op.create_index("ix_decision_executions_user", "decision_executions", ["user_id", "captured_at"])
    op.create_index("ix_decision_executions_expires", "decision_executions", ["expires_at"])

    op.create_table(
        "capture_gaps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), _user(),
        sa.Column("record_kind", sa.String(16), nullable=False), sa.Column("record_id", UUID, nullable=False),
        sa.Column("label", sa.String(40), nullable=False), sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False, server_default="0"), sa.Column("detail", sa.Text()),
        sa.Column("turn_id", UUID), _ts("detected_at"),
        sa.CheckConstraint("reason IN ('queue_full', 'retries_exhausted', 'unserializable', 'source_gone', "
                           "'shutdown')", name="ck_capture_gaps_reason"),
        sa.UniqueConstraint("record_id", name="uq_capture_gaps_record"))
    op.create_index("ix_capture_gaps_user", "capture_gaps", ["user_id", "detected_at"])

    op.create_table(
        "training_examples",
        sa.Column("id", UUID, primary_key=True), _user(), sa.Column("learner", sa.String(24), nullable=False),
        sa.Column("attempt_id", UUID, nullable=False), sa.Column("label_source", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(12), nullable=False, server_default="real"),
        sa.Column("input", JSONB, nullable=False), sa.Column("target", JSONB, nullable=False),
        _json("versions"), _json("evidence"),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="unreviewed"),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ineligible_reason", sa.Text()), _ts("decided_at", default=False), _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(f"learner IN {LEARNERS}", name="ck_training_examples_learner"),
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


def downgrade() -> None:
    for table in ("training_example_deletions", "training_example_sources", "training_examples", "capture_gaps",
                  "decision_executions", "decision_attempt_sources", "decision_attempts", "capture_blobs"):
        op.drop_table(table)
