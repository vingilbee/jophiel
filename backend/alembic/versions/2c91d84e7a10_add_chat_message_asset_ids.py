"""add chat message asset ids

Revision ID: 2c91d84e7a10
Revises: e9a1c2d3f4b5
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2c91d84e7a10"
down_revision = "e9a1c2d3f4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("asset_ids", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "asset_ids")
