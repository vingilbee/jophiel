"""Deterministic compilation from capture + lineage to the canonical request."""

from __future__ import annotations

import uuid

import pytest

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineRequest
from app.ai.Cognitive.system.v0.contracts.safety import SafetyDecisionProvenance, SafetyDisposition
from app.ai.Cognitive.system.v0.live_shadow.compiler import (
    CompileError, CompileSkip, PredecessorRecord, bootstrap_turn_id_for, capability_snapshot_id_for,
    compile_request, resolve_lineage, runtime_snapshot_id_for,
)
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import (
    CapturedAttachment, SegmentResetReason, SkipReason, TerminalStatus, segment_id_for,
)
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, observe
from cognitive_shadow_support import baseline_projection, make_capture


def _observe(capture, predecessor=None):
    return observe(capture, predecessor, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-09-26T00:00:00+00:00",
    ))


def _as_predecessor(record, capture) -> PredecessorRecord:
    return PredecessorRecord(
        terminal_id=record.terminal_id, turn_id=record.turn_id, user_ref=record.user_ref,
        conversation_ref=record.conversation_ref, focus_message_id=capture.focus_message_id,
        segment_id=record.segment_id, status=record.status, pipeline_trace=record.pipeline_trace,
    )


def test_identity_and_runtime_snapshot_lineage_are_exact() -> None:
    capture = make_capture(contents=(("user", "hi"), ("assistant", "Hello!"), ("user", "Protein in eggs?")))
    request = compile_request(capture, resolve_lineage(capture, None))
    assert isinstance(request, CognitivePipelineRequest)
    assert request.cognitive_turn_id == capture.turn_id
    assert request.conversation_ref == capture.conversation_ref
    assert request.focus_message_id == capture.focus_message_id == request.messages[-1].message_id
    assert [(m.message_id, m.role.value, m.content) for m in request.messages] == [
        (m.message_id, m.role, m.content) for m in capture.messages
    ]
    assert request.runtime.snapshot_id == runtime_snapshot_id_for(capture.turn_id)
    assert request.runtime.capability_snapshot_id == capability_snapshot_id_for(
        capture.turn_id, capture.capabilities.mapping_version
    )
    assert request.runtime.available_capabilities == capture.capabilities.capability_keys
    assert request.runtime.response_obligation_ref == capture.response_obligation.evidence_ref
    assert request.language_tag == "en" and request.context_truncated is False
    assert CognitivePipelineRequest.model_validate(request.model_dump(mode="json")) == request


def test_unsupported_configured_language_is_skipped_not_processed() -> None:
    capture = make_capture(language="id")
    assert compile_request(capture, resolve_lineage(capture, None)) == CompileSkip(SkipReason.UNSUPPORTED_LANGUAGE)
    record = _observe(capture)
    assert record.status == TerminalStatus.SKIPPED and record.skip_reason == SkipReason.UNSUPPORTED_LANGUAGE
    assert record.compiled_request is None


def test_missing_personality_skips_rather_than_inventing_neutrality() -> None:
    capture = make_capture(personality=None)
    assert compile_request(capture, resolve_lineage(capture, None)) == CompileSkip(
        SkipReason.MISSING_PERSONALITY_PROJECTION
    )
    record = _observe(capture)
    assert record.status == TerminalStatus.SKIPPED
    assert record.skip_reason == SkipReason.MISSING_PERSONALITY_PROJECTION
    assert record.versions.personality_projection is None


def test_reviewed_baseline_retains_provenance() -> None:
    capture = make_capture()
    request = compile_request(capture, resolve_lineage(capture, None))
    baseline = capture.personality
    assert request.interpretation_biases == baseline.interpretation_biases
    assert request.state_personality == baseline.state_personality
    assert request.action_personality == baseline.action_personality
    assert request.core_memory_ref == capture.core_memory.core_memory_ref
    record = _observe(capture)
    assert record.versions.personality_projection == "reviewed_baseline:test-baseline:test-baseline-v1"


