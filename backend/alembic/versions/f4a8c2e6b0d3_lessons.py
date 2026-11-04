"""Learning step 3, immediate adaptation: lessons (docs/learning-system/03-implementation.md).

- ``memory_lessons``: a lesson as it stands (memory owns it): a preference the user stated, or a strategy she
  inferred, with its scope, where it applies, its exceptions, status and revision.
- ``memory_lesson_revisions``: every revision, whole, with the operation and the assessment revision behind it.
- ``memory_lesson_evidence``: support and counterevidence, once per occasion and role.
- ``memory_lesson_uses``: which lesson revision each decision had, whether it reached the consumer, and what the
  consumer said about it.
- ``learning_lesson_jobs``: learning's reading of one assessment revision for lessons (leased, retried).

Everything cascades with its user; evidence, revisions and uses with their lesson; a job with its assessment.
Downgrade drops all of it (the data is gone).

Revision ID: f4a8c2e6b0d3
Revises: e3f9b5d1a7c4
Create Date: 2026-11-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f4a8c2e6b0d3"
down_revision = "e3f9b5d1a7c4"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()

KINDS = ("preference", "strategy")
ORIGINS = ("stated", "inferred")
SCOPES = ("standing", "situation", "episode")
STATUSES = ("accepted", "hypothesis", "episode_only", "withdrawn")
ROLES = ("support", "counter")
OPS = ("create", "support", "counter", "narrow", "revise", "withdraw", "reconsider")
REPORTED = ("applied", "not_relevant", "outweighed", "conflict", "unclear")
JOB_STATES = ("pending", "running", "done", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _user():
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _lesson():
    return sa.Column("lesson_id", UUID, sa.ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False)


def _ts(name, *, null=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=null, server_default=None if null else sa.func.now())


def _list(name):
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "memory_lessons",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("applies_when", sa.Text, nullable=False),
        _list("exceptions"),
        _list("user_words"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        _list("conflicts_with"),
        sa.Column("status_reason", sa.Text, nullable=True),
        _ts("withdrawn_at", null=True),
        _ts("withdrawn_occasion_at", null=True),
        sa.Column("embedding", JSONB, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(_in("kind", KINDS), name="ck_memory_lessons_kind"),
        sa.CheckConstraint(_in("origin", ORIGINS), name="ck_memory_lessons_origin"),
        sa.CheckConstraint(_in("scope", SCOPES), name="ck_memory_lessons_scope"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_memory_lessons_status"),
        sa.CheckConstraint("btrim(statement) <> '' AND btrim(applies_when) <> ''", name="ck_memory_lessons_text"),
        sa.CheckConstraint("revision >= 1", name="ck_memory_lessons_revision"),
        sa.CheckConstraint("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name="ck_memory_lessons_withdrawn"),
        sa.CheckConstraint("(kind = 'preference') = (origin = 'stated')", name="ck_memory_lessons_origin_kind"),
        sa.UniqueConstraint("id", "user_id", name="uq_memory_lessons_id_user"),
    )
    op.create_index("ix_memory_lessons_user", "memory_lessons", ["user_id", "status"])

    op.create_table(
        "memory_lesson_revisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _lesson(),
        _user(),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("op", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("applies_when", sa.Text, nullable=False),
        _list("exceptions"),
        _list("user_words"),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("assessment_id", UUID, nullable=True),
        sa.Column("assessment_revision", sa.Integer, nullable=True),
        sa.Column("job_id", UUID, nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(_in("op", OPS), name="ck_memory_lesson_revisions_op"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_memory_lesson_revisions_status"),
        sa.UniqueConstraint("lesson_id", "revision", name="uq_memory_lesson_revisions"),
    )

    op.create_table(
        "memory_lesson_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _lesson(),
        _user(),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("occasion", sa.String(160), nullable=False),
        sa.Column("occasion_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessment_id", UUID, nullable=True),
        sa.Column("assessment_revision", sa.Integer, nullable=True),
        _list("refs"),
        sa.Column("quote", sa.Text, nullable=True),
        sa.Column("why", sa.Text, nullable=False, server_default=""),
        sa.Column("current", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("added_revision", sa.Integer, nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(_in("role", ROLES), name="ck_memory_lesson_evidence_role"),
        sa.UniqueConstraint("lesson_id", "role", "occasion", name="uq_memory_lesson_evidence"),
    )
    op.create_index("ix_memory_lesson_evidence_assessment", "memory_lesson_evidence", ["user_id", "assessment_id"])
    op.create_index("ix_memory_lesson_evidence_refs", "memory_lesson_evidence", ["refs"], postgresql_using="gin")

    op.create_table(
        "memory_lesson_uses",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _user(),
        _lesson(),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("consumer", sa.String(24), nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("received", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("ref", sa.String(8), nullable=True),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column("reported", sa.String(16), nullable=True),
        sa.Column("reported_why", sa.Text, nullable=True),
        sa.Column("decision_status", sa.String(16), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(f"reported IS NULL OR {_in('reported', REPORTED)}", name="ck_memory_lesson_uses_reported"),
        sa.CheckConstraint("received OR reported IS NULL", name="ck_memory_lesson_uses_received"),
        sa.UniqueConstraint("attempt_id", "lesson_id", "consumer", name="uq_memory_lesson_uses"),
    )
    op.create_index("ix_memory_lesson_uses_attempt", "memory_lesson_uses", ["user_id", "attempt_id"])
    op.create_index("ix_memory_lesson_uses_lesson", "memory_lesson_uses", ["lesson_id", "created_at"])

    op.create_table(
        "learning_lesson_jobs",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("assessment_id", UUID, sa.ForeignKey("learning_assessments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("assessment_revision", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("state", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _ts("next_attempt_at"),
        _ts("lease_expires_at", null=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(_in("state", JOB_STATES), name="ck_learning_lesson_jobs_state"),
        sa.UniqueConstraint("assessment_id", "assessment_revision", name="uq_learning_lesson_jobs"),
    )
    op.create_index("ix_learning_lesson_jobs_due", "learning_lesson_jobs", ["next_attempt_at"],
                    postgresql_where=sa.text("state IN ('pending', 'running')"))


def downgrade() -> None:
    op.drop_index("ix_learning_lesson_jobs_due", table_name="learning_lesson_jobs")
    op.drop_table("learning_lesson_jobs")
    op.drop_index("ix_memory_lesson_uses_lesson", table_name="memory_lesson_uses")
    op.drop_index("ix_memory_lesson_uses_attempt", table_name="memory_lesson_uses")
    op.drop_table("memory_lesson_uses")
    op.drop_index("ix_memory_lesson_evidence_refs", table_name="memory_lesson_evidence")
    op.drop_index("ix_memory_lesson_evidence_assessment", table_name="memory_lesson_evidence")
    op.drop_table("memory_lesson_evidence")
    op.drop_table("memory_lesson_revisions")
    op.drop_index("ix_memory_lessons_user", table_name="memory_lessons")
    op.drop_table("memory_lessons")
