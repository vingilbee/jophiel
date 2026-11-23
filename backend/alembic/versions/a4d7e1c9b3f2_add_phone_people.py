"""Phone people, conversations, relationship records and her outbound replies (affinity B1,
docs/PHONE_PHASE_8_AFFINITY_B1.md). Names, opaque ids and her own sent text only."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4d7e1c9b3f2"
down_revision = "e73b2c6d9a10"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _user() -> sa.Column:
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "phone_people",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("names", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("in_contacts", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_phone_people_status"),
    )
    op.create_index("ix_phone_people_user_id", "phone_people", ["user_id"])

    op.create_table(
        "phone_person_keys",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("sender_id", sa.String(128), nullable=False),
        sa.Column("identity", sa.String(16), nullable=False),
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "sender_id", name="uq_phone_person_keys_sender"),
        sa.CheckConstraint("identity IN ('contact', 'name')", name="ck_phone_person_keys_identity"),
    )
    op.create_index("ix_phone_person_keys_person_id", "phone_person_keys", ["person_id"])

    op.create_table(
        "phone_conversations",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("conversation_id", sa.String(128), nullable=False),
        sa.Column("conversation_identity", sa.String(16), nullable=True),
        sa.Column("package", sa.String(200), nullable=True),
        sa.Column("app", sa.String(80), nullable=True),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "conversation_id", name="uq_phone_conversations_conversation"),
        sa.CheckConstraint("conversation_identity IS NULL OR conversation_identity IN ('shortcut', 'title')",
                           name="ck_phone_conversations_identity"),
    )

    op.create_table(
        "phone_conversation_members",
        sa.Column("conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_message_at", TS, nullable=True),
    )
    op.create_index("ix_phone_conversation_members_person_id", "phone_conversation_members", ["person_id"])

    op.create_table(
        "phone_affinities",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("familiarity", sa.String(16), nullable=False, server_default="new"),
        sa.Column("warmth", sa.String(16), nullable=False, server_default="neutral"),
        sa.Column("initiative", sa.String(16), nullable=False, server_default="none"),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("note_conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("preferences", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_direct_exchange_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "person_id", name="uq_phone_affinities_person"),
        sa.CheckConstraint("familiarity IN ('new', 'acquainted', 'familiar', 'close')",
                           name="ck_phone_affinities_familiarity"),
        sa.CheckConstraint("warmth IN ('wary', 'cool', 'neutral', 'warm', 'fond')", name="ck_phone_affinities_warmth"),
        sa.CheckConstraint("initiative IN ('none', 'would_enjoy', 'wants_to', 'holding_back')",
                           name="ck_phone_affinities_initiative"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 300", name="ck_phone_affinities_note"),
    )
    op.create_index("ix_phone_affinities_person_id", "phone_affinities", ["person_id"])

    op.create_table(
        "phone_relationship_evidence",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("observation", sa.String(300), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("retracted_at", TS, nullable=True),
        sa.UniqueConstraint("person_id", "event_id", "dimension", name="uq_phone_relationship_evidence_event"),
        sa.CheckConstraint("dimension IN ('familiarity', 'warmth', 'initiative', 'note', 'preferences')",
                           name="ck_phone_relationship_evidence_dimension"),
    )
    op.create_index("ix_phone_relationship_evidence_user_id", "phone_relationship_evidence", ["user_id"])

    op.create_table(
        "phone_outbound",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        sa.Column("conversation_id", sa.String(128), nullable=True),
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notification_key", sa.String(256), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("asked_by", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="sending"),
        sa.Column("echo_event_id", sa.String(128), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("settled_at", TS, nullable=True),
        sa.CheckConstraint("asked_by IN ('user', 'her')", name="ck_phone_outbound_asked_by"),
        sa.CheckConstraint("status IN ('sending', 'sent', 'failed', 'unknown')", name="ck_phone_outbound_status"),
    )
    op.create_index("ix_phone_outbound_person_id", "phone_outbound", ["person_id"])
    op.create_index("ix_phone_outbound_conversation", "phone_outbound", ["user_id", "conversation_id", "created_at"])
    op.create_index("uq_phone_outbound_echo", "phone_outbound", ["user_id", "echo_event_id"], unique=True,
                    postgresql_where=sa.text("echo_event_id IS NOT NULL"))


def downgrade() -> None:
    for table in ("phone_outbound", "phone_relationship_evidence", "phone_affinities", "phone_conversation_members",
                  "phone_conversations", "phone_person_keys", "phone_people"):
        op.drop_table(table)
