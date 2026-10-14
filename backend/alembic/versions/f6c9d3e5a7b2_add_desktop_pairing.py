"""Desktop agent pairing codes and controller device credentials.

Revision ID: f6c9d3e5a7b2
Revises: e5b8c2d4f6a1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6c9d3e5a7b2"
down_revision = "e5b8c2d4f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_environments", sa.Column("controller_token_hash", sa.String(64), nullable=True))
    op.add_column("desktop_environments", sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("desktop_environments", sa.Column("controller_version", sa.String(32), nullable=True))

    op.create_table(
        "desktop_pairings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_lookup", sa.String(64), nullable=False, unique=True),
        sa.Column("code", sa.String(16), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_desktop_pairings_owner_user_id", "desktop_pairings", ["owner_user_id"])
    op.create_index("ix_desktop_pairings_environment_id", "desktop_pairings", ["environment_id"])


def downgrade() -> None:
    op.drop_index("ix_desktop_pairings_environment_id", table_name="desktop_pairings")
    op.drop_index("ix_desktop_pairings_owner_user_id", table_name="desktop_pairings")
    op.drop_table("desktop_pairings")
    op.drop_column("desktop_environments", "controller_version")
    op.drop_column("desktop_environments", "paired_at")
    op.drop_column("desktop_environments", "controller_token_hash")
