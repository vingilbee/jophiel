"""Which player in a game is the user: "Vincent plays Minecraft as tearDrop12".

Before, she guessed the user was the one other player online, and her
memories of play only knew them by their in-game name.

Revision ID: d8f3a1c5e7b2
Revises: c4e1f7a2b9d3
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "d8f3a1c5e7b2"
down_revision = "c4e1f7a2b9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_player_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game", sa.String(32), nullable=False),
        sa.Column("player_name", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "game", name="uq_game_player_links_user_game"),
    )


def downgrade() -> None:
    op.drop_table("game_player_links")
