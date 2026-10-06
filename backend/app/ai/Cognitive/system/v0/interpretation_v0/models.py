"""Immutable inputs and typed failures for Interpretation V0."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.interpretation import FrozenModel
from ..perception_v0.models import (
    AttachmentFact,
    PerceptionInterpretationHandoff,
    PerceptionMessage,
    ToolEventFact,
)


class TaskContract(FrozenModel):
    task_type: str = Field(min_length=1)
    required_parameters: tuple[str, ...] = ()
    provided_parameters: Mapping[str, object] = Field(default_factory=dict)


class InterpretationRuntimeContext(FrozenModel):
    snapshot_id: str = Field(min_length=1)
    predicate_registry_version: str = Field(min_length=1)
    known_predicates: tuple[str, ...]
    known_entities: tuple[str, ...] = ()
    attachments: tuple[AttachmentFact, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    task_contract: TaskContract | None = None
    authorization: str | None = None
    safety_decision: str | None = None
    last_tool_event: ToolEventFact | None = None


class InterpretationMemory(FrozenModel):
    memory_id: str = Field(min_length=1)
    memory_type: str = Field(min_length=1)
    content: str
    provenance: str = Field(min_length=1)
    status: str = Field(min_length=1)
    retrieval_score: float | None = None
    version: int | None = Field(default=None, ge=1)
    authorized: bool = True

    @model_validator(mode="after")
    def finite_retrieval_score(self) -> InterpretationMemory:
        if self.retrieval_score is not None and not math.isfinite(self.retrieval_score):
            raise ValueError("memory retrieval score must be finite")
        return self


class InterpretationRelationshipContext(FrozenModel):
    relationship_ref: str = Field(min_length=1)
    status: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    authorized: bool = True


class InterpretationBiasProfile(FrozenModel):
    profile_id: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    source_core_memory_ref: str = Field(min_length=1)
    benign_prior: float = Field(ge=0.0, le=1.0)
    trust_prior: float = Field(ge=0.0, le=1.0)
    threat_sensitivity: float = Field(ge=0.0, le=1.0)
    confirmation_tendency: float = Field(ge=0.0, le=1.0)
    self_attribution_tendency: float = Field(ge=0.0, le=1.0)
    ambiguity_tolerance: float = Field(ge=0.0, le=1.0)
    social_affiliation_prior: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def finite_biases(self) -> InterpretationBiasProfile:
        values = (
            self.benign_prior, self.trust_prior, self.threat_sensitivity,
            self.confirmation_tendency, self.self_attribution_tendency,
            self.ambiguity_tolerance, self.social_affiliation_prior,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("compiled interpretation biases must be finite")
        return self


class InterpretationDialogueItem(FrozenModel):
    """One attributed dialogue object with exact provenance (never a verified fact)."""

    item_ref: str = Field(min_length=1)
    item_kind: Literal["proposition", "entity", "question", "option", "plan", "topic", "request", "utterance"]
    speaker: Literal["user", "columbina", "runtime"]
    epistemic_status: Literal["attributed", "observed", "unresolved"]
    surface_anchor: str | None = None
    source_message_refs: tuple[str, ...] = Field(min_length=1)
    related_refs: tuple[str, ...] = ()
    completeness: Literal["complete", "partial"] | None = None

    @model_validator(mode="after")
    def valid(self) -> InterpretationDialogueItem:
        for values in (self.source_message_refs, self.related_refs):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError("dialogue item references must be unique and non-blank")
        if self.surface_anchor is not None and not self.surface_anchor.strip():
            raise ValueError("a dialogue surface anchor must not be blank")
        if (self.item_kind == "utterance") != (self.completeness is not None):
            raise ValueError("utterances, and only they, carry their completeness")
        return self


class InterpretationDialogueReferent(FrozenModel):
    """A dialogue cue in the focus message and its deterministic resolution."""

    cue_ref: str = Field(min_length=1)
    cue_kind: str = Field(min_length=1)
    surface_anchor: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    resolution: Literal["resolved", "ambiguous", "no_candidate"]
    resolved_refs: tuple[str, ...] = ()
    candidate_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> InterpretationDialogueReferent:
        if self.end <= self.start:
            raise ValueError("referent spans are non-empty")
        if (self.resolution == "resolved") != bool(self.resolved_refs):
            raise ValueError("a resolved referent, and only it, names its resolution")
        if self.resolution == "ambiguous" and len(self.candidate_refs) < 2:
            raise ValueError("an ambiguous referent keeps its competing candidates")
        return self


class InterpretationDialogueContext(FrozenModel):
    """Typed short-term dialogue context (Dialogue Working Memory V0 projection).

    Deliberately not an ``InterpretationMemory``: it is conversation-scoped,
    attributed, and cites exact source messages.  Ambiguity is preserved --
    an ambiguous referent keeps every candidate.  Interpretation V0 rules do
    not consume it yet; it is carried so the context a turn was interpreted
    under is exact and replayable.
    """

    context_ref: str = Field(min_length=1)
    projection_version: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    focus_message_ref: str = Field(min_length=1)
    source_snapshot_ref: str = Field(min_length=1)
    source_snapshot_digest: str = Field(min_length=64, max_length=64)
    items: tuple[InterpretationDialogueItem, ...] = Field(default=(), max_length=128)
    referents: tuple[InterpretationDialogueReferent, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> InterpretationDialogueContext:
        refs = tuple(item.item_ref for item in self.items)
        if len(refs) != len(set(refs)):
            raise ValueError("dialogue context items must be unique")
        known = set(refs)
        cues = tuple(item.cue_ref for item in self.referents)
        if len(cues) != len(set(cues)):
            raise ValueError("dialogue referents must be unique")
        for referent in self.referents:
            if not set(referent.resolved_refs) <= known or not set(referent.candidate_refs) <= known:
                raise ValueError("a dialogue referent names an object outside the context")
        return self


class InterpretationOpenInquiry(FrozenModel):
    """A typed question the conversation still awaits an answer to.

    Projected only from the conversational workspace's open-inquiry records
    (which exist only after the question was actually delivered).  It carries
    a reviewed anchor and closed options -- never generated wording -- and is
    the only thing an ``inquiry_answer`` frame may cite.
    """

    inquiry_ref: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    asked_about: str = Field(min_length=1)
    answer_options: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> InterpretationOpenInquiry:
        if len(self.answer_options) != len(set(self.answer_options)) or any(
            not option.strip() for option in self.answer_options
        ):
            raise ValueError("inquiry answer options must be unique and non-blank")
        return self


class InterpretationRequest(FrozenModel):
    schema_version: Literal[1] = 1
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    perception: PerceptionInterpretationHandoff
    messages: tuple[PerceptionMessage, ...] = Field(min_length=1)
    runtime: InterpretationRuntimeContext
    memories: tuple[InterpretationMemory, ...] = ()
    relationship: InterpretationRelationshipContext | None = None
    biases: InterpretationBiasProfile
    language_tag: str | None = "en"
    ontology_version: str = "interpretation-ontology-v1"
    feature_manifest_version: str = "interpretation-features-v1"
    dialogue_context: InterpretationDialogueContext | None = None
    # Registry v0.4: the open inquiries an answer may be linked to.
    open_inquiries: tuple[InterpretationOpenInquiry, ...] = ()

    @model_validator(mode="after")
    def exact_ownership(self) -> InterpretationRequest:
        refs = tuple(item.inquiry_ref for item in self.open_inquiries)
        if len(refs) != len(set(refs)):
            raise ValueError("open inquiry refs must be unique")
        dialogue = self.dialogue_context
        if dialogue is not None and (
            dialogue.conversation_ref != self.conversation_ref
            or dialogue.focus_message_ref != self.perception.focus_message_id
        ):
            raise ValueError("the dialogue context belongs to a different conversation or focus message")
        if self.cognitive_turn_id != self.perception.cognitive_turn_id:
            raise ValueError("interpretation and perception cognitive-turn ids differ")
        message_ids = tuple(message.message_id for message in self.messages)
        if message_ids != self.perception.input_message_ids:
            raise ValueError("interpretation messages differ from perception bounded inputs")
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("interpretation message ids must be unique")
        if self.perception.focus_message_id != message_ids[-1]:
            raise ValueError("interpretation focus must be the final supplied message")
        if self.runtime.snapshot_id != self.perception.runtime_snapshot_id:
            raise ValueError("interpretation and perception runtime snapshot ids differ")
        memory_ids = tuple(memory.memory_id for memory in self.memories)
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("interpretation memory ids must be unique")
        if any(not memory.authorized for memory in self.memories):
            raise ValueError("unauthorized memories cannot enter interpretation")
        if self.relationship is not None and not self.relationship.authorized:
            raise ValueError("an unauthorized relationship record cannot enter interpretation")
        if self.language_tag != "en":
            raise ValueError("Interpretation V0 supports only language_tag='en'")
        if not self.ontology_version.strip() or not self.feature_manifest_version.strip():
            raise ValueError("interpretation version ids must not be blank")
        return self

    @property
    def focus_message(self) -> PerceptionMessage:
        return self.messages[-1]


InterpretationFailurePhase = Literal[
    "validation", "evidence", "semantic_grounding", "hypothesis_rules",
    "bias_adjustment", "scenario_building", "envelope_validation",
]


class InterpretationExecutionError(RuntimeError):
    def __init__(
        self,
        *,
        cognitive_turn_id: str,
        failed_phase: InterpretationFailurePhase,
        safe_message: str,
    ) -> None:
        super().__init__(safe_message)
        self.cognitive_turn_id = cognitive_turn_id
        self.failed_phase = failed_phase
        self.safe_message = safe_message
