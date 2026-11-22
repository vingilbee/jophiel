"""Add quiet conversation entries for phone actions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e73b2c6d9a10"
down_revision = "b2e5d8f1a4c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_activity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mind_pending", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_phone_activity_conversation_id", "phone_activity", ["conversation_id"])
    op.create_index("ix_phone_activity_mind_pending", "phone_activity", ["created_at"],
                    postgresql_where=sa.text("mind_pending"))


def downgrade() -> None:
    op.drop_table("phone_activity")
