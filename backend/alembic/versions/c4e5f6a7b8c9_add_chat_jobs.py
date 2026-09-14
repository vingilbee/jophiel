"""Add durable chat generation jobs.

Revision ID: c4e5f6a7b8c9
Revises: 7b4c8d9e1f20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4e5f6a7b8c9"
down_revision = "7b4c8d9e1f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id"),
    )
    for name, column in (("status", "status"), ("turn", "turn_id"), ("user", "user_id"), ("lease", "lease_expires_at")):
        op.create_index(f"ix_chat_jobs_{name}", "chat_jobs", [column])


def downgrade() -> None:
    for name in ("lease", "user", "turn", "status"):
        op.drop_index(f"ix_chat_jobs_{name}", table_name="chat_jobs")
    op.drop_table("chat_jobs")
