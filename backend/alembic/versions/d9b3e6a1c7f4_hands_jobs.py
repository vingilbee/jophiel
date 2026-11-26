"""Her hands on the phone, apart from her mind (PLAN_PHONE_HANDS.md): ``hands_jobs`` (goal, versioned
instructions, status, the phone turn it holds, its worker's claim) and ``hands_steps`` (one line per action, never
what was on the screen)."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9b3e6a1c7f4"
down_revision = "a3f8c1e6d9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hands_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revisions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("paused_by", sa.String(16), nullable=True),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("report_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("apps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turn", sa.String(64), nullable=False),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("zone", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('running', 'paused', 'ended')", name="ck_hands_jobs_status"),
    )
    op.create_index("uq_hands_jobs_open", "hands_jobs", ["user_id"], unique=True,
                    postgresql_where=sa.text("status <> 'ended'"))
    op.create_index("ix_hands_jobs_user_started", "hands_jobs", ["user_id", "started_at"])
    op.create_table(
        "hands_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hands_jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("did", sa.String(300), nullable=False),
        sa.Column("result", sa.String(200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "index", name="uq_hands_steps_job_index"),
    )


def downgrade() -> None:
    op.drop_table("hands_steps")
    op.drop_index("ix_hands_jobs_user_started", table_name="hands_jobs")
    op.drop_index("uq_hands_jobs_open", table_name="hands_jobs")
    op.drop_table("hands_jobs")
