"""A promise kept by words on a channel that confirms delivery later (WhatsApp) waits on that message:
``working_items.keeping``. Fulfilled when the channel says delivered; open with why when it fails."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2a6d9c3b8e1"
down_revision = "e8b3f1a6c4d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("working_items", sa.Column("keeping", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("working_items", "keeping")
