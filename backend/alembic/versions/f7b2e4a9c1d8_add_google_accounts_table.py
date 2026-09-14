"""add google_accounts table

Stores the per-user Google OAuth grant that backs the Gmail MCP tools. Tokens
are held as Fernet ciphertext (see app/core/crypto.py), so the columns are
plain text of unbounded length.

Revision ID: f7b2e4a9c1d8
Revises: c3f2a1b4d5e6
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b2e4a9c1d8'
down_revision: Union[str, None] = 'c3f2a1b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'google_accounts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('google_sub', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('encrypted_refresh_token', sa.Text(), nullable=False),
        sa.Column('encrypted_access_token', sa.Text(), nullable=True),
        sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scopes', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_google_accounts_user_id'),
        'google_accounts',
        ['user_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_google_accounts_user_id'), table_name='google_accounts')
    op.drop_table('google_accounts')
