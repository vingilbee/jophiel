"""Her judgment of an effort on her hard thoughts (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md, stage 2).

- ``mind_thoughts.assessment``: when she had an effort to judge, whether it's getting anywhere (yes / no /
  unclear) and, when it isn't, the cause she favours (ability / perception / situation / understanding), a
  check, what it would support and what it would leave unknown. Hers, as she gave it; code never fills it.

Revision ID: d8a2c6f4b1e7
Revises: c5f1a7d3e9b2
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8a2c6f4b1e7"
down_revision = "c5f1a7d3e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mind_thoughts", sa.Column("assessment", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("mind_thoughts", "assessment")
