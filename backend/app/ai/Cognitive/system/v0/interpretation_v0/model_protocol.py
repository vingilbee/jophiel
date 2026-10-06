"""Bounded, provider-neutral protocol for model-backed semantic grounding.

The payload deliberately contains only conversation text, focus ownership,
code-owned predicate definitions, and code-owned evidence regions.  Runtime
facts, memories, relationship state, personality, biases, goals, and actions
are not part of this protocol.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from ..contracts.interpretation import FrozenModel, SemanticParserProvenance
from ..perception_v0.models import MessageRole

# v0.2 adds per-argument descriptions and closed value vocabularies to each
# predicate spec, and a model-facing predicate description in place of the
# offline lexical patterns.  The payload still carries no runtime, memory,
# relationship, personality, goal, action, or tool state.
# v0.3 (Collaborative Content and Inquiry V0) adds the optional
# ``open_inquiries`` list -- the typed questions this conversation is still
# awaiting an answer to -- so the parser can link an answer to an exact
# inquiry instead of downstream text matching.  It is omitted when empty.
SEMANTIC_MODEL_PROTOCOL_VERSION = "interpretation-semantic-model-protocol-v0.3"
SEMANTIC_RESPONSE_SCHEMA_NAME = "cognitive_semantic_candidates_v0_3"


class SemanticParserErrorCode(StrEnum):
    """Stable, content-free semantic-parser outcomes.

    Only ``timeout`` and ``transport`` are transient; every other outcome is
    deterministic for the stored capture and is recorded without retrying.
    """

    MODEL_NOT_CONFIGURED = "model_not_configured"
    CONFIGURATION_INCOMPATIBLE = "configuration_incompatible"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    UNSUPPORTED_REQUEST = "unsupported_request"
    REFUSED = "refused"
    OUTPUT_TRUNCATED = "output_truncated"
    OUTPUT_INVALID = "output_invalid"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_INVALID = "schema_invalid"
    NO_FRAMES = "no_frames"
    UNREGISTERED_PREDICATE = "unregistered_predicate"
    DISALLOWED_ARGUMENT = "disallowed_argument"
    MISSING_REQUIRED_ARGUMENT = "missing_required_argument"
    ARGUMENT_VALUE_OUTSIDE_VOCABULARY = "argument_value_outside_vocabulary"
    FOREIGN_EVIDENCE = "foreign_evidence"
    FRAME_INVALID = "frame_invalid"
    # Protocol v0.3: an ``inquiry_answer`` cited an inquiry that was not
    # supplied, or an answer shape its inquiry does not admit.
    FOREIGN_INQUIRY = "foreign_inquiry"
    INQUIRY_ANSWER_INVALID = "inquiry_answer_invalid"


RETRYABLE_PARSER_ERRORS = frozenset({SemanticParserErrorCode.TIMEOUT, SemanticParserErrorCode.TRANSPORT})


class SemanticParserError(RuntimeError):
    """A typed parser failure.  Carries no payload text or provider body."""

    def __init__(self, code: SemanticParserErrorCode) -> None:
        super().__init__(code.value)
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_PARSER_ERRORS


class SemanticParserMessage(FrozenModel):
    message_ref: str = Field(min_length=1)
    role: MessageRole
    text: str


class SemanticEvidenceRegion(FrozenModel):
    evidence_ref: str = Field(min_length=1)
    message_ref: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1)


class SemanticArgumentSpec(FrozenModel):
    name: str = Field(min_length=1)
    required: bool
    description: str = ""
    allowed_values: tuple[str, ...] = ()


class SemanticPredicateSpec(FrozenModel):
    predicate: str = Field(min_length=1)
    description: str = Field(min_length=1)
    arguments: tuple[SemanticArgumentSpec, ...]


class SemanticOpenInquiry(FrozenModel):
    """One question the conversation still awaits an answer to (protocol v0.3).

    Code-owned and typed: ``asked_about`` is a reviewed anchor naming what was
    asked, never generated text; ``answer_options`` is the closed set an
    ``option`` answer must select from (empty for an open question).
    """

    inquiry_ref: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    asked_about: str = Field(min_length=1)
    answer_options: tuple[str, ...] = ()


class SemanticModelPayload(FrozenModel):
    protocol_version: Literal["interpretation-semantic-model-protocol-v0.3"] = SEMANTIC_MODEL_PROTOCOL_VERSION
    focus_message_ref: str = Field(min_length=1)
    messages: tuple[SemanticParserMessage, ...] = Field(min_length=1)
    evidence_regions: tuple[SemanticEvidenceRegion, ...] = Field(min_length=1)
    predicates: tuple[SemanticPredicateSpec, ...] = Field(min_length=1)
    open_inquiries: tuple[SemanticOpenInquiry, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_absent_inquiries(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("open_inquiries"):
            data.pop("open_inquiries", None)
        return data

    @model_validator(mode="after")
    def coherent_inquiries(self) -> "SemanticModelPayload":
        refs = tuple(item.inquiry_ref for item in self.open_inquiries)
        if len(refs) != len(set(refs)):
            raise ValueError("open inquiry refs must be unique")
        offered = {item.predicate for item in self.predicates}
        if bool(self.open_inquiries) != ("inquiry_answer" in offered):
            raise ValueError("inquiry_answer is offered exactly when open inquiries are supplied")
        return self


SemanticArgumentValue = str | bool | int | tuple[str, ...] | None


class SemanticArgumentCandidate(FrozenModel):
    name: str = Field(min_length=1)
    value: SemanticArgumentValue


class SemanticFrameCandidate(FrozenModel):
    predicate: str = Field(min_length=1)
    polarity: Literal["positive", "negative", "unknown"]
    arguments: tuple[SemanticArgumentCandidate, ...]
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_arguments(self) -> "SemanticFrameCandidate":
        names = tuple(item.name for item in self.arguments)
        if len(names) != len(set(names)) or len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("semantic candidate names and evidence refs must be unique")
        return self

    @property
    def unresolved_arguments(self) -> tuple[str, ...]:
        """Code, not the model, owns the null-to-unresolved interpretation."""

        return tuple(item.name for item in self.arguments if item.value is None)


class SemanticModelOutput(FrozenModel):
    frames: tuple[SemanticFrameCandidate, ...]


class SemanticCandidateModel(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def provenance(self) -> SemanticParserProvenance | None:
        """Typed request configuration; ``None`` only for offline test doubles."""
        ...

    def generate(self, payload: SemanticModelPayload) -> SemanticModelOutput: ...


def canonical_payload_json(payload: SemanticModelPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def response_json_schema(payload: SemanticModelPayload) -> Mapping[str, Any]:
    """Strict schema narrowed to the exact code-owned predicates and refs."""

    predicates = [item.predicate for item in payload.predicates]
    refs = [item.evidence_ref for item in payload.evidence_regions]
    scalar = {
        "anyOf": [
            {"type": "string"}, {"type": "boolean"}, {"type": "integer"},
            {"type": "array", "items": {"type": "string"}}, {"type": "null"},
        ]
    }
    return {
        "type": "object",
        "properties": {
            "frames": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "predicate": {"type": "string", "enum": predicates},
                        "polarity": {"type": "string", "enum": ["positive", "negative", "unknown"]},
                        "arguments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}, "value": scalar},
                                "required": ["name", "value"], "additionalProperties": False,
                            },
                        },
                        "evidence_refs": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": refs}},
                    },
                    "required": ["predicate", "polarity", "arguments", "evidence_refs"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["frames"], "additionalProperties": False,
    }


__all__ = [
    "RETRYABLE_PARSER_ERRORS", "SEMANTIC_MODEL_PROTOCOL_VERSION", "SEMANTIC_RESPONSE_SCHEMA_NAME",
    "SemanticArgumentSpec", "SemanticCandidateModel", "SemanticOpenInquiry", "SemanticParserError",
    "SemanticParserErrorCode", "SemanticModelOutput", "SemanticModelPayload", "canonical_payload_json",
    "response_json_schema",
]
