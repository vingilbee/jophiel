"""Desktop agent: the game's input mode as last reported by the controller.

Revision ID: c4f8a2e6b9d1
Revises: b7e2c5d8f1a3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4f8a2e6b9d1"
down_revision = "b7e2c5d8f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_environments", sa.Column("input_context", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("desktop_environments", "input_context")
