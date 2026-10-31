"""Her thoughts reaching memory (docs/COLUMBINA_THOUGHT_MEMORY_SPEC.md §8).

- ``working_items``: kinds ``hypothesis`` (open -> supported / disproved / unresolved) and ``decision``
  (active -> superseded / withdrawn), both hers and inferred; ``reason``: why she concluded it, from her own
  thinking (null for what the user said).
- ``memory_evidence``: roles ``basis`` (what she read when she thought it; never support) and ``restated``
  (she reached the same conclusion again; never support).
- ``observations``: epistemic ``inferred`` (her own reasoning) and ``thought_id`` (the thought a
  ``mind.thought_concluded`` report hands over; cascades with it like the other source links).

Downgrade is refused while anything uses the new kinds, roles or values; otherwise it restores the exact
earlier constraints.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3e7d1f5a9c4"
down_revision = "a7d3f1c9e5b2"
branch_labels = None
depends_on = None

OLD_STATUSES = {
    "goal": (("active", "suspended"), ("completed", "abandoned")),
    "commitment": (("open", "needs_review"), ("fulfilled", "cancelled")),
    "intention": (("active",), ("done", "dropped", "lapsed")),
    "question": (("open",), ("answered", "dropped")),
}
NEW_STATUSES = {
    **OLD_STATUSES,
    "hypothesis": (("open",), ("supported", "disproved", "unresolved")),
    "decision": (("active",), ("superseded", "withdrawn")),
}
OLD_ROLES = ("origin", "support", "resolution", "correction")
NEW_ROLES = (*OLD_ROLES, "basis", "restated")
OLD_EPISTEMIC = ("observed", "reported")
NEW_EPISTEMIC = (*OLD_EPISTEMIC, "inferred")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _item_checks(statuses: dict) -> None:
    closed = sorted({s for _, shut in statuses.values() for s in shut})
    status = " OR ".join(f"(kind = '{kind}' AND status IN ({_in(opened + shut)}))"
                         for kind, (opened, shut) in statuses.items())
    for name in ("ck_working_items_kind", "ck_working_items_status", "ck_working_items_closed"):
        op.drop_constraint(name, "working_items", type_="check")
    op.create_check_constraint("ck_working_items_kind", "working_items", f"kind IN ({_in(statuses)})")
    op.create_check_constraint("ck_working_items_status", "working_items", status)
    op.create_check_constraint("ck_working_items_closed", "working_items",
                               f"(closed_at IS NOT NULL) = (status IN ({_in(closed)}))")


def _role_check(roles) -> None:
    op.drop_constraint("ck_memory_evidence_role", "memory_evidence", type_="check")
    op.create_check_constraint("ck_memory_evidence_role", "memory_evidence",
                               f"role IN ({_in(roles)}) AND (role <> 'resolution' OR item_id IS NOT NULL)")


def _epistemic_check(values) -> None:
    op.drop_constraint("ck_observations_epistemic", "observations", type_="check")
    op.create_check_constraint("ck_observations_epistemic", "observations", f"epistemic IN ({_in(values)})")


def upgrade() -> None:
    _item_checks(NEW_STATUSES)
    op.add_column("working_items", sa.Column("reason", sa.Text(), nullable=True))
    _role_check(NEW_ROLES)
    _epistemic_check(NEW_EPISTEMIC)
    op.add_column("observations", sa.Column("thought_id", postgresql.UUID(as_uuid=True),
                                            sa.ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_observations_thought", "observations", ["thought_id"],
                    postgresql_where=sa.text("thought_id IS NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    in_use = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM working_items WHERE kind IN ('hypothesis', 'decision') OR reason IS NOT NULL)"
        " + (SELECT count(*) FROM memory_evidence WHERE role IN ('basis', 'restated'))"
        " + (SELECT count(*) FROM observations WHERE epistemic = 'inferred' OR thought_id IS NOT NULL)")).scalar()
    if in_use:
        raise RuntimeError(f"{in_use} rows use her remembered thoughts (hypotheses, decisions, basis/restated "
                           "evidence, thought observations); remove them before downgrading")
    op.drop_index("ix_observations_thought", table_name="observations")
    op.drop_column("observations", "thought_id")
    _epistemic_check(OLD_EPISTEMIC)
    _role_check(OLD_ROLES)
    op.drop_column("working_items", "reason")
    _item_checks(OLD_STATUSES)
