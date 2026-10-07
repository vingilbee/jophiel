"""Desktop agent capture scope: whole VM screen or game window only.

Revision ID: b7e2c5d8f1a3
Revises: a1d4f7b9c2e6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e2c5d8f1a3"
down_revision = "a1d4f7b9c2e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "desktop_environments", sa.Column("capture_scope", sa.String(16), nullable=False, server_default="desktop")
    )
    op.create_check_constraint(
        "ck_desktop_environments_capture_scope", "desktop_environments", "capture_scope IN ('desktop', 'game_window')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_desktop_environments_capture_scope", "desktop_environments", type_="check")
    op.drop_column("desktop_environments", "capture_scope")
