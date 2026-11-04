"""Her name in core memory is Jophiel (the rename from Columbina, 2026-11-03).

Sets ``identity.name`` of the core to "Jophiel" and bumps its version, as every
edit of an active core must. The row key stays ``columbina``: it is an internal
id that other rows and earlier migrations refer to.

Revision ID: b2e6c4a8d1f3
Revises: a9c3e7f1d5b8
Create Date: 2026-11-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2e6c4a8d1f3"
down_revision = "a9c3e7f1d5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE core_memory SET identity = jsonb_set(identity, '{name}', '\"Jophiel\"'), version = version + 1 "
        "WHERE name = 'columbina' AND identity->>'name' = 'Columbina'"
    ))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE core_memory SET identity = jsonb_set(identity, '{name}', '\"Columbina\"'), version = version - 1 "
        "WHERE name = 'columbina' AND identity->>'name' = 'Jophiel'"
    ))