def test_unavailable_safety_is_typed_fail_closed_never_allow() -> None:
    capture = make_capture()
    request = compile_request(capture, resolve_lineage(capture, None))
    decision = request.runtime.safety_decision
    assert decision.disposition == SafetyDisposition.UNAVAILABLE
    assert decision.provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE
    assert decision.runtime_snapshot_ref == request.runtime.snapshot_id
    assert (decision.schema_version, decision.policy_version, decision.producer_ref) == (
        capture.safety_decision.schema_version, capture.safety_decision.policy_version,
        capture.safety_decision.producer_ref,
    )
    trace = _observe(capture).pipeline_trace
    safety = trace.action_selection.safety
    assert safety.disposition == SafetyDisposition.UNAVAILABLE
    assert safety.restriction.value == "commitments"
    assert all(gate.safety == safety for step in trace.action_selection.steps for gate in step.gate_results)


def test_optional_inputs_remain_explicit_omissions() -> None:
    capture = make_capture()
    request = compile_request(capture, resolve_lineage(capture, None))
    assert request.memories == () and request.relationship is None
    assert request.runtime.task_contract is None and request.runtime.last_tool_event is None
    assert request.runtime.authorization is None and request.runtime.consent is None
    assert request.runtime.external_export_control is None and request.perception_working_context is None
    for name in (
        "action_grounded_objects", "action_target_descriptors", "prior_action_records",
        "prior_realization_records", "action_observed_outcomes", "outcomes", "grounded_objects",
        "new_goal_evidence", "values", "supplemental_inputs",
    ):
        assert getattr(request, name) == (), name
    assert {item.value for item in capture.omitted_inputs} >= {
        "memory_projection", "relationship_projection", "task_contract", "last_tool_event",
        "application_safety_policy", "action_grounded_objects", "action_target_descriptors",
        "prior_action_records", "prior_realization_records", "action_observed_outcomes",
    }


def test_attachments_come_only_from_captured_server_facts() -> None:
    capture = make_capture(attachments=(CapturedAttachment(attachment_id="a1b2c3", store="image_store"),))
    request = compile_request(capture, resolve_lineage(capture, None))
    (fact,) = request.runtime.attachments
    assert fact.attachment_id == "image_store:a1b2c3"
    assert fact.trusted_runtime_provenance == "server_store_record:image_store"
    assert fact.filename is None
    unavailable = make_capture(attachments_available=False)
    assert compile_request(unavailable, resolve_lineage(unavailable, None)).runtime.attachments_available is False


def test_first_turn_initializes_state_deterministically() -> None:
    capture = make_capture()
    lineage = resolve_lineage(capture, None)
    assert lineage.reset_reason == SegmentResetReason.INITIAL_CONVERSATION
    assert lineage.segment_id == segment_id_for(capture.turn_id)
    request = compile_request(capture, lineage)
    assert request.previous_goal_snapshot is None
    state = request.previous_state
    assert state.cognitive_turn_id == bootstrap_turn_id_for(capture.turn_id) != capture.turn_id
    assert state.revision == 1 and state.conversation_ref == capture.conversation_ref
    assert state == compile_request(capture, lineage).previous_state


def test_continuous_turn_hands_over_exact_goal_and_state() -> None:
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    first = make_capture(user_ref=user, conversation_ref=conversation, contents=(("user", "hi there"),))
    first_record = _observe(first)
    assert first_record.status == TerminalStatus.COMPLETED
    second = make_capture(
        user_ref=user, conversation_ref=conversation,
        contents=(("user", "hi there"), ("assistant", "Hello!"), ("user", "How much protein is in tofu?")),
        message_ids=(first.focus_message_id, str(uuid.uuid4()), str(uuid.uuid4())),
        previous_user_message_id=first.focus_message_id,
    )
    lineage = resolve_lineage(second, _as_predecessor(first_record, first))
    assert lineage.continuous and lineage.predecessor_trace_id == first_record.terminal_id
    assert lineage.segment_id == first_record.segment_id
    request = compile_request(second, lineage)
    trace = first_record.pipeline_trace
    assert request.previous_state == trace.cognitive_turn.dynamic_state_update.snapshot
    assert request.previous_goal_snapshot == trace.goal_snapshot
    second_record = _observe(second, _as_predecessor(first_record, first))
    assert second_record.status == TerminalStatus.COMPLETED
    assert second_record.segment_reset_reason is None
    assert second_record.predecessor_trace_id == first_record.terminal_id
    state = second_record.pipeline_trace.cognitive_turn.dynamic_state_update.snapshot
    assert state.revision == trace.cognitive_turn.dynamic_state_update.snapshot.revision + 1
    assert second_record.pipeline_trace.goal_snapshot.revision == trace.goal_snapshot.revision + 1


