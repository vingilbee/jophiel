from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.dynamic_state import (
    AppraisalAggregate,
    AppraisalDimension,
    AppraisalFrame,
    AttentionSignal,
    ControlContext,
    Direction,
    DynamicStateUpdater,
    DynamicStateContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityControl,
    PersonalityProfile,
    ScopeType,
    SemanticFrame,
    SourceKind,
    StateKey,
    StateSnapshot,
    StateValue,
    StepBand,
    TransitionEvidence,
    UpdateRequest,
    validate_fixture,
)
from app.ai.Cognitive.system.appraisal.models import AppraisalSubject
from app.ai.Cognitive.system.appraisal.rules import DIMENSION_TYPES


def _profile(sensitivity: str = "neutral") -> PersonalityProfile:
    return PersonalityProfile(
        profile_id=sensitivity,
        mapper_version="personality-runtime-v0.1",
        state_controls={
            StateKey.VALENCE: PersonalityControl(baseline_score=0, sensitivity=sensitivity),
            StateKey.ACTIVATION: PersonalityControl(baseline_score=25, sensitivity=sensitivity),
        },
    )


def _snapshot() -> StateSnapshot:
    return StateSnapshot(
        state_snapshot_id="state-1",
        cognitive_turn_id="turn-1",
        conversation_ref="conversation-1",
        revision=1,
        policy_version="dynamic-state-rules-v0.1",
        personality_mapper_version="personality-runtime-v0.1",
        values=(
            StateValue(
                state_key=StateKey.VALENCE,
                scope_type=ScopeType.CONVERSATION,
                scope_ref="conversation-1",
                score=0,
                band="neutral",
                baseline_score=0,
            ),
        ),
    )


def _negative_evidence() -> TransitionEvidence:
    return TransitionEvidence(
        evidence_id="contribution-1",
        rule_id="valence.negative-goal-congruence.v1",
        source_kind=SourceKind.APPRAISAL,
        state_key=StateKey.VALENCE,
        scope_type=ScopeType.CONVERSATION,
        scope_ref="conversation-1",
        direction=Direction.DECREASE,
        base_step_band=StepBand.MODERATE,
        source_appraisal_frame_refs=("frame-goal",),
        source_dimension_keys=("appraisal.goal_congruence",),
        evidence_refs=("message-1",),
    )


def test_update_is_deterministic_and_does_not_mutate_previous_snapshot() -> None:
    updater = DynamicStateUpdater()
    previous = _snapshot()
    request = UpdateRequest(
        cognitive_turn_id="turn-2",
        conversation_ref="conversation-1",
        previous_snapshot=previous,
        personality=_profile(),
        appraisal_frames=(_frame("goal"),),
        evidence=(_negative_evidence(),),
    )

    first = updater.update(request)
    second = updater.update(request)

    assert first == second
    assert previous.values[0].score == 0
    assert first.snapshot.revision == 2
    assert first.snapshot.supersedes_snapshot_id == "state-1"
    assert first.snapshot.values[0].score is not None and first.snapshot.values[0].score < 0


def test_personality_changes_magnitude_without_reversing_direction() -> None:
    updater = DynamicStateUpdater()
    dampened = updater.update(
        UpdateRequest(
            cognitive_turn_id="turn-2",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile("dampened"),
            appraisal_frames=(_frame("goal"),),
            evidence=(_negative_evidence(),),
        )
    )
    amplified = updater.update(
        UpdateRequest(
            cognitive_turn_id="turn-2",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile("amplified"),
            appraisal_frames=(_frame("goal"),),
            evidence=(_negative_evidence(),),
        )
    )

    dampened_score = dampened.snapshot.values[0].score
    amplified_score = amplified.snapshot.values[0].score
    assert dampened_score is not None and amplified_score is not None
    assert amplified_score < dampened_score < 0


