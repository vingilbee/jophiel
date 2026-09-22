"""Contract and real-engine checks for the canonical Cognitive Pipeline V0."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.appraisal import ActiveGoal
from app.ai.Cognitive.system.cognitive_pipeline_v0 import (
    CognitivePipelineExecutionError,
    CognitivePipelineRelationshipContext,
    CognitivePipelineRequest,
    CognitivePipelineRuntimeSnapshot,
    CognitivePipelineV0,
)
from app.ai.Cognitive.system.cognitive_turn import (
    CognitiveTurnExecutionError,
    CognitiveTurnRunner,
)
from app.ai.Cognitive.system.dynamic_state import (
    DynamicStateSnapshotFactory,
    PersonalityProfile,
)
from app.ai.Cognitive.system.interpretation_v0.fixture_validator import (
    DEFAULT_FIXTURE,
    _runtime_case,
)
from app.ai.Cognitive.system.interpretation_v0 import (
    InterpretationEngine,
    InterpretationExecutionError,
)
from app.ai.Cognitive.system.perception_v0 import (
    PerceptionEngine,
    PerceptionEvent,
    PerceptionMessage,
    TextSpanEvidence,
)


def _request(case_id: str = "factual-information-question") -> CognitivePipelineRequest:
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = next(item for item in document["cases"] if item["case_id"] == case_id)
    _, interpretation_request = _runtime_case(case)
    runtime = interpretation_request.runtime
    personality = PersonalityProfile(
        profile_id="pipeline-profile", mapper_version="personality-runtime-v0.1"
    )
    previous = DynamicStateSnapshotFactory().create_initial(
        conversation_ref=interpretation_request.conversation_ref,
        cognitive_turn_id="pipeline-before",
        personality=personality,
    )
    return CognitivePipelineRequest(
        cognitive_turn_id=interpretation_request.cognitive_turn_id,
        conversation_ref=interpretation_request.conversation_ref,
        messages=interpretation_request.messages,
        focus_message_id=interpretation_request.perception.focus_message_id,
        runtime=CognitivePipelineRuntimeSnapshot(
            snapshot_id=runtime.snapshot_id,
            predicate_registry_version=runtime.predicate_registry_version,
            known_predicates=runtime.known_predicates,
            known_entities=runtime.known_entities,
            attachments=runtime.attachments,
            available_capabilities=runtime.available_capabilities,
            capability_snapshot_id=f"capability:{case_id}",
            task_contract=runtime.task_contract,
            authorization=runtime.authorization,
            safety_decision=runtime.safety_decision,
            last_tool_event=runtime.last_tool_event,
        ),
        memories=interpretation_request.memories,
        interpretation_biases=interpretation_request.biases,
        active_goals=(ActiveGoal(
            goal_id="goal-answer", owner="columbina", goal_type="conversation",
            description="Answer the grounded request", desired_state={"answered": True},
            priority_band="high",
        ),),
        previous_state=previous,
        state_personality=personality,
        core_memory_ref=interpretation_request.biases.source_core_memory_ref,
        memory_retrieval_policy_version="memory-retrieval-v1",
    )


def test_real_pipeline_preserves_exact_authoritative_outputs() -> None:
    request = _request()
    pipeline = CognitivePipelineV0(
        cognitive_turn_runner=CognitiveTurnRunner(
            id_factory=lambda: "turn-trace-1",
            clock=iter((0.0, 0.001, 0.003)).__next__,
        ),
        trace_id_factory=lambda: "pipeline-trace-1",
        clock=iter((0.0, 0.001, 0.003, 0.006)).__next__,
    )

    trace = pipeline.run(request)

    assert trace.interpretation.perception_event_id == trace.perception.perception_event_id
    assert trace.cognitive_turn.input_snapshot.interpretation is trace.interpretation
    assert trace.cognitive_turn.versions.interpretation_versions is trace.interpretation.versions
    assert trace.versions.perception is trace.perception.versions
    assert trace.versions.interpretation is trace.interpretation.versions
    assert trace.versions.cognitive_turn is trace.cognitive_turn.versions
    assert trace.timings.total_ms == 6.0
    assert type(trace).model_validate_json(trace.model_dump_json()) == trace


@pytest.mark.parametrize("case_id", (
    "action-question-missing-recipient",
    "explicit-correction-tuesday",
    "decline-no-thanks",
    "short-acknowledgement",
    "confirmation-with-acknowledgement-request",
    "quoted-hypothetical-warning",
    "indirect-window-request-with-attachment",
    "runtime-capability-memory-conflict",
    "unknown-out-of-scope-command",
    "consequential-delete-first-request",
    "topic-shift-new-request",
))
def test_required_real_cases_cross_the_complete_pipeline(case_id: str) -> None:
    trace = CognitivePipelineV0(
        cognitive_turn_runner=CognitiveTurnRunner(
            id_factory=lambda: f"turn-trace:{case_id}",
            clock=iter((0.0, 0.001, 0.003)).__next__,
        ),
        trace_id_factory=lambda: f"pipeline-trace:{case_id}",
        clock=iter((0.0, 0.001, 0.003, 0.006)).__next__,
    ).run(_request(case_id))

    assert trace.status == "completed"
    assert trace.interpretation.perception_event_id == trace.perception.perception_event_id
    assert trace.cognitive_turn.input_snapshot.interpretation is trace.interpretation


def test_runtime_views_cannot_diverge() -> None:
    runtime = _request("indirect-window-request-with-attachment").runtime

    perception = runtime.to_perception_runtime()
    interpretation = runtime.to_interpretation_runtime()
    appraisal = runtime.to_appraisal_runtime()

    assert perception.snapshot_id == interpretation.snapshot_id == runtime.snapshot_id
    assert perception.attachments == interpretation.attachments == runtime.attachments
    assert perception.available_capabilities == interpretation.available_capabilities
    assert appraisal.available_actions == runtime.available_capabilities
    assert appraisal.capability_snapshot.available_actions == runtime.available_capabilities
    assert dict(appraisal.last_tool_event) == (
        runtime.last_tool_event.model_dump(mode="python")
        if runtime.last_tool_event else {}
    )


def test_pipeline_rejects_forged_unauthorized_memory_before_engine_calls() -> None:
    request = _request("runtime-capability-memory-conflict")
    memory_type = type(request.memories[0])
    forged = memory_type.model_construct(
        memory_id="secret", memory_type="semantic", content="private content",
        provenance="private", status="current", authorized=False,
        retrieval_score=1.0, version=1,
    )
    forged_request = CognitivePipelineRequest.model_construct(
        **{**request.__dict__, "memories": (forged,)}
    )

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        CognitivePipelineV0().run(forged_request)

    assert captured.value.failed_stage == "validation"
    assert "private content" not in captured.value.safe_message


def test_contract_rejects_cross_conversation_state() -> None:
    request = _request()
    data = request.model_dump(mode="python")
    data["previous_state"]["conversation_ref"] = "foreign-conversation"

    with pytest.raises(ValidationError, match="different conversation"):
        CognitivePipelineRequest.model_validate(data)


class _ForgedPerceptionEvidenceEngine:
    def perceive(self, request):
        event = PerceptionEngine().perceive(request)
        activation_index = next(
            index for index, item in enumerate(event.cue_activations)
            if any(isinstance(evidence, TextSpanEvidence) for evidence in item.evidence)
        )
        activation = event.cue_activations[activation_index]
        original = next(
            evidence for evidence in activation.evidence
            if isinstance(evidence, TextSpanEvidence)
        )
        forged = original.model_copy(update={"quoted_text": "forged text"})
        forged_activation = activation.model_copy(update={"evidence": (forged,)})
        observation_ref = activation.observation_refs[0]
        observation_index = next(
            index for index, item in enumerate(event.observations)
            if item.observation_id == observation_ref
        )
        observation = event.observations[observation_index]
        forged_observation = observation.model_copy(update={"evidence": (forged,)})
        activations = list(event.cue_activations)
        observations = list(event.observations)
        activations[activation_index] = forged_activation
        observations[observation_index] = forged_observation
        return event.model_copy(update={
            "cue_activations": tuple(activations),
            "observations": tuple(observations),
        })


class _ForgedInterpretationEvidenceEngine:
    def interpret(self, request):
        envelope = InterpretationEngine().interpret(request)
        evidence = list(envelope.evidence)
        evidence[0] = evidence[0].model_copy(
            update={"source_ref": "foreign-perception:forged-cue"}
        )
        return envelope.model_copy(update={"evidence": tuple(evidence)})


class _MismatchedActivationEvidenceEngine:
    def perceive(self, request):
        event = PerceptionEngine().perceive(request)
        index = next(
            index for index, item in enumerate(event.cue_activations)
            if item.evidence
        )
        activation = event.cue_activations[index]
        message = request.messages[-1]
        different_but_valid = TextSpanEvidence(
            message_id=message.message_id,
            start=0,
            end=1,
            quoted_text=message.content[:1],
        )
        activations = list(event.cue_activations)
        activations[index] = activation.model_copy(
            update={"evidence": (different_but_valid,)}
        )
        return event.model_copy(update={"cue_activations": tuple(activations)})


class _ForeignPerceptionOutputEngine:
    def perceive(self, request):
        return PerceptionEngine().perceive(request).model_copy(
            update={"runtime_snapshot_id": "foreign-runtime"}
        )


class _ForeignInterpretationOutputEngine:
    def interpret(self, request):
        return InterpretationEngine().interpret(request).model_copy(
            update={"perception_event_id": "foreign-perception"}
        )


class _ForeignTurnOutputRunner:
    def __init__(self) -> None:
        self.delegate = _turn_runner("foreign-turn-trace")

    def evaluate(self, request):
        return self.delegate.evaluate(request).model_copy(
            update={"conversation_ref": "foreign-conversation"}
        )


def test_pipeline_rejects_perception_evidence_forged_against_request() -> None:
    with pytest.raises(CognitivePipelineExecutionError) as captured:
        CognitivePipelineV0(
            perception_engine=_ForgedPerceptionEvidenceEngine()
        ).run(_request())

    assert captured.value.failed_stage == "perception"
    assert "forged text" not in captured.value.safe_message


def test_pipeline_rejects_noncanonical_interpretation_evidence() -> None:
    with pytest.raises(CognitivePipelineExecutionError) as captured:
        CognitivePipelineV0(
            interpretation_engine=_ForgedInterpretationEvidenceEngine()
        ).run(_request())

    assert captured.value.failed_stage == "interpretation"
    assert "foreign-perception" not in captured.value.safe_message


def test_pipeline_rejects_activation_evidence_that_disagrees_with_observation() -> None:
    with pytest.raises(CognitivePipelineExecutionError) as captured:
        CognitivePipelineV0(
            perception_engine=_MismatchedActivationEvidenceEngine()
        ).run(_request())

    assert captured.value.failed_stage == "perception"


@pytest.mark.parametrize(("override_factory", "expected_stage"), (
    (lambda: {"perception_engine": _ForeignPerceptionOutputEngine()}, "perception"),
    (lambda: {"interpretation_engine": _ForeignInterpretationOutputEngine()}, "interpretation"),
    (lambda: {"cognitive_turn_runner": _ForeignTurnOutputRunner()}, "cognitive_turn"),
))
def test_foreign_stage_outputs_are_rejected(override_factory, expected_stage) -> None:
    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(**override_factory()).run(_request())

    assert captured.value.failed_stage == expected_stage


class _Recorder:
    def __init__(self, delegate=None, failure: Exception | None = None) -> None:
        self.delegate = delegate
        self.failure = failure
        self.calls = 0
        self.last_request = None

    def perceive(self, request):
        self.calls += 1
        self.last_request = request
        if self.failure is not None:
            raise self.failure
        return self.delegate.perceive(request)

    def interpret(self, request):
        self.calls += 1
        self.last_request = request
        if self.failure is not None:
            raise self.failure
        return self.delegate.interpret(request)

    def evaluate(self, request):
        self.calls += 1
        self.last_request = request
        if self.failure is not None:
            raise self.failure
        return self.delegate.evaluate(request)


def _turn_runner(trace_id: str = "turn-trace-fixed") -> CognitiveTurnRunner:
    return CognitiveTurnRunner(
        id_factory=lambda: trace_id,
        clock=iter((0.0, 0.001, 0.003)).__next__,
    )


def _pipeline(**overrides) -> CognitivePipelineV0:
    values = {
        "cognitive_turn_runner": _turn_runner(),
        "trace_id_factory": lambda: "pipeline-trace-fixed",
        "clock": iter((0.0, 0.001, 0.003, 0.006)).__next__,
    }
    values.update(overrides)
    return CognitivePipelineV0(**values)


def test_perception_failure_stops_all_later_engines() -> None:
    perception = _Recorder(failure=RuntimeError("raw private perception error"))
    interpretation = _Recorder(delegate=InterpretationEngine())
    turn = _Recorder(delegate=_turn_runner())

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(
            perception_engine=perception,
            interpretation_engine=interpretation,
            cognitive_turn_runner=turn,
        ).run(_request())

    assert captured.value.failed_stage == "perception"
    assert captured.value.__cause__ is perception.failure
    assert (perception.calls, interpretation.calls, turn.calls) == (1, 0, 0)
    assert "private" not in captured.value.safe_message


def test_handoff_failure_stops_later_engines(monkeypatch) -> None:
    request = _request()
    original = PerceptionEvent.interpretation_handoff

    def forged_handoff(self):
        return original(self).model_copy(update={"runtime_snapshot_id": "foreign-runtime"})

    monkeypatch.setattr(
        "app.ai.Cognitive.system.perception_v0.models.PerceptionEvent.interpretation_handoff",
        forged_handoff,
    )
    interpretation = _Recorder(delegate=InterpretationEngine())
    turn = _Recorder(delegate=_turn_runner())

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(
            interpretation_engine=interpretation,
            cognitive_turn_runner=turn,
        ).run(request)

    assert captured.value.failed_stage == "handoff_validation"
    assert (interpretation.calls, turn.calls) == (0, 0)


def test_interpretation_nested_failure_is_preserved_and_stops_turn() -> None:
    nested = InterpretationExecutionError(
        cognitive_turn_id="fixture:factual-information-question",
        failed_phase="semantic_grounding",
        safe_message="Interpretation failed safely.",
    )
    interpretation = _Recorder(failure=nested)
    turn = _Recorder(delegate=_turn_runner())

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(
            interpretation_engine=interpretation,
            cognitive_turn_runner=turn,
        ).run(_request())

    assert captured.value.failed_stage == "interpretation"
    assert captured.value.nested_stage == "semantic_grounding"
    assert captured.value.__cause__ is nested
    assert turn.calls == 0


def test_turn_request_failure_stops_turn(monkeypatch) -> None:
    turn = _Recorder(delegate=_turn_runner())

    def reject_turn_request(*args, **kwargs):
        raise ValueError("injected turn request failure")

    monkeypatch.setattr(
        "app.ai.Cognitive.system.cognitive_pipeline_v0.runner.CognitiveTurnRequest",
        reject_turn_request,
    )
    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(cognitive_turn_runner=turn).run(_request())

    assert captured.value.failed_stage == "turn_request"
    assert turn.calls == 0


def test_cognitive_turn_nested_failure_is_preserved() -> None:
    nested = CognitiveTurnExecutionError(
        cognitive_turn_id="fixture:factual-information-question",
        failed_stage="dynamic_state",
        safe_message="Dynamic state failed safely.",
    )
    turn = _Recorder(failure=nested)

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(cognitive_turn_runner=turn).run(_request())

    assert captured.value.failed_stage == "cognitive_turn"
    assert captured.value.nested_stage == "dynamic_state"
    assert captured.value.__cause__ is nested


def test_trace_validation_failure_is_typed() -> None:
    def fail_trace_id() -> str:
        raise RuntimeError("private trace id failure")

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(trace_id_factory=fail_trace_id).run(_request())

    assert captured.value.failed_stage == "trace_validation"
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert "private" not in captured.value.safe_message


def test_validation_failure_calls_no_engine_and_preserves_input() -> None:
    request = _request("runtime-capability-memory-conflict")
    memory = request.memories[0].model_copy(update={"authorized": False})
    forged = CognitivePipelineRequest.model_construct(
        **{**request.__dict__, "memories": (memory,)}
    )
    before = forged.model_dump_json()
    perception = _Recorder(delegate=PerceptionEngine())
    interpretation = _Recorder(delegate=InterpretationEngine())
    turn = _Recorder(delegate=_turn_runner())

    with pytest.raises(CognitivePipelineExecutionError) as captured:
        _pipeline(
            perception_engine=perception,
            interpretation_engine=interpretation,
            cognitive_turn_runner=turn,
        ).run(forged)

    assert captured.value.failed_stage == "validation"
    assert (perception.calls, interpretation.calls, turn.calls) == (0, 0, 0)
    assert forged.model_dump_json() == before


def test_complete_trace_is_deterministic_with_fixed_operational_sources() -> None:
    first = _pipeline().run(_request())
    second = _pipeline().run(_request())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_success_does_not_mutate_pipeline_input() -> None:
    request = _request("runtime-capability-memory-conflict")
    before = request.model_dump_json()

    _pipeline().run(request)

    assert request.model_dump_json() == before


def test_relationship_is_projected_consistently_to_both_stages() -> None:
    base = _request()
    relationship = CognitivePipelineRelationshipContext(
        relationship_ref="relationship-1",
        status="established",
        provenance="relationship-store-v1",
        evidence="prior interactions",
        records=("record-1",),
    )
    request = CognitivePipelineRequest.model_validate({
        **base.model_dump(mode="python"),
        "relationship": relationship,
    })
    interpretation = _Recorder(delegate=InterpretationEngine())
    turn = _Recorder(delegate=_turn_runner())

    trace = _pipeline(
        interpretation_engine=interpretation,
        cognitive_turn_runner=turn,
    ).run(request)

    assert interpretation.last_request.relationship.relationship_ref == "relationship-1"
    assert interpretation.last_request.relationship.status == "established"
    assert turn.last_request.relationship.relationship_snapshot_id == "relationship-1"
    assert turn.last_request.relationship.status == "established"
    assert all(
        frame.subject.relationship_snapshot_ref == "relationship-1"
        for frame in trace.cognitive_turn.appraisal.frames
    )


def test_topic_shift_attachment_is_grounded_through_complete_pipeline() -> None:
    trace = _pipeline().run(_request("topic-shift-new-request"))
    frames = trace.interpretation.semantic_frames

    attachment_frame = next(item for item in frames if item.predicate == "summarize_attachment")
    assert dict(attachment_frame.arguments)["attachment_id"] == "att-2"
    assert trace.perception.runtime_snapshot_id == trace.interpretation.runtime_snapshot_id


def test_thirty_message_truncated_context_executes() -> None:
    base = _request()
    messages = tuple(
        PerceptionMessage(
            message_id=f"context-{index}",
            role="assistant" if index % 2 == 0 else "user",
            content=f"Context message {index}.",
        )
        for index in range(29)
    ) + (PerceptionMessage(
        message_id="focus-30",
        role="user",
        content="When does the museum close?",
    ),)
    request = CognitivePipelineRequest.model_validate({
        **base.model_dump(mode="python"),
        "messages": messages,
        "focus_message_id": "focus-30",
        "context_truncated": True,
    })

    trace = _pipeline().run(request)

    assert len(trace.input_message_ids) == 30
    assert trace.perception.context_truncated is True
    assert trace.interpretation.context_truncated is True
