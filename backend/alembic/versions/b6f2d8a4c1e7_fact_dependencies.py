"""What her working items rely on (stage 4, services/memory_dependencies.py).

- ``memory_dependencies``: one row per (item, fact): whether the item relies on the fact (``required``), only
  mentions it (``context``), doesn't depend on it (``none``) or isn't known yet (``unknown``), and whether that
  was checked against the fact's current version (``valid``), is being rechecked (``pending``) or needs a look
  (``review``). Both ends are owner-keyed and go with either side.
- ``memory_fact_revisions``: status ``superseded`` (a newer change of the same fact took its items over) and
  ``daily_until`` (a recheck that keeps failing is retried once a day until then, its items held meanwhile).

Backfill: items held by a revision still in progress get an ``unknown`` row owned by it, so the hold carries on
exactly as before. Downgrade drops both (a stage-2 reader holds by revision status; nothing is lost that the
items and facts don't keep themselves).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6f2d8a4c1e7"
down_revision = "a4d8f2c6e9b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_memory_fact_revisions_status", "memory_fact_revisions", type_="check")
    op.create_check_constraint(
        "ck_memory_fact_revisions_status", "memory_fact_revisions",
        "status IN ('pending', 'processing', 'applying', 'done', 'failed', 'superseded')")
    op.drop_constraint("ck_memory_fact_revisions_settled", "memory_fact_revisions", type_="check")
    op.create_check_constraint(
        "ck_memory_fact_revisions_settled", "memory_fact_revisions",
        "(status IN ('done', 'failed', 'superseded')) = (settled_at IS NOT NULL)")
    op.add_column("memory_fact_revisions", sa.Column("daily_until", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "memory_dependencies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("checked_revision", sa.Integer(), nullable=True),
        sa.Column("pending_revision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("memory_fact_revisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('required', 'context', 'none', 'unknown')", name="ck_memory_dependencies_role"),
        sa.CheckConstraint("state IN ('valid', 'pending', 'review')", name="ck_memory_dependencies_state"),
        sa.CheckConstraint("origin IN ('derived', 'judged', 'candidate', 'confirmed')",
                           name="ck_memory_dependencies_origin"),
        sa.CheckConstraint("state <> 'valid' OR checked_revision IS NOT NULL", name="ck_memory_dependencies_checked"),
        sa.ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                                ondelete="CASCADE", name="fk_memory_dependencies_item"),
        sa.ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                                ondelete="CASCADE", name="fk_memory_dependencies_fact"),
        sa.UniqueConstraint("item_id", "fact_id", name="uq_memory_dependencies"),
    )
    op.create_index("ix_memory_dependencies_fact", "memory_dependencies", ["user_id", "fact_id"])
    op.create_index("ix_memory_dependencies_item", "memory_dependencies", ["user_id", "item_id"])
    op.create_index("ix_memory_dependencies_unsettled", "memory_dependencies", ["user_id", "state"],
                    postgresql_where=sa.text("state <> 'valid'"))
    op.create_index("ix_memory_dependencies_revision", "memory_dependencies", ["pending_revision_id"],
                    postgresql_where=sa.text("pending_revision_id IS NOT NULL"))
    # Holds in progress carry on: each held item becomes an unknown dependency owned by its revision (the
    # newest one per item and fact, when more than one holds it).
    op.execute("""
        INSERT INTO memory_dependencies (user_id, item_id, fact_id, role, state, origin, pending_revision_id)
        SELECT DISTINCT ON (w.id, r.fact_id) r.user_id, w.id, r.fact_id, 'unknown', 'pending', 'candidate', r.id
        FROM memory_fact_revisions r
        CROSS JOIN LATERAL jsonb_array_elements_text(r.held_item_ids) AS held(item_id)
        JOIN working_items w ON w.id = held.item_id::uuid AND w.user_id = r.user_id
        WHERE r.status IN ('pending', 'processing', 'applying')
        ORDER BY w.id, r.fact_id, r.created_at DESC
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_memory_dependencies_revision", table_name="memory_dependencies")
    op.drop_index("ix_memory_dependencies_unsettled", table_name="memory_dependencies")
    op.drop_index("ix_memory_dependencies_item", table_name="memory_dependencies")
    op.drop_index("ix_memory_dependencies_fact", table_name="memory_dependencies")
    op.drop_table("memory_dependencies")
    op.drop_column("memory_fact_revisions", "daily_until")
    # A superseded revision was settled: the stage-2 reader knows it as done.
    op.execute("UPDATE memory_fact_revisions SET status = 'done' WHERE status = 'superseded'")
    op.drop_constraint("ck_memory_fact_revisions_settled", "memory_fact_revisions", type_="check")
    op.create_check_constraint("ck_memory_fact_revisions_settled", "memory_fact_revisions",
                               "(status IN ('done', 'failed')) = (settled_at IS NOT NULL)")
    op.drop_constraint("ck_memory_fact_revisions_status", "memory_fact_revisions", type_="check")
    op.create_check_constraint("ck_memory_fact_revisions_status", "memory_fact_revisions",
                               "status IN ('pending', 'processing', 'applying', 'done', 'failed')")
