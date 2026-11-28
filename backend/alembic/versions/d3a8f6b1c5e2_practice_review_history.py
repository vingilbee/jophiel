"""The reviewer of her practice reads its own history (services/practice.py): every review kept per place
(practice_reviews: summary, result, counts, the user's advice), and each note's earlier versions
(practice_notes.history)."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3a8f6b1c5e2"
down_revision = "c1e5a9d3f7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("practice_notes", sa.Column("history", postgresql.JSONB(), nullable=False,
                                              server_default=sa.text("'[]'::jsonb")))
    op.create_table(
        "practice_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("places", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("user_notes", sa.Text(), nullable=True),
        sa.Column("tallies", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_practice_reviews_user", "practice_reviews", ["user_id", "surface", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_practice_reviews_user", table_name="practice_reviews")
    op.drop_table("practice_reviews")
    op.drop_column("practice_notes", "history")
