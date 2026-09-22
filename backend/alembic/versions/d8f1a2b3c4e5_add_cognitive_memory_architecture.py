"""Add the cognitive memory architecture.

Revision ID: d8f1a2b3c4e5
Revises: c4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d8f1a2b3c4e5"
down_revision = "c4e5f6a7b8c9"
branch_labels = None
depends_on = None


EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    # The former long-term store already contains generalized facts, so retain
    # all rows and give the table its precise cognitive-memory name.
    op.rename_table("long_term_memories", "semantic_memories")
    op.execute("ALTER INDEX ix_long_term_memories_user_id RENAME TO ix_semantic_memories_user_id")
    op.drop_constraint("uq_memory_user_key", "semantic_memories", type_="unique")
    op.create_unique_constraint(
        "uq_semantic_memory_user_key", "semantic_memories", ["user_id", "normalized_key"]
    )
    op.add_column(
        "semantic_memories",
        sa.Column("supporting_episode_ids", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
    )
    op.add_column(
        "semantic_memories",
        sa.Column("contradicting_episode_ids", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
    )
    op.add_column(
        "semantic_memories", sa.Column("evidence_count", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "semantic_memories", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint(
        "ck_semantic_memory_importance", "semantic_memories", "importance BETWEEN 0 AND 1"
    )
    op.create_check_constraint(
        "ck_semantic_memory_confidence", "semantic_memories", "confidence BETWEEN 0 AND 1"
    )
    op.create_check_constraint(
        "ck_semantic_memory_status",
        "semantic_memories",
        "status IN ('active', 'superseded', 'deleted')",
    )

    # Replace four flat prototype metrics with versioned, extensible groups.
    for name, column_type, default in (
        ("schema_version", sa.Integer(), "1"),
        ("version", sa.Integer(), "1"),
        ("identity", postgresql.JSONB(), EMPTY_OBJECT),
        ("traits", postgresql.JSONB(), EMPTY_OBJECT),
        ("perception_priors", postgresql.JSONB(), EMPTY_OBJECT),
        ("interpretation_biases", postgresql.JSONB(), EMPTY_OBJECT),
        ("drives", postgresql.JSONB(), EMPTY_OBJECT),
        ("action_style", postgresql.JSONB(), EMPTY_OBJECT),
        ("values", postgresql.JSONB(), EMPTY_OBJECT),
        ("learning_policy", postgresql.JSONB(), EMPTY_OBJECT),
        ("locked_fields", postgresql.JSONB(), EMPTY_ARRAY),
        ("is_active", sa.Boolean(), "true"),
    ):
        op.add_column(
            "core_memory",
            sa.Column(name, column_type, server_default=default, nullable=False),
        )
    op.execute(
        """
        UPDATE core_memory
        SET identity = jsonb_build_object('name', name),
            perception_priors = jsonb_build_object('caution', caution),
            drives = jsonb_build_object('curiosity', curiosity),
            action_style = jsonb_build_object('initiative', initiative, 'warmth', warmth)
        """
    )
    for constraint in (
        "ck_core_memory_curiosity",
        "ck_core_memory_initiative",
        "ck_core_memory_caution",
        "ck_core_memory_warmth",
    ):
        op.drop_constraint(constraint, "core_memory", type_="check")
    for column in ("curiosity", "initiative", "caution", "warmth"):
        op.drop_column("core_memory", column)

    op.execute(
        """
        INSERT INTO core_memory (
            name, identity, traits, perception_priors, interpretation_biases,
            drives, action_style, values, learning_policy, locked_fields
        ) VALUES (
            'columbina',
            jsonb_build_object('name', 'Columbina', 'role', 'cognitive companion'),
            jsonb_build_object('openness', 0.85, 'conscientiousness', 0.70, 'sociability', 0.55, 'agreeableness', 0.82, 'emotional_stability', 0.72),
            jsonb_build_object('novelty_sensitivity', 0.80, 'social_cue_sensitivity', 0.68, 'threat_sensitivity', 0.25, 'contradiction_sensitivity', 0.60, 'detail_attention', 0.65),
            jsonb_build_object('optimism_prior', 0.72, 'trust_prior', 0.64, 'hostile_attribution_bias', 0.10, 'ambiguity_tolerance', 0.55, 'charitable_interpretation', 0.82),
            jsonb_build_object('curiosity', 0.88, 'affiliation', 0.75, 'helpfulness', 0.84, 'competence', 0.70, 'autonomy', 0.55),
            jsonb_build_object('warmth', 0.80, 'directness', 0.62, 'assertiveness', 0.42, 'playfulness', 0.58, 'initiative', 0.67, 'emotional_expressiveness', 0.60),
            jsonb_build_object('honesty', 0.95, 'compassion', 0.88, 'user_autonomy', 0.95, 'privacy', 0.95, 'non_harm', 1.0),
            jsonb_build_object('allow_automatic_core_updates', false, 'minimum_supporting_events', 3, 'maximum_metric_step', 0.01, 'human_feedback_weight', 1.0, 'ai_feedback_weight', 0.25),
            jsonb_build_array('values.non_harm', 'values.privacy', 'values.user_autonomy')
        ) ON CONFLICT (name) DO NOTHING
        """
    )

    op.create_table(
        "working_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), server_default="agent", nullable=False),
        sa.Column("context_window_size", sa.Integer(), server_default="30", nullable=False),
        sa.Column("current_topic", sa.Text(), nullable=True),
        sa.Column("active_goals", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("unresolved_questions", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("cognitive_state", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("retrieved_memories", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("last_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("mode IN ('agent', 'cognitive')", name="ck_working_memory_mode"),
        sa.CheckConstraint(
            "context_window_size BETWEEN 1 AND 200", name="ck_working_memory_context_window"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index("ix_working_memories_user_id", "working_memories", ["user_id"])

    op.create_table(
        "episodic_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("participants", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("context", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("source_message_ids", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("emotional_significance", sa.Float(), server_default="0", nullable=False),
        sa.Column("importance", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consolidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "emotional_significance BETWEEN 0 AND 1", name="ck_episodic_memory_emotion"
        ),
        sa.CheckConstraint("importance BETWEEN 0 AND 1", name="ck_episodic_memory_importance"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_episodic_memory_confidence"),
        sa.CheckConstraint(
            "status IN ('active', 'consolidated', 'superseded', 'deleted')",
            name="ck_episodic_memory_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodic_memories_user_id", "episodic_memories", ["user_id"])
    op.create_index("ix_episodic_memories_conversation_id", "episodic_memories", ["conversation_id"])

    op.create_table(
        "learning_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("core_memory_name", sa.String(100), nullable=True),
        sa.Column("core_memory_version", sa.Integer(), nullable=True),
        sa.Column("situation", postgresql.JSONB(), nullable=False),
        sa.Column("perceived_cues", postgresql.JSONB(), server_default=EMPTY_ARRAY, nullable=False),
        sa.Column("predicted_interpretation", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("selected_action", postgresql.JSONB(), server_default=EMPTY_OBJECT, nullable=False),
        sa.Column("generated_response", sa.Text(), nullable=True),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column("feedback", postgresql.JSONB(), nullable=True),
        sa.Column("feedback_source", sa.String(16), nullable=True),
        sa.Column("reward", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("model_version", sa.String(120), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_learning_memory_confidence"),
        sa.CheckConstraint("reward IS NULL OR reward BETWEEN -1 AND 1", name="ck_learning_memory_reward"),
        sa.CheckConstraint(
            "feedback_source IS NULL OR feedback_source IN ('human', 'ai', 'system', 'outcome')",
            name="ck_learning_memory_feedback_source",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewed', 'applied', 'rejected')",
            name="ck_learning_memory_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["core_memory_name"], ["core_memory.name"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_memories_user_id", "learning_memories", ["user_id"])
    op.create_index("ix_learning_memories_conversation_id", "learning_memories", ["conversation_id"])
    op.create_index("ix_learning_memories_status", "learning_memories", ["status"])


def downgrade() -> None:
    op.drop_index("ix_learning_memories_status", table_name="learning_memories")
    op.drop_index("ix_learning_memories_conversation_id", table_name="learning_memories")
    op.drop_index("ix_learning_memories_user_id", table_name="learning_memories")
    op.drop_table("learning_memories")
    op.drop_index("ix_episodic_memories_conversation_id", table_name="episodic_memories")
    op.drop_index("ix_episodic_memories_user_id", table_name="episodic_memories")
    op.drop_table("episodic_memories")
    op.drop_index("ix_working_memories_user_id", table_name="working_memories")
    op.drop_table("working_memories")

    for name in ("curiosity", "initiative", "caution", "warmth"):
        op.add_column(
            "core_memory", sa.Column(name, sa.Float(), server_default="0.5", nullable=False)
        )
    op.execute(
        """
        UPDATE core_memory
        SET curiosity = COALESCE((drives->>'curiosity')::float, 0.5),
            initiative = COALESCE((action_style->>'initiative')::float, 0.5),
            caution = COALESCE((perception_priors->>'caution')::float, 0.5),
            warmth = COALESCE((action_style->>'warmth')::float, 0.5)
        """
    )
    for name in ("curiosity", "initiative", "caution", "warmth"):
        op.create_check_constraint(
            f"ck_core_memory_{name}", "core_memory", f"{name} BETWEEN 0 AND 1"
        )
    for column in (
        "is_active",
        "locked_fields",
        "learning_policy",
        "values",
        "action_style",
        "drives",
        "interpretation_biases",
        "perception_priors",
        "traits",
        "identity",
        "version",
        "schema_version",
    ):
        op.drop_column("core_memory", column)

    for constraint in (
        "ck_semantic_memory_status",
        "ck_semantic_memory_confidence",
        "ck_semantic_memory_importance",
    ):
        op.drop_constraint(constraint, "semantic_memories", type_="check")
    for column in (
        "revision",
        "evidence_count",
        "contradicting_episode_ids",
        "supporting_episode_ids",
    ):
        op.drop_column("semantic_memories", column)
    op.drop_constraint("uq_semantic_memory_user_key", "semantic_memories", type_="unique")
    op.create_unique_constraint(
        "uq_memory_user_key", "semantic_memories", ["user_id", "normalized_key"]
    )
    op.execute("ALTER INDEX ix_semantic_memories_user_id RENAME TO ix_long_term_memories_user_id")
    op.rename_table("semantic_memories", "long_term_memories")
