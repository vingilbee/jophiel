"""The old learner is removed (2026-11-26): retrospective assessments, memory lessons, lesson jobs, bounded game
trials and the test-learning cutoff go, with the phone exchanges' link to an assessment. Her practice notes
(``practice_notes``) replace it. A database that once ran ``feature/phone-hands``'s ``d9b3e6a1c7f4`` also loses its
``hands_jobs`` / ``hands_steps`` (that split isn't in main). Not reversible: downgrade recreates nothing.

Revision ID: c1e5a9d3f7b2
Revises: b9d4e2f7a1c3
Create Date: 2026-11-26
"""
from alembic import op

revision = "c1e5a9d3f7b2"
down_revision = "b9d4e2f7a1c3"
branch_labels = None
depends_on = None

TABLES = (
    "learning_trial_assessments", "learning_trial_events", "learning_trial_revisions", "learning_trial_triggers",
    "learning_trials",
    "learning_lesson_jobs", "memory_lesson_uses", "memory_lesson_links", "memory_lesson_evidence",
    "memory_lesson_revisions", "memory_lessons",
    "learning_assessment_uses", "learning_assessment_links", "learning_assessment_revisions",
    "learning_assessment_evidence", "learning_assessments",
    "learning_resets",
    "hands_steps", "hands_jobs",
)


def upgrade() -> None:
    op.execute("ALTER TABLE phone_exchanges DROP CONSTRAINT IF EXISTS ck_phone_exchanges_lesson")
    op.execute("ALTER TABLE phone_exchanges DROP COLUMN IF EXISTS assessment_id")
    op.execute("ALTER TABLE phone_exchanges DROP COLUMN IF EXISTS lesson_why")
    op.execute("ALTER TABLE phone_exchanges DROP COLUMN IF EXISTS lesson")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS learning_trial_append_only() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS learning_trial_revision_open() CASCADE")


def downgrade() -> None:
    raise NotImplementedError("the old learner's tables are gone; restore from a backup if they're needed")
