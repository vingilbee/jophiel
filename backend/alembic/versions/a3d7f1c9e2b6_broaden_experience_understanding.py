"""Post-audit 3: review opportunities, persistent inquiries, broader lessons.

Adds the ``review`` trigger, the ``left`` outcome and the ``waiting`` / ``left`` stops, an assessment's open
inquiry, and a lesson's ``understanding`` kind with its descriptive form and competing explanations. Nothing is
rewritten: existing assessments and lessons keep what they are.

Revision ID: a3d7f1c9e2b6
Revises: f2a6c8e1b4d9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a3d7f1c9e2b6"
down_revision = "f2a6c8e1b4d9"
branch_labels = None
depends_on = None

OLD_TRIGGERS = ("correction", "complaint", "execution", "sample")
NEW_TRIGGERS = OLD_TRIGGERS + ("review",)
OLD_OUTCOMES = ("concluded", "unresolved", "no_feedback")
NEW_OUTCOMES = OLD_OUTCOMES + ("left",)
OLD_STOPS = ("concluded", "missing_evidence", "stuck")
NEW_STOPS = OLD_STOPS + ("waiting", "left")
OLD_KINDS = ("preference", "strategy")
NEW_KINDS = OLD_KINDS + ("understanding",)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _checks(triggers, outcomes, stops, kinds) -> None:
    for table, name, condition in (
        ("learning_assessments", "ck_learning_assessments_trigger", _in("trigger_kind", triggers)),
        ("learning_assessments", "ck_learning_assessments_outcome", f"outcome IS NULL OR {_in('outcome', outcomes)}"),
        ("learning_assessments", "ck_learning_assessments_stop", f"stop_reason IS NULL OR {_in('stop_reason', stops)}"),
        ("learning_assessment_revisions", "ck_learning_assessment_revisions_outcome", _in("outcome", outcomes)),
        ("learning_assessment_revisions", "ck_learning_assessment_revisions_stop", _in("stop_reason", stops)),
        ("memory_lessons", "ck_memory_lessons_kind", _in("kind", kinds)),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, condition)


def upgrade():
    _checks(NEW_TRIGGERS, NEW_OUTCOMES, NEW_STOPS, NEW_KINDS)
    op.add_column("learning_assessments", sa.Column("inquiry", postgresql.JSONB(), nullable=False,
                                                    server_default=sa.text("'{}'::jsonb")))
    for table in ("memory_lessons", "memory_lesson_revisions"):
        op.add_column(table, sa.Column("form", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("alternatives", postgresql.JSONB(), nullable=False,
                                       server_default=sa.text("'[]'::jsonb")))


def downgrade():
    # Rows that only the new contract allows can't be kept under the old one: they are test-learning products.
    op.execute("DELETE FROM memory_lessons WHERE kind = 'understanding'")
    op.execute("DELETE FROM learning_assessments WHERE trigger_kind = 'review' OR outcome = 'left' "
               "OR stop_reason IN ('waiting', 'left')")
    op.execute("DELETE FROM learning_assessment_revisions WHERE outcome = 'left' OR stop_reason IN ('waiting', 'left')")
    for table in ("memory_lesson_revisions", "memory_lessons"):
        op.drop_column(table, "alternatives")
        op.drop_column(table, "form")
    op.drop_column("learning_assessments", "inquiry")
    _checks(OLD_TRIGGERS, OLD_OUTCOMES, OLD_STOPS, OLD_KINDS)
