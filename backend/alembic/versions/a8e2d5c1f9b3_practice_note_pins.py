"""Pinned practice notes (services/practice.py): a note the user pinned stays as it is; her reviews read it and
never rewrite or remove it. Only the user pins or unpins one (/api/practice/notes, the System page).

Revision ID: a8e2d5c1f9b3
Revises: f5c1a8e3b7d2
"""
import sqlalchemy as sa
from alembic import op

revision = "a8e2d5c1f9b3"
down_revision = "f5c1a8e3b7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("practice_notes", sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("practice_notes", "pinned")
