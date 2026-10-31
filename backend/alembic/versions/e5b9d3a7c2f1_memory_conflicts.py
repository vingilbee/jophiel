"""A message that disagrees with a fact, kept unresolved (services/memory_conflicts.py).

- ``memory_conflicts``: one row per fact a message disagreed with without saying it changed or was wrong; the
  fact itself is left as it was. Its owner is part of the key to the fact.

Downgrade drops it (the facts and messages keep their own history).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5b9d3a7c2f1"
down_revision = "c4e8a2f6b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_revision", sa.Integer(), nullable=False),
        sa.Column("known_content", sa.Text(), nullable=False),
        sa.Column("stated", sa.Text(), nullable=False),
        sa.Column("user_words", sa.Text(), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(("fact_id", "user_id"), ("semantic_memories.id", "semantic_memories.user_id"),
                                ondelete="CASCADE", name="fk_memory_conflicts_fact_owner"),
        sa.CheckConstraint("status IN ('open', 'known_confirmed', 'fact_changed', 'forgotten')",
                           name="ck_memory_conflicts_status"),
        sa.CheckConstraint("(status = 'open') = (settled_at IS NULL)", name="ck_memory_conflicts_settled"),
        sa.UniqueConstraint("fact_id", "source_message_id", name="uq_memory_conflicts_once"),
    )
    op.create_index("ix_memory_conflicts_user", "memory_conflicts", ["user_id", "status"])
    op.create_index("ix_memory_conflicts_fact", "memory_conflicts", ["fact_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_memory_conflicts_fact", table_name="memory_conflicts")
    op.drop_index("ix_memory_conflicts_user", table_name="memory_conflicts")
    op.drop_table("memory_conflicts")
