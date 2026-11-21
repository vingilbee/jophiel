"""Phone app, phase 1 (frontend-phone/PLAN_PHASE_0-3.md): when a message was written, and when she picked it up.

- ``chat_messages.written_at``: the phone's clock when the user hit send (nullable; information only, never
  used for ordering, which stays ``conversation_sequence``).
- ``chat_messages.seen_at``: when her chat worker claimed the job for this user message (the second tick).

``source_channel`` gains the value ``phone``; it is a free string column, so no schema change for that.

Revision ID: a1f4c7e2d9b3
Revises: e3a7c5d9f2b4
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "a1f4c7e2d9b3"
down_revision = "e3a7c5d9f2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("written_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "seen_at")
    op.drop_column("chat_messages", "written_at")
