"""Contracts, digests, and semantic normalization for Live-Shadow Trace V0."""

from __future__ import annotations

import copy
import json
import uuid

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.contracts.safety import SafetyDecisionProvenance, SafetyDisposition
from app.ai.Cognitive.system.v0.live_shadow.compiler import compile_request, resolve_lineage
from app.ai.Cognitive.system.v0.live_shadow.digests import (
    SEMANTIC_EXCLUDED_PATHS, canonical_json, payload_digest, semantic_digest, semantic_projection,
)
from app.ai.Cognitive.system.v0.live_shadow.models import (
    CAPTURE_SCHEMA_VERSION, COMPILER_VERSION, SEMANTIC_DIGEST_VERSION, TERMINAL_SCHEMA_VERSION,
    WORKER_VERSION, CapturedCapabilities, CapturedSafetyDecision, CognitiveShadowCapture,
    CognitiveShadowTerminalRecord, OmittedInput, SegmentResetReason, ShadowComponentVersions,
    ShadowFailure, SkipReason, TerminalStatus, segment_id_for, terminal_id_for,
)
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, observe
from cognitive_shadow_support import make_capture


def _completed_record():
    capture = make_capture()
    job_id = capture.capture_id
    return capture, observe(capture, None, ObservationContext(
        job_id=job_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-09-26T00:00:00+00:00",
    ))


def test_versions_are_explicit() -> None:
    # Capture v0.2 adds the explicit realization-content omission; every other
    # component, including the core semantic digest, is unchanged.
    assert (CAPTURE_SCHEMA_VERSION, TERMINAL_SCHEMA_VERSION, COMPILER_VERSION, SEMANTIC_DIGEST_VERSION, WORKER_VERSION) == (
        "cognitive-shadow-capture-v0.2", "cognitive-shadow-terminal-v0.1", "cognitive-shadow-compiler-v0.1",
        "cognitive-shadow-semantic-digest-v0.1", "cognitive-shadow-worker-v0.1",
    )
    assert make_capture().schema_version == CAPTURE_SCHEMA_VERSION


def test_a_prior_capture_schema_is_skipped_never_reinterpreted() -> None:
    from app.ai.Cognitive.system.v0.live_shadow import SkipReason, load_capture
    from app.ai.Cognitive.system.v0.live_shadow.models import OmittedInput

    payload = make_capture().model_dump(mode="json")
    assert OmittedInput.REALIZATION_CONTENT_INVENTORY.value in payload["omitted_inputs"]
    legacy = {**payload, "schema_version": "cognitive-shadow-capture-v0.1",
              "omitted_inputs": [item for item in payload["omitted_inputs"] if item != "realization_content_inventory"]}
    assert load_capture(legacy) == SkipReason.CAPTURE_SCHEMA_UNSUPPORTED
    with pytest.raises(ValidationError, match="must be recorded as omitted"):
        CognitiveShadowCapture.model_validate({**legacy, "schema_version": CAPTURE_SCHEMA_VERSION})


def test_capture_is_immutable_extra_forbid_and_round_trips_exactly() -> None:
    capture = make_capture(contents=(("user", "hi"), ("assistant", "Hello!"), ("user", "ünïcode ✓ protein?")))
    with pytest.raises(ValidationError):
        capture.context_truncated = True  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        CognitiveShadowCapture.model_validate({**capture.model_dump(mode="json"), "raw_audio": "..."})
    payload = capture.model_dump(mode="json")
    assert CognitiveShadowCapture.model_validate(payload) == capture
    assert CognitiveShadowCapture.model_validate_json(capture.model_dump_json()) == capture
    assert json.loads(canonical_json(payload)) == payload


def test_capture_carries_no_secret_or_prose_fields() -> None:
    fields = set(CognitiveShadowCapture.model_fields)
    for forbidden in ("access_token", "gmail_access_token", "cookie", "awareness", "long_term_memory", "audio"):
        assert not any(forbidden in name for name in fields)


