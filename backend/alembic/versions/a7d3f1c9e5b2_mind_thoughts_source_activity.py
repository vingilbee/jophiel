"""Her thoughts say which source and activity they came from (mind in everyday chat, CLAUDE.md "A general mind").

``mind_thoughts.session_id`` only names a game session, so a thought about a conversation had nowhere to say what
it was about. ``source`` ("minecraft", "conversation") and ``activity_id`` (the source's own id: a game session id,
a conversation id) say it for every source. No foreign key on ``activity_id``: it points at different tables per
source, and a thought outlives neither the user (cascade) nor its retention window. Existing rows are game thoughts:
``source='minecraft'``, ``activity_id=session_id``. ``session_id`` stays, as before, for game thoughts.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d3f1c9e5b2"
down_revision = "e4c8a2f6b9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mind_thoughts", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("mind_thoughts", sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute("UPDATE mind_thoughts SET source = 'minecraft', activity_id = session_id WHERE session_id IS NOT NULL")
    op.create_index("ix_mind_thoughts_activity_created", "mind_thoughts", ["user_id", "activity_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mind_thoughts_activity_created", table_name="mind_thoughts")
    op.drop_column("mind_thoughts", "activity_id")
    op.drop_column("mind_thoughts", "source")
