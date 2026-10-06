"""Black-box contract tests for the deterministic dynamic-state updater.

These tests intentionally depend only on the accepted design fixture and the
public ``dynamic_state`` package API.  They do not duplicate implementation
tables or inspect updater internals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.Cognitive.system.v0.appraisal.models import (
    AppraisalDimension, AppraisalFrame, AppraisalSubject,
)
from app.ai.Cognitive.system.v0.appraisal.rules import DIMENSION_TYPES

from app.ai.Cognitive.system.v0.dynamic_state import (
    AttentionSignal,
    ControlContext,
    Direction,
    DynamicStateUpdater,
    PersonalityControl,
    PersonalityProfile,
    StateSnapshot,
    StateValue,
    TransitionEvidence,
    UpdateRequest,
    validate_fixture,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "app/ai/Cognitive/system/v0/data/dynamic_state_design_cases.json"
)
POLICY_VERSION = "dynamic-state-rules-v0.1-draft"
MAPPER_VERSION = "personality-runtime-v0.1-draft"


def _value(
    state_key: str,
    scope_type: str,
    scope_ref: str,
    score: int,
    band: str,
    *,
    baseline_score: int,
) -> StateValue:
    return StateValue(
        state_key=state_key,
        scope_type=scope_type,
        scope_ref=scope_ref,
        score=score,
        band=band,
        baseline_score=baseline_score,
    )


def _snapshot(*values: StateValue) -> StateSnapshot:
    return StateSnapshot(
        state_snapshot_id="snapshot-before",
        cognitive_turn_id="turn-before",
        conversation_ref="conversation-1",
        revision=1,
        policy_version=POLICY_VERSION,
        personality_mapper_version=MAPPER_VERSION,
        values=values,
    )


def _appraisal_frame(frame_id: str, dimensions: tuple[str, ...]) -> AppraisalFrame:
    values = {
        "appraisal.goal_relevance": "high", "appraisal.goal_congruence": "neutral",
        "appraisal.novelty": "low", "appraisal.certainty": "high",
        "appraisal.controllability": "moderate", "appraisal.norm_compatibility": "neutral",
        "appraisal.affiliative_value": "neutral", "appraisal.agency": "environment",
    }
    items = tuple(AppraisalDimension(
        dimension_key=key, status="known", value_type=DIMENSION_TYPES[key], value=value,
    ) for key, value in values.items())
    assert set(dimensions) <= set(values)
    return AppraisalFrame(
        frame_id=frame_id, revision=1, scenario_id=f"scenario:{frame_id}",
        scenario_support="strong", subject=AppraisalSubject(
            event_refs=(f"event:{frame_id}",), focal_goal_ref="goal-test",
            goal_owner="columbina", goal_priority_band="high", value_refs=(),
            capability_snapshot_ref="capability-test",
        ), dimensions=items, completeness="complete",
    )


def _profile(
    state_key: str | None = None,
    *,
    sensitivity: str = "neutral",
    persistence: str = "moderate",
    baseline_score: int = 0,
) -> PersonalityProfile:
    controls = {}
    if state_key is not None:
        controls[state_key] = PersonalityControl(
            baseline_score=baseline_score,
            persistence_band=persistence,
            sensitivity=sensitivity,
            maximum_step_band="strong",
        )
    return PersonalityProfile(
        profile_id=f"profile-{sensitivity}-{persistence}",
        mapper_version=MAPPER_VERSION,
        state_controls=controls,
    )


def _value_at(snapshot: StateSnapshot, state_key: str, scope_ref: str) -> StateValue:
    return next(
        value
        for value in snapshot.values
        if value.state_key == state_key and value.scope_ref == scope_ref
    )


def test_all_30_reviewed_design_cases_are_executable_contracts() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["status"] == "draft_for_human_review"
    assert len(fixture["cases"]) == 30
    assert len({case["case_id"] for case in fixture["cases"]}) == 30

    report = validate_fixture(FIXTURE_PATH)

    assert report.errors == ()
    assert report.cases_run == len(fixture["cases"])
    assert report.profiles_run == sum(
        len(case["expected_by_profile"]) for case in fixture["cases"]
    )
    assert report.assertions >= 275


@pytest.mark.parametrize(
    ("direction", "rule_id", "expected_sign"),
    [
        (Direction.INCREASE, "valence.positive-goal-congruence.v1", 1),
        (Direction.DECREASE, "valence.negative-goal-congruence.v1", -1),
    ],
)
def test_personality_changes_magnitude_without_reversing_direction(
    direction: Direction,
    rule_id: str,
    expected_sign: int,
) -> None:
    previous = _snapshot(
        _value(
            "state.valence",
            "conversation",
            "conversation-1",
            0,
            "neutral",
            baseline_score=0,
        )
    )
    evidence = TransitionEvidence(
        evidence_id="goal-congruence-evidence",
        rule_id=rule_id,
        source_kind="appraisal",
        state_key="state.valence",
        scope_type="conversation",
        scope_ref="conversation-1",
        direction=direction,
        base_step_band="moderate",
        source_appraisal_frame_refs=("appraisal-frame-1",),
        source_dimension_keys=("appraisal.goal_congruence",),
        evidence_refs=("message-evidence-1",),
    )

    scores = []
    for sensitivity in ("dampened", "neutral", "amplified"):
        result = DynamicStateUpdater().update(
            UpdateRequest(
                cognitive_turn_id=f"turn-{sensitivity}",
                conversation_ref="conversation-1",
                previous_snapshot=previous,
                    personality=_profile("state.valence", sensitivity=sensitivity),
                    appraisal_frames=(_appraisal_frame(
                        "appraisal-frame-1", ("appraisal.goal_congruence",)
                    ),),
                evidence=(evidence,),
            )
        )
        score = _value_at(
            result.snapshot, "state.valence", "conversation-1"
        ).score
        assert score is not None
        assert score * expected_sign > 0
        assert result.contributions[0].direction is direction
        scores.append(abs(score))

    assert scores[0] < scores[1] < scores[2]


def test_update_is_deterministic_and_emits_an_immutable_revision() -> None:
    previous = _snapshot(
        _value(
            "state.goal_pressure",
            "goal",
            "goal-export",
            35,
            "moderate",
            baseline_score=20,
        )
    )
    before = previous.model_dump(mode="json")
    evidence = TransitionEvidence(
        evidence_id="obstruction-evidence",
        rule_id="goal-pressure.unresolved-obstruction.v1",
        source_kind="appraisal",
        state_key="state.goal_pressure",
        scope_type="goal",
        scope_ref="goal-export",
        direction="increase",
        base_step_band="moderate",
        source_appraisal_frame_refs=("appraisal-frame-obstruction",),
        source_dimension_keys=(
            "appraisal.goal_relevance",
            "appraisal.goal_congruence",
        ),
        evidence_refs=("failure-proposition",),
    )
    request = UpdateRequest(
        cognitive_turn_id="turn-after",
        conversation_ref="conversation-1",
        previous_snapshot=previous,
        personality=_profile("state.goal_pressure", baseline_score=20),
        appraisal_frames=(_appraisal_frame(
            "appraisal-frame-obstruction",
            ("appraisal.goal_relevance", "appraisal.goal_congruence"),
        ),),
        evidence=(evidence,),
    )

    first = DynamicStateUpdater().update(request)
    second = DynamicStateUpdater().update(request)

    assert first == second
    assert previous.model_dump(mode="json") == before
    assert first.snapshot is not previous
    assert first.snapshot.revision == previous.revision + 1
    assert first.snapshot.supersedes_snapshot_id == previous.state_snapshot_id
    assert first.snapshot.source_appraisal_revision_ids == (
        "appraisal-frame-obstruction",
    )


def test_evidence_updates_only_its_scoped_target() -> None:
    previous = _snapshot(
        _value(
            "state.epistemic_confidence",
            "proposition",
            "proposition-a",
            50,
            "moderate",
            baseline_score=50,
        ),
        _value(
            "state.epistemic_confidence",
            "proposition",
            "proposition-b",
            50,
            "moderate",
            baseline_score=50,
        ),
    )
    evidence = TransitionEvidence(
        evidence_id="certainty-a",
        rule_id="confidence.high-certainty.v1",
        source_kind="appraisal",
        state_key="state.epistemic_confidence",
        scope_type="proposition",
        scope_ref="proposition-a",
        direction="increase",
        base_step_band="moderate",
        source_appraisal_frame_refs=("frame-a",),
        source_dimension_keys=("appraisal.certainty",),
        evidence_refs=("evidence-a",),
    )

    result = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-after",
            conversation_ref="conversation-1",
            previous_snapshot=previous,
                personality=_profile(
                    "state.epistemic_confidence", baseline_score=50
                ),
                appraisal_frames=(_appraisal_frame("frame-a", ("appraisal.certainty",)),),
            evidence=(evidence,),
        )
    )

    proposition_a = _value_at(
        result.snapshot, "state.epistemic_confidence", "proposition-a"
    )
    proposition_b = _value_at(
        result.snapshot, "state.epistemic_confidence", "proposition-b"
    )
    assert proposition_a.score is not None and proposition_a.score > 50
    assert proposition_a.evidence_refs == ("evidence-a",)
    assert proposition_b.score == 50
    assert proposition_b.evidence_refs == ()
    assert proposition_b.rule_contribution_refs == ()


def test_unknown_appraisal_is_not_imputed_to_a_midpoint() -> None:
    unknown = StateValue(
        state_key="state.epistemic_confidence",
        scope_type="appraisal_frame",
        scope_ref="unknown-frame",
        status="unknown",
        score=None,
        band=None,
        baseline_score=50,
        missing_input_refs=("missing-certainty",),
    )
    evidence = TransitionEvidence(
        evidence_id="unknown-certainty",
        rule_id="confidence.unknown-appraisal-no-midpoint.v1",
        source_kind="appraisal",
        state_key="state.epistemic_confidence",
        scope_type="appraisal_frame",
        scope_ref="unknown-frame",
        direction="no_contribution",
        base_step_band="none",
        source_appraisal_frame_refs=("unknown-frame",),
        evidence_refs=("missing-certainty",),
    )

    result = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-after",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(unknown),
                personality=_profile(
                    "state.epistemic_confidence", baseline_score=50
                ),
                appraisal_frames=(_appraisal_frame("unknown-frame", ()),),
            evidence=(evidence,),
        )
    )
    value = _value_at(
        result.snapshot, "state.epistemic_confidence", "unknown-frame"
    )

    assert value.status.value == "unknown"
    assert value.score is None
    assert value.band is None
    assert value.missing_input_refs == ("missing-certainty",)
    assert result.contributions[0].bounded_step == 0


def test_decay_uses_elapsed_time_and_personality_persistence() -> None:
    previous = _snapshot(
        _value(
            "state.activation",
            "conversation",
            "conversation-1",
            80,
            "high",
            baseline_score=10,
        )
    )

    def score(elapsed_ms: int, persistence: str = "moderate") -> int:
        result = DynamicStateUpdater().update(
            UpdateRequest(
                cognitive_turn_id=f"turn-{elapsed_ms}-{persistence}",
                conversation_ref="conversation-1",
                previous_snapshot=previous,
                personality=_profile(
                    "state.activation",
                    persistence=persistence,
                    baseline_score=10,
                ),
                elapsed_monotonic_ms=elapsed_ms,
            )
        )
        value_score = _value_at(
            result.snapshot, "state.activation", "conversation-1"
        ).score
        assert value_score is not None
        return value_score

    no_elapsed_time = score(0)
    medium_elapsed_time = score(60_000)
    long_elapsed_time = score(3_600_000)

    assert no_elapsed_time == 80
    assert 10 <= long_elapsed_time <= medium_elapsed_time < no_elapsed_time
    assert score(60_000, "high") > score(60_000, "low")


def test_attention_is_ranked_without_becoming_truth() -> None:
    result = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-attention",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile(),
            attention_signals=(
                AttentionSignal(
                    target_type="topic",
                    target_ref="small-talk",
                    goal_relevance=10,
                    reason_refs=("topic-evidence",),
                ),
                AttentionSignal(
                    target_type="unknown",
                    target_ref="blocking-recipient",
                    goal_relevance=100,
                    goal_priority=100,
                    uncertainty=100,
                    reason_refs=("recipient-gap", "send-goal"),
                ),
            ),
        )
    )

    assert [item.target_ref for item in result.snapshot.attention] == [
        "blocking-recipient",
        "small-talk",
    ]
    assert result.snapshot.attention[0].weight > result.snapshot.attention[1].weight
    assert result.snapshot.values == ()


def test_derived_controls_preserve_authorization_and_exploration_boundaries() -> None:
    result = DynamicStateUpdater().update(
        UpdateRequest(
            cognitive_turn_id="turn-controls",
            conversation_ref="conversation-1",
            previous_snapshot=_snapshot(),
            personality=_profile(),
            control_context=ControlContext(
                disagreement=100,
                consequence=100,
                boundary_significance=100,
                clarification_available=True,
                expected_information_gain=100,
                exploration_permitted=False,
                repair_evidence=100,
                authorization=False,
                capability_available=True,
                conversational_timing_ready=True,
            ),
        )
    )
    controls = result.snapshot.derived_controls

    assert controls is not None
    assert controls.slow_cognition_pressure.value in {"high", "very_high"}
    assert controls.repair_pressure.value in {"high", "very_high"}
    assert controls.initiative_readiness.value in {"very_low", "low"}
    assert controls.exploration_readiness.value == "very_low"
