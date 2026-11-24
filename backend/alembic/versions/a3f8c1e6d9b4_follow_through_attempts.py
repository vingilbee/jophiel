"""Her follow-through on promises a change made due: ``follow_through_attempts`` (services/follow_through.py).
One row per thing she did for an item on a wake that wasn't a message, with the change that made it due, what shows
the result, and how it turned out (a command accepted is never the goal reached)."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3f8c1e6d9b4"
down_revision = "f2a6d9c3b8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "follow_through_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("working_items.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("transition", sa.String(80), nullable=False),
        sa.Column("wake_work_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("observe", sa.String(40), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="sending"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("check_work_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('sending', 'pending', 'achieved', 'failed', 'unresolved', 'cancelled')",
                           name="ck_follow_through_attempts_status"),
    )
    op.create_index("ix_follow_through_attempts_item", "follow_through_attempts", ["user_id", "item_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_follow_through_attempts_item", table_name="follow_through_attempts")
    op.drop_table("follow_through_attempts")
