"""Continuity of her mind (docs/MIND_CONTINUITY_FOLLOWUP_HANDOFF.md): one record per evaluation, and awaited
evidence separated from notes about missing evidence.

- ``mind_evaluations``: one row per evaluation of her mind (a user's message or a wake): why it ran, what it was
  about, what she did (said / silent / no words / failed / cancelled) and what was delivered, with times. History
  shows an evaluation that produced no message as a labelled record (never as words she said); the clock section
  measures the time since the previous one.
- ``learning_assessments.awaiting_notes``: explanations of missing or truncated evidence ("trajectory missing or
  expired for this stretch"), which used to share ``awaiting`` with machine references (``attempt:<id>``,
  ``execution:<id>``). Upgrade moves every ``awaiting`` entry that isn't a ``type:<uuid>`` reference into the notes;
  nothing is deleted. Downgrade folds the notes back.

Revision ID: c8e4a1f7b3d2
Revises: b7e2c9f4a1d6
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8e4a1f7b3d2"
down_revision = "b7e2c9f4a1d6"
branch_labels = None
depends_on = None

REF = r"^[a-z_]+:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    op.create_table(
        "mind_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("coordinator_work.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("delivery", sa.String(16), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("silence_reason", sa.Text(), nullable=True),
        sa.Column("tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", name="uq_mind_evaluations_turn"),
        sa.CheckConstraint("outcome IN ('said', 'partial', 'silent', 'no_words', 'cancelled', 'failed')",
                           name="ck_mind_evaluations_outcome"),
    )
    op.create_index("ix_mind_evaluations_conversation", "mind_evaluations", ["conversation_id", "finished_at"])
    op.create_index("ix_mind_evaluations_user", "mind_evaluations", ["user_id", "finished_at"])

    op.add_column("learning_assessments", sa.Column("awaiting_notes", postgresql.JSONB(), nullable=False,
                                                    server_default=sa.text("'[]'::jsonb")))
    # Explanations that were stored among the references move to the notes, in order; references stay.
    op.execute(sa.text(f"""
        UPDATE learning_assessments la SET
          awaiting_notes = coalesce((SELECT jsonb_agg(e ORDER BY o) FROM jsonb_array_elements(la.awaiting)
                                     WITH ORDINALITY AS t(e, o)
                                     WHERE jsonb_typeof(e) <> 'string' OR NOT (e #>> '{{}}') ~ '{REF}'),
                                    '[]'::jsonb),
          awaiting = coalesce((SELECT jsonb_agg(e ORDER BY o) FROM jsonb_array_elements(la.awaiting)
                               WITH ORDINALITY AS t(e, o)
                               WHERE jsonb_typeof(e) = 'string' AND (e #>> '{{}}') ~ '{REF}'), '[]'::jsonb)
        WHERE jsonb_typeof(la.awaiting) = 'array'
    """))


def downgrade() -> None:
    op.execute(sa.text("UPDATE learning_assessments SET awaiting = awaiting || awaiting_notes"))
    op.drop_column("learning_assessments", "awaiting_notes")
    op.drop_index("ix_mind_evaluations_user", table_name="mind_evaluations")
    op.drop_index("ix_mind_evaluations_conversation", table_name="mind_evaluations")
    op.drop_table("mind_evaluations")
