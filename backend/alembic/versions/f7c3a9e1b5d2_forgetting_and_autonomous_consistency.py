"""Forgetting what the user asks, and keeping what she says on her own consistent
(docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md stage 2).

- ``memory_forgets``: one row per message that asked her to forget something (services/memory_forget.py);
  content-free once it ends.
- ``memory_forgotten_sources``: the messages forgotten records came from, never re-read by memory.
- ``working_items.premise_conflict_id``: the unresolved disagreement a question depends on; settling it closes
  the question. ``memory_conflicts`` gets a unique (id, user_id) so the link is owned.
- ``autonomous_deliveries``: what she said on her own in each quiet stretch, so it isn't said again.

Downgrade drops them (tombstones included: forgetting is not undone by a downgrade's data, only its records).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7c3a9e1b5d2"
down_revision = "e5b9d3a7c2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_forgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_jobs.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("request", postgresql.JSONB(), nullable=True),
        sa.Column("removed", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'done', 'nothing_found', 'failed')", name="ck_memory_forgets_status"),
        sa.CheckConstraint("(status = 'pending') = (finished_at IS NULL)", name="ck_memory_forgets_finished"),
        sa.CheckConstraint("status = 'pending' OR request IS NULL", name="ck_memory_forgets_request_cleared"),
        sa.UniqueConstraint("source_message_id", name="uq_memory_forgets_message"),
        sa.UniqueConstraint("id", "user_id", name="uq_memory_forgets_id_user"),
    )
    op.create_index("ix_memory_forgets_due", "memory_forgets", ["next_attempt_at"],
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("ix_memory_forgets_user", "memory_forgets", ["user_id", "created_at"])

    op.create_table(
        "memory_forgotten_sources",
        sa.Column("forget_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.ForeignKeyConstraint(["forget_id", "user_id"], ["memory_forgets.id", "memory_forgets.user_id"],
                                ondelete="CASCADE", name="fk_memory_forgotten_sources_forget"),
    )
    op.create_index("ix_memory_forgotten_sources_message", "memory_forgotten_sources", ["user_id", "message_id"])

    op.create_unique_constraint("uq_memory_conflicts_id_user", "memory_conflicts", ["id", "user_id"])
    op.add_column("working_items", sa.Column("premise_conflict_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_working_items_premise_conflict", "working_items", "memory_conflicts",
                          ["premise_conflict_id", "user_id"], ["id", "user_id"],
                          ondelete="SET NULL (premise_conflict_id)")
    op.create_index("ix_working_items_premise_conflict", "working_items", ["premise_conflict_id"],
                    postgresql_where=sa.text("premise_conflict_id IS NOT NULL"))

    op.create_table(
        "autonomous_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("after_sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(80), nullable=True),
        sa.Column("words", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("work_id", name="uq_autonomous_deliveries_work"),
    )
    op.create_index("uq_autonomous_deliveries_key", "autonomous_deliveries",
                    ["conversation_id", "after_sequence", "key"], unique=True,
                    postgresql_where=sa.text("key IS NOT NULL"))
    op.create_index("ix_autonomous_deliveries_stretch", "autonomous_deliveries", ["conversation_id", "after_sequence"])


def downgrade() -> None:
    op.drop_table("autonomous_deliveries")
    op.drop_index("ix_working_items_premise_conflict", table_name="working_items")
    op.drop_constraint("fk_working_items_premise_conflict", "working_items", type_="foreignkey")
    op.drop_column("working_items", "premise_conflict_id")
    op.drop_constraint("uq_memory_conflicts_id_user", "memory_conflicts", type_="unique")
    op.drop_table("memory_forgotten_sources")
    op.drop_table("memory_forgets")
