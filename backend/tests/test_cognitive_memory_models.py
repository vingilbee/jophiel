from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.base import Base
from app.db.models import (
    CoreMemory,
    EpisodicMemory,
    LearningMemory,
    SemanticMemory,
    WorkingMemory,
)


def _constraint_names(table, kind):
    return {constraint.name for constraint in table.constraints if isinstance(constraint, kind)}


def test_cognitive_memory_tables_are_registered_without_legacy_long_term_table():
    assert {
        "core_memory",
        "working_memories",
        "episodic_memories",
        "semantic_memories",
        "learning_memories",
    } <= set(Base.metadata.tables)
    assert "long_term_memories" not in Base.metadata.tables


def test_core_memory_is_versioned_and_grouped_by_cognitive_role():
    columns = set(CoreMemory.__table__.columns.keys())
    assert {
        "schema_version",
        "version",
        "identity",
        "traits",
        "perception_priors",
        "interpretation_biases",
        "drives",
        "action_style",
        "values",
        "learning_policy",
        "locked_fields",
    } <= columns
    assert {"curiosity", "initiative", "caution", "warmth"}.isdisjoint(columns)


def test_working_memory_is_one_snapshot_per_conversation():
    assert "ck_working_memory_mode" in _constraint_names(
        WorkingMemory.__table__, CheckConstraint
    )
    assert any(
        constraint.columns.keys() == ["conversation_id"]
        for constraint in WorkingMemory.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert WorkingMemory.__table__.c.context_window_size.server_default.arg == "30"


def test_semantic_and_episodic_memory_keep_evidence_and_provenance():
    assert {
        "supporting_episode_ids",
        "contradicting_episode_ids",
        "evidence_count",
        "revision",
    } <= set(SemanticMemory.__table__.columns.keys())
    assert {
        "conversation_id",
        "event_at",
        "source_message_ids",
        "emotional_significance",
        "consolidated_at",
    } <= set(EpisodicMemory.__table__.columns.keys())


def test_learning_memory_separates_prediction_from_feedback():
    columns = set(LearningMemory.__table__.columns.keys())
    assert {
        "situation",
        "perceived_cues",
        "predicted_interpretation",
        "selected_action",
        "outcome",
        "feedback",
        "feedback_source",
        "reward",
        "core_memory_version",
    } <= columns
    assert {
        "ck_learning_memory_feedback_source",
        "ck_learning_memory_reward",
        "ck_learning_memory_status",
    } <= _constraint_names(LearningMemory.__table__, CheckConstraint)
