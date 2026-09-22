"""Static, code-owned Perception V0 cue manifest."""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    CueCategory,
    CueDefinition,
    CueLifecycleStatus,
    CueValueType,
    DetectorType,
    EvidenceRequirement,
)

MANIFEST_VERSION = "cue-manifest-v1"


def _cue(
    key: str,
    name: str,
    description: str,
    category: CueCategory,
    value_type: CueValueType,
    lifecycle: CueLifecycleStatus,
    detector_type: DetectorType,
    detector_reference: str,
    evidence_requirement: EvidenceRequirement,
) -> CueDefinition:
    return CueDefinition(
        cue_key=key,
        definition_version=1,
        name=name,
        description=description,
        category=category,
        value_type=value_type,
        lifecycle_status=lifecycle,
        detector_type=detector_type,
        detector_reference=detector_reference,
        evidence_requirement=evidence_requirement,
        value_constraints={"nullable_states": ["unknown", "not_applicable"]},
        downstream_consumers=("interpretation_v0",)
        if lifecycle == CueLifecycleStatus.V0_ACTIVE
        else ("shadow_evaluation",),
    )


CUE_MANIFEST: tuple[CueDefinition, ...] = (
    _cue(
        "conversation.question_form", "Question form",
        "Explicit grammatical or punctuated question in the focus message.",
        CueCategory.CONVERSATION, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "question-form-rules-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "conversation.explicit_action_request", "Explicit action request",
        "Direct request for Columbina to perform an action, excluding factual questions.",
        CueCategory.CONVERSATION, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "action-request-rules-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "conversation.explicit_correction", "Explicit correction",
        "Explicit replacement, rejection, or repair of prior conversational material.",
        CueCategory.CONVERSATION, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "explicit-correction-rules-v0.1", EvidenceRequirement.TEXT_OR_SEQUENCE,
    ),
    _cue(
        "conversation.repetition", "Repetition",
        "Count of prior materially matching occurrences by the same speaker.",
        CueCategory.CONVERSATION, CueValueType.INTEGER, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "repetition-normalization-v0.1", EvidenceRequirement.MESSAGE_SEQUENCE,
    ),
    _cue(
        "linguistic.negation", "Negation", "Scoped negation marker in current-speaker text.",
        CueCategory.LINGUISTIC, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "english-negation-lexicon-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "linguistic.uncertainty_marker", "Uncertainty marker",
        "Explicit uncertainty wording owned by the current speaker.",
        CueCategory.LINGUISTIC, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "english-uncertainty-lexicon-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "linguistic.self_reference", "Self reference",
        "Count of first-person references owned by the current speaker.",
        CueCategory.LINGUISTIC, CueValueType.INTEGER, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.DETERMINISTIC, "english-self-reference-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "runtime.attachment_present", "Attachment present",
        "One or more attachments reported by trusted runtime context.",
        CueCategory.RUNTIME, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.RUNTIME, "trusted-attachment-runtime-v0.1", EvidenceRequirement.RUNTIME_FACT,
    ),
    _cue(
        "runtime.tool_failure", "Tool failure",
        "Recognized failure from a trusted, authorized tool invocation.",
        CueCategory.RUNTIME, CueValueType.BOOLEAN, CueLifecycleStatus.V0_ACTIVE,
        DetectorType.RUNTIME, "trusted-tool-event-runtime-v0.1", EvidenceRequirement.RUNTIME_FACT,
    ),
    _cue(
        "conversation.topic_shift", "Topic shift",
        "Focus subject differs materially from supplied recent or active topic context.",
        CueCategory.CONVERSATION, CueValueType.BOOLEAN, CueLifecycleStatus.SHADOW,
        DetectorType.DETERMINISTIC, "topic-overlap-shadow-v0.1", EvidenceRequirement.TEXT_OR_SEQUENCE,
    ),
    _cue(
        "linguistic.positive_wording", "Positive wording",
        "Positive lexical evaluation or affect marker, without inferred emotion.",
        CueCategory.LINGUISTIC, CueValueType.BOOLEAN, CueLifecycleStatus.SHADOW,
        DetectorType.DETERMINISTIC, "english-positive-wording-shadow-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "linguistic.negative_wording", "Negative wording",
        "Negative lexical evaluation or affect marker, without inferred emotion.",
        CueCategory.LINGUISTIC, CueValueType.BOOLEAN, CueLifecycleStatus.SHADOW,
        DetectorType.DETERMINISTIC, "english-negative-wording-shadow-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
    _cue(
        "task.urgency", "Urgency wording",
        "Explicit time pressure, deadline, or immediate-handling wording.",
        CueCategory.TASK, CueValueType.BOOLEAN, CueLifecycleStatus.SHADOW,
        DetectorType.DETERMINISTIC, "english-urgency-shadow-v0.1", EvidenceRequirement.TEXT_SPAN,
    ),
)


def validate_manifest(
    definitions: Iterable[CueDefinition],
    detector_references: Iterable[str] | None = None,
) -> tuple[CueDefinition, ...]:
    manifest = tuple(definitions)
    keys = tuple(item.cue_key for item in manifest)
    if len(keys) != len(set(keys)):
        raise ValueError("manifest contains duplicate cue keys")
    known = set(keys)
    for item in manifest:
        if item.parent_cue_key is not None and item.parent_cue_key not in known:
            raise ValueError(f"unknown parent cue: {item.parent_cue_key}")
        if any(key not in known for key in item.conflicts_with):
            raise ValueError(f"unknown cue conflict on {item.cue_key}")
        if item.cue_key in item.conflicts_with:
            raise ValueError(f"cue cannot conflict with itself: {item.cue_key}")
    if detector_references is not None:
        implemented = set(detector_references)
        missing = [item.detector_reference for item in manifest if item.detector_reference not in implemented]
        if missing:
            raise ValueError(f"manifest detector references are not implemented: {missing}")
    return manifest


def get_manifest(version: str = MANIFEST_VERSION) -> tuple[CueDefinition, ...]:
    if version != MANIFEST_VERSION:
        raise ValueError(f"unsupported cue manifest version: {version}")
    return CUE_MANIFEST
