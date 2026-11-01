"""Independent component work and bounded retry eligibility (step 7)."""
from alembic import op
import sqlalchemy as sa

revision = "b3e7f9a1c5d2"
down_revision = "a2d4f6b8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_coordinator_work_kind", "coordinator_work")
    op.drop_constraint("ck_coordinator_work_status", "coordinator_work")
    op.create_check_constraint("ck_coordinator_work_kind", "coordinator_work",
                               "kind IN ('recall', 'mind', 'memory', 'observation', 'cognition')")
    op.create_check_constraint("ck_coordinator_work_status", "coordinator_work",
        "status IN ('queued', 'waiting_for_memory', 'running', 'completed', 'failed', 'cancelled', 'expired', 'superseded', 'blocked')")
    op.alter_column("coordinator_work", "conversation_id", nullable=True)
    op.add_column("coordinator_work", sa.Column("next_attempt_at", sa.DateTime(timezone=True),
                                               nullable=False, server_default=sa.func.now()))


def downgrade():
    # New component jobs must be explicitly archived before returning to a two-kind schema.
    op.drop_column("coordinator_work", "next_attempt_at")
    op.alter_column("coordinator_work", "conversation_id", nullable=False)
    op.drop_constraint("ck_coordinator_work_kind", "coordinator_work")
    op.drop_constraint("ck_coordinator_work_status", "coordinator_work")
    op.create_check_constraint("ck_coordinator_work_kind", "coordinator_work", "kind IN ('recall', 'mind')")
    op.create_check_constraint("ck_coordinator_work_status", "coordinator_work",
        "status IN ('queued', 'waiting_for_memory', 'running', 'completed', 'failed', 'cancelled', 'expired', 'superseded')")
