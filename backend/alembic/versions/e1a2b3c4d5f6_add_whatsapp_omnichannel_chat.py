"""Add WhatsApp identities, inbound batching, and delivery outbox.

Revision ID: e1a2b3c4d5f6
Revises: d8f1a2b3c4e5
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e1a2b3c4d5f6"
down_revision = "d8f1a2b3c4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("origin_channel", sa.String(32), nullable=False, server_default="web"),
    )
    op.add_column(
        "chat_messages",
        sa.Column("source_channel", sa.String(32), nullable=False, server_default="web"),
    )
    op.add_column(
        "chat_messages", sa.Column("external_message_id", sa.String(255), nullable=True)
    )
    op.create_index(
        "ix_chat_messages_external_message_id",
        "chat_messages",
        ["external_message_id"],
    )
    op.create_unique_constraint(
        "uq_chat_messages_turn_role", "chat_messages", ["turn_id", "role"]
    )
    op.add_column(
        "chat_jobs",
        sa.Column("user_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_jobs_user_message_id",
        "chat_jobs",
        "chat_messages",
        ["user_message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_chat_jobs_user_message_id", "chat_jobs", ["user_message_id"])

    op.create_table(
        "user_chat_states",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["primary_conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("primary_conversation_id"),
    )

    op.create_table(
        "channel_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_account_id", sa.String(255), nullable=False),
        sa.Column("external_subject", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "user_id", name="uq_channel_identity_provider_user"),
        sa.UniqueConstraint(
            "provider", "provider_account_id", "external_subject",
            name="uq_channel_identity_provider_subject",
        ),
    )
    op.create_index("ix_channel_identities_user_id", "channel_identities", ["user_id"])

    op.create_table(
        "channel_link_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_channel_link_codes_user_id", "channel_link_codes", ["user_id"])
    op.create_index("ix_channel_link_codes_code_hash", "channel_link_codes", ["code_hash"])
    op.create_index("ix_channel_link_codes_expires_at", "channel_link_codes", ["expires_at"])

    op.create_table(
        "channel_inbound_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_account_id", sa.String(255), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chat_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["channel_identity_id"], ["channel_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_account_id", "provider_message_id",
            name="uq_channel_inbound_provider_message",
        ),
    )
    for name, column in (
        ("identity", "channel_identity_id"),
        ("conversation", "conversation_id"),
        ("status", "status"),
        ("batch", "batch_id"),
    ):
        op.create_index(f"ix_channel_inbound_messages_{name}", "channel_inbound_messages", [column])

    op.create_table(
        "conversation_turn_states",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_pending_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_pending_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index("ix_conversation_turn_states_dispatch_after", "conversation_turn_states", ["dispatch_after"])

    op.create_table(
        "message_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("channel_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("part_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_identity_id"], ["channel_identities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assistant_message_id", "channel", "part_index",
            name="uq_delivery_message_channel_part",
        ),
    )
    for name, column in (
        ("assistant", "assistant_message_id"),
        ("identity", "channel_identity_id"),
        ("status", "status"),
        ("next_attempt", "next_attempt_at"),
        ("claim", "claim_token"),
        ("provider_message", "provider_message_id"),
    ):
        op.create_index(f"ix_message_deliveries_{name}", "message_deliveries", [column])


def downgrade() -> None:
    op.drop_table("message_deliveries")
    op.drop_table("conversation_turn_states")
    op.drop_table("channel_inbound_messages")
    op.drop_table("channel_link_codes")
    op.drop_table("channel_identities")
    op.drop_table("user_chat_states")
    op.drop_index("ix_chat_jobs_user_message_id", table_name="chat_jobs")
    op.drop_constraint("fk_chat_jobs_user_message_id", "chat_jobs", type_="foreignkey")
    op.drop_column("chat_jobs", "user_message_id")
    op.drop_index("ix_chat_messages_external_message_id", table_name="chat_messages")
    op.drop_constraint("uq_chat_messages_turn_role", "chat_messages", type_="unique")
    op.drop_column("chat_messages", "external_message_id")
    op.drop_column("chat_messages", "source_channel")
    op.drop_column("conversations", "origin_channel")
