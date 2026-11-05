"""Post-audit 1: fill missing experience capture (docs/learning-system/POST-AUDIT-01-implementation.md).

- ``decision_attempts.learner`` (and ``training_examples.learner``): the planner and hard thoughts are captured
  learners too.
- ``decision_executions.kind``: ``plan_apply`` (what code did with a planner decision), ``thought_route`` (where a
  hard thought's conclusion went), ``step_check`` (code counting a plan step done).
- ``capture_gaps.reason``: ``pack_dropped`` (trajectory chunks the pack let go of before the backend took them).
- ``trajectory_streams`` + ``trajectory_chunks``: her trajectory, every client tick, as the pack encoded it. Raw
  capture: they go with their user (and chunks with their game session); retention drops them after
  LEARNING_CAPTURE_RETENTION_DAYS.

Revision ID: e7c3a9f1b5d2
Revises: d8a2c6f4b1e7
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7c3a9f1b5d2"
down_revision = "d8a2c6f4b1e7"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()

OLD_LEARNERS = "('student_1', 'student_2', 'hands')"
LEARNERS = "('student_1', 'student_2', 'hands', 'planner', 'hard_thought')"
OLD_KINDS = "('effect', 'reply', 'heard', 'channel_delivery', 'skill_run')"
KINDS = "('effect', 'reply', 'heard', 'channel_delivery', 'skill_run', 'plan_apply', 'thought_route', 'step_check')"
OLD_REASONS = "('queue_full', 'retries_exhausted', 'unserializable', 'source_gone', 'shutdown')"
REASONS = "('queue_full', 'retries_exhausted', 'unserializable', 'source_gone', 'shutdown', 'pack_dropped')"


def _check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _check("decision_attempts", "ck_decision_attempts_learner", f"learner IN {LEARNERS}")
    _check("training_examples", "ck_training_examples_learner", f"learner IN {LEARNERS}")
    _check("decision_executions", "ck_decision_executions_kind", f"kind IN {KINDS}")
    _check("capture_gaps", "ck_capture_gaps_reason", f"reason IN {REASONS}")

    op.create_table(
        "trajectory_streams",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("stream_index", sa.Integer(), nullable=False),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("version", sa.String(32)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.Text()),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("coverage", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trajectory_streams_user", "trajectory_streams", ["user_id", "started_at"])
    op.create_index("ix_trajectory_streams_expires", "trajectory_streams", ["expires_at"])

    op.create_table(
        "trajectory_chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stream_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("game_session_id", UUID, sa.ForeignKey("desktop_sessions.id", ondelete="CASCADE")),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("first_tick", sa.BigInteger(), nullable=False),
        sa.Column("ticks", sa.Integer(), nullable=False),
        sa.Column("events", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("format", sa.Integer(), nullable=False),
        sa.Column("encoding", sa.String(16), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("kinds", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("overhead", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stream_id", "seq", name="uq_trajectory_chunks_stream_seq"),
    )
    op.create_index("ix_trajectory_chunks_user", "trajectory_chunks", ["user_id", "started_at"])
    op.create_index("ix_trajectory_chunks_session", "trajectory_chunks", ["game_session_id", "started_at"])
    op.create_index("ix_trajectory_chunks_expires", "trajectory_chunks", ["expires_at"])


def downgrade() -> None:
    op.drop_table("trajectory_chunks")
    op.drop_table("trajectory_streams")
    # Rows of the new kinds can't satisfy the old checks: they go first (raw capture; the data is gone).
    op.execute("DELETE FROM capture_gaps WHERE reason = 'pack_dropped'")
    op.execute("DELETE FROM decision_executions WHERE kind IN ('plan_apply', 'thought_route', 'step_check')")
    op.execute("DELETE FROM training_examples WHERE learner IN ('planner', 'hard_thought')")
    op.execute("DELETE FROM decision_attempts WHERE learner IN ('planner', 'hard_thought')")
    _check("capture_gaps", "ck_capture_gaps_reason", f"reason IN {OLD_REASONS}")
    _check("decision_executions", "ck_decision_executions_kind", f"kind IN {OLD_KINDS}")
    _check("training_examples", "ck_training_examples_learner", f"learner IN {OLD_LEARNERS}")
    _check("decision_attempts", "ck_decision_attempts_learner", f"learner IN {OLD_LEARNERS}")
