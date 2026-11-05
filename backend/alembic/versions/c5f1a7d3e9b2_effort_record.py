"""The effort record (docs/COLUMBINA_EFFORT_AND_STUCK_PLAN.md, stage 1).

- ``constraint``: a new working-item kind, a rule the user gave for an outcome, in their words
  (``user_words`` required), ``active`` until ``lifted``.
- Her own items (decision, intention, hypothesis, question) and constraints may point at the goal they
  serve (``parent_id``), so an outcome gathers the approaches she took for it. Before, only a sub-goal
  could have a parent.
- ``reference``: a new entity link role, a place or thing that matters to an outcome.
- ``mind_thoughts.follows``: the earlier conclusion a thought or plan says it carries out (its short id),
  as the model said it. Code never infers it.

Revision ID: c5f1a7d3e9b2
Revises: b2e6c4a8d1f3
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5f1a7d3e9b2"
down_revision = "b2e6c4a8d1f3"
branch_labels = None
depends_on = None

OLD_KINDS = ("goal", "intention", "commitment", "question", "hypothesis", "decision", "plan")
OLD_STATUSES = {
    "goal": ("active", "suspended", "completed", "abandoned"),
    "commitment": ("open", "needs_review", "fulfilled", "cancelled"),
    "intention": ("active", "done", "dropped", "lapsed"),
    "question": ("open", "answered", "dropped"),
    "hypothesis": ("open", "supported", "disproved", "unresolved"),
    "decision": ("active", "superseded", "withdrawn"),
    "plan": ("planned", "needs_review", "happened", "cancelled"),
}
NEW_STATUSES = {**OLD_STATUSES, "constraint": ("active", "lifted")}
OLD_PARENT = "parent_id IS NULL OR (kind = 'goal' AND parent_id <> id)"
NEW_PARENT = ("parent_id IS NULL OR (kind IN ('goal', 'decision', 'intention', 'hypothesis', 'question', "
              "'constraint') AND parent_id <> id)")
OLD_ROLES = ("participant", "place", "about")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _status_check(statuses: dict) -> str:
    return " OR ".join(f"(kind = '{kind}' AND status IN ({_in(values)}))" for kind, values in statuses.items())


def _replace(name: str, table: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _replace("ck_working_items_kind", "working_items", f"kind IN ({_in((*OLD_KINDS, 'constraint'))})")
    _replace("ck_working_items_status", "working_items", _status_check(NEW_STATUSES))
    _replace("ck_working_items_parent", "working_items", NEW_PARENT)
    op.create_check_constraint("ck_working_items_constraint_words", "working_items",
                               "kind <> 'constraint' OR coalesce(btrim(user_words), '') <> ''")
    _replace("ck_memory_entity_links_role", "memory_entity_links", f"role IN ({_in((*OLD_ROLES, 'reference'))})")
    op.add_column("mind_thoughts", sa.Column("follows", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("mind_thoughts", "follows")
    op.execute("DELETE FROM memory_entity_links WHERE role = 'reference'")
    _replace("ck_memory_entity_links_role", "memory_entity_links", f"role IN ({_in(OLD_ROLES)})")
    op.drop_constraint("ck_working_items_constraint_words", "working_items", type_="check")
    op.execute("UPDATE working_items SET parent_id = NULL WHERE kind <> 'goal' AND parent_id IS NOT NULL")
    _replace("ck_working_items_parent", "working_items", OLD_PARENT)
    op.execute("DELETE FROM memory_evidence WHERE item_id IN (SELECT id FROM working_items WHERE kind = 'constraint')")
    op.execute("UPDATE memory_events SET item_id = NULL WHERE item_id IN "
               "(SELECT id FROM working_items WHERE kind = 'constraint')")
    op.execute("DELETE FROM working_items WHERE kind = 'constraint'")
    _replace("ck_working_items_status", "working_items", _status_check(OLD_STATUSES))
    _replace("ck_working_items_kind", "working_items", f"kind IN ({_in(OLD_KINDS)})")