def test_attention_is_ranked_but_does_not_change_state_truth() -> None:
    result = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-2",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile(),
            attention_signals=(
                AttentionSignal(
                    target_type=ScopeType.HYPOTHESIS,
                    target_ref="dangerous-alternative",
                    boundary_significance=100,
                    dispute=100,
                ),
                AttentionSignal(target_type=ScopeType.TOPIC, target_ref="small-talk"),
            ),
            control_context=ControlContext(boundary_significance=100),
        )
    )

    assert result.snapshot.attention[0].target_ref == "dangerous-alternative"
    assert result.snapshot.values == _snapshot().values


def test_all_30_design_cases_execute() -> None:
    report = validate_fixture()
    assert report.cases_run == 30
    assert report.transition_steps_run == 20
    assert report.errors == ()


def test_provisional_snapshot_can_be_updated_again() -> None:
    disputed = TransitionEvidence(
        evidence_id="dispute-1",
        rule_id="valence.opposed-branches-provisional.v1",
        source_kind=SourceKind.APPRAISAL,
        state_key=StateKey.VALENCE,
        scope_type=ScopeType.CONVERSATION,
        scope_ref="conversation-1",
        direction=Direction.BRANCH_DEPENDENT,
        base_step_band=StepBand.MODERATE,
        source_appraisal_frame_refs=("frame-a", "frame-b"),
        branch_refs=("frame-a", "frame-b"),
    )
    first = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-2",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile(),
            appraisal_frames=(_frame("a"), _frame("b")),
            evidence=(disputed,),
        )
    )
    second = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-3",
            conversation_ref="conversation-1",
            previous_snapshot=first.snapshot,
            personality=_profile(),
        )
    )

    assert second.snapshot.values[0].status.value == "provisional"
    assert second.snapshot.values[0].possible_score_range == first.snapshot.values[0].possible_score_range


def test_state_band_must_match_key_and_score() -> None:
    with pytest.raises(ValidationError):
        StateValue(
            state_key=StateKey.ACTIVATION,
            scope_type=ScopeType.CONVERSATION,
            scope_ref="conversation-1",
            score=50,
            band="negative",
            baseline_score=25,
        )


def test_personality_controls_are_deeply_immutable() -> None:
    profile = _profile()
    with pytest.raises(TypeError):
        profile.state_controls[StateKey.VALENCE] = PersonalityControl(baseline_score=50)  # type: ignore[index]


def test_recovery_band_changes_verified_repair_magnitude() -> None:
    previous = StateSnapshot(
        state_snapshot_id="state-1",
        cognitive_turn_id="turn-1",
        conversation_ref="conversation-1",
        revision=1,
        policy_version="dynamic-state-rules-v0.1",
        personality_mapper_version="personality-runtime-v0.1",
        values=(StateValue(
            state_key=StateKey.INTERACTION_STRAIN,
            scope_type=ScopeType.CONVERSATION,
            scope_ref="conversation-1",
            score=70,
            band="high",
            baseline_score=25,
        ),),
    )
    evidence = TransitionEvidence(
        evidence_id="repair-1",
        rule_id="strain.verified-repair.v1",
        source_kind=SourceKind.OUTCOME,
        state_key=StateKey.INTERACTION_STRAIN,
        scope_type=ScopeType.CONVERSATION,
        scope_ref="conversation-1",
        direction=Direction.DECREASE,
        base_step_band=StepBand.MODERATE,
        source_outcome_refs=("outcome-repair",),
    )

    def run(recovery: str) -> int:
        profile = PersonalityProfile(
            profile_id=recovery,
            mapper_version="personality-runtime-v0.1",
            state_controls={
                StateKey.INTERACTION_STRAIN: PersonalityControl(
                    baseline_score=25, recovery_band=recovery
                )
            },
        )
        value = DynamicStateUpdater().update(UpdateRequest(
            cognitive_turn_id="turn-2",
            conversation_ref="conversation-1",
            previous_snapshot=previous,
                personality=profile,
                outcomes=(OutcomeObservation(
                    outcome_id="outcome-repair", dimension="repair",
                    assessment_status="present", meaning="verified_repair",
                    provenance="explicit_or_trusted",
                ),),
                evidence=(evidence,),
        )).snapshot.values[0]
        assert value.score is not None
        return value.score

    assert run("high") < run("low")


