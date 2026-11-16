"""Post-audit 4: broader learning products.

A lesson's optional details (what is untested, whether it was tried, what it needs, what would support or
challenge it), links between a lesson and other memories (its premises and ingredients among them), revisions
prompted by a critique or a changed premise, the ``noted`` evidence role and the ``note`` / ``keep`` operations,
and ``reconsider`` lesson jobs. Nothing is rewritten: existing lessons keep what they are, with no links.

Revision ID: b4e8d2a6f1c3
Revises: a3d7f1c9e2b6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b4e8d2a6f1c3"
down_revision = "a3d7f1c9e2b6"
branch_labels = None
depends_on = None

OLD_ROLES = ("support", "counter")
NEW_ROLES = OLD_ROLES + ("noted",)
OLD_OPS = ("create", "support", "counter", "narrow", "revise", "withdraw", "reconsider")
NEW_OPS = OLD_OPS + ("note", "keep")
RELATIONS = ("depends_on", "builds_on", "same_as", "distinct", "supports", "challenges", "related")
TARGETS = ("lesson", "fact", "item")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _checks(roles, ops) -> None:
    for table, name, condition in (
        ("memory_lesson_evidence", "ck_memory_lesson_evidence_role", _in("role", roles)),
        ("memory_lesson_revisions", "ck_memory_lesson_revisions_op", _in("op", ops)),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _checks(NEW_ROLES, NEW_OPS)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column("memory_lessons", sa.Column("details", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("memory_lesson_revisions",
                  sa.Column("details", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("memory_lesson_revisions", sa.Column("prompted_by", sa.String(16), nullable=True))
    op.create_check_constraint("ck_memory_lesson_revisions_prompted", "memory_lesson_revisions",
                               "prompted_by IS NULL OR prompted_by IN ('critique', 'premise')")

    op.create_table(
        "memory_lesson_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("target_kind", sa.String(8), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("target_status", sa.String(16), nullable=True),
        sa.Column("target_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("lesson_revision", sa.Integer(), nullable=False),
        sa.Column("settled_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("relation", RELATIONS), name="ck_memory_lesson_links_relation"),
        sa.CheckConstraint(_in("target_kind", TARGETS), name="ck_memory_lesson_links_target"),
        sa.ForeignKeyConstraint(["lesson_id", "user_id"], ["memory_lessons.id", "memory_lessons.user_id"],
                                ondelete="CASCADE", name="fk_memory_lesson_links_lesson_user"),
        sa.UniqueConstraint("lesson_id", "target_kind", "target_id", name="uq_memory_lesson_links"),
    )
    op.create_index("ix_memory_lesson_links_target", "memory_lesson_links", ["user_id", "target_kind", "target_id"])

    op.alter_column("learning_lesson_jobs", "assessment_id", nullable=True)
    op.alter_column("learning_lesson_jobs", "assessment_revision", nullable=True)
    op.add_column("learning_lesson_jobs", sa.Column("kind", sa.String(12), nullable=False,
                                                    server_default="assessment"))
    op.add_column("learning_lesson_jobs", sa.Column("lesson_id", postgresql.UUID(as_uuid=True),
                                                    sa.ForeignKey("memory_lessons.id", ondelete="CASCADE"),
                                                    nullable=True))
    op.add_column("learning_lesson_jobs", sa.Column("job_key", sa.String(200), nullable=True))
    op.add_column("learning_lesson_jobs", sa.Column("input", jsonb, nullable=False,
                                                    server_default=sa.text("'{}'::jsonb")))
    op.create_check_constraint("ck_learning_lesson_jobs_kind", "learning_lesson_jobs",
                               "kind IN ('assessment', 'reconsider')")
    op.create_check_constraint("ck_learning_lesson_jobs_assessment", "learning_lesson_jobs",
                               "(kind = 'assessment') = (assessment_id IS NOT NULL AND assessment_revision IS NOT NULL)")
    op.create_check_constraint("ck_learning_lesson_jobs_reconsider", "learning_lesson_jobs",
                               "(kind = 'reconsider') = (lesson_id IS NOT NULL AND job_key IS NOT NULL)")
    op.create_unique_constraint("uq_learning_lesson_jobs_key", "learning_lesson_jobs", ["job_key"])


def downgrade() -> None:
    op.drop_constraint("uq_learning_lesson_jobs_key", "learning_lesson_jobs", type_="unique")
    op.drop_constraint("ck_learning_lesson_jobs_reconsider", "learning_lesson_jobs", type_="check")
    op.drop_constraint("ck_learning_lesson_jobs_assessment", "learning_lesson_jobs", type_="check")
    op.drop_constraint("ck_learning_lesson_jobs_kind", "learning_lesson_jobs", type_="check")
    op.execute("DELETE FROM learning_lesson_jobs WHERE kind = 'reconsider'")
    op.drop_column("learning_lesson_jobs", "input")
    op.drop_column("learning_lesson_jobs", "job_key")
    op.drop_column("learning_lesson_jobs", "lesson_id")
    op.drop_column("learning_lesson_jobs", "kind")
    op.alter_column("learning_lesson_jobs", "assessment_revision", nullable=False)
    op.alter_column("learning_lesson_jobs", "assessment_id", nullable=False)

    op.drop_index("ix_memory_lesson_links_target", table_name="memory_lesson_links")
    op.drop_table("memory_lesson_links")

    op.drop_constraint("ck_memory_lesson_revisions_prompted", "memory_lesson_revisions", type_="check")
    op.drop_column("memory_lesson_revisions", "prompted_by")
    op.drop_column("memory_lesson_revisions", "details")
    op.drop_column("memory_lessons", "details")
    # Rows that only the new values allow can't survive the old checks.
    op.execute("DELETE FROM memory_lesson_evidence WHERE role = 'noted'")
    op.execute("UPDATE memory_lesson_revisions SET op = 'reconsider' WHERE op IN ('note', 'keep')")
    _checks(OLD_ROLES, OLD_OPS)
