"""Reconciling a revised fact with her working memory (services/memory_reconcile.py).

- ``memory_fact_revisions``: one row per change of a fact's wording in consolidation (old and new versions,
  the message behind it, the open items held until it's settled), a leased queue for the memory worker.
- ``working_items.checked_facts``: which revision of each fact an item was last checked against.

Downgrade drops both (the revisions are a queue and their record; the facts and items keep their own history).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8a2f6b1d3"
down_revision = "b3e7d1f5a9c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("working_items", sa.Column("checked_facts", postgresql.JSONB(), nullable=False,
                                             server_default=sa.text("'{}'::jsonb")))
    op.create_table(
        "memory_fact_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("semantic_memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("old_content", sa.Text(), nullable=False),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("held_item_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'processing', 'applying', 'done', 'failed')",
                           name="ck_memory_fact_revisions_status"),
        sa.CheckConstraint("to_revision > from_revision", name="ck_memory_fact_revisions_order"),
        sa.CheckConstraint("(status IN ('done', 'failed')) = (settled_at IS NOT NULL)",
                           name="ck_memory_fact_revisions_settled"),
        sa.UniqueConstraint("fact_id", "to_revision", name="uq_memory_fact_revisions_once"),
    )
    op.create_index("ix_memory_fact_revisions_due", "memory_fact_revisions", ["status", "next_attempt_at"])
    op.create_index("ix_memory_fact_revisions_user", "memory_fact_revisions", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_memory_fact_revisions_user", table_name="memory_fact_revisions")
    op.drop_index("ix_memory_fact_revisions_due", table_name="memory_fact_revisions")
    op.drop_table("memory_fact_revisions")
    op.drop_column("working_items", "checked_facts")