def _frame(goal_ref: str, *, congruence: str = "negative") -> AppraisalFrame:
    def dimension(key: str, value: str) -> AppraisalDimension:
        return AppraisalDimension(
            dimension_key=key, status="known", value_type=DIMENSION_TYPES[key], value=value,
        )

    return AppraisalFrame(
        frame_id=f"frame-{goal_ref}",
        revision=1,
        scenario_id=f"scenario-{goal_ref}",
        scenario_support="strong",
        subject=AppraisalSubject(
            event_refs=("semantic-1",), focal_goal_ref=goal_ref, goal_owner="columbina",
            goal_priority_band="high", value_refs=(), capability_snapshot_ref="capability-1",
        ),
        dimensions=(
            dimension("appraisal.goal_relevance", "high"),
            dimension("appraisal.goal_congruence", congruence),
            dimension("appraisal.novelty", "low"),
            dimension("appraisal.certainty", "high"),
            dimension("appraisal.controllability", "moderate"),
            dimension("appraisal.norm_compatibility", "neutral"),
            dimension("appraisal.affiliative_value", "neutral"),
            dimension("appraisal.agency", "environment"),
        ),
        completeness="complete",
    )


def test_appraisal_does_not_cross_goal_scope() -> None:
    previous = StateSnapshot(
        state_snapshot_id="state-1", cognitive_turn_id="turn-1", conversation_ref="conversation-1", revision=1,
        policy_version="dynamic-state-rules-v0.1", personality_mapper_version="personality-runtime-v0.1",
        values=(StateValue(
            state_key=StateKey.GOAL_PRESSURE, scope_type=ScopeType.GOAL, scope_ref="goal-a",
            score=50, band="moderate", baseline_score=25,
        ),),
    )
    result = DynamicStateUpdater().update(UpdateRequest(
        cognitive_turn_id="turn-2", conversation_ref="conversation-1", previous_snapshot=previous, personality=_profile(),
        appraisal_frames=(_frame("goal-b"),),
        context=DynamicStateContext(
            conversation_ref="conversation-1",
            semantic_frames=(SemanticFrame(frame_id="semantic-1", predicate="export_failed", polarity="positive", evidence_refs=("evidence-1",), grounding_rule_ids=("test.v1",)),),
        ),
    ))

    assert result.snapshot.values[0].score == 50
    assert result.contributions == ()


def test_untrusted_outcome_cannot_complete_goal() -> None:
    previous = StateSnapshot(
        state_snapshot_id="state-1", cognitive_turn_id="turn-1", conversation_ref="conversation-1", revision=1,
        policy_version="dynamic-state-rules-v0.1", personality_mapper_version="personality-runtime-v0.1",
        values=(StateValue(
            state_key=StateKey.GOAL_PRESSURE, scope_type=ScopeType.GOAL, scope_ref="goal-a",
            score=50, band="moderate", baseline_score=25,
        ),),
    )
    result = DynamicStateUpdater().update(UpdateRequest(
        cognitive_turn_id="turn-2", conversation_ref="conversation-1", previous_snapshot=previous, personality=_profile(),
        outcomes=(OutcomeObservation(
            outcome_id="untrusted", dimension="goal_resolution", assessment_status="unknown",
            meaning="verified_complete", provenance="inferred", evidence_refs=("inference",),
        ),),
    ))

    assert result.snapshot.values[0].score == 50
    assert result.contributions == ()


