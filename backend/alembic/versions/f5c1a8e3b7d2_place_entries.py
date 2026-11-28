"""Her picture of a place as entries (services/place_memory.py): what lasts in a place is kept as one row per thing
(place_entries: a control, landmark, area or thing seen there, per world), edited one at a time by the updater.
The one-paragraph place_layouts.layout goes (written from today's screenshots only; it isn't carried over:
the updater writes entries from her next screenshots).

Revision ID: f5c1a8e3b7d2
Revises: e4b9a2c7d1f3
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f5c1a8e3b7d2"
down_revision = "e4b9a2c7d1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("place_layouts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("world", sa.String(80), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("near_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("place_entries.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_place_entries_place_world", "place_entries", ["place_id", "world"])
    op.drop_column("place_layouts", "layout")


def downgrade() -> None:
    op.add_column("place_layouts", sa.Column("layout", sa.Text(), nullable=False, server_default=""))
    op.drop_index("ix_place_entries_place_world", table_name="place_entries")
    op.drop_table("place_entries")
