"""Phone privacy (docs/PHONE_AFFINITY_PRIVACY_PLAN.md step 1): a grant to pass a detail is checked against the user's
own message (services/phone_authority.py). Existing grants keep ``verified`` NULL and are verified when first read."""

import sqlalchemy as sa
from alembic import op

revision = "d7a1f4c8e2b9"
down_revision = "c6f9e3b8d5a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phone_disclosures", sa.Column("verified", sa.Boolean(), nullable=True))
    op.add_column("phone_disclosures", sa.Column("verification", sa.String(300), nullable=True))
    op.add_column("phone_disclosures", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("phone_disclosures", "verified_at")
    op.drop_column("phone_disclosures", "verification")
    op.drop_column("phone_disclosures", "verified")
