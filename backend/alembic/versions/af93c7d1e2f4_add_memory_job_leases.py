"""Add ownership leases to memory jobs.

Revision ID: af93c7d1e2f4
Revises: 9d72a4f831c0
"""
from alembic import op
import sqlalchemy as sa

revision = "af93c7d1e2f4"
down_revision = "9d72a4f831c0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory_jobs", sa.Column("claim_token", sa.String(64), nullable=True))
    op.add_column("memory_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_memory_jobs_claim_token", "memory_jobs", ["claim_token"])
    op.create_index("ix_memory_jobs_lease_expires_at", "memory_jobs", ["lease_expires_at"])


def downgrade():
    op.drop_index("ix_memory_jobs_lease_expires_at", table_name="memory_jobs")
    op.drop_index("ix_memory_jobs_claim_token", table_name="memory_jobs")
    op.drop_column("memory_jobs", "lease_expires_at")
    op.drop_column("memory_jobs", "claim_token")
