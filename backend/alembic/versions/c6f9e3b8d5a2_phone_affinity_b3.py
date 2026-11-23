"""Affinity B3 (docs/PHONE_PHASE_8_AFFINITY_B3.md): one claim per autonomous reply attempt on ``phone_outbound``
(the exchange she answers, or a follow-up's due occurrence), so a replay, retry or concurrent wake publishes once."""

import sqlalchemy as sa
from alembic import op

revision = "c6f9e3b8d5a2"
down_revision = "b5e8d2a7c4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phone_outbound", sa.Column("attempt_key", sa.String(300), nullable=True))
    op.create_index("uq_phone_outbound_attempt", "phone_outbound", ["user_id", "attempt_key"], unique=True,
                    postgresql_where=sa.text("attempt_key IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_phone_outbound_attempt", table_name="phone_outbound")
    op.drop_column("phone_outbound", "attempt_key")
