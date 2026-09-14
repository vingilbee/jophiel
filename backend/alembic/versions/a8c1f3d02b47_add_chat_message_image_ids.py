"""add image_ids to chat_messages

Lets a user attach photos to a chat turn. The ids point at the per-user image
store (services/image_store.py); the agent passes them to the food-vision
specialist rather than ever seeing the pixels.

Nullable with no backfill: existing messages simply had no attachments, and
NULL says that more honestly than an empty array would.

Revision ID: a8c1f3d02b47
Revises: f7b2e4a9c1d8
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a8c1f3d02b47'
down_revision: Union[str, None] = 'f7b2e4a9c1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'chat_messages',
        sa.Column('image_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('chat_messages', 'image_ids')
