"""Add durable account file cleanup jobs.

Revision ID: 9d72a4f831c0
Revises: 6a4d2c8e1f90
"""
from alembic import op
import sqlalchemy as sa

revision = "9d72a4f831c0"
down_revision = "6a4d2c8e1f90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "file_deletion_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        # No foreign key: this row must outlive the deleted account.
        sa.Column("user_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_file_deletion_jobs_next_attempt_at", "file_deletion_jobs", ["next_attempt_at"])


def downgrade():
    op.drop_table("file_deletion_jobs")
