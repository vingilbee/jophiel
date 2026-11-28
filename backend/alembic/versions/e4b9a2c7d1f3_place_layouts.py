"""Her picture of a place (services/place_memory.py): what stays the same each time she comes back to an app or game,
kept per place (place_layouts). This sitting's part lives in Redis only."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e4b9a2c7d1f3"
down_revision = "d3a8f6b1c5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_layouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("place", sa.String(80), nullable=False),
        sa.Column("layout", sa.Text(), nullable=False, server_default=""),
        sa.Column("her_layout", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "surface", "place", name="uq_place_layouts_place"),
    )


def downgrade() -> None:
    op.drop_table("place_layouts")
