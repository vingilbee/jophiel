from __future__ import annotations

from uuid import uuid4

import pytest

from app.ai.Cognitive.system.v0.dynamic_state import (
    AppraisalDimension as DynamicAppraisalDimension,
    AppraisalFrame as DynamicAppraisalFrame,
    DynamicStateContext,
    DynamicStateSnapshotFactory,
    DynamicStateSnapshotPersistence,
    DynamicStateUpdater,
    InitialSnapshotAlreadyExists,
    PersonalityControl,
    PersonalityProfile,
    ScopeType,
    SemanticFrame as DynamicSemanticFrame,
    StateKey,
    UpdateRequest,
)
from app.ai.Cognitive.system.v0.appraisal.engine import AppraisalEngine
from app.ai.Cognitive.system.v0.appraisal.models import (
    ActiveGoal,
    AppraisalDimension,
    AppraisalFrame,
    AppraisalRequest,
    CapabilitySnapshot,
    InterpretationEnvelope,
    InterpretationScenario,
    RuntimeContext,
    SemanticFrame,
)
from app.ai.Cognitive.system.v0.dynamic_state.policy import DEFAULT_BASELINE_SCORES
from app.ai.Cognitive.system.v0.contracts.interpretation import (
    InterpretationEvidence, InterpretationEvidenceKind, InterpretationVersions,
)
from app.ai.Cognitive.system.v0.contracts.goals import (
    GoalDefinitionRef, GoalRequirement, GoalTargetRef,
)


def _personality(**controls: PersonalityControl) -> PersonalityProfile:
    return PersonalityProfile(
        profile_id="test-profile",
        mapper_version="personality-runtime-v0.1",
        state_controls={StateKey(key): value for key, value in controls.items()},
    )


def test_factory_creates_only_five_conversation_scoped_values() -> None:
    snapshot = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-1",
        personality=_personality(),
    )

    assert {value.state_key for value in snapshot.values} == {
        StateKey.VALENCE,
        StateKey.ACTIVATION,
        StateKey.ENGAGEMENT,
        StateKey.SOCIAL_ALIGNMENT,
        StateKey.INTERACTION_STRAIN,
    }
    assert len(snapshot.values) == 5
    assert all(value.scope_type == ScopeType.CONVERSATION for value in snapshot.values)
    assert all(value.scope_ref == "conversation-1" for value in snapshot.values)
    assert {value.state_key for value in snapshot.values}.isdisjoint(
        {StateKey.CURIOSITY, StateKey.EPISTEMIC_CONFIDENCE, StateKey.GOAL_PRESSURE}
    )


def test_personality_overrides_baseline_and_defaults_fill_missing_controls() -> None:
    snapshot = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-1",
        personality=_personality(
            **{
                StateKey.VALENCE.value: PersonalityControl(baseline_score=20),
                StateKey.ACTIVATION.value: PersonalityControl(baseline_score=50),
            }
        ),
    )
    values = {value.state_key: value for value in snapshot.values}

    assert values[StateKey.VALENCE].score == 20
    assert values[StateKey.VALENCE].baseline_score == 20
    assert values[StateKey.ACTIVATION].score == 50
    assert values[StateKey.ENGAGEMENT].score == DEFAULT_BASELINE_SCORES[StateKey.ENGAGEMENT]
    assert values[StateKey.INTERACTION_STRAIN].score == DEFAULT_BASELINE_SCORES[StateKey.INTERACTION_STRAIN]
    assert all(value.evidence_refs == ("state-initialization:conversation-1",) for value in values.values())


def test_initial_revision_is_deterministic_and_has_no_transition_outputs() -> None:
    factory = DynamicStateSnapshotFactory()
    arguments = dict(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-1",
        personality=_personality(),
    )

    first = factory.create_initial(**arguments)
    second = factory.create_initial(**arguments)

    assert first == second
    assert first.state_snapshot_id == "turn-1:state:r1"
    assert first.revision == 1
    assert first.supersedes_snapshot_id is None
    assert first.source_appraisal_revision_ids == ()
    assert first.source_outcome_ids == ()
    assert first.attention == ()
    assert first.derived_controls is None


class _ScalarResults:
    def __init__(self, *results: object) -> None:
        self.results = iter(results)
        self.statements = []

    def scalar(self, statement):  # type: ignore[no-untyped-def]
        self.statements.append(statement)
        return next(self.results)


