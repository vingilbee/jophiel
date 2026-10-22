"""Persist pending follow-up announcements with their reply receipt.

Revision ID: a2d4f6b8c0e1
Revises: f1c3a5e7b9d2
"""
from alembic import op
import sqlalchemy as sa

revision = "a2d4f6b8c0e1"
down_revision = "f1c3a5e7b9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("coordinator_outputs", sa.Column("announcement_pending", sa.Boolean(),
                                                  nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("coordinator_outputs", "announcement_pending")