def _two_turns():
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    first = make_capture(user_ref=user, conversation_ref=conversation, contents=(("user", "hi there"),))
    first_record = _observe(first)
    second = make_capture(
        user_ref=user, conversation_ref=conversation,
        contents=(("user", "hi there"), ("assistant", "Hello!"), ("user", "How much protein is in tofu?")),
        message_ids=(first.focus_message_id, str(uuid.uuid4()), str(uuid.uuid4())),
        previous_user_message_id=first.focus_message_id,
    )
    return first, first_record, second


def test_continuous_turn_carries_the_trusted_goal_rollover() -> None:
    first, first_record, second = _two_turns()
    first_request = first_record.compiled_request
    # Nothing to close on an initial turn: the field is absent from the payload.
    assert first_request.goal_turn_rollover is None
    assert "goal_turn_rollover" not in first_request.model_dump(mode="json")
    request = compile_request(second, resolve_lineage(second, _as_predecessor(first_record, first)))
    rollover = request.goal_turn_rollover
    prior = first_record.pipeline_trace.goal_snapshot
    assert rollover is not None and rollover.proof_kind == "successor_response_turn"
    assert (rollover.closed_turn_ref, rollover.closed_goal_snapshot_ref) == (first.turn_id, prior.goal_snapshot_id)
    assert rollover.closing_turn_ref == second.turn_id
    assert rollover.closing_response_obligation_ref == second.response_obligation.evidence_ref
    assert CognitivePipelineRequest.model_validate(request.model_dump(mode="json")) == request
    # A forged rollover cannot enter the canonical request.
    payload = request.model_dump(mode="json")
    payload["goal_turn_rollover"]["closed_goal_snapshot_ref"] = "goal-snapshot-forged"
    with pytest.raises(ValueError, match="rollover does not match"):
        CognitivePipelineRequest.model_validate(payload)
    record = _observe(second, _as_predecessor(first_record, first))
    goals = record.pipeline_trace.goal_snapshot
    assert not [
        item for item in goals.records
        if item.scope == "turn" and item.scope_ref == first.turn_id and item.status == "active"
    ]


def test_historical_runner_predecessor_resets_lineage_but_still_loads() -> None:
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineTrace

    first, first_record, second = _two_turns()
    payload = first_record.pipeline_trace.model_dump(mode="json")
    payload["versions"]["pipeline_runner"] = "cognitive-pipeline-runner-v0.2"
    # A historical runner never resolved requirements or ran the
    # collaboration stages: its trace carries neither.
    with pytest.raises(ValueError, match="resolving-runner trace records requirement resolution"):
        CognitivePipelineTrace.model_validate(payload)
    payload.pop("requirement_resolution")
    payload["versions"].pop("requirement_resolution")
    payload["cognitive_turn"]["input_snapshot"].pop("requirement_handoff")
    with pytest.raises(ValueError, match="collaboration-runner trace records the collaboration stages"):
        CognitivePipelineTrace.model_validate(payload)
    payload.pop("collaboration")
    payload["versions"].pop("collaboration")
    payload["cognitive_turn"]["input_snapshot"].pop("collaboration_handoff")
    historical = CognitivePipelineTrace.model_validate(payload)  # still a valid audit record
    predecessor = _as_predecessor(first_record, first)
    lineage = resolve_lineage(second, PredecessorRecord(**{**predecessor.__dict__, "pipeline_trace": historical}))
    assert lineage.reset_reason == SegmentResetReason.LINEAGE_VERSION_CHANGE
    payload["versions"]["pipeline_runner"] = "cognitive-pipeline-runner-v9"
    with pytest.raises(ValueError, match="runner version mismatch"):
        CognitivePipelineTrace.model_validate(payload)


