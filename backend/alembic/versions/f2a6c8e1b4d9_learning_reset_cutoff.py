"""Per-user test-learning reset cutoff; preserve raw evidence and generated skills.

Revision ID: f2a6c8e1b4d9
Revises: e7c3a9f1b5d2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2a6c8e1b4d9"
down_revision = "e7c3a9f1b5d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("game_experiences", sa.Column("event_key", sa.String(160), nullable=True))
    op.create_unique_constraint("uq_game_experiences_event", "game_experiences", ["user_id", "event_key"])
    op.create_table("learning_resets",
                    sa.Column("user_id", postgresql.UUID(as_uuid=True),
                              sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("learning_resets")
    op.drop_constraint("uq_game_experiences_event", "game_experiences", type_="unique")
    op.drop_column("game_experiences", "event_key")
