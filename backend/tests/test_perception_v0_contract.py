"""Contract and manifest tests for the isolated Perception V0 runtime."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.perception_v0 import (
    CUE_MANIFEST,
    AttachmentFact,
    CueActivation,
    CueLifecycleStatus,
    CueState,
    CueValueType,
    PerceptionEvent,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    RuntimeFactEvidence,
    TextSpanEvidence,
    build_bounded_request,
    perceive,
    validate_manifest,
)
from app.ai.Cognitive.system.perception_v0.detectors import DETECTORS
from app.ai.Cognitive.system.perception_v0.evidence import validate_evidence


def _request(content: str = "Please send the report.") -> PerceptionRequest:
    return PerceptionRequest(
        cognitive_turn_id="turn-contract",
        messages=(PerceptionMessage(message_id="m1", role="user", content=content),),
        focus_message_id="m1",
        runtime=PerceptionRuntimeContext(snapshot_id="runtime-contract"),
    )


def _activation(**changes: object) -> CueActivation:
    values: dict[str, object] = {
        "cue_key": "conversation.question_form",
        "definition_version": 1,
        "value_type": "boolean",
        "state": "absent",
        "value": False,
        "detector_source": "deterministic",
        "detector_reference": "question-form-rules-v0.1",
        "lifecycle_status": "v0_active",
    }
    values.update(changes)
    return CueActivation(**values)


def test_manifest_has_exactly_thirteen_ordered_and_implemented_cues() -> None:
    validated = validate_manifest(CUE_MANIFEST, DETECTORS)

    assert len(validated) == 13
    assert len({item.cue_key for item in validated}) == 13
    assert sum(item.lifecycle_status == CueLifecycleStatus.V0_ACTIVE for item in validated) == 9
    assert sum(item.lifecycle_status == CueLifecycleStatus.SHADOW for item in validated) == 4
    assert tuple(item.cue_key for item in validated) == tuple(item.cue_key for item in CUE_MANIFEST)


def test_manifest_rejects_duplicate_unknown_relationship_and_detector() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_manifest((*CUE_MANIFEST, CUE_MANIFEST[0]), DETECTORS)

    invalid_parent = CUE_MANIFEST[0].model_copy(update={"parent_cue_key": "missing.cue"})
    with pytest.raises(ValueError, match="parent"):
        validate_manifest((invalid_parent, *CUE_MANIFEST[1:]), DETECTORS)

    invalid_conflict = CUE_MANIFEST[0].model_copy(update={"conflicts_with": ("missing.cue",)})
    with pytest.raises(ValueError, match="conflict"):
        validate_manifest((invalid_conflict, *CUE_MANIFEST[1:]), DETECTORS)

    invalid_detector = CUE_MANIFEST[0].model_copy(update={"detector_reference": "missing-v1"})
    with pytest.raises(ValueError, match="not implemented"):
        validate_manifest((invalid_detector, *CUE_MANIFEST[1:]), DETECTORS)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "present", "value": False, "evidence": (TextSpanEvidence(message_id="m1", start=0, end=1, quoted_text="x"),)},
        {"state": "unknown", "value": False, "missing_information": ("missing",)},
        {"state": "unknown", "value": None},
        {"state": "present", "value": True},
        {"raw_score": 0.9},
        {"salience": 0.5},
        {"confidence": 0.8},
    ],
)
def test_activation_state_and_unapproved_numeric_fields_fail(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _activation(**changes)


def test_integer_unknown_is_distinct_from_zero() -> None:
    absent = _activation(
        cue_key="conversation.repetition",
        value_type=CueValueType.INTEGER,
        state=CueState.ABSENT,
        value=0,
        detector_reference="repetition-normalization-v0.1",
    )
    unknown = _activation(
        cue_key="conversation.repetition",
        value_type=CueValueType.INTEGER,
        state=CueState.UNKNOWN,
        value=None,
        missing_information=("paraphrase cannot be resolved",),
        detector_reference="repetition-normalization-v0.1",
    )

    assert absent.value == 0
    assert unknown.value is None
    assert absent.state != unknown.state


def test_text_and_runtime_evidence_validate_against_exact_request() -> None:
    request = PerceptionRequest(
        cognitive_turn_id="turn-evidence",
        messages=(PerceptionMessage(message_id="m1", role="user", content="I attached it."),),
        focus_message_id="m1",
        runtime=PerceptionRuntimeContext(
            snapshot_id="runtime-evidence",
            attachments=(AttachmentFact(
                attachment_id="att-1",
                filename="scan.pdf",
                trusted_runtime_provenance="upload-service",
                evidence_reference="upload-event-1",
            ),),
        ),
    )
    span = TextSpanEvidence(message_id="m1", start=0, end=1, quoted_text="I")
    runtime = RuntimeFactEvidence(
        runtime_snapshot_id="runtime-evidence",
        json_pointer="/attachments/0/attachment_id",
        value="att-1",
    )
    validate_evidence(span, request)
    validate_evidence(runtime, request)

    with pytest.raises(ValueError, match="quoted text"):
        validate_evidence(span.model_copy(update={"quoted_text": "x"}), request)
    with pytest.raises(ValueError, match="does not equal"):
        validate_evidence(runtime.model_copy(update={"value": "att-2"}), request)


def test_request_rejects_duplicate_future_and_oversized_message_windows() -> None:
    runtime = PerceptionRuntimeContext(snapshot_id="runtime-window")
    duplicate = (
        PerceptionMessage(message_id="m1", role="user", content="one"),
        PerceptionMessage(message_id="m1", role="assistant", content="two"),
    )
    with pytest.raises(ValidationError, match="unique"):
        PerceptionRequest(
            cognitive_turn_id="turn", messages=duplicate, focus_message_id="m1", runtime=runtime
        )
    future = (
        PerceptionMessage(message_id="m1", role="user", content="focus"),
        PerceptionMessage(message_id="m2", role="assistant", content="future"),
    )
    with pytest.raises(ValidationError, match="future"):
        PerceptionRequest(
            cognitive_turn_id="turn", messages=future, focus_message_id="m1", runtime=runtime
        )
    oversized = tuple(
        PerceptionMessage(message_id=f"m{index}", role="user", content=str(index))
        for index in range(31)
    )
    with pytest.raises(ValidationError, match="at most 30"):
        PerceptionRequest(
            cognitive_turn_id="turn", messages=oversized, focus_message_id="m30", runtime=runtime
        )


def test_bounded_adapter_keeps_newest_thirty_and_records_truncation() -> None:
    messages = tuple(
        PerceptionMessage(message_id=f"m{index}", role="user", content=str(index))
        for index in range(35)
    )
    request = build_bounded_request(
        cognitive_turn_id="turn-bounded",
        messages=messages,
        focus_message_id="m34",
        runtime=PerceptionRuntimeContext(snapshot_id="runtime-bounded"),
    )
    event = perceive(request)

    assert len(request.messages) == 30
    assert request.messages[0].message_id == "m5"
    assert event.context_truncated is True
    assert any(item.observation_key == "input.context_truncated" for item in event.observations)


def test_contracts_forbid_raw_personality_and_arbitrary_runtime_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PerceptionRuntimeContext(snapshot_id="s", personality={"agreeableness": 1.0})
    with pytest.raises(ValidationError, match="extra"):
        PerceptionRequest(
            cognitive_turn_id="t",
            messages=(PerceptionMessage(message_id="m", role="user", content="x"),),
            focus_message_id="m",
            runtime=PerceptionRuntimeContext(snapshot_id="s"),
            autobiographical_memories=("private",),
        )


def test_event_is_deeply_immutable_complete_and_json_round_trips() -> None:
    event = perceive(_request())
    payload = event.model_dump(mode="json")
    restored = PerceptionEvent.model_validate(deepcopy(payload))

    assert restored == event
    assert tuple(item.cue_key for item in event.cue_activations) == tuple(
        item.cue_key for item in CUE_MANIFEST
    )
    with pytest.raises(ValidationError):
        event.overall_uncertainty = 1.0
    with pytest.raises(TypeError):
        CUE_MANIFEST[0].value_constraints["new"] = True


def test_event_rejects_incomplete_duplicate_or_manifest_conflicting_activations() -> None:
    event = perceive(_request())
    payload = event.model_dump(mode="python")
    payload["cue_activations"] = event.cue_activations[:-1]
    with pytest.raises(ValidationError, match="complete"):
        PerceptionEvent.model_validate(payload)

    payload = event.model_dump(mode="python")
    changed = event.cue_activations[0].model_copy(update={"lifecycle_status": "shadow"})
    payload["cue_activations"] = (changed, *event.cue_activations[1:])
    with pytest.raises(ValidationError, match="manifest"):
        PerceptionEvent.model_validate(payload)