def test_atomic_persistence_rejects_a_second_initial_snapshot() -> None:
    snapshot = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-1",
        personality=_personality(),
    )
    db = _ScalarResults(uuid4(), None)
    persistence = DynamicStateSnapshotPersistence()
    ids = dict(user_id=uuid4(), conversation_id=uuid4())

    assert persistence.persist_initial(db, snapshot=snapshot, **ids) is snapshot  # type: ignore[arg-type]
    with pytest.raises(InitialSnapshotAlreadyExists):
        persistence.persist_initial(db, snapshot=snapshot, **ids)  # type: ignore[arg-type]

    sql = str(db.statements[0])
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    assert "working_memories.cognitive_state" in sql
    assert "working_memories.user_id" in sql


def test_initial_snapshot_flows_into_first_appraisal_revision() -> None:
    personality = _personality()
    initial = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-1",
        personality=personality,
    )
    semantic = SemanticFrame(
        frame_id="semantic-1", predicate="explain_estimate_change", polarity="positive",
        evidence_refs=("evidence-1",), grounding_rule_ids=("test.grounding.v1",),
    )
    appraisal_request = AppraisalRequest(
        cognitive_turn_id="turn-2",
        interpretation=InterpretationEnvelope(
            interpretation_trace_id="interpretation-1",
            cognitive_turn_id="turn-2", conversation_ref="conversation-1",
            perception_event_id="perception-1", input_message_ids=("message-1",),
            focus_message_id="message-1", runtime_snapshot_id="runtime-1",
            evidence=(InterpretationEvidence(
                evidence_id="evidence-1", source_kind=InterpretationEvidenceKind.RUNTIME_FACT,
                source_ref="evidence-1", runtime_snapshot_ref="runtime-1",
                json_pointer="/test", value=True,
            ),), hypotheses=(),
            semantic_frames=(semantic,),
            scenarios=(InterpretationScenario(
                scenario_id="scenario-1", support="strong", disposition="leading",
                semantic_frame_refs=(semantic.frame_id,),
                hypothesis_refs={}, compatibility_rule_ids=("test.compatibility.v1",),
            ),),
            versions=InterpretationVersions(
                schema="interpretation-envelope-v0.1", ontology="test-ontology-v1",
                feature_manifest="test-features-v1", semantic_grounder="test-grounder-v1",
                hypothesis_rules="test-rules-v1", scenario_builder="interpretation-scenarios-v0.1",
                bias_policy="test-bias-v1", evidence_validator="test-evidence-v1",
                perception_schema="test-perception-v1", perception_cue_manifest="test-cues-v1",
                perception_detectors="test-detectors-v1",
            ),
        ),
        active_goals=(ActiveGoal(
            goal_id="goal-1",
            definition_ref=GoalDefinitionRef(
                definition_key="interaction.answer_information_request",
                definition_version="v0.1-draft",
            ),
            owner="columbina", domain="interaction", mode="achieve",
            scope="turn", scope_ref="turn-2", priority_band="high",
            requirements=(GoalRequirement(
                requirement_id="goal-1-requirement",
                requirement_key="supported_answer_provided",
                target_refs=(GoalTargetRef(role="topic_ref", ref="estimate-change"),),
                parameters={"answer_scope": "estimate-change"},
                satisfaction_policy_key="goal-satisfaction.supported-answer.v1",
            ),),
            source_refs=("message-1",),
        ),),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(snapshot_id="capability-1")
        ),
    )
    appraisal_result = AppraisalEngine().appraise(appraisal_request)
    update_request = UpdateRequest(
        cognitive_turn_id="turn-2",
        conversation_ref="conversation-1",
        previous_snapshot=initial,
        personality=personality,
        appraisal_frames=appraisal_result.frames,
        context=DynamicStateContext(
            conversation_ref="conversation-1",
            semantic_frames=appraisal_request.interpretation.semantic_frames,
            aggregate=appraisal_result.aggregate,
        ),
    )
    assert DynamicAppraisalFrame is AppraisalFrame
    assert DynamicAppraisalDimension is AppraisalDimension
    assert DynamicSemanticFrame is SemanticFrame
    assert update_request.appraisal_frames[0] is appraisal_result.frames[0]

    result = DynamicStateUpdater().update(update_request)
    values = {value.state_key: value for value in result.snapshot.values}

    assert result.snapshot.revision == 2
    assert result.snapshot.supersedes_snapshot_id == initial.state_snapshot_id
    assert values[StateKey.VALENCE].score > initial.values[0].score
    initial_activation = next(value.score for value in initial.values if value.state_key == StateKey.ACTIVATION)
    assert values[StateKey.ACTIVATION].score is not None
    assert initial_activation is not None
    assert values[StateKey.ACTIVATION].score > initial_activation
