"""Closed, versioned contracts for trusted answer-content producers (SH-EVAL-005).

A supported answer domain needs: a typed semantic query, an authorized
versioned producer, turn-time provenance and freshness, an explicit
unavailable/unknown representation, a projection into Action grounded
objects, and a closed-world Realization content item.  These contracts carry
the first four; ``projection`` carries the last two.

Producers are grouped by authority class rather than one generic dictionary.
A result never contains raw CoreMemory, transcripts, database objects,
provider tokens, or unreviewed model knowledge, and an empty result is never
evidence that a fact does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.interpretation import FrozenModel
from ..contracts.realization import (
    ArgumentValueType, ContentAttribution, ContentSourceKind, GapKind, SensitivityClass,
)

PRODUCER_CONTRACT_VERSION = "trusted-producer-contract-v0.1"
_DOTTED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.")


def _nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _dotted(value: str, label: str) -> None:
    if not value or set(value) - _DOTTED or "." not in value or value.startswith(".") or value.endswith("."):
        raise ValueError(f"{label} must be a registered dotted key")


class AuthorityClass(StrEnum):
    APPLICATION_IDENTITY = "application_identity"          # immutable, reviewed application facts
    CAPTURED_RUNTIME_FACT = "captured_runtime_fact"        # observed at a defined turn-time boundary
    REVIEWED_KNOWLEDGE = "reviewed_knowledge"              # authorized, human-reviewed knowledge
    DELIVERED_DIALOGUE_CLAIM = "delivered_dialogue_claim"  # what was actually delivered, with attribution
    AUTHORIZED_TOOL_RESULT = "authorized_tool_result"      # finalized results of authorized tools
    KNOWLEDGE_GAP = "knowledge_gap"                        # explicit "no grounded answer" records
    # Registry v1.6 open-world contribution (``two_call/contribution.py``): model
    # general knowledge, and Columbina's own personality-selected view. Labelled,
    # never presented as verified; personal facts never come from these.
    MODEL_KNOWLEDGE = "model_general_knowledge"
    COLUMBINA_VIEW = "columbina_view"


class ProducerStatus(StrEnum):
    KNOWN = "known"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNAUTHORIZED = "unauthorized"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class ProducerReason(StrEnum):
    NO_MATCHING_RECORD = "no_matching_record"
    PRODUCER_NOT_INSTALLED = "producer_not_installed"
    REQUIRES_REVIEW = "requires_privacy_and_authorization_review"
    QUERY_UNSUPPORTED = "query_unsupported"
    MISSING_QUERY_ARGUMENT = "missing_query_argument"
    STALE_OBSERVATION = "stale_observation"
    NOT_AUTHORIZED = "not_authorized"
    CONFLICTING_VALUES = "conflicting_values"


class SemanticQuery(FrozenModel):
    """A typed question a trusted producer may answer.

    ``query_kind`` is a dotted key checked against the capability manifest, so
    an unsupported query stays representable and explicit rather than being
    dropped.  ``subject_anchor`` is the user's own wording for the topic.
    """

    query_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    query_kind: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    subject_anchor: str = Field(min_length=1)
    arguments: Mapping[str, str] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> SemanticQuery:
        for value, label in ((self.query_id, "query id"), (self.user_ref, "query user"),
                             (self.subject_ref, "query subject"), (self.subject_anchor, "query subject anchor")):
            _nonblank(value, label)
        _dotted(self.query_kind, "query kind")
        for key, value in self.arguments.items():
            _nonblank(key, "query argument key"); _nonblank(value, "query argument value")
        return self


class ProducedFact(FrozenModel):
    """One authorized fact with owner, source, revision, sensitivity, and time."""

    fact_id: str = Field(min_length=1)
    query_ref: str = Field(min_length=1)
    authority_class: AuthorityClass
    producer_id: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    subject_anchor: str = Field(min_length=1)
    value: str = Field(min_length=1)
    value_type: ArgumentValueType
    unit: str | None = None
    value_anchor: str = Field(min_length=1)
    owner: Literal["application", "runtime", "user", "third_party"]
    owner_ref: str | None = None
    source_kind: ContentSourceKind
    source_ref: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    supersedes_ref: str | None = None
    sensitivity: SensitivityClass = SensitivityClass.ORDINARY
    attribution: ContentAttribution
    observed_at: str | None = None
    valid_until: str | None = None

    @model_validator(mode="after")
    def valid(self) -> ProducedFact:
        _dotted(self.predicate, "fact predicate")
        if self.authority_class == AuthorityClass.KNOWLEDGE_GAP:
            raise ValueError("a knowledge gap is never a fact")
        if self.source_kind in {ContentSourceKind.RETRIEVAL_NO_RESULT, ContentSourceKind.CURRENT_USER_STATEMENT}:
            raise ValueError("a produced fact cannot come from a failed retrieval or the current statement")
        if (self.owner == "user") != (self.owner_ref is not None):
            raise ValueError("exactly user-owned facts name their owning user")
        if self.supersedes_ref is not None and self.supersedes_ref == self.revision:
            raise ValueError("a fact revision cannot supersede itself")
        return self


class ProducerResult(FrozenModel):
    """The typed outcome of one query: known facts or an explicit non-answer."""

    result_id: str = Field(min_length=1)
    contract_version: Literal["trusted-producer-contract-v0.1"] = PRODUCER_CONTRACT_VERSION
    query_ref: str = Field(min_length=1)
    status: ProducerStatus
    authority_class: AuthorityClass | None = None
    producer_id: str | None = None
    producer_version: str | None = None
    facts: tuple[ProducedFact, ...] = ()
    superseded_fact_refs: tuple[str, ...] = ()
    withheld_fact_refs: tuple[str, ...] = ()
    conflicting_fact_refs: tuple[str, ...] = ()
    reason: ProducerReason | None = None
    manifest_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ProducerResult:
        if (self.status == ProducerStatus.KNOWN) != bool(self.facts):
            raise ValueError("only a known result, and every known result, carries facts")
        if (self.status == ProducerStatus.KNOWN) == (self.reason is not None):
            raise ValueError("a non-answer carries exactly one typed reason; an answer carries none")
        if any(fact.query_ref != self.query_ref for fact in self.facts):
            raise ValueError("every fact answers this result's query")
        if any(fact.authority_class != self.authority_class for fact in self.facts):
            raise ValueError("facts come from the result's authority class")
        if self.status == ProducerStatus.CONFLICTING and len(self.conflicting_fact_refs) < 2:
            raise ValueError("a conflict names at least two competing facts")
        for label, values in (("superseded", self.superseded_fact_refs), ("withheld", self.withheld_fact_refs),
                              ("conflicting", self.conflicting_fact_refs)):
            if len(values) != len(set(values)) or values != tuple(sorted(values)):
                raise ValueError(f"{label} fact refs are unique and canonically ordered")
        return self


class KnowledgeGapRecord(FrozenModel):
    """An explicit gap: supports "I have no grounded answer", never "X is false"."""

    gap_id: str = Field(min_length=1)
    query_ref: str = Field(min_length=1)
    gap_kind: Literal[GapKind.NO_GROUNDED_ANSWER] = GapKind.NO_GROUNDED_ANSWER
    producer_status: ProducerStatus
    reason: ProducerReason
    source_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> KnowledgeGapRecord:
        if self.producer_status in {ProducerStatus.KNOWN, ProducerStatus.UNAUTHORIZED, ProducerStatus.CONFLICTING}:
            raise ValueError("only an unavailable, stale, or unsupported answer is a knowledge gap")
        return self


__all__ = [
    "AuthorityClass", "KnowledgeGapRecord", "PRODUCER_CONTRACT_VERSION", "ProducedFact", "ProducerReason",
    "ProducerResult", "ProducerStatus", "SemanticQuery",
]
