"""Canonical deterministic Perception V0 engine."""

from __future__ import annotations

from .detectors import DETECTORS, DETECTOR_BUNDLE_VERSION
from .evidence import EVIDENCE_VALIDATOR_VERSION, stable_id, validate_evidence
from .manifest import MANIFEST_VERSION, get_manifest, validate_manifest
from .models import (
    CueActivation,
    CueLifecycleStatus,
    CueState,
    CueValueType,
    DetectorSource,
    DetectorType,
    EvidenceRequirement,
    MessageSequenceEvidence,
    PerceptionEvent,
    PerceptionObservation,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    PerceptionVersions,
    PerceptionWorkingContext,
    RuntimeFactEvidence,
    TextSpanEvidence,
    WholeMessageEvidence,
)

SCHEMA_VERSION = "perception-event-v0.1"
UNCERTAINTY_POLICY_VERSION = "perception-coverage-uncertainty-v0.1"
ATTENTION_POLICY_VERSION = "none-v0"


def _observation_value_type(value: object, fallback: CueValueType) -> CueValueType:
    if type(value) is bool:
        return CueValueType.BOOLEAN
    if type(value) is int:
        return CueValueType.INTEGER
    if isinstance(value, str) or isinstance(value, tuple):
        return CueValueType.CATEGORY
    return fallback


