"""Game learning: experiences, lessons, proposals, reflection jobs.

Revision ID: a3f8d2c6e9b1
Revises: e2c7a9d4b6f1
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "a3f8d2c6e9b1"
down_revision = "e2c7a9d4b6f1"
branch_labels = None
depends_on = None

_EMPTY_LIST = sa.text("'[]'::jsonb")


def _user():
    return sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _session(**kw):
    return sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("desktop_sessions.id", ondelete="CASCADE"),
                     nullable=False, **kw)


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "game_experiences",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _user(), _session(),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('skill_outcome', 'notice', 'lessons_applied')", name="ck_game_experiences_kind"),
    )
    op.create_index("ix_game_experiences_session", "game_experiences", ["session_id", "id"])
    op.create_index("ix_game_experiences_user_game", "game_experiences", ["user_id", "game", "id"])

    op.create_table(
        "game_lessons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user(),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("normalized_key", sa.String(96), nullable=False),
        sa.Column("situation", sa.Text, nullable=False),
        sa.Column("advice", sa.Text, nullable=False),
        sa.Column("level", sa.String(8), nullable=False, server_default="skill"),
        sa.Column("status", sa.String(16), nullable=False, server_default="trial"),
        sa.Column("support_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("contradict_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("evidence", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint("status IN ('trial', 'confirmed', 'retired')", name="ck_game_lessons_status"),
        sa.CheckConstraint("level IN ('skill', 'plan')", name="ck_game_lessons_level"),
        sa.UniqueConstraint("user_id", "game", "normalized_key", name="uq_game_lessons_key"),
    )

    op.create_table(
        "game_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user(),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("normalized_key", sa.String(96), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("area", sa.String(64), nullable=False),
        sa.Column("problem", sa.Text, nullable=False),
        sa.Column("suggestion", sa.Text, nullable=False),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
        sa.Column("evidence", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("user_note", sa.Text, nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('open', 'accepted', 'implemented', 'rejected')", name="ck_game_proposals_status"),
        sa.UniqueConstraint("user_id", "game", "normalized_key", name="uq_game_proposals_key"),
    )

    op.create_table(
        "game_reflection_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user(), _session(unique=True),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'skipped', 'failed')", name="ck_game_reflection_jobs_status"
        ),
    )
    op.create_index("ix_game_reflection_jobs_user_id", "game_reflection_jobs", ["user_id"])
    op.create_index("ix_game_reflection_jobs_claim", "game_reflection_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("game_reflection_jobs")
    op.drop_table("game_proposals")
    op.drop_table("game_lessons")
    op.drop_table("game_experiences")
