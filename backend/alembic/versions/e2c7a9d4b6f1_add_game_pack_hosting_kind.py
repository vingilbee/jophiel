"""Desktop environments may be a game pack (stage G).

Revision ID: e2c7a9d4b6f1
Revises: d5a9c3f7e1b4
Create Date: 2026-10-09
"""
from alembic import op

revision = "e2c7a9d4b6f1"
down_revision = "d5a9c3f7e1b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_desktop_environments_hosting_kind", "desktop_environments", type_="check")
    op.create_check_constraint(
        "ck_desktop_environments_hosting_kind", "desktop_environments",
        "hosting_kind IN ('local_vm', 'cloud_vm', 'game_pack')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM desktop_environments WHERE hosting_kind = 'game_pack'")
    op.drop_constraint("ck_desktop_environments_hosting_kind", "desktop_environments", type_="check")
    op.create_check_constraint(
        "ck_desktop_environments_hosting_kind", "desktop_environments",
        "hosting_kind IN ('local_vm', 'cloud_vm')",
    )