def test_capture_identity_and_focus_rules() -> None:
    capture = make_capture(contents=(("user", "a"), ("user", "b")))
    payload = capture.model_dump(mode="json")
    with pytest.raises(ValidationError, match="capture_id must be derived"):
        CognitiveShadowCapture.model_validate({**payload, "capture_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError, match="final bounded message"):
        CognitiveShadowCapture.model_validate({**payload, "focus_message_id": payload["messages"][0]["message_id"]})
    with pytest.raises(ValidationError, match="ChatJob"):
        CognitiveShadowCapture.model_validate({**payload, "chat_job_ref": None})
    with pytest.raises(ValidationError, match="unavailable attachment"):
        CognitiveShadowCapture.model_validate({
            **payload, "attachments_available": False,
            "attachments": [{"attachment_id": "abc", "store": "image_store"}],
        })
    with pytest.raises(ValidationError, match="focus message must be a user"):
        make_capture(contents=(("user", "a"), ("assistant", "b")))


def test_capture_omissions_must_match_inputs() -> None:
    payload = make_capture().model_dump(mode="json")
    omitted = [item for item in payload["omitted_inputs"] if item != OmittedInput.MEMORY_PROJECTION.value]
    with pytest.raises(ValidationError, match="memory_projection"):
        CognitiveShadowCapture.model_validate({**payload, "omitted_inputs": omitted})
    with pytest.raises(ValidationError, match="without a V0 producer"):
        CognitiveShadowCapture.model_validate({
            **payload, "omitted_inputs": [i for i in payload["omitted_inputs"] if i != "action_grounded_objects"],
        })
    with pytest.raises(ValidationError, match="personality projection or an explicit missing reason"):
        CognitiveShadowCapture.model_validate({**payload, "personality_missing_reason": "reviewed_baseline_invalid"})


def test_capability_keys_must_come_from_the_registered_mapping() -> None:
    with pytest.raises(ValidationError, match="registered capability mapping"):
        CapturedCapabilities(mapping_version="cognitive-shadow-capability-map-v0.1", capability_keys=("send_email",))


def test_captured_safety_cannot_be_a_synthetic_allow() -> None:
    with pytest.raises(ValidationError, match="only the trusted application safety policy"):
        CapturedSafetyDecision(
            policy_version="application-safety-policy-v0.1", disposition=SafetyDisposition.ALLOW,
            provenance=SafetyDecisionProvenance.RUNTIME_UNAVAILABLE, producer_ref="shadow",
        )


def test_closed_vocabularies() -> None:
    assert {item.value for item in TerminalStatus} == {"completed", "failed", "skipped"}
    assert {item.value for item in SkipReason} == {
        "feature_disabled", "not_sampled", "unsupported_language", "missing_personality_projection",
        "missing_required_runtime_fact", "unauthorized_input", "predecessor_unavailable",
        "capture_schema_unsupported",
    }
    assert {item.value for item in SegmentResetReason} == {
        "initial_conversation", "predecessor_gap", "lineage_version_change", "lineage_unresolved",
    }
    with pytest.raises(ValidationError):
        ShadowFailure(stage="pipeline.made_up", code="x", safe_message="x", attempt_count=1, retryable=False)
    with pytest.raises(ValidationError):
        ShadowFailure(stage="compile", code="Raw Exception: boom", safe_message="x", attempt_count=1, retryable=False)


def test_completed_record_round_trips_and_digests_are_stable() -> None:
    _, record = _completed_record()
    assert record.status == TerminalStatus.COMPLETED
    payload = record.model_dump(mode="json")
    assert CognitiveShadowTerminalRecord.model_validate(payload) == record
    assert CognitiveShadowTerminalRecord.model_validate_json(record.model_dump_json()) == record
    trace_payload = record.pipeline_trace.model_dump(mode="json")
    assert payload_digest(trace_payload) == record.trace_payload_digest
    assert payload_digest(copy.deepcopy(trace_payload)) == payload_digest(trace_payload)
    assert payload_digest(json.loads(json.dumps(trace_payload))) == record.trace_payload_digest


def test_digests_are_mutation_sensitive() -> None:
    _, record = _completed_record()
    request = record.compiled_request.model_dump(mode="json")
    request["messages"][0]["content"] += "!"
    assert payload_digest(request) != record.compiled_request_digest
    trace = record.pipeline_trace.model_dump(mode="json")
    trace["action_selection"]["plan"]["termination"] = "no_viable_plan"
    assert semantic_digest(trace) != record.semantic_digest
    assert payload_digest(trace) != record.trace_payload_digest


def test_semantic_digest_excludes_only_reviewed_volatile_fields() -> None:
    _, record = _completed_record()
    trace = record.pipeline_trace.model_dump(mode="json")
    assert SEMANTIC_EXCLUDED_PATHS == (
        ("pipeline_trace_id",), ("timings",), ("cognitive_turn", "trace_id"), ("cognitive_turn", "timings"),
    )
    volatile = copy.deepcopy(trace)
    volatile["pipeline_trace_id"] = str(uuid.uuid4())
    volatile["timings"] = {key: value + 1 for key, value in volatile["timings"].items()}
    volatile["cognitive_turn"]["trace_id"] = "another-turn-trace"
    volatile["cognitive_turn"]["timings"] = {key: 0.0 for key in volatile["cognitive_turn"]["timings"]}
    assert semantic_digest(volatile) == record.semantic_digest
    assert payload_digest(volatile) != record.trace_payload_digest
    projected = semantic_projection(trace)
    assert "timings" not in projected and "trace_id" not in projected["cognitive_turn"]
    # Everything decision-relevant is still present.
    for key in ("perception", "interpretation", "goal_snapshot", "action_selection", "versions"):
        assert projected[key] == trace[key]
    for key in ("appraisal", "dynamic_state_update", "action_selection", "versions", "input_snapshot"):
        assert projected["cognitive_turn"][key] == trace["cognitive_turn"][key]
    with pytest.raises(ValueError, match="reviewed semantic shape"):
        semantic_projection({"pipeline_trace_id": "x"})


def test_terminal_status_payload_rules() -> None:
    capture, record = _completed_record()
    payload = record.model_dump(mode="json")
    with pytest.raises(ValidationError, match="no failure or skip"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "skip_reason": "unauthorized_input"})
    with pytest.raises(ValidationError, match="exact request and trace"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "pipeline_trace": None})
    with pytest.raises(ValidationError, match="only a completed record"):
        CognitiveShadowTerminalRecord.model_validate({
            **payload, "status": "failed",
            "failure": {"stage": "worker", "code": "x", "safe_message": "x", "attempt_count": 1, "retryable": False},
        })
    skipped = {
        **payload, "status": "skipped", "skip_reason": "unsupported_language", "pipeline_trace": None,
        "trace_payload_digest": None, "semantic_digest": None, "worker_timings": None,
        "versions": ShadowComponentVersions(capture_schema=CAPTURE_SCHEMA_VERSION).model_dump(mode="json"),
    }
    with pytest.raises(ValidationError, match="cannot claim a compilation"):
        CognitiveShadowTerminalRecord.model_validate(skipped)
    ok = CognitiveShadowTerminalRecord.model_validate({**skipped, "compiled_request": None, "compiled_request_digest": None})
    assert ok.status == TerminalStatus.SKIPPED
    with pytest.raises(ValidationError, match="metrics, never terminal rows"):
        CognitiveShadowTerminalRecord.model_validate({
            **skipped, "compiled_request": None, "compiled_request_digest": None, "skip_reason": "not_sampled",
        })


def test_invalid_ownership_and_lineage_combinations_fail() -> None:
    capture, record = _completed_record()
    payload = record.model_dump(mode="json")
    with pytest.raises(ValidationError, match="terminal_id must be derived"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "terminal_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError, match="different turn"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "turn_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError, match="compiled request ownership"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "focus_message_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError, match="must cite its predecessor"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "segment_reset_reason": None})
    with pytest.raises(ValidationError, match="reset segment is named"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "segment_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError, match="missing predecessor"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "missing_predecessor_refs": ["chat-message:x"]})
    versions = dict(payload["versions"])
    versions["pipeline"] = None
    with pytest.raises(ValidationError, match="canonical versions"):
        CognitiveShadowTerminalRecord.model_validate({**payload, "versions": versions})
    assert record.terminal_id == terminal_id_for(record.job_id)
    assert record.segment_id == segment_id_for(capture.turn_id)


def test_lineage_is_pure_and_first_turn_is_initial() -> None:
    capture = make_capture()
    lineage = resolve_lineage(capture, None)
    assert lineage.reset_reason == SegmentResetReason.INITIAL_CONVERSATION
    assert compile_request(capture, lineage) == compile_request(capture, lineage)
