"""Preserve full nested cognitive shadow failure codes.

Revision ID: c9e3a5b7d2f4
Revises: b8d2f4a6c1e3
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e3a5b7d2f4"
down_revision = "b8d2f4a6c1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cognitive_shadow_jobs", "last_error_code",
        existing_type=sa.String(128), type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    # Never silently truncate persisted diagnostics to restore the old schema.
    too_long = op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM cognitive_shadow_jobs WHERE length(last_error_code) > 128)"
    ))
    if too_long:
        raise RuntimeError("Cannot restore the 128-character limit while longer shadow error codes exist.")
    op.alter_column("cognitive_shadow_jobs", "last_error_code",
        existing_type=sa.Text(), type_=sa.String(128), existing_nullable=True)
