"""Record how much of an interrupted spoken reply the user heard.

Revision ID: d4f7a1c3e9b2
Revises: c9e3a5b7d2f4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f7a1c3e9b2"
down_revision = "c9e3a5b7d2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("heard_chars", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "heard_chars")
