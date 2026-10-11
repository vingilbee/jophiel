"""Game reflection per stretch of play, not per session.

Sessions are rarely ended outright (the pack drops offline, the user pauses),
so a reflection covers the experiences between two ids of the session.

Revision ID: c4e1f7a2b9d3
Revises: a3f8d2c6e9b1
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "c4e1f7a2b9d3"
down_revision = "a3f8d2c6e9b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("game_reflection_jobs",
                  sa.Column("from_experience_id", sa.BigInteger, nullable=False, server_default="0"))
    op.add_column("game_reflection_jobs",
                  sa.Column("up_to_experience_id", sa.BigInteger, nullable=False, server_default="0"))
    op.drop_constraint("game_reflection_jobs_session_id_key", "game_reflection_jobs", type_="unique")
    op.create_unique_constraint("uq_game_reflection_jobs_segment", "game_reflection_jobs",
                                ["session_id", "up_to_experience_id"])


def downgrade() -> None:
    op.drop_constraint("uq_game_reflection_jobs_segment", "game_reflection_jobs", type_="unique")
    op.execute("""DELETE FROM game_reflection_jobs a USING game_reflection_jobs b
                  WHERE a.session_id = b.session_id AND a.created_at < b.created_at""")
    op.create_unique_constraint("game_reflection_jobs_session_id_key", "game_reflection_jobs", ["session_id"])
    op.drop_column("game_reflection_jobs", "up_to_experience_id")
    op.drop_column("game_reflection_jobs", "from_experience_id")
