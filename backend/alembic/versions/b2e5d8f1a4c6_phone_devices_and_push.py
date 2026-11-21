"""Phone app, phase 2 (frontend-phone/PLAN_PHASE_0-3.md): push notifications.

- ``phone_devices``: a signed-in phone (one per bearer session) and its FCM token, notification switches.
- ``push_deliveries``: the push outbox (one row per message and device, or a ``clear``).
- ``chat_messages.spoken_at``: when she said a message aloud on a live voice session (it isn't pushed then).

Revision ID: b2e5d8f1a4c6
Revises: a1f4c7e2d9b3
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2e5d8f1a4c6"
down_revision = "a1f4c7e2d9b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "phone_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("fcm_token", sa.String(4096), nullable=False, unique=True),
        sa.Column("device_name", sa.String(120)),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("show_text", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("needs_clear", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_phone_devices_user_id", "phone_devices", ["user_id"])
    op.create_table(
        "push_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("phone_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="CASCADE")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("assistant_message_id", "device_id", name="uq_push_delivery_message_device"),
    )
    op.create_index("ix_push_deliveries_user_id", "push_deliveries", ["user_id"])
    op.create_index("ix_push_deliveries_device_id", "push_deliveries", ["device_id"])
    op.create_index("ix_push_deliveries_assistant_message_id", "push_deliveries", ["assistant_message_id"])
    op.create_index("ix_push_deliveries_due", "push_deliveries", ["next_attempt_at"],
                    postgresql_where=sa.text("status IN ('pending', 'sending')"))


def downgrade() -> None:
    op.drop_table("push_deliveries")
    op.drop_table("phone_devices")
    op.drop_column("chat_messages", "spoken_at")
