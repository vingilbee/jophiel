"""Trusted producers by authority class (pure; no I/O, SDK, or database).

Each producer answers only the query kinds its class declares, from inputs
fixed at a defined turn-time boundary, and returns a closed
:class:`ProducerResult`.  Admission is decided by typed fields only:
authorization, the class's admission state (captured / approved / delivered /
final), user ownership, realizable sensitivity, supersession, and freshness.
Records observed after the turn boundary do not exist for that turn.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from ..contracts.interpretation import FrozenModel
from ..contracts.realization import (
    REALIZABLE_SENSITIVITY, ArgumentValueType, ContentAttribution, ContentSourceKind, SensitivityClass,
)
from .contracts import (
    AuthorityClass, KnowledgeGapRecord, ProducedFact, ProducerReason, ProducerResult, ProducerStatus, SemanticQuery,
)
from .manifest import QUERY_MANIFEST_VERSION, Availability, QueryKind, capability_for

APPLICATION_IDENTITY_VERSION = "application-identity-v0.1"
# The reviewed application identity contract: application-owned, immutable,
# identical for every user, and never derived from personality or a persona.
IDENTITY_FACTS: Mapping[str, tuple[str, str]] = {
    QueryKind.ASSISTANT_NAME.value: ("application_identity.assistant_name", "Columbina"),
}
GAP_STATUSES = frozenset({ProducerStatus.UNAVAILABLE, ProducerStatus.STALE, ProducerStatus.UNSUPPORTED})


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("producer timestamps must carry a timezone")
    return parsed


@runtime_checkable
class TrustedProducer(Protocol):
    authority_class: AuthorityClass
    producer_id: str
    producer_version: str

    def produce(self, query: SemanticQuery) -> ProducerResult: ...


def _result(
    query: SemanticQuery, status: ProducerStatus, *, reason: ProducerReason | None = None,
    authority: AuthorityClass | None = None, producer_id: str | None = None, producer_version: str | None = None,
    facts: tuple[ProducedFact, ...] = (), superseded: Iterable[str] = (), withheld: Iterable[str] = (),
    conflicting: Iterable[str] = (),
) -> ProducerResult:
    return ProducerResult(
        result_id=f"producer-result-{_digest(query.query_id, producer_id or 'none', status.value)}",
        query_ref=query.query_id, status=status, authority_class=authority, producer_id=producer_id,
        producer_version=producer_version, facts=facts, superseded_fact_refs=tuple(sorted(set(superseded))),
        withheld_fact_refs=tuple(sorted(set(withheld))), conflicting_fact_refs=tuple(sorted(set(conflicting))),
        reason=reason, manifest_version=QUERY_MANIFEST_VERSION,
    )


class ApplicationIdentityProducer:
    """Immutable reviewed identity facts (no capture, no user scope, no expiry)."""

    authority_class = AuthorityClass.APPLICATION_IDENTITY
    producer_id = "application-identity-producer"
    producer_version = APPLICATION_IDENTITY_VERSION

    def produce(self, query: SemanticQuery) -> ProducerResult:
        entry = IDENTITY_FACTS.get(query.query_kind)
        if entry is None:
            return _result(query, ProducerStatus.UNSUPPORTED, reason=ProducerReason.QUERY_UNSUPPORTED,
                           authority=self.authority_class, producer_id=self.producer_id,
                           producer_version=self.producer_version)
        predicate, value = entry
        fact = ProducedFact(
            fact_id=f"identity-fact:{self.producer_version}:{query.query_kind}", query_ref=query.query_id,
            authority_class=self.authority_class, producer_id=self.producer_id,
            producer_version=self.producer_version, predicate=predicate, subject_anchor=query.subject_anchor,
            value=value, value_type=ArgumentValueType.STRING, value_anchor=value, owner="application",
            source_kind=ContentSourceKind.REVIEWED_SYSTEM_KNOWLEDGE,
            source_ref=f"{self.producer_version}#{query.query_kind}", revision=self.producer_version,
            attribution=ContentAttribution.TRUSTED_SOURCE,
        )
        return _result(query, ProducerStatus.KNOWN, authority=self.authority_class, producer_id=self.producer_id,
                       producer_version=self.producer_version, facts=(fact,))


class ProducerRecord(FrozenModel):
    """One source record fixed at the turn boundary (captured, reviewed, delivered, or final)."""

    record_id: str = Field(min_length=1)
    query_kind: str = Field(min_length=1)
    arguments: Mapping[str, str] = Field(default_factory=dict)
    predicate: str = Field(min_length=1)
    value: str = Field(min_length=1)
    value_type: ArgumentValueType
    unit: str | None = None
    value_anchor: str = Field(min_length=1)
    owner: Literal["application", "runtime", "user", "third_party"]
    owner_ref: str | None = None
    authorized: bool
    admission_state: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    supersedes_ref: str | None = None
    sensitivity: SensitivityClass = SensitivityClass.ORDINARY
    observed_at: str | None = None
    max_age_seconds: int | None = Field(default=None, ge=0)
    valid_until: str | None = None

    @model_validator(mode="after")
    def valid(self) -> ProducerRecord:
        if (self.owner == "user") != (self.owner_ref is not None):
            raise ValueError("exactly user-owned records name their owning user")
        for stamp in (self.observed_at, self.valid_until):
            if stamp is not None:
                parse_time(stamp)
        if self.max_age_seconds is not None and self.observed_at is None:
            raise ValueError("a freshness window needs an observation time")
        return self


class RecordBackedProducer:
    """The shared admission, supersession, freshness, and conflict policy."""

    def __init__(
        self, *, authority_class: AuthorityClass, producer_id: str, producer_version: str,
        records: Iterable[ProducerRecord], admitted_states: frozenset[str], source_kind: ContentSourceKind,
        attribution: ContentAttribution, turn_time: datetime, require_freshness: bool = False,
    ) -> None:
        if turn_time.tzinfo is None:
            raise ValueError("the turn boundary must carry a timezone")
        self.authority_class = authority_class
        self.producer_id = producer_id
        self.producer_version = producer_version
        self.records = tuple(records)
        self.admitted_states = admitted_states
        self.source_kind = source_kind
        self.attribution = attribution
        self.turn_time = turn_time
        if require_freshness and any(item.observed_at is None or item.max_age_seconds is None for item in self.records):
            raise ValueError("captured runtime facts need an observation time and a freshness window")

    def _fact_ref(self, record: ProducerRecord) -> str:
        return f"{self.authority_class.value}-fact:{record.record_id}:{record.revision}"

    def _stale(self, record: ProducerRecord) -> bool:
        if record.valid_until is not None and self.turn_time > parse_time(record.valid_until):
            return True
        if record.max_age_seconds is not None and record.observed_at is not None:
            return self.turn_time - parse_time(record.observed_at) > timedelta(seconds=record.max_age_seconds)
        return False

    def _fact(self, query: SemanticQuery, record: ProducerRecord) -> ProducedFact:
        return ProducedFact(
            fact_id=self._fact_ref(record), query_ref=query.query_id, authority_class=self.authority_class,
            producer_id=self.producer_id, producer_version=self.producer_version, predicate=record.predicate,
            subject_anchor=query.subject_anchor, value=record.value, value_type=record.value_type, unit=record.unit,
            value_anchor=record.value_anchor, owner=record.owner, owner_ref=record.owner_ref,
            source_kind=self.source_kind, source_ref=f"{self.producer_id}#{record.record_id}", revision=record.revision,
            supersedes_ref=record.supersedes_ref, sensitivity=record.sensitivity, attribution=self.attribution,
            observed_at=record.observed_at, valid_until=record.valid_until,
        )

    def produce(self, query: SemanticQuery) -> ProducerResult:
        common = dict(authority=self.authority_class, producer_id=self.producer_id,
                      producer_version=self.producer_version)
        matching = [
            item for item in self.records
            if item.query_kind == query.query_kind and dict(item.arguments) == dict(query.arguments)
            # State recorded after the turn boundary did not exist for this turn.
            and (item.observed_at is None or parse_time(item.observed_at) <= self.turn_time)
        ]
        withheld = [
            item for item in matching
            if not item.authorized or item.admission_state not in self.admitted_states
            or (item.owner == "user" and item.owner_ref != query.user_ref)
            or item.sensitivity not in REALIZABLE_SENSITIVITY
        ]
        admitted = [item for item in matching if item not in withheld]
        withheld_refs = [self._fact_ref(item) for item in withheld]
        if not admitted:
            if withheld:
                return _result(query, ProducerStatus.UNAUTHORIZED, reason=ProducerReason.NOT_AUTHORIZED,
                               withheld=withheld_refs, **common)
            return _result(query, ProducerStatus.UNAVAILABLE, reason=ProducerReason.NO_MATCHING_RECORD, **common)
        replaced = {item.supersedes_ref for item in admitted if item.supersedes_ref}
        current = [item for item in admitted if item.revision not in replaced]
        superseded_refs = [self._fact_ref(item) for item in admitted if item.revision in replaced]
        fresh = [item for item in current if not self._stale(item)]
        if not fresh:
            return _result(query, ProducerStatus.STALE, reason=ProducerReason.STALE_OBSERVATION,
                           superseded=superseded_refs, withheld=withheld_refs, **common)
        if len({(item.value, item.unit) for item in fresh}) > 1:
            return _result(query, ProducerStatus.CONFLICTING, reason=ProducerReason.CONFLICTING_VALUES,
                           conflicting=[self._fact_ref(item) for item in fresh], superseded=superseded_refs,
                           withheld=withheld_refs, **common)
        facts = tuple(self._fact(query, item) for item in sorted(fresh, key=lambda item: item.record_id))
        return _result(query, ProducerStatus.KNOWN, facts=facts, superseded=superseded_refs,
                       withheld=withheld_refs, **common)


def captured_runtime_producer(records: Iterable[ProducerRecord], *, turn_time: datetime) -> RecordBackedProducer:
    return RecordBackedProducer(
        authority_class=AuthorityClass.CAPTURED_RUNTIME_FACT, producer_id="captured-runtime-producer",
        producer_version="captured-runtime-producer-v0.1", records=records, admitted_states=frozenset({"captured"}),
        source_kind=ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION, attribution=ContentAttribution.RUNTIME,
        turn_time=turn_time, require_freshness=True,
    )


def reviewed_knowledge_producer(records: Iterable[ProducerRecord], *, turn_time: datetime) -> RecordBackedProducer:
    return RecordBackedProducer(
        authority_class=AuthorityClass.REVIEWED_KNOWLEDGE, producer_id="reviewed-knowledge-producer",
        producer_version="reviewed-knowledge-producer-v0.1", records=records, admitted_states=frozenset({"approved"}),
        source_kind=ContentSourceKind.REVIEWED_SYSTEM_KNOWLEDGE, attribution=ContentAttribution.TRUSTED_SOURCE,
        turn_time=turn_time,
    )


def delivered_claim_producer(records: Iterable[ProducerRecord], *, turn_time: datetime) -> RecordBackedProducer:
    # A delivered user claim is attributed to the user, never grounded as true.
    return RecordBackedProducer(
        authority_class=AuthorityClass.DELIVERED_DIALOGUE_CLAIM, producer_id="delivered-claim-producer",
        producer_version="delivered-claim-producer-v0.1", records=records,
        admitted_states=frozenset({"delivered", "read"}), source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE,
        attribution=ContentAttribution.USER, turn_time=turn_time,
    )


def tool_result_producer(records: Iterable[ProducerRecord], *, turn_time: datetime) -> RecordBackedProducer:
    return RecordBackedProducer(
        authority_class=AuthorityClass.AUTHORIZED_TOOL_RESULT, producer_id="tool-result-producer",
        producer_version="tool-result-producer-v0.1", records=records, admitted_states=frozenset({"final"}),
        source_kind=ContentSourceKind.FINALIZED_TOOL_RESULT, attribution=ContentAttribution.RUNTIME,
        turn_time=turn_time,
    )


class ProducerRegistry:
    """Dispatch a query through the capability manifest to its one producer."""

    def __init__(self, producers: Iterable[TrustedProducer], *, allow_review_classes: bool = False) -> None:
        by_class: dict[AuthorityClass, TrustedProducer] = {}
        for producer in producers:
            if producer.authority_class in by_class:
                raise ValueError("one producer per authority class")
            by_class[producer.authority_class] = producer
        self._by_class = by_class
        self.allow_review_classes = allow_review_classes

    def resolve(self, query: SemanticQuery) -> ProducerResult:
        capability = capability_for(query.query_kind)
        if capability is None:
            return _result(query, ProducerStatus.UNSUPPORTED, reason=ProducerReason.QUERY_UNSUPPORTED)
        authority = capability.authority_class
        if any(name not in query.arguments for name in capability.required_arguments):
            return _result(query, ProducerStatus.UNAVAILABLE, reason=ProducerReason.MISSING_QUERY_ARGUMENT,
                           authority=authority)
        if capability.availability == Availability.REQUIRES_REVIEW and not self.allow_review_classes:
            return _result(query, ProducerStatus.UNAVAILABLE, reason=ProducerReason.REQUIRES_REVIEW,
                           authority=authority)
        producer = self._by_class.get(authority)
        if producer is None:
            return _result(query, ProducerStatus.UNAVAILABLE, reason=ProducerReason.PRODUCER_NOT_INSTALLED,
                           authority=authority)
        result = producer.produce(query)
        if result.authority_class != authority or result.query_ref != query.query_id:
            raise ValueError("a producer answered outside its declared class or query")
        return result


def knowledge_gap_for(query: SemanticQuery, result: ProducerResult) -> KnowledgeGapRecord | None:
    """The explicit gap for an unavailable, stale, or unsupported answer (else ``None``)."""

    if result.query_ref != query.query_id:
        raise ValueError("the result answers a different query")
    if result.status not in GAP_STATUSES:
        return None
    return KnowledgeGapRecord(
        gap_id=f"knowledge-gap-{_digest(query.query_id, result.result_id)}", query_ref=query.query_id,
        producer_status=result.status, reason=result.reason, source_ref=result.result_id,
    )


__all__ = [
    "APPLICATION_IDENTITY_VERSION", "ApplicationIdentityProducer", "GAP_STATUSES", "IDENTITY_FACTS",
    "ProducerRecord", "ProducerRegistry", "RecordBackedProducer", "TrustedProducer", "captured_runtime_producer",
    "delivered_claim_producer", "knowledge_gap_for", "parse_time", "reviewed_knowledge_producer",
    "tool_result_producer",
]
