"""Affinity B2 (docs/PHONE_PHASE_8_AFFINITY_B2.md): her mind's readings of phone exchanges, shadow relationship
proposals, requests for space, details the user authorized per audience, and phone-scoped assessments and lessons
(shadow until live, removed with the person)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b5e8d2a7c4f1"
down_revision = "a4d7e1c9b3f2"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB()
TRIGGERS_BEFORE = ("correction", "complaint", "execution", "sample", "review")
TRIGGERS = (*TRIGGERS_BEFORE, "phone")


def _user() -> sa.Column:
    return sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _person(index: bool = False) -> sa.Column:
    return sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=False,
                     index=index)


def _conversation(ondelete: str = "CASCADE", nullable: bool = False) -> sa.Column:
    return sa.Column("conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete=ondelete),
                     nullable=nullable)


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_learning_assessments_trigger", "learning_assessments", type_="check")
    op.create_check_constraint("ck_learning_assessments_trigger", "learning_assessments", _in("trigger_kind", TRIGGERS))
    op.add_column("learning_assessments", sa.Column(
        "phone_person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_learning_assessments_phone_person_id", "learning_assessments", ["phone_person_id"])

    op.add_column("memory_lessons", sa.Column(
        "phone_person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), nullable=True))
    op.add_column("memory_lessons", sa.Column(
        "phone_conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete="CASCADE"), nullable=True))
    op.add_column("memory_lessons", sa.Column("phone_applies_to", JSONB, nullable=False,
                                              server_default=sa.text("'[]'::jsonb")))
    op.add_column("memory_lessons", sa.Column("phone_live", sa.Boolean(), nullable=False,
                                              server_default=sa.text("false")))
    op.create_index("ix_memory_lessons_phone_person_id", "memory_lessons", ["phone_person_id"])
    op.create_check_constraint("ck_memory_lessons_phone_scope", "memory_lessons",
                               "(phone_person_id IS NULL) = (phone_conversation_id IS NULL)")

    op.create_table(
        "phone_exchanges",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        _person(),
        _conversation(),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("events", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("addressed", sa.String(8), nullable=False),
        sa.Column("participated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("observation", sa.String(300), nullable=False),
        sa.Column("their_words", sa.String(300), nullable=True),
        sa.Column("applies_to", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("lesson", sa.String(12), nullable=False, server_default="none"),
        sa.Column("lesson_why", sa.String(300), nullable=True),
        sa.Column("assessment_id", UUID, sa.ForeignKey("learning_assessments.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "person_id", "event_key", name="uq_phone_exchanges_event"),
        sa.CheckConstraint("addressed IN ('her', 'owner', 'group', 'unclear')", name="ck_phone_exchanges_addressed"),
        sa.CheckConstraint("lesson IN ('none', 'assessing', 'skipped')", name="ck_phone_exchanges_lesson"),
    )
    op.create_index("ix_phone_exchanges_user_id", "phone_exchanges", ["user_id"])
    op.create_index("ix_phone_exchanges_person", "phone_exchanges", ["person_id", "conversation_id", "at"])

    op.add_column("phone_relationship_evidence", sa.Column("value", sa.String(300), nullable=True))
    op.add_column("phone_relationship_evidence", sa.Column(
        "exchange_id", UUID, sa.ForeignKey("phone_exchanges.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_phone_relationship_evidence_exchange_id", "phone_relationship_evidence", ["exchange_id"])

    op.create_table(
        "phone_affinity_shadows",
        sa.Column("person_id", UUID, sa.ForeignKey("phone_people.id", ondelete="CASCADE"), primary_key=True),
        _user(),
        sa.Column("familiarity", sa.String(16), nullable=True),
        sa.Column("warmth", sa.String(16), nullable=True),
        sa.Column("initiative", sa.String(16), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("note_conversation_id", UUID, sa.ForeignKey("phone_conversations.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_phone_affinity_shadows_user_id", "phone_affinity_shadows", ["user_id"])

    op.create_table(
        "phone_contact_boundaries",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        _person(index=True),
        _conversation(),
        sa.Column("scope", sa.String(12), nullable=False),
        sa.Column("topic", sa.String(120), nullable=True),
        sa.Column("words", sa.String(300), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("lifted_at", TS, nullable=True),
        sa.UniqueConstraint("person_id", "event_key", name="uq_phone_contact_boundaries_event"),
        sa.CheckConstraint("scope IN ('everywhere', 'here')", name="ck_phone_contact_boundaries_scope"),
    )
    op.create_index("ix_phone_contact_boundaries_user_id", "phone_contact_boundaries", ["user_id"])

    op.create_table(
        "phone_disclosures",
        sa.Column("id", UUID, primary_key=True),
        _user(),
        _person(index=True),
        _conversation(),
        sa.Column("detail", sa.String(500), nullable=False),
        sa.Column("source_message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", TS, nullable=True),
    )
    op.create_index("ix_phone_disclosures_user_id", "phone_disclosures", ["user_id"])


def downgrade() -> None:
    op.drop_table("phone_disclosures")
    op.drop_table("phone_contact_boundaries")
    op.drop_table("phone_affinity_shadows")
    op.drop_index("ix_phone_relationship_evidence_exchange_id", "phone_relationship_evidence")
    op.drop_column("phone_relationship_evidence", "exchange_id")
    op.drop_column("phone_relationship_evidence", "value")
    op.drop_table("phone_exchanges")
    # Phone lessons and assessments have no place in the earlier schema: they go with it.
    op.execute("DELETE FROM memory_lessons WHERE phone_person_id IS NOT NULL")
    op.drop_constraint("ck_memory_lessons_phone_scope", "memory_lessons", type_="check")
    op.drop_index("ix_memory_lessons_phone_person_id", "memory_lessons")
    for column in ("phone_live", "phone_applies_to", "phone_conversation_id", "phone_person_id"):
        op.drop_column("memory_lessons", column)
    op.execute("DELETE FROM learning_assessments WHERE trigger_kind = 'phone'")
    op.drop_index("ix_learning_assessments_phone_person_id", "learning_assessments")
    op.drop_column("learning_assessments", "phone_person_id")
    op.drop_constraint("ck_learning_assessments_trigger", "learning_assessments", type_="check")
    op.create_check_constraint("ck_learning_assessments_trigger", "learning_assessments",
                               _in("trigger_kind", TRIGGERS_BEFORE))
