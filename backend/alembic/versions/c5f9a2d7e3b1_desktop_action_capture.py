"""General desktop control: her desktop actions are captured experience.

``decision_executions.kind`` gains ``desktop_action``: one action of her hands on her own desktop
(services/desktop_control), on its turn's chain. Nothing else changes; observations of kind
``desktop.desktop_action`` need no migration (kinds live in app/ai/observe/registry.py).

Revision ID: c5f9a2d7e3b1
Revises: b4e8d2a6f1c3
Create Date: 2026-11-07
"""
from __future__ import annotations

from alembic import op

revision = "c5f9a2d7e3b1"
down_revision = "b4e8d2a6f1c3"
branch_labels = None
depends_on = None

OLD_KINDS = "('effect', 'reply', 'heard', 'channel_delivery', 'skill_run', 'plan_apply', 'thought_route', 'step_check')"
KINDS = ("('effect', 'reply', 'heard', 'channel_delivery', 'skill_run', 'plan_apply', 'thought_route', 'step_check', "
         "'desktop_action')")


def _check(condition: str) -> None:
    op.drop_constraint("ck_decision_executions_kind", "decision_executions", type_="check")
    op.create_check_constraint("ck_decision_executions_kind", "decision_executions", condition)


def upgrade() -> None:
    _check(f"kind IN {KINDS}")


def downgrade() -> None:
    op.execute("DELETE FROM decision_executions WHERE kind = 'desktop_action'")
    _check(f"kind IN {OLD_KINDS}")
