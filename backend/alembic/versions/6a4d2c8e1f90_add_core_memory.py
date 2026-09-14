"""add user-independent agent core memory

Revision ID: 6a4d2c8e1f90
Revises: 2c91d84e7a10
Create Date: 2026-09-04 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6a4d2c8e1f90"
down_revision: Union[str, None] = "2c91d84e7a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "core_memory",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("curiosity", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("initiative", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("caution", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("warmth", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("caution BETWEEN 0 AND 1", name="ck_core_memory_caution"),
        sa.CheckConstraint("curiosity BETWEEN 0 AND 1", name="ck_core_memory_curiosity"),
        sa.CheckConstraint("initiative BETWEEN 0 AND 1", name="ck_core_memory_initiative"),
        sa.CheckConstraint("warmth BETWEEN 0 AND 1", name="ck_core_memory_warmth"),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("core_memory")
