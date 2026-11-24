"""Promises kept on evidence, and questions with a state (fix/commitments-and-open-questions).

- ``working_items.trigger`` / ``trigger_met_at``: a promise owed when something happens (her mind recognises an
  event; code recognises background work of hers ending).
- ``working_items.offered_at``: a question of hers handed to her mind once as a wake, so she can follow it up while
  the user is quiet. Every question already open is marked as handed over: the backlog never becomes a burst of
  messages.
- Question statuses: ``asked`` (open: asked, waiting on their answer) and ``stale`` (closed: no longer matters).
- ``memory_evidence`` role ``asked``: the message of hers that asked a question.

Downgrade is refused while anything uses the new statuses or role.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8b3f1a6c4d7"
down_revision = "d7a1f4c8e2b9"
branch_labels = None
depends_on = None

OLD_STATUSES = {
    "goal": (("active", "suspended"), ("completed", "abandoned")),
    "commitment": (("open", "needs_review"), ("fulfilled", "cancelled")),
    "intention": (("active",), ("done", "dropped", "lapsed")),
    "question": (("open",), ("answered", "dropped")),
    "hypothesis": (("open",), ("supported", "disproved", "unresolved")),
    "decision": (("active",), ("superseded", "withdrawn")),
    "plan": (("planned", "needs_review"), ("happened", "cancelled")),
    "constraint": (("active",), ("lifted",)),
}
NEW_STATUSES = {**OLD_STATUSES, "question": (("open", "asked"), ("answered", "dropped", "stale"))}
OLD_ROLES = ("origin", "support", "resolution", "correction", "basis", "restated")
NEW_ROLES = (*OLD_ROLES, "asked")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _item_checks(statuses: dict) -> None:
    closed = sorted({s for _, shut in statuses.values() for s in shut})
    status = " OR ".join(f"(kind = '{kind}' AND status IN ({_in(opened + shut)}))"
                         for kind, (opened, shut) in statuses.items())
    for name in ("ck_working_items_status", "ck_working_items_closed"):
        op.drop_constraint(name, "working_items", type_="check")
    op.create_check_constraint("ck_working_items_status", "working_items", status)
    op.create_check_constraint("ck_working_items_closed", "working_items",
                               f"(closed_at IS NOT NULL) = (status IN ({_in(closed)}))")


def _role_check(roles) -> None:
    op.drop_constraint("ck_memory_evidence_role", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_role", "memory_evidence",
                               f"role IN ({_in(roles)}) AND (role <> 'resolution' OR item_id IS NOT NULL)")


def upgrade() -> None:
    op.add_column("working_items", sa.Column("trigger", postgresql.JSONB(), nullable=True))
    op.add_column("working_items", sa.Column("trigger_met_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("working_items", sa.Column("offered_at", sa.DateTime(timezone=True), nullable=True))
    _item_checks(NEW_STATUSES)
    _role_check(NEW_ROLES)
    # The backlog is never handed over as wakes: she still sees it in her working memory.
    op.execute("UPDATE working_items SET offered_at = now() WHERE kind = 'question' AND closed_at IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    in_use = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM working_items WHERE kind = 'question' AND status IN ('asked', 'stale'))"
        " + (SELECT count(*) FROM memory_evidence WHERE role = 'asked')")).scalar()
    if in_use:
        raise RuntimeError(f"{in_use} rows use asked/stale questions or asked evidence; remove them before "
                           "downgrading")
    _role_check(OLD_ROLES)
    _item_checks(OLD_STATUSES)
    op.drop_column("working_items", "offered_at")
    op.drop_column("working_items", "trigger_met_at")
    op.drop_column("working_items", "trigger")
