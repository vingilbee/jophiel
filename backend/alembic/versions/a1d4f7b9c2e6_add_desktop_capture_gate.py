"""Desktop agent screen-capture gate and controller target state.

Revision ID: a1d4f7b9c2e6
Revises: f6c9d3e5a7b2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1d4f7b9c2e6"
down_revision = "f6c9d3e5a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_environments", sa.Column("capture_gate", sa.String(16), nullable=False, server_default="blocked"))
    op.add_column("desktop_environments", sa.Column("capture_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "desktop_environments", sa.Column("capture_confirmed_epoch", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("desktop_environments", sa.Column("target_state", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_desktop_environments_capture_gate", "desktop_environments",
        "capture_gate IN ('allowed', 'blocked', 'manual_auth')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_desktop_environments_capture_gate", "desktop_environments", type_="check")
    op.drop_column("desktop_environments", "target_state")
    op.drop_column("desktop_environments", "capture_confirmed_epoch")
    op.drop_column("desktop_environments", "capture_epoch")
    op.drop_column("desktop_environments", "capture_gate")
