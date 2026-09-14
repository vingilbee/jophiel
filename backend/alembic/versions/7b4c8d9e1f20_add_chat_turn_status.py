"""add turn correlation and outcome status to chat messages

Revision ID: 7b4c8d9e1f20
Revises: af93c7d1e2f4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "7b4c8d9e1f20"
down_revision = "af93c7d1e2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("status", sa.String(length=16), server_default="completed", nullable=False),
    )
    op.create_index("ix_chat_messages_turn_id", "chat_messages", ["turn_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_turn_id", table_name="chat_messages")
    op.drop_column("chat_messages", "status")
    op.drop_column("chat_messages", "turn_id")