class PerceptionEngine:
    """Run all manifest detectors and validate one immutable event."""

    def __init__(self, manifest_version: str = MANIFEST_VERSION) -> None:
        self.manifest_version = manifest_version
        self.manifest = validate_manifest(get_manifest(manifest_version), DETECTORS)

    def perceive(self, request: PerceptionRequest) -> PerceptionEvent:
        if request.cue_manifest_version != self.manifest_version:
            raise ValueError(
                f"request manifest {request.cue_manifest_version!r} does not match "
                f"engine manifest {self.manifest_version!r}"
            )

        observations: list[PerceptionObservation] = []
        activations: list[CueActivation] = []
        for definition in self.manifest:
            detector = DETECTORS[definition.detector_reference]
            detection = detector(request)
            for evidence in detection.evidence:
                validate_evidence(evidence, request)
            if detection.state == CueState.PRESENT:
                if definition.evidence_requirement == EvidenceRequirement.TEXT_SPAN:
                    sufficient = any(isinstance(item, TextSpanEvidence) for item in detection.evidence)
                elif definition.evidence_requirement == EvidenceRequirement.MESSAGE_SEQUENCE:
                    sufficient = any(isinstance(item, MessageSequenceEvidence) for item in detection.evidence)
                elif definition.evidence_requirement == EvidenceRequirement.RUNTIME_FACT:
                    sufficient = any(isinstance(item, RuntimeFactEvidence) for item in detection.evidence)
                else:
                    sufficient = any(
                        isinstance(item, (TextSpanEvidence, WholeMessageEvidence, MessageSequenceEvidence))
                        for item in detection.evidence
                    )
                if not sufficient:
                    raise ValueError(
                        f"detector evidence conflicts with manifest requirement: {definition.cue_key}"
                    )
            observation_id = stable_id(
                "observation",
                request.cognitive_turn_id,
                definition.cue_key,
                detection.observation_key,
                detection.observation_value,
                tuple(item.model_dump(mode="json") for item in detection.evidence),
                definition.detector_reference,
            )
            observation = PerceptionObservation(
                observation_id=observation_id,
                observation_key=detection.observation_key,
                value_type=_observation_value_type(detection.observation_value, definition.value_type),
                value=detection.observation_value,
                evidence=detection.evidence,
                source=definition.detector_reference,
                source_version=definition.detector_reference,
            )
            observations.append(observation)
            activations.append(
                CueActivation(
                    cue_key=definition.cue_key,
                    definition_version=definition.definition_version,
                    value_type=definition.value_type,
                    state=detection.state,
                    value=detection.value,
                    evidence=detection.evidence,
                    observation_refs=(observation_id,),
                    missing_information=detection.missing_information,
                    detector_source=(
                        DetectorSource.RUNTIME
                        if definition.detector_type == DetectorType.RUNTIME
                        else DetectorSource.DETERMINISTIC
                    ),
                    detector_reference=definition.detector_reference,
                    lifecycle_status=definition.lifecycle_status,
                )
            )

        missing = [
            f"{activation.cue_key}: {reason}"
            for activation in activations
            for reason in activation.missing_information
        ]
        if request.context_truncated:
            observation_id = stable_id(
                "observation", request.cognitive_turn_id, "input.context_truncated", True
            )
            observations.append(
                PerceptionObservation(
                    observation_id=observation_id,
                    observation_key="input.context_truncated",
                    value_type=CueValueType.BOOLEAN,
                    value=True,
                    evidence=(),
                    source="perception-request-adapter-v0.1",
                    source_version="perception-request-adapter-v0.1",
                )
            )
            missing.append("input context was truncated to the newest 30 messages ending at focus")

        applicable = [
            activation
            for activation in activations
            if activation.lifecycle_status == CueLifecycleStatus.V0_ACTIVE
            and activation.state != CueState.NOT_APPLICABLE
        ]
        if not applicable:
            uncertainty = 1.0
            missing.append("no V0-active cue was applicable")
        else:
            unknown_count = sum(item.state == CueState.UNKNOWN for item in applicable)
            uncertainty = round(unknown_count / len(applicable), 6)

        versions = PerceptionVersions(
            schema=SCHEMA_VERSION,
            cue_manifest=self.manifest_version,
            deterministic_detectors=DETECTOR_BUNDLE_VERSION,
            evidence_validator=EVIDENCE_VALIDATOR_VERSION,
            uncertainty_policy=UNCERTAINTY_POLICY_VERSION,
            attention_policy=ATTENTION_POLICY_VERSION,
        )
        event_id = stable_id(
            "perception",
            request.model_dump(mode="json"),
            versions.model_dump(mode="json"),
        )
        return PerceptionEvent(
            perception_event_id=event_id,
            cognitive_turn_id=request.cognitive_turn_id,
            input_message_ids=tuple(message.message_id for message in request.messages),
            focus_message_id=request.focus_message_id,
            runtime_snapshot_id=request.runtime.snapshot_id,
            observations=tuple(observations),
            cue_activations=tuple(activations),
            overall_uncertainty=uncertainty,
            missing_information=tuple(missing),
            context_truncated=request.context_truncated,
            versions=versions,
        )


def perceive(request: PerceptionRequest) -> PerceptionEvent:
    return PerceptionEngine(request.cue_manifest_version).perceive(request)


def build_bounded_request(
    *,
    cognitive_turn_id: str,
    messages: tuple[PerceptionMessage, ...],
    focus_message_id: str,
    runtime: PerceptionRuntimeContext,
    working_context: PerceptionWorkingContext | None = None,
    language_tag: str | None = "en",
    cue_manifest_version: str = MANIFEST_VERSION,
) -> PerceptionRequest:
    """Bound an ordered caller history to the newest 30 messages ending at focus."""

    matching = [index for index, message in enumerate(messages) if message.message_id == focus_message_id]
    if len(matching) != 1:
        raise ValueError("focus_message_id must resolve exactly once before bounding")
    focus_index = matching[0]
    if focus_index != len(messages) - 1:
        raise ValueError("future messages after focus are rejected")
    bounded = messages[max(0, len(messages) - 30):]
    return PerceptionRequest(
        cognitive_turn_id=cognitive_turn_id,
        messages=bounded,
        focus_message_id=focus_message_id,
        runtime=runtime,
        working_context=working_context,
        language_tag=language_tag,
        cue_manifest_version=cue_manifest_version,
        context_truncated=len(bounded) != len(messages),
    )
