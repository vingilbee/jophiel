"""Paired machines (docs/VM_CONNECTION_PLAN.md, steps 2-4): what each controller reports about its machine,
the owner's per-machine "Allow Jophiel to start sessions" switch, and who started each session.

- ``desktop_environments.auto_start_allowed`` (off for every existing and new pairing) and
  ``auto_start_changed_at``.
- ``desktop_environments.controller_status`` / ``controller_status_at``: the controller's last report (name, OS,
  capture and input availability, start at login). Display only.
- ``desktop_sessions.started_by``: ``user`` or ``jophiel``; existing sessions stay NULL (not recorded then).

Revision ID: e3a7c5d9f2b4
Revises: c8e4a1f7b3d2
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3a7c5d9f2b4"
down_revision = "c8e4a1f7b3d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_environments", sa.Column(
        "auto_start_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("desktop_environments", sa.Column("auto_start_changed_at", sa.DateTime(timezone=True)))
    op.add_column("desktop_environments", sa.Column("controller_status", postgresql.JSONB()))
    op.add_column("desktop_environments", sa.Column("controller_status_at", sa.DateTime(timezone=True)))
    op.add_column("desktop_sessions", sa.Column("started_by", sa.String(16)))
    op.create_check_constraint(
        "ck_desktop_sessions_started_by", "desktop_sessions", "started_by IN ('user', 'jophiel')")


def downgrade() -> None:
    op.drop_constraint("ck_desktop_sessions_started_by", "desktop_sessions", type_="check")
    op.drop_column("desktop_sessions", "started_by")
    op.drop_column("desktop_environments", "controller_status_at")
    op.drop_column("desktop_environments", "controller_status")
    op.drop_column("desktop_environments", "auto_start_changed_at")
    op.drop_column("desktop_environments", "auto_start_allowed")