def test_predecessor_failure_or_unobserved_turn_begins_a_marked_segment() -> None:
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    first = make_capture(user_ref=user, conversation_ref=conversation, personality=None)
    skipped = _observe(first)
    second = make_capture(
        user_ref=user, conversation_ref=conversation, previous_user_message_id=first.focus_message_id,
    )
    lineage = resolve_lineage(second, _as_predecessor(skipped, first))
    assert lineage.reset_reason == SegmentResetReason.PREDECESSOR_GAP
    assert lineage.missing_predecessor_refs == (f"cognitive-shadow-trace:{skipped.terminal_id}",)
    assert lineage.segment_id == segment_id_for(second.turn_id)
    # No shadow record at all for an earlier user turn (unsampled/uncaptured).
    unobserved = str(uuid.uuid4())
    third = make_capture(previous_user_message_id=unobserved)
    gap = resolve_lineage(third, None)
    assert gap.reset_reason == SegmentResetReason.PREDECESSOR_GAP
    assert gap.missing_predecessor_refs == (f"chat-message:{unobserved}",)
    # A completed predecessor that is not the immediately preceding user turn.
    completed = _observe(make_capture(user_ref=user, conversation_ref=conversation))
    fourth = make_capture(user_ref=user, conversation_ref=conversation, previous_user_message_id=unobserved)
    predecessor = PredecessorRecord(
        terminal_id=completed.terminal_id, turn_id=completed.turn_id, user_ref=user,
        conversation_ref=conversation, focus_message_id=completed.focus_message_id,
        segment_id=completed.segment_id, status=TerminalStatus.COMPLETED, pipeline_trace=completed.pipeline_trace,
    )
    assert resolve_lineage(fourth, predecessor).reset_reason == SegmentResetReason.PREDECESSOR_GAP


def test_mapper_change_is_a_lineage_version_reset() -> None:
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    first = make_capture(user_ref=user, conversation_ref=conversation)
    record = _observe(first)
    second = make_capture(
        user_ref=user, conversation_ref=conversation, previous_user_message_id=first.focus_message_id,
        personality=baseline_projection(state_mapper="test-baseline-state-v2"),
    )
    lineage = resolve_lineage(second, _as_predecessor(record, first))
    assert lineage.reset_reason == SegmentResetReason.LINEAGE_VERSION_CHANGE
    assert compile_request(second, lineage).previous_state.personality_mapper_version == "test-baseline-state-v2"


@pytest.mark.parametrize("field", ("user_ref", "conversation_ref"))
def test_cross_user_or_conversation_predecessors_are_rejected(field: str) -> None:
    first = make_capture()
    record = _observe(first)
    second = make_capture(
        user_ref=first.user_ref if field == "conversation_ref" else str(uuid.uuid4()),
        conversation_ref=first.conversation_ref if field == "user_ref" else str(uuid.uuid4()),
        previous_user_message_id=first.focus_message_id,
    )
    with pytest.raises(CompileError) as caught:
        resolve_lineage(second, _as_predecessor(record, first))
    assert caught.value.code == "predecessor_ownership_mismatch"
    failed = _observe(second, _as_predecessor(record, first))
    assert failed.status == TerminalStatus.FAILED and failed.failure.stage == "compile"
    assert failed.failure.code == "predecessor_ownership_mismatch"


def test_same_turn_and_stale_predecessors_are_rejected() -> None:
    capture = make_capture()
    record = _observe(capture)
    with pytest.raises(CompileError, match="own shadow predecessor"):
        resolve_lineage(capture, _as_predecessor(record, capture))
    tampered = PredecessorRecord(
        terminal_id=record.terminal_id, turn_id=str(uuid.uuid4()), user_ref=record.user_ref,
        conversation_ref=record.conversation_ref, focus_message_id=capture.focus_message_id,
        segment_id=record.segment_id, status=TerminalStatus.COMPLETED, pipeline_trace=record.pipeline_trace,
    )
    follower = make_capture(
        user_ref=record.user_ref, conversation_ref=record.conversation_ref,
        previous_user_message_id=capture.focus_message_id,
    )
    with pytest.raises(CompileError) as caught:
        resolve_lineage(follower, tampered)
    assert caught.value.code == "predecessor_lineage_mismatch"


def test_unauthorized_or_foreign_inputs_never_compile() -> None:
    capture = make_capture()
    # The capture contract itself rejects unauthorized memories.
    with pytest.raises(ValueError, match="unauthorized memories"):
        make_capture(memories=({
            "memory_id": "m1", "memory_type": "fact", "content": "x", "provenance": "p", "status": "active",
            "authorized": False,
        },), omitted_inputs=tuple(sorted(i for i in capture.omitted_inputs if i.value != "memory_projection")))


def test_personality_must_share_the_captured_core_lineage() -> None:
    with pytest.raises(ValueError, match="core-memory lineage"):
        make_capture(personality=baseline_projection(core_ref="core_memory:other:v9"))
    with pytest.raises(ValueError, match="core-memory lineage"):
        make_capture(core_memory=None)
