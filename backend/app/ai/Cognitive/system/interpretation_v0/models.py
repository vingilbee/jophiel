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

    @model_validator(mode="after")
    def exact_ownership(self) -> InterpretationRequest:
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
