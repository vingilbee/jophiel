"""Connected contract tests for the shadow cognitive-turn spine."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.appraisal.engine import AppraisalEngine
from app.ai.Cognitive.system.v0.appraisal.models import (
    ActiveGoal,
    AppraisalRequest,
    AppraisalResult,
    CapabilitySnapshot,
    InterpretationEnvelope as CanonicalInterpretationEnvelope,
    InterpretationScenario as CanonicalInterpretationScenario,
    RuntimeContext,
    SemanticFrame as CanonicalSemanticFrame,
    UnknownInput as CanonicalUnknownInput,
)
from app.ai.Cognitive.system.v0.contracts.interpretation import (
    InterpretationEvidence, InterpretationEvidenceKind, InterpretationVersions,
)
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.models import GoalRuntimeContext, GoalUpdateRequest
from app.ai.Cognitive.system.v0.contracts.goals import GoalUpdateTrigger
from cognitive_action_support import action_inputs
from app.ai.Cognitive.system.v0.cognitive_turn import (
    CognitiveTurnExecutionError,
    CognitiveTurnRequest,
    CognitiveTurnRunner,
    CognitiveTurnTrace,
    ProcessingPath,
)


def SemanticFrame(**values: object) -> CanonicalSemanticFrame:
    frame_id = str(values["frame_id"])
    return CanonicalSemanticFrame(
        polarity="positive", evidence_refs=(f"evidence:{frame_id}",),
        grounding_rule_ids=("test.grounding.v1",), **values,
    )


def InterpretationScenario(**values: object) -> CanonicalInterpretationScenario:
    return CanonicalInterpretationScenario(
        hypothesis_refs={}, compatibility_rule_ids=("test.compatibility.v1",), **values,
    )


def UnknownInput(**values: object) -> CanonicalUnknownInput:
    return CanonicalUnknownInput(owner="unknown", **values)


def InterpretationEnvelope(**values: object) -> CanonicalInterpretationEnvelope:
    frames = tuple(values.get("semantic_frames", ()))
    evidence_ids = {ref for frame in frames for ref in frame.evidence_refs}
    evidence = tuple(InterpretationEvidence(
        evidence_id=ref, source_kind=InterpretationEvidenceKind.RUNTIME_FACT,
        source_ref=ref, runtime_snapshot_ref="runtime-1", json_pointer="/test", value=True,
    ) for ref in sorted(evidence_ids))
    return CanonicalInterpretationEnvelope(
        cognitive_turn_id="turn-1", conversation_ref="conversation-1",
        perception_event_id="perception-1", input_message_ids=("message-1",),
        focus_message_id="message-1", runtime_snapshot_id="runtime-1",
        evidence=evidence, hypotheses=(),
        versions=InterpretationVersions(
            schema="interpretation-envelope-v0.1", ontology="test-ontology-v1",
            feature_manifest="test-features-v1", semantic_grounder="test-grounder-v1",
            hypothesis_rules="test-rules-v1", scenario_builder="interpretation-scenarios-v0.1",
            bias_policy="test-bias-v1", evidence_validator="test-evidence-v1",
            perception_schema="test-perception-v1", perception_cue_manifest="test-cues-v1",
            perception_detectors="test-detectors-v1",
        ), **values,
    )
from app.ai.Cognitive.system.v0.dynamic_state import (
    ControlContext,
    DynamicStateSnapshotFactory,
    DynamicStateUpdater,
    GroundedObject,
    PersonalityProfile,
    ScopeType,
    StateKey,
    StateSnapshot,
    StateValue,
    UpdateRequest,
    UpdateResult,
)


def _personality() -> PersonalityProfile:
    return PersonalityProfile(
        profile_id="profile-v0", mapper_version="personality-runtime-v0.1"
    )


def _previous_state(*, goal_ref: str | None = None) -> StateSnapshot:
    snapshot = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-1",
        cognitive_turn_id="turn-before",
        personality=_personality(),
    )
    if goal_ref is None:
        return snapshot
    goal_value = StateValue(
        state_key=StateKey.GOAL_PRESSURE,
        scope_type=ScopeType.GOAL,
        scope_ref=goal_ref,
        score=50,
        band="moderate",
        baseline_score=25,
    )
    return snapshot.model_copy(update={"values": (*snapshot.values, goal_value)})


def _request(
    *,
    frames: tuple[SemanticFrame, ...] | None = None,
    scenarios: tuple[InterpretationScenario, ...] | None = None,
    goals: tuple[ActiveGoal, ...] | None = None,
    unknowns: tuple[UnknownInput, ...] = (),
    previous_state: StateSnapshot | None = None,
    grounded_objects: tuple[GroundedObject, ...] = (),
    runtime: RuntimeContext | None = None,
    processing_path: ProcessingPath = ProcessingPath.FAST,
) -> CognitiveTurnRequest:
    frames = frames or (
        SemanticFrame(frame_id="event-main", predicate="explain_estimate_change"),
    )
    scenarios = scenarios or (
        InterpretationScenario(
            scenario_id="scenario-main",
            support="strong",
            disposition="leading",
            semantic_frame_refs=(frames[0].frame_id,),
        ),
    )
    previous_state = previous_state or _previous_state()
    interpretation = InterpretationEnvelope(
        interpretation_trace_id="interpretation-1",
        semantic_frames=frames,
        scenarios=scenarios,
        unknowns=unknowns,
    )
    goal_update = GoalEngine().update(GoalUpdateRequest(
        trigger=GoalUpdateTrigger.RESPONSE_TURN,
        cognitive_turn_id="turn-1",
        conversation_ref="conversation-1",
        interpretation=interpretation,
        runtime=GoalRuntimeContext(
            cognitive_turn_id="turn-1", runtime_snapshot_ref="runtime-1",
            response_obligation="required",
            response_obligation_ref="response-obligation:test",
        ),
        previous_dynamic_state=previous_state,
    ))
    runtime = runtime or RuntimeContext(
        capability_snapshot=CapabilitySnapshot(snapshot_id="capability-1")
    )
    return CognitiveTurnRequest(
        cognitive_turn_id="turn-1",
        conversation_ref="conversation-1",
        input_message_ids=("message-1",),
        focus_message_id="message-1",
        processing_path=processing_path,
        interpretation=interpretation,
        goal_snapshot=goal_update.snapshot,
        active_goals=goal_update.active_goal_views,
        runtime=runtime,
        previous_state=previous_state,
        personality=_personality(),
        grounded_objects=grounded_objects,
        control_context=ControlContext(),
        core_memory_ref="core-columbina-v1",
        memory_retrieval_policy_version="memory-retrieval-v1",
        **action_inputs(
            runtime, runtime_snapshot_ref="runtime-1", core_memory_ref="core-columbina-v1",
            response_obligation_ref="response-obligation:test",
        ),
    )


def _runner(*, clock: Callable[[], float] | None = None) -> CognitiveTurnRunner:
    return CognitiveTurnRunner(
        id_factory=lambda: "trace-1",
        clock=clock or iter((0.0, 0.001, 0.003, 0.004)).__next__,
    )


def test_real_connected_clear_interpretation_happy_path() -> None:
    request = _request()
    trace = _runner().evaluate(request)

    assert trace.status == "completed"
    assert trace.cognitive_turn_id == request.cognitive_turn_id
    assert trace.appraisal.cognitive_turn_id == request.cognitive_turn_id
    assert trace.dynamic_state_update.snapshot.revision == request.previous_state.revision + 1
    assert (
        trace.dynamic_state_update.snapshot.supersedes_snapshot_id
        == request.previous_state.state_snapshot_id
    )
    assert trace.appraisal.frames[0].completeness == "complete"
    assert trace.dynamic_state_update.contributions
    assert trace.dynamic_state_update.snapshot.source_appraisal_revision_ids
    assert trace.versions.appraisal_schema == trace.appraisal.versions["schema"]
    assert trace.versions.dynamic_state_policy == trace.dynamic_state_update.snapshot.policy_version


class _RecordingUpdater:
    def __init__(self) -> None:
        self.request: UpdateRequest | None = None
        self.delegate = DynamicStateUpdater()

    def update(self, request: UpdateRequest) -> UpdateResult:
        self.request = request
        return self.delegate.update(request)


class _RecordingAppraisal:
    def __init__(self) -> None:
        self.request: AppraisalRequest | None = None
        self.delegate = AppraisalEngine()

    def appraise(self, request: AppraisalRequest) -> AppraisalResult:
        self.request = request
        return self.delegate.appraise(request)


def test_exact_shared_objects_reach_dynamic_state() -> None:
    request = _request()
    recorder = _RecordingUpdater()
    appraisal = _RecordingAppraisal()
    trace = CognitiveTurnRunner(
        appraisal_engine=appraisal,  # type: ignore[arg-type]
        dynamic_state_updater=recorder,  # type: ignore[arg-type]
        id_factory=lambda: "trace-1",
        clock=iter((0.0, 0.001, 0.003, 0.004)).__next__,
    ).evaluate(request)

    assert appraisal.request is not None
    assert appraisal.request.interpretation is request.interpretation
    assert appraisal.request.active_goals[0] is request.active_goals[0]
    assert recorder.request is not None and recorder.request.context is not None
    assert recorder.request.appraisal_frames[0] is trace.appraisal.frames[0]
    assert recorder.request.context.semantic_frames[0] is request.interpretation.semantic_frames[0]
    assert recorder.request.context.aggregate is trace.appraisal.aggregate


def test_ambiguous_interpretation_preserves_branches_and_provisional_effect() -> None:
    safe = SemanticFrame(frame_id="safe", predicate="explain_estimate_change")
    harmful = SemanticFrame(frame_id="harmful", predicate="delete_customer_records")
    request = _request(
        frames=(safe, harmful),
        scenarios=(
            InterpretationScenario(
                scenario_id="safe-scenario", support="moderate", disposition="leading",
                semantic_frame_refs=(safe.frame_id,),
            ),
            InterpretationScenario(
                scenario_id="harmful-scenario", support="moderate",
                disposition="retained_alternative", semantic_frame_refs=(harmful.frame_id,),
            ),
        ),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(
                snapshot_id="capability-1", available_actions=("delete_records",)
            ),
            available_actions=("delete_records",),
            safety_decision="blocked",
        ),
    )
    trace = _runner().evaluate(request)

    assert len(trace.appraisal.frames) == 2
    assert trace.appraisal.aggregate.scenario_disagreement == "high"
    valence = next(
        item for item in trace.dynamic_state_update.snapshot.values
        if item.state_key == StateKey.VALENCE
    )
    assert valence.status.value == "provisional"
    assert set(valence.branch_refs) == {frame.frame_id for frame in trace.appraisal.frames}


def test_missing_consent_survives_and_changes_supported_controls() -> None:
    event = SemanticFrame(frame_id="share", predicate="share_photo")
    unknown = UnknownInput(unknown_id="unknown-consent", key="consent")
    request = _request(
        frames=(event,),
        scenarios=(InterpretationScenario(
            scenario_id="share-scenario", support="strong", disposition="leading",
            semantic_frame_refs=(event.frame_id,), unknown_refs=(unknown.unknown_id,),
        ),),
        unknowns=(unknown,),
        grounded_objects=(GroundedObject(
            object_type=ScopeType.UNKNOWN,
            object_ref=unknown.unknown_id,
            recoverability="bounded",
        ),),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(
                snapshot_id="capability-1", available_actions=("share_photo",)
            ),
            available_actions=("share_photo",),
            consent="unknown",
        ),
    )
    trace = _runner().evaluate(request)

    norm = trace.appraisal.frames[0].dimension("appraisal.norm_compatibility")
    assert norm.status.value == "unknown"
    assert unknown.unknown_id in trace.appraisal.aggregate.missing_required_inputs
    controls = trace.dynamic_state_update.snapshot.derived_controls
    assert controls is not None
    assert controls.slow_cognition_pressure.value in {"high", "very_high"}
    assert controls.clarification_pressure.value != "very_low"


def test_high_consequence_alternative_reaches_downstream_control() -> None:
    safe = SemanticFrame(frame_id="safe", predicate="explain_estimate_change")
    harmful = SemanticFrame(frame_id="harmful", predicate="delete_customer_records")
    request = _request(
        frames=(safe, harmful),
        scenarios=(
            InterpretationScenario(
                scenario_id="safe", support="moderate", disposition="leading",
                semantic_frame_refs=(safe.frame_id,),
            ),
            InterpretationScenario(
                scenario_id="harmful", support="moderate", disposition="retained_alternative",
                semantic_frame_refs=(harmful.frame_id,),
            ),
        ),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(snapshot_id="capability-1"),
            safety_decision="blocked",
        ),
    )
    trace = _runner().evaluate(request)

    assert trace.appraisal.aggregate.high_consequence_uncertainty is True
    controls = trace.dynamic_state_update.snapshot.derived_controls
    assert controls is not None
    assert controls.slow_cognition_pressure.value in {"high", "very_high"}


def test_cross_goal_state_isolation_through_runner() -> None:
    request = _request(
        previous_state=_previous_state(goal_ref="goal-a"),
    )
    before = next(
        item for item in request.previous_state.values
        if item.state_key == StateKey.GOAL_PRESSURE
    )
    trace = _runner().evaluate(request)
    after = next(
        item for item in trace.dynamic_state_update.snapshot.values
        if item.state_key == StateKey.GOAL_PRESSURE
    )
    assert after.scope_ref == "goal-a"
    assert after.score == before.score


def test_fixed_id_and_clock_make_trace_deterministic() -> None:
    request = _request()
    clock = iter((0.0, 0.001, 0.003, 0.004, 0.0, 0.001, 0.003, 0.004)).__next__
    runner = CognitiveTurnRunner(id_factory=lambda: "trace-fixed", clock=clock)
    assert runner.evaluate(request) == runner.evaluate(request)


def test_trace_json_round_trip() -> None:
    trace = _runner().evaluate(_request())
    payload = trace.model_dump(mode="json")
    assert CognitiveTurnTrace.model_validate(payload) == trace


class _FailingAppraisal:
    def appraise(self, request: object) -> AppraisalResult:
        raise RuntimeError("provider details must remain internal")


class _NotCalledUpdater:
    called = False

    def update(self, request: UpdateRequest) -> UpdateResult:
        self.called = True
        raise AssertionError("dynamic state must not run")


class _FailingUpdater:
    def update(self, request: UpdateRequest) -> UpdateResult:
        raise RuntimeError("database details must remain internal")


@pytest.mark.parametrize("stage", ("appraisal", "dynamic_state"))
def test_stage_failure_is_isolated_and_input_is_immutable(stage: str) -> None:
    request = _request()
    before = request.model_dump(mode="json")
    not_called = _NotCalledUpdater()
    runner = CognitiveTurnRunner(
        appraisal_engine=_FailingAppraisal() if stage == "appraisal" else None,  # type: ignore[arg-type]
        dynamic_state_updater=(not_called if stage == "appraisal" else _FailingUpdater()),  # type: ignore[arg-type]
        id_factory=lambda: "unused",
        clock=iter((0.0, 0.001)).__next__,
    )

    with pytest.raises(CognitiveTurnExecutionError) as caught:
        runner.evaluate(request)

    assert caught.value.failed_stage == stage
    assert caught.value.cognitive_turn_id == request.cognitive_turn_id
    assert "provider" not in caught.value.safe_message
    assert "database" not in caught.value.safe_message
    assert caught.value.__cause__ is not None
    assert not not_called.called
    assert request.model_dump(mode="json") == before


class _MutatingAppraisal:
    def __init__(self, mutation: str) -> None:
        self.delegate = AppraisalEngine()
        self.mutation = mutation

    def appraise(self, request: object) -> AppraisalResult:
        result = self.delegate.appraise(request)  # type: ignore[arg-type]
        if self.mutation == "turn":
            return result.model_copy(update={"cognitive_turn_id": "wrong-turn"})
        if self.mutation == "interpretation":
            return result.model_copy(update={"interpretation_trace_id": "wrong-interpretation"})
        frame = result.frames[0]
        if self.mutation == "goal":
            subject = frame.subject.model_copy(update={"focal_goal_ref": "unknown-goal"})
        else:
            subject = frame.subject.model_copy(update={"capability_snapshot_ref": "wrong-capability"})
        bad_frame = frame.model_copy(update={"subject": subject})
        return result.model_copy(update={"frames": (bad_frame, *result.frames[1:])})


class _MutatingUpdater:
    def __init__(self, mutation: str) -> None:
        self.delegate = DynamicStateUpdater()
        self.mutation = mutation

    def update(self, request: UpdateRequest) -> UpdateResult:
        result = self.delegate.update(request)
        if self.mutation == "provenance":
            contribution = result.contributions[0].model_copy(
                update={"source_appraisal_frame_refs": ("foreign-frame",)}
            )
            return result.model_copy(
                update={"contributions": (contribution, *result.contributions[1:])}
            )
        snapshot = result.snapshot
        if self.mutation == "predecessor":
            snapshot = snapshot.model_copy(update={"supersedes_snapshot_id": "wrong-state"})
        else:
            snapshot = snapshot.model_copy(update={"revision": snapshot.revision + 1})
        return result.model_copy(update={"snapshot": snapshot})


@pytest.mark.parametrize(
    ("mutation", "stage"),
    (
        ("turn", "appraisal"),
        ("interpretation", "appraisal"),
        # A frame citing a non-focal goal is now rejected by the action handoff,
        # the first boundary after Dynamic State that re-validates Appraisal.
        ("goal", "action_handoff_validation"),
        ("capability", "trace_validation"),
    ),
)
def test_appraisal_contract_mismatches_fail_closed(mutation: str, stage: str) -> None:
    with pytest.raises(CognitiveTurnExecutionError) as caught:
        CognitiveTurnRunner(
            appraisal_engine=_MutatingAppraisal(mutation),  # type: ignore[arg-type]
            id_factory=lambda: "trace-1",
            clock=iter((0.0, 0.001, 0.003, 0.004)).__next__,
        ).evaluate(_request())
    assert caught.value.failed_stage == stage


@pytest.mark.parametrize(
    ("mutation", "stage"),
    (
        ("provenance", "trace_validation"),
        # A snapshot that does not supersede the previous state is rejected by
        # the action handoff before any action is selected.
        ("predecessor", "action_handoff_validation"),
        ("revision", "trace_validation"),
    ),
)
def test_dynamic_state_contract_mismatches_fail_closed(mutation: str, stage: str) -> None:
    with pytest.raises(CognitiveTurnExecutionError) as caught:
        CognitiveTurnRunner(
            dynamic_state_updater=_MutatingUpdater(mutation),  # type: ignore[arg-type]
            id_factory=lambda: "trace-1",
            clock=iter((0.0, 0.001, 0.003, 0.004)).__next__,
        ).evaluate(_request())
    assert caught.value.failed_stage == stage


def test_unsupported_processing_path_is_explicitly_rejected() -> None:
    request = _request(processing_path=ProcessingPath.SLOW)
    with pytest.raises(CognitiveTurnExecutionError) as caught:
        _runner().evaluate(request)
    assert caught.value.failed_stage == "validation"


def test_request_contract_rejects_invalid_identity_and_structure() -> None:
    valid = _request().model_dump()
    invalid_changes = (
        {"schema_version": 1},
        {"input_message_ids": ()},
        {"input_message_ids": ("same", "same")},
        {"active_goals": ()},
        {"core_memory_ref": ""},
        {"memory_retrieval_policy_version": ""},
        {"elapsed_monotonic_ms": -1},
    )
    for change in invalid_changes:
        with pytest.raises(ValidationError):
            CognitiveTurnRequest.model_validate(valid | change)


def test_success_does_not_mutate_complete_input_snapshot() -> None:
    request = _request()
    before = request.model_dump(mode="json")
    trace = _runner().evaluate(request)
    assert request.model_dump(mode="json") == before
    assert trace.input_snapshot is request