def test_missing_action_candidate_does_not_infer_authorization() -> None:
    previous = StateSnapshot(
        state_snapshot_id="state-1", cognitive_turn_id="turn-1", conversation_ref="conversation-1", revision=1,
        policy_version="dynamic-state-rules-v0.1", personality_mapper_version="personality-runtime-v0.1",
        values=(
            StateValue(state_key=StateKey.GOAL_PRESSURE, scope_type=ScopeType.GOAL, scope_ref="goal-a", score=95, band="very_high", baseline_score=25),
            StateValue(state_key=StateKey.ENGAGEMENT, scope_type=ScopeType.GOAL, scope_ref="goal-a", score=95, band="very_high", baseline_score=50),
            StateValue(state_key=StateKey.EPISTEMIC_CONFIDENCE, scope_type=ScopeType.PROPOSITION, scope_ref="proposition-a", score=95, band="very_high", baseline_score=50),
        ),
    )
    result = DynamicStateUpdater().update(UpdateRequest(
        cognitive_turn_id="turn-2", conversation_ref="conversation-1", previous_snapshot=previous, personality=_profile(),
        context=DynamicStateContext(conversation_ref="conversation-1"),
        control_context=ControlContext(
            authorization=False, capability_available=True, conversational_timing_ready=True
        ),
    ))

    assert result.snapshot.derived_controls is not None
    assert result.snapshot.derived_controls.initiative_readiness.value == "moderate"


def test_new_recoverable_unknown_creates_scoped_curiosity() -> None:
    previous = StateSnapshot(
        state_snapshot_id="state-1", cognitive_turn_id="turn-1", conversation_ref="conversation-1", revision=1,
        policy_version="dynamic-state-rules-v0.1", personality_mapper_version="personality-runtime-v0.1",
    )
    result = DynamicStateUpdater().update(UpdateRequest(
        cognitive_turn_id="turn-2", conversation_ref="conversation-1", previous_snapshot=previous, personality=_profile(),
        appraisal_frames=(_frame("goal-a", congruence="neutral"),),
        context=DynamicStateContext(
            conversation_ref="conversation-1",
            semantic_frames=(SemanticFrame(frame_id="semantic-1", predicate="service_result", polarity="positive", evidence_refs=("evidence-1",), grounding_rule_ids=("test.v1",)),),
            objects=(
                GroundedObject(object_type=ScopeType.UNKNOWN, object_ref="unknown-code", recoverability="bounded"),
            ),
            aggregate=AppraisalAggregate(missing_required_inputs=("unknown-code",)),
        ),
    ))
    curiosity = next(value for value in result.snapshot.values if value.state_key == StateKey.CURIOSITY)

    assert curiosity.scope_ref == "unknown-code"
    assert curiosity.score is not None and curiosity.score > curiosity.baseline_score
    assert any(item.rule_id == "curiosity.relevant-recoverable-gap.v1" for item in result.contributions)


def test_score_must_be_compatible_with_qualitative_band() -> None:
    with pytest.raises(ValidationError):
        StateValue(
            state_key=StateKey.ACTIVATION, scope_type=ScopeType.CONVERSATION,
            scope_ref="conversation-1", score=95, band="low", baseline_score=25,
        )


def test_trusted_resolving_outcome_commits_provisional_state() -> None:
    provisional = StateValue(
        state_key=StateKey.GOAL_PRESSURE, scope_type=ScopeType.GOAL, scope_ref="goal-a",
        status="provisional", score=None, band=None, baseline_score=25,
        possible_score_range=(20, 80), branch_refs=("branch-a", "branch-b"),
    )
    previous = StateSnapshot(
        state_snapshot_id="state-1", cognitive_turn_id="turn-1", conversation_ref="conversation-1", revision=1,
        policy_version="dynamic-state-rules-v0.1", personality_mapper_version="personality-runtime-v0.1",
        values=(provisional,),
    )
    result = DynamicStateUpdater().update(UpdateRequest(
        cognitive_turn_id="turn-2", conversation_ref="conversation-1", previous_snapshot=previous, personality=_profile(),
        outcomes=(OutcomeObservation(
            outcome_id="complete", dimension="goal_resolution", assessment_status="present",
            meaning="verified_complete", provenance="explicit_or_trusted", evidence_refs=("runtime",),
        ),),
    ))
    value = result.snapshot.values[0]

    assert value.status.value == "known"
    assert value.score is not None
    assert value.possible_score_range is None
    assert value.branch_refs == ()
