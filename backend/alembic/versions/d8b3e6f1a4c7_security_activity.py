"""Security activity on her desktop: monitoring switch, coverage, investigations.

``desktop_environments`` gains the owner's monitoring switch (``security_monitoring``, ``security_epoch``,
``security_changed_at``) and her mind's progress through the machine's security observations
(``security_seen_through``, ``security_checked_at``). ``security_coverage`` keeps what the collector could vouch
for per channel and interval; ``security_investigations`` groups what her mind looks into. Events themselves are
observations (``desktop_security.security_event``), so they need no table.

Revision ID: d8b3e6f1a4c7
Revises: c5f9a2d7e3b1
Create Date: 2026-11-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8b3e6f1a4c7"
down_revision = "c5f9a2d7e3b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_environments", sa.Column("security_monitoring", sa.Boolean(), nullable=False,
                                                    server_default=sa.text("false")))
    op.add_column("desktop_environments", sa.Column("security_epoch", sa.Integer(), nullable=False,
                                                    server_default="0"))
    op.add_column("desktop_environments", sa.Column("security_changed_at", sa.DateTime(timezone=True),
                                                    nullable=True))
    op.add_column("desktop_environments", sa.Column("security_seen_through", sa.BigInteger(), nullable=False,
                                                    server_default="0"))
    op.add_column("desktop_environments", sa.Column("security_checked_at", sa.DateTime(timezone=True),
                                                    nullable=True))

    op.create_table(
        "security_coverage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stream", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_record", sa.BigInteger(), nullable=True),
        sa.Column("last_record", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('covered', 'gap', 'reset', 'unavailable', 'off')",
                           name="ck_security_coverage_status"),
        sa.CheckConstraint("end_at IS NULL OR end_at >= start_at", name="ck_security_coverage_order"),
    )
    op.create_index("ix_security_coverage_env_channel_start", "security_coverage",
                    ["environment_id", "channel", "start_at"])

    op.create_table(
        "security_investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("opened_by", sa.String(16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("considered_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_thought_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_thought_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conclusion_kind", sa.String(12), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'concluded', 'closed')", name="ck_security_investigations_status"),
        sa.CheckConstraint("window_end >= window_start", name="ck_security_investigations_window"),
    )
    op.create_index("ix_security_investigations_env_status", "security_investigations",
                    ["environment_id", "status"])
    op.create_index("ix_security_investigations_conversation", "security_investigations", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_security_investigations_conversation", table_name="security_investigations")
    op.drop_index("ix_security_investigations_env_status", table_name="security_investigations")
    op.drop_table("security_investigations")
    op.drop_index("ix_security_coverage_env_channel_start", table_name="security_coverage")
    op.drop_table("security_coverage")
    op.execute("DELETE FROM observations WHERE source = 'desktop_security'")
    for column in ("security_checked_at", "security_seen_through", "security_changed_at", "security_epoch",
                   "security_monitoring"):
        op.drop_column("desktop_environments", column)
