"""Bounded, provider-neutral protocol for collaborative content proposals.

Separate from the semantic-parser and Realization protocols.  The payload
holds only what the operation needs: the operation and artifact type, the
exact current artifact revision (typed payload) or the delivered dialogue it
targets, the attributed sources the proposal may cite (user requirements,
the user's request, delivered dialogue items, artifact elements), the bounded
conversation as context, the closed vocabularies, and a coarse deliberation
hint.  No runtime, memory, relationship, goal, action, safety, or tool state.

The output is structured data, never final prose: typed patch operations,
content items with closed roles, classes, and claim kinds, and typed inquiry
candidates.  Every value is untrusted until the deterministic compiler
accepts it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from ..contracts.collaboration import (
    COLLABORATION_PROTOCOL_VERSION, ClaimKind, CollaborationFailureCode, CollaborationProviderProvenance,
    ContentItemRole, RESTRICTED_CLAIM_KINDS, RETRYABLE_COLLABORATION_FAILURES, ROLES_BY_OPERATION,
)
from ..contracts.interpretation import FrozenModel
from ..contracts.workspace import (
    ALLOWED_OPERATIONS, MODEL_ASSIGNABLE_CLASSES, PAYLOAD_TYPE_FOR_ARTIFACT, ArtifactOperation, ArtifactType,
    ConstraintKind, ConstraintReason, ContentClass, DietaryValue, DishTrait, FoodClass, InquiryExpectedUse,
    InquiryPurpose, InquirySensitivity, InquiryTargetKind,
)

COLLABORATION_RESPONSE_SCHEMA_NAME = "cognitive_collaborative_content_v0_1"


class CollaborationProviderError(RuntimeError):
    """A typed provider failure.  Carries no payload text or provider body."""

    def __init__(self, code: CollaborationFailureCode) -> None:
        super().__init__(code.value)
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_COLLABORATION_FAILURES


class SourceKind(StrEnum):
    USER_REQUEST = "user_request"
    USER_REQUIREMENT = "user_requirement"
    USER_ANSWER = "user_answer"
    USER_WITHDRAWAL = "user_withdrawal"
    DELIVERED_DIALOGUE = "delivered_dialogue"
    ARTIFACT_ELEMENT = "artifact_element"


USER_SOURCE_KINDS = frozenset({
    SourceKind.USER_REQUEST, SourceKind.USER_REQUIREMENT, SourceKind.USER_ANSWER, SourceKind.USER_WITHDRAWAL,
})


class CollaborationSource(FrozenModel):
    """One citable source.  ``text`` is attributed context, never a trusted fact."""

    ref: str = Field(min_length=1)
    kind: SourceKind
    speaker: Literal["user", "columbina", "workspace"]
    text: str = Field(min_length=1)
    current_turn: bool


class CollaborationMessage(FrozenModel):
    role: Literal["user", "assistant"]
    text: str


class CollaborationArtifactView(FrozenModel):
    artifact_ref: str = Field(min_length=1)
    artifact_type: ArtifactType
    revision_ref: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    title: str | None = None
    payload: Mapping[str, Any]
    element_ids: tuple[str, ...] = ()


class CollaborationVocabulary(FrozenModel):
    """The closed values the output may use (narrowed per request)."""

    patch_operations: tuple[str, ...]
    roles: tuple[str, ...]
    content_classes: tuple[str, ...]
    claim_kinds: tuple[str, ...]
    inquiry_purposes: tuple[str, ...]
    inquiry_expected_uses: tuple[str, ...]
    inquiry_target_kinds: tuple[str, ...]
    sensitivities: tuple[str, ...]
    constraint_kinds: tuple[str, ...]
    constraint_reasons: tuple[str, ...]
    dietary_values: tuple[str, ...]
    food_classes: tuple[str, ...]
    dish_traits: tuple[str, ...]


class CollaborationModelPayload(FrozenModel):
    protocol_version: Literal["collaborative-content-model-protocol-v0.1"] = COLLABORATION_PROTOCOL_VERSION
    operation: ArtifactOperation
    artifact_type: ArtifactType
    requested_form: str | None = None
    # ``new``: create a new artifact; ``revision``: the exact current revision;
    # ``dialogue``: delivered dialogue (no workspace artifact exists yet).
    target_kind: Literal["new", "revision", "dialogue"]
    artifact: CollaborationArtifactView | None = None
    sources: tuple[CollaborationSource, ...] = Field(min_length=1)
    messages: tuple[CollaborationMessage, ...] = ()
    vocabulary: CollaborationVocabulary
    curiosity: Literal["low", "moderate", "high"] = "moderate"
    max_inquiries: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def coherent(self) -> CollaborationModelPayload:
        refs = tuple(item.ref for item in self.sources)
        if len(refs) != len(set(refs)):
            raise ValueError("source refs must be unique")
        if (self.target_kind == "revision") != (self.artifact is not None):
            raise ValueError("an artifact view is supplied exactly for a revision target")
        return self


def vocabulary_for(operation: ArtifactOperation, artifact_type: ArtifactType) -> CollaborationVocabulary:
    revising = operation in {ArtifactOperation.CREATE, ArtifactOperation.REVISE}
    classes = {item.value for item in MODEL_ASSIGNABLE_CLASSES}
    if not revising:
        # Re-presenting the target's own content (describe, summarize, ...).
        classes.add(ContentClass.ARTIFACT_CONTENT.value)
    return CollaborationVocabulary(
        patch_operations=tuple(sorted(ALLOWED_OPERATIONS[PAYLOAD_TYPE_FOR_ARTIFACT[artifact_type]])) if revising else (),
        roles=tuple(sorted(item.value for item in ROLES_BY_OPERATION[operation])),
        content_classes=tuple(sorted(classes)),
        claim_kinds=tuple(sorted(item.value for item in ClaimKind if item not in RESTRICTED_CLAIM_KINDS)),
        inquiry_purposes=tuple(item.value for item in InquiryPurpose),
        inquiry_expected_uses=tuple(item.value for item in InquiryExpectedUse
                                    if item != InquiryExpectedUse.COMPLETE_REQUEST),
        inquiry_target_kinds=tuple(item.value for item in InquiryTargetKind
                                   if item != InquiryTargetKind.REQUEST_ARGUMENT),
        sensitivities=tuple(item.value for item in InquirySensitivity),
        constraint_kinds=tuple(item.value for item in ConstraintKind),
        constraint_reasons=tuple(item.value for item in ConstraintReason),
        dietary_values=tuple(item.value for item in DietaryValue),
        food_classes=tuple(item.value for item in FoodClass),
        dish_traits=tuple(item.value for item in DishTrait),
    )


# ---------------------------------------------------------------------------
# Untrusted output
# ---------------------------------------------------------------------------

class ConstraintCandidate(FrozenModel):
    constraint_id: str
    kind: str
    value: str
    basis: str
    reason: str


class DishCandidate(FrozenModel):
    dish_id: str
    name: str
    content_class: str
    contains: tuple[str, ...]
    traits: tuple[str, ...]
    prep_time_minutes: int | None


class OperationCandidate(FrozenModel):
    """One patch operation; fields an operation does not use are null."""

    op: str
    element_id: str | None
    section_id: str | None
    integer_value: int | None
    integer_class: str | None
    constraint: ConstraintCandidate | None
    dish: DishCandidate | None
    text: str | None
    text_class: str | None
    heading: str | None
    source_refs: tuple[str, ...]


class ItemCandidate(FrozenModel):
    role: str
    statement: str
    anchors: tuple[str, ...]
    content_class: str
    claim_kind: str
    subject_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    numbers: tuple[int, ...]


class InquiryCandidate(FrozenModel):
    target_kind: str
    target_id: str
    question_kind: str
    purpose: str
    expected_use: str
    criticality: str
    sensitivity: str
    asked_about: str
    answer_options: tuple[str, ...]
    source_refs: tuple[str, ...]


class CollaborationModelOutput(FrozenModel):
    title: str | None
    operations: tuple[OperationCandidate, ...]
    content_items: tuple[ItemCandidate, ...]
    inquiries: tuple[InquiryCandidate, ...]


class CollaborationCandidateModel(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def provenance(self) -> CollaborationProviderProvenance | None: ...

    def generate(self, payload: CollaborationModelPayload) -> CollaborationModelOutput: ...


def canonical_payload_json(payload: CollaborationModelPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def response_json_schema(payload: CollaborationModelPayload) -> Mapping[str, Any]:
    """Strict schema narrowed to the request's closed vocabularies and citable refs."""

    vocabulary = payload.vocabulary
    refs = [item.ref for item in payload.sources]

    def enum(values) -> dict:
        return {"type": "string", "enum": list(values)} if values else {"type": "string", "enum": ["none"]}

    def nullable(schema: dict) -> dict:
        return {"anyOf": [schema, {"type": "null"}]}

    def obj(properties: dict) -> dict:
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    # Factories, not shared dicts: callers narrow individual fields (e.g.
    # anchors minItems), which must never leak into sibling fields.
    def ref_list() -> dict:
        return {"type": "array", "items": enum(refs)}

    def string_list() -> dict:
        return {"type": "array", "items": {"type": "string"}}
    constraint = obj({
        "constraint_id": {"type": "string"}, "kind": enum(vocabulary.constraint_kinds), "value": {"type": "string"},
        "basis": enum(("user_supplied_constraint", "attributed_user_claim")),
        "reason": enum(vocabulary.constraint_reasons),
    })
    dish = obj({
        "dish_id": {"type": "string"}, "name": {"type": "string"},
        "content_class": enum(("generated_proposal", "uncertain_suggestion", "user_supplied_constraint")),
        "contains": {"type": "array", "items": enum(vocabulary.food_classes)},
        "traits": {"type": "array", "items": enum(vocabulary.dish_traits)},
        "prep_time_minutes": nullable({"type": "integer"}),
    })
    operation = obj({
        "op": enum(vocabulary.patch_operations), "element_id": nullable({"type": "string"}),
        "section_id": nullable({"type": "string"}), "integer_value": nullable({"type": "integer"}),
        "integer_class": nullable(enum(("user_supplied_constraint", "generated_proposal"))),
        "constraint": nullable(constraint), "dish": nullable(dish), "text": nullable({"type": "string"}),
        "text_class": nullable(enum(vocabulary.content_classes)), "heading": nullable({"type": "string"}),
        "source_refs": ref_list(),
    })
    item = obj({
        "role": enum(vocabulary.roles), "statement": {"type": "string"}, "anchors": string_list(),
        "content_class": enum(vocabulary.content_classes), "claim_kind": enum(vocabulary.claim_kinds),
        "subject_ids": string_list(), "source_refs": ref_list(),
        "numbers": {"type": "array", "items": {"type": "integer"}},
    })
    inquiry = obj({
        "target_kind": enum(vocabulary.inquiry_target_kinds), "target_id": {"type": "string"},
        "question_kind": enum(("open_set", "yes_no", "choice")), "purpose": enum(vocabulary.inquiry_purposes),
        "expected_use": enum(vocabulary.inquiry_expected_uses),
        "criticality": enum(("quality_improving", "optional_curiosity")),
        "sensitivity": enum(vocabulary.sensitivities), "asked_about": {"type": "string"},
        "answer_options": string_list(), "source_refs": ref_list(),
    })
    return obj({
        "title": nullable({"type": "string"}),
        "operations": {"type": "array", "items": operation},
        "content_items": {"type": "array", "items": item},
        "inquiries": {"type": "array", "items": inquiry},
    })


__all__ = [
    "COLLABORATION_RESPONSE_SCHEMA_NAME", "CollaborationArtifactView", "CollaborationCandidateModel",
    "CollaborationMessage", "CollaborationModelOutput", "CollaborationModelPayload", "CollaborationProviderError",
    "CollaborationSource", "CollaborationVocabulary", "ConstraintCandidate", "DishCandidate", "InquiryCandidate",
    "ItemCandidate", "OperationCandidate", "SourceKind", "USER_SOURCE_KINDS", "canonical_payload_json",
    "response_json_schema", "vocabulary_for",
]
