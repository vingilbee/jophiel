"""What she watches for in a place (services/practice.py): the newest review of a place keeps one or two lines on
the mistakes that keep coming back there, shown to her with every screen in it.

Revision ID: b2f7c4e9a1d8
Revises: a8e2d5c1f9b3
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "b2f7c4e9a1d8"
down_revision = "a8e2d5c1f9b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("practice_reviews", sa.Column("watch", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("practice_reviews", "watch")
