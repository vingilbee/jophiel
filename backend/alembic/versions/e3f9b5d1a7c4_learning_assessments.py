"""Learning step 2, evidence and attribution (docs/learning-system/02-implementation.md).

- ``learning_assessments``: one per thing worth assessing (a correction, a complaint, an execution failure, a
  sampled exchange), its job state and current conclusion.
- ``learning_assessment_evidence``: evidence attached beyond its window, once each.
- ``learning_assessment_revisions``: every evaluation, kept; a newer one supersedes an older one.
- ``learning_assessment_links``: what each revision targeted, blamed and cited, as opaque refs.
- ``learning_assessment_uses``: conclusions another owner acted on, flagged for reconsideration on revision.

Everything cascades with its user; assessments also with their conversation, game session and trigger message.
Downgrade drops all of it (the data is gone).

Revision ID: e3f9b5d1a7c4
Revises: d9b4e7a2c6f8
Create Date: 2026-11-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3f9b5d1a7c4"
down_revision = "d9b4e7a2c6f8"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _user():
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _assessment():
    return sa.Column("assessment_id", UUID, sa.ForeignKey("learning_assessments.id", ondelete="CASCADE"),
                     nullable=False)


def _ts(name, *, null=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=null, server_default=None if null else sa.func.now())


def _json(name, default="'{}'::jsonb"):
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text(default))


def _fk(name, table):
    return sa.Column(name, UUID, sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=True)


OUTCOMES = ("concluded", "unresolved", "no_feedback")
STOPS = ("concluded", "missing_evidence", "stuck")


def upgrade() -> None:
    op.create_table(
        "learning_assessments",
        sa.Column("id", UUID, primary_key=True), _user(),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("trigger_key", sa.String(160), nullable=False), _json("trigger"),
        _fk("conversation_id", "conversations"), _fk("game_session_id", "desktop_sessions"),
        _fk("anchor_message_id", "chat_messages"),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), _ts("next_attempt_at"),
        _ts("lease_expires_at", null=True), sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()), sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(16)), sa.Column("target_status", sa.String(16)),
        sa.Column("cause_status", sa.String(16)), sa.Column("stop_reason", sa.String(24)),
        sa.Column("explanation", sa.Text()), _json("awaiting", "'[]'::jsonb"), _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(_in("trigger_kind", ("correction", "complaint", "execution", "sample")),
                           name="ck_learning_assessments_trigger"),
        sa.CheckConstraint(_in("state", ("pending", "running", "done", "failed")),
                           name="ck_learning_assessments_state"),
        sa.CheckConstraint(f"outcome IS NULL OR {_in('outcome', OUTCOMES)}", name="ck_learning_assessments_outcome"),
        sa.CheckConstraint(f"stop_reason IS NULL OR {_in('stop_reason', STOPS)}", name="ck_learning_assessments_stop"),
        sa.CheckConstraint("attempts >= 0 AND revision >= 0 AND epoch >= 0", name="ck_learning_assessments_counts"),
        sa.UniqueConstraint("user_id", "trigger_key", name="uq_learning_assessments_trigger"))
    op.create_index("ix_learning_assessments_due", "learning_assessments", ["next_attempt_at"],
                    postgresql_where=sa.text("state IN ('pending', 'running')"))
    op.create_index("ix_learning_assessments_user", "learning_assessments", ["user_id", "updated_at"])
    op.create_index("ix_learning_assessments_conversation", "learning_assessments", ["conversation_id"])

    op.create_table(
        "learning_assessment_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), _assessment(), _user(),
        sa.Column("ref", sa.String(100), nullable=False), sa.Column("reason", sa.String(300)), _json("detail"),
        _ts("added_at"),
        sa.UniqueConstraint("assessment_id", "ref", name="uq_learning_assessment_evidence"))
    op.create_index("ix_learning_assessment_evidence_ref", "learning_assessment_evidence", ["user_id", "ref"])

    op.create_table(
        "learning_assessment_revisions",
        sa.Column("id", UUID, primary_key=True), _assessment(), _user(),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("evaluator", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(24), nullable=False), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.String(32), nullable=False), sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("result", JSONB, nullable=False), _json("dropped", "'[]'::jsonb"), _json("rounds", "'[]'::jsonb"),
        sa.Column("outcome", sa.String(16), nullable=False), sa.Column("target_status", sa.String(16), nullable=False),
        sa.Column("cause_status", sa.String(16), nullable=False), sa.Column("stop_reason", sa.String(24), nullable=False),
        sa.Column("independent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""), _json("usage"),
        _ts("superseded_at", null=True), sa.Column("superseded_by", sa.Integer()), _ts("created_at"),
        sa.CheckConstraint(_in("outcome", OUTCOMES), name="ck_learning_assessment_revisions_outcome"),
        sa.CheckConstraint(_in("stop_reason", STOPS), name="ck_learning_assessment_revisions_stop"),
        sa.UniqueConstraint("assessment_id", "revision", name="uq_learning_assessment_revisions"))

    op.create_table(
        "learning_assessment_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), _assessment(), _user(),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("role", sa.String(12), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("component", sa.String(16), nullable=False, server_default=""), sa.Column("status", sa.String(16)),
        sa.CheckConstraint(_in("role", ("target", "cause", "cited")), name="ck_learning_assessment_links_role"),
        sa.UniqueConstraint("assessment_id", "revision", "role", "ref", "component",
                            name="uq_learning_assessment_links"))
    op.create_index("ix_learning_assessment_links_ref", "learning_assessment_links", ["user_id", "ref"])

    op.create_table(
        "learning_assessment_uses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), _assessment(), _user(),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("consumer", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="current"), sa.Column("reason", sa.Text()),
        _ts("noted_at"), _ts("changed_at", null=True),
        sa.CheckConstraint(_in("state", ("current", "reconsider")), name="ck_learning_assessment_uses_state"),
        sa.UniqueConstraint("assessment_id", "consumer", "ref", name="uq_learning_assessment_uses"))
    op.create_index("ix_learning_assessment_uses_ref", "learning_assessment_uses", ["user_id", "consumer", "ref"])


def downgrade() -> None:
    for table in ("learning_assessment_uses", "learning_assessment_links", "learning_assessment_revisions",
                  "learning_assessment_evidence", "learning_assessments"):
        op.drop_table(table)
