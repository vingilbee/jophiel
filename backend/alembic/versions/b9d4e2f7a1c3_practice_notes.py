"""Her practice notes (the tool-learning revamp, services/practice.py): what she learns from using her tools, per
place and tool. The old learner is retired: its pending observation receipts are marked skipped, so retention can
prune what they held."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b9d4e2f7a1c3"
down_revision = "a3f8c1e6d9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "practice_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("place", sa.String(80), nullable=False),
        sa.Column("tool", sa.String(40), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default="known"),
        sa.Column("source", sa.String(8), nullable=False, server_default="practice"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('known', 'to_try')", name="ck_practice_notes_status"),
        sa.CheckConstraint("source IN ('practice', 'user')", name="ck_practice_notes_source"),
    )
    op.create_index("ix_practice_notes_place", "practice_notes", ["user_id", "surface", "place"])
    op.execute("UPDATE observation_processing SET status = 'skipped', processed_at = now(), "
               "reason = 'the old learner is retired' "
               "WHERE consumer = 'learning' AND status NOT IN ('applied', 'skipped', 'failed')")


def downgrade() -> None:
    op.drop_index("ix_practice_notes_place", table_name="practice_notes")
    op.drop_table("practice_notes")
