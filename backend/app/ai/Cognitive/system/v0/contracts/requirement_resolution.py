"""Typed requirement resolution contracts (``requirement-resolution-contract-v0.1``).

Requirement resolution answers one question per current user request goal:
is fulfillment ready, blocked, unavailable, or prohibited -- and why?  It sits
between Goal (what the user wants handled) and Action Selection (which move
truthfully addresses that state), and it never decides the move itself.

Three things are kept separate on purpose:

- a Goal requirement assessment is the goal's *terminal* satisfaction state;
- a :class:`RequirementResolution` is the *readiness* of one declared
  prerequisite of the next step (an argument, a referent, trusted content, a
  capability, authorization, consent, or safety);
- a :class:`RequirementActionBinding` names the exact Action index objects a
  resolution was projected into.

Every record is closed and typed.  Nothing here carries response wording, raw
user text, or model output: resolutions hold only references and codes, and
:func:`validate_requirement_snapshot` rejects any reference outside the turn's
permitted lineage.  This module imports no engine and no Action contract (the
Action contract imports it), so Goal never depends on Action and Action never
depends on a producer implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .interpretation import FrozenModel

# v0.2 (Collaborative Content and Inquiry V0): every resolution records a
# closed ``criticality``; only ``blocking`` prerequisites can make a goal
# unready.  New kinds cover conversational deliverables (artifact target,
# requested change, requested outcome), typed inquiry linkage, and
# quality-improving domain context.  A v0.1 snapshot keeps its exact bytes:
# ``criticality`` is omitted when it is ``blocking``.
REQUIREMENT_RESOLUTION_CONTRACT_VERSION = "requirement-resolution-contract-v0.2"
LEGACY_REQUIREMENT_RESOLUTION_CONTRACT_VERSIONS = frozenset({"requirement-resolution-contract-v0.1"})
REQUIREMENT_RESOLUTION_ENGINE_VERSION = "requirement-resolution-engine-v0.2"
REQUIREMENT_RESOLUTION_MANIFEST_VERSION = "requirement-resolution-manifest-v0.2"
# v0.2 projects the new deliverable blockers as user-owned unresolved
# elements with reviewed gap anchors.
REQUIREMENT_ACTION_PROJECTION_VERSION = "requirement-action-projection-v0.2"
REQUIREMENT_CAPABILITY_MAP_VERSION = "requirement-capability-map-v0.1"
# The Realization content producer id for projected answers, requests, and reasons.
REQUIREMENT_RESOLUTION_CONTENT_PRODUCER = "requirement-resolution-content-projection-v0.1"

_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _canonical_refs(values: tuple[str, ...], label: str) -> None:
    for value in values:
        _nonblank(value, label)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be unique and in canonical order")


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

class RequirementKind(StrEnum):
    REQUIRED_ARGUMENT = "required_argument"
    DIALOGUE_REFERENT = "dialogue_referent"
    TRUSTED_CONTENT = "trusted_content"
    CAPABILITY = "capability"
    AUTHORIZATION = "authorization"
    CONSENT = "consent"
    SAFETY = "safety"
    TOOL_OUTCOME = "tool_outcome"
    # Contract v0.2 (conversational deliverables).
    ARTIFACT_TARGET = "artifact_target"
    REQUESTED_CHANGE = "requested_change"
    REQUESTED_OUTCOME = "requested_outcome"
    INQUIRY_LINK = "inquiry_link"
    DOMAIN_CONTEXT = "domain_context"


class RequirementCriticality(StrEnum):
    """How much a prerequisite matters (Section 10.1).  Only ``blocking`` gates readiness."""

    BLOCKING = "blocking"
    QUALITY_IMPROVING = "quality_improving"
    OPTIONAL_CURIOSITY = "optional_curiosity"


class RequirementResolutionStatus(StrEnum):
    SATISFIED = "satisfied"
    MISSING_USER_INPUT = "missing_user_input"
    MISSING_GROUNDING = "missing_grounding"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    PROHIBITED = "prohibited"
    TEMPORARY_FAILURE = "temporary_failure"
    CONFLICTING = "conflicting"


class RequirementReason(StrEnum):
    """Why a prerequisite has its status.  Codes only; never prose."""

    ARGUMENT_PRESENT = "argument_present"
    ARGUMENT_UNRESOLVED = "argument_unresolved"
    REFERENT_STATED = "referent_stated"
    REFERENT_RESOLVED_FROM_DIALOGUE = "referent_resolved_from_dialogue"
    REFERENT_UNRESOLVED = "referent_unresolved"
    REFERENT_AMBIGUOUS = "referent_ambiguous"
    PRODUCER_KNOWN = "producer_known"
    PRODUCER_NO_MATCHING_RECORD = "producer_no_matching_record"
    PRODUCER_STALE_OBSERVATION = "producer_stale_observation"
    PRODUCER_QUERY_UNSUPPORTED = "producer_query_unsupported"
    PRODUCER_REQUIRES_REVIEW = "producer_requires_review"
    PRODUCER_NOT_INSTALLED = "producer_not_installed"
    PRODUCER_MISSING_QUERY_ARGUMENT = "producer_missing_query_argument"
    PRODUCER_NOT_AUTHORIZED = "producer_not_authorized"
    PRODUCER_CONFLICTING_VALUES = "producer_conflicting_values"
    CAPABILITY_AVAILABLE = "capability_available"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    CAPABILITY_UNREGISTERED = "capability_unregistered"
    AUTHORIZATION_GRANTED = "authorization_granted"
    AUTHORIZATION_MISSING = "authorization_missing"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_MISSING = "consent_missing"
    SAFETY_ALLOWED = "safety_allowed"
    SAFETY_BLOCKS_COMMITMENTS = "safety_blocks_commitments"
    SAFETY_BLOCKS_RESPONSE = "safety_blocks_response"
    SAFETY_DECISION_MISSING = "safety_decision_missing"
    SAFETY_DECISION_UNKNOWN = "safety_decision_unknown"
    SAFETY_DECISION_UNAVAILABLE = "safety_decision_unavailable"
    SAFETY_POLICY_VERSION_UNSUPPORTED = "safety_policy_version_unsupported"
    TOOL_OUTCOME_FINAL = "tool_outcome_final"
    TOOL_OUTCOME_FAILED = "tool_outcome_failed"
    TOOL_OUTCOME_UNAVAILABLE = "tool_outcome_unavailable"
    # Contract v0.2.
    ARTIFACT_TARGET_RESOLVED = "artifact_target_resolved"
    DIALOGUE_TARGET_RESOLVED = "dialogue_target_resolved"
    ARTIFACT_TARGET_AMBIGUOUS = "artifact_target_ambiguous"
    ARTIFACT_TARGET_NOT_FOUND = "artifact_target_not_found"
    CHANGE_STATED = "change_stated"
    CHANGE_UNSTATED = "change_unstated"
    OUTCOME_CLEAR = "outcome_clear"
    OUTCOME_AMBIGUOUS = "outcome_ambiguous"
    INQUIRY_OPEN = "inquiry_open"
    INQUIRY_NOT_OPEN = "inquiry_not_open"
    CONTEXT_KNOWN = "context_known"
    CONTEXT_UNKNOWN = "context_unknown"


class ContentProducerStatus(StrEnum):
    """The producer outcome a trusted-content prerequisite recorded.

    Mirrors ``producers_v0.ProducerStatus`` value for value (parity is checked
    where both are imported); this module deliberately does not import a
    producer module, so Action can depend on it without a cycle.
    """

    KNOWN = "known"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNAUTHORIZED = "unauthorized"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


K, S, R = RequirementKind, RequirementResolutionStatus, RequirementReason

# The only (status, reason) pairs each kind may record.  A resolver cannot
# emit, say, a "missing user input" safety state or a "prohibited" argument.
ALLOWED_OUTCOMES: Mapping[RequirementKind, frozenset[tuple[RequirementResolutionStatus, RequirementReason]]] = {
    K.REQUIRED_ARGUMENT: frozenset({
        (S.SATISFIED, R.ARGUMENT_PRESENT), (S.MISSING_USER_INPUT, R.ARGUMENT_UNRESOLVED),
    }),
    K.DIALOGUE_REFERENT: frozenset({
        (S.SATISFIED, R.REFERENT_STATED), (S.SATISFIED, R.REFERENT_RESOLVED_FROM_DIALOGUE),
        (S.MISSING_USER_INPUT, R.REFERENT_UNRESOLVED), (S.MISSING_USER_INPUT, R.REFERENT_AMBIGUOUS),
    }),
    K.TRUSTED_CONTENT: frozenset({
        (S.SATISFIED, R.PRODUCER_KNOWN),
        (S.MISSING_GROUNDING, R.PRODUCER_NO_MATCHING_RECORD), (S.MISSING_GROUNDING, R.PRODUCER_STALE_OBSERVATION),
        (S.MISSING_GROUNDING, R.PRODUCER_QUERY_UNSUPPORTED), (S.MISSING_GROUNDING, R.PRODUCER_REQUIRES_REVIEW),
        (S.MISSING_GROUNDING, R.PRODUCER_NOT_INSTALLED), (S.MISSING_GROUNDING, R.PRODUCER_MISSING_QUERY_ARGUMENT),
        (S.UNAUTHORIZED, R.PRODUCER_NOT_AUTHORIZED), (S.CONFLICTING, R.PRODUCER_CONFLICTING_VALUES),
    }),
    K.CAPABILITY: frozenset({
        (S.SATISFIED, R.CAPABILITY_AVAILABLE), (S.UNAVAILABLE, R.CAPABILITY_UNAVAILABLE),
        (S.UNAVAILABLE, R.CAPABILITY_UNREGISTERED),
    }),
    K.AUTHORIZATION: frozenset({
        (S.SATISFIED, R.AUTHORIZATION_GRANTED), (S.UNAUTHORIZED, R.AUTHORIZATION_MISSING),
    }),
    K.CONSENT: frozenset({
        (S.SATISFIED, R.CONSENT_GRANTED), (S.MISSING_USER_INPUT, R.CONSENT_MISSING),
    }),
    K.SAFETY: frozenset({
        (S.SATISFIED, R.SAFETY_ALLOWED),
        (S.PROHIBITED, R.SAFETY_BLOCKS_COMMITMENTS), (S.PROHIBITED, R.SAFETY_BLOCKS_RESPONSE),
        (S.UNAVAILABLE, R.SAFETY_DECISION_MISSING), (S.UNAVAILABLE, R.SAFETY_DECISION_UNKNOWN),
        (S.UNAVAILABLE, R.SAFETY_DECISION_UNAVAILABLE), (S.UNAVAILABLE, R.SAFETY_POLICY_VERSION_UNSUPPORTED),
    }),
    K.TOOL_OUTCOME: frozenset({
        (S.SATISFIED, R.TOOL_OUTCOME_FINAL), (S.TEMPORARY_FAILURE, R.TOOL_OUTCOME_FAILED),
        (S.UNAVAILABLE, R.TOOL_OUTCOME_UNAVAILABLE),
    }),
    K.ARTIFACT_TARGET: frozenset({
        (S.SATISFIED, R.ARTIFACT_TARGET_RESOLVED), (S.SATISFIED, R.DIALOGUE_TARGET_RESOLVED),
        (S.MISSING_USER_INPUT, R.ARTIFACT_TARGET_AMBIGUOUS), (S.MISSING_USER_INPUT, R.ARTIFACT_TARGET_NOT_FOUND),
    }),
    K.REQUESTED_CHANGE: frozenset({(S.SATISFIED, R.CHANGE_STATED), (S.MISSING_USER_INPUT, R.CHANGE_UNSTATED)}),
    K.REQUESTED_OUTCOME: frozenset({(S.SATISFIED, R.OUTCOME_CLEAR), (S.MISSING_USER_INPUT, R.OUTCOME_AMBIGUOUS)}),
    K.INQUIRY_LINK: frozenset({(S.SATISFIED, R.INQUIRY_OPEN), (S.UNAVAILABLE, R.INQUIRY_NOT_OPEN)}),
    K.DOMAIN_CONTEXT: frozenset({(S.SATISFIED, R.CONTEXT_KNOWN), (S.MISSING_USER_INPUT, R.CONTEXT_UNKNOWN)}),
}
# Kinds whose resolutions may be non-blocking (quality or curiosity); every
# other kind is a blocking prerequisite by construction.
NON_BLOCKING_KINDS = frozenset({K.DOMAIN_CONTEXT})
if set(ALLOWED_OUTCOMES) != set(RequirementKind):
    raise RuntimeError("every requirement kind declares its allowed outcomes")

ARGUMENT_KINDS = frozenset({K.REQUIRED_ARGUMENT, K.DIALOGUE_REFERENT})
# The producer outcome a trusted-content resolution must record for each
# producer status (a content resolution never invents its own status).
CONTENT_STATUS_BY_PRODUCER: Mapping[ContentProducerStatus, RequirementResolutionStatus] = {
    ContentProducerStatus.KNOWN: S.SATISFIED,
    ContentProducerStatus.UNAVAILABLE: S.MISSING_GROUNDING,
    ContentProducerStatus.STALE: S.MISSING_GROUNDING,
    ContentProducerStatus.UNSUPPORTED: S.MISSING_GROUNDING,
    ContentProducerStatus.UNAUTHORIZED: S.UNAUTHORIZED,
    ContentProducerStatus.CONFLICTING: S.CONFLICTING,
}
GAP_PRODUCER_STATUSES = frozenset({
    ContentProducerStatus.UNAVAILABLE, ContentProducerStatus.STALE, ContentProducerStatus.UNSUPPORTED,
})


class RequirementResolutionVersions(FrozenModel):
    """Every version a requirement-resolution outcome depends on."""

    contract: str = REQUIREMENT_RESOLUTION_CONTRACT_VERSION
    engine: str = REQUIREMENT_RESOLUTION_ENGINE_VERSION
    manifest: str = REQUIREMENT_RESOLUTION_MANIFEST_VERSION
    capability_map: str = REQUIREMENT_CAPABILITY_MAP_VERSION
    projection: str = REQUIREMENT_ACTION_PROJECTION_VERSION
    content_producer: str = REQUIREMENT_RESOLUTION_CONTENT_PRODUCER
    # The trusted-producer query manifest (``producers_v0``) the queries used.
    producer_manifest: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Inputs captured at the turn boundary
# ---------------------------------------------------------------------------

class RequirementResolutionInputs(FrozenModel):
    """Trusted turn-boundary facts the resolution stage may consume.

    ``turn_time`` is the server clock at capture (never the model's or the
    client's claim).  ``time_zone`` is the IANA zone the runtime date is
    reported in; ``None`` means no trusted user zone was captured and the
    date is reported explicitly in UTC.  ``user_ref`` scopes user-owned
    producer records; without it every user-owned record is withheld.
    """

    schema_version: Literal[1] = 1
    turn_time: str | None = None
    time_zone: str | None = None
    user_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> RequirementResolutionInputs:
        if self.turn_time is not None:
            from datetime import datetime

            parsed = datetime.fromisoformat(self.turn_time)
            if parsed.tzinfo is None:
                raise ValueError("the turn time must carry a timezone")
        if self.time_zone is not None:
            from zoneinfo import ZoneInfo

            _nonblank(self.time_zone, "time zone")
            ZoneInfo(self.time_zone)  # an unknown zone is rejected, never guessed
        if self.user_ref is not None:
            _nonblank(self.user_ref, "user ref")
        return self


# ---------------------------------------------------------------------------
# Resolution records
# ---------------------------------------------------------------------------

class RequirementResolution(FrozenModel):
    """The readiness of one declared prerequisite of one current request goal."""

    resolution_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    goal_requirement_ref: str | None = None
    frame_ref: str = Field(min_length=1)

    kind: RequirementKind
    key: str = Field(min_length=1)
    argument_name: str | None = None
    status: RequirementResolutionStatus
    blocking: bool
    # The manifest precedence tier this prerequisite was evaluated in; the
    # lowest unsatisfied tier of a goal is the one that blocks it.
    tier: int = Field(ge=0, le=9)
    reason_code: RequirementReason

    subject_refs: tuple[str, ...] = ()
    value_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    capability_refs: tuple[str, ...] = ()

    resolver_id: str = Field(min_length=1)
    resolver_version: str = Field(min_length=1)
    # Contract v0.2; omitted from serialization when ``blocking`` so v0.1
    # snapshots (which had no criticality: everything blocked) keep their bytes.
    criticality: RequirementCriticality = RequirementCriticality.BLOCKING

    @model_serializer(mode="wrap")
    def _omit_default_criticality(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("criticality") in {None, RequirementCriticality.BLOCKING,
                                                                   RequirementCriticality.BLOCKING.value}:
            data.pop("criticality", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> RequirementResolution:
        if self.criticality != RequirementCriticality.BLOCKING:
            if self.kind not in NON_BLOCKING_KINDS:
                raise ValueError("only a declared non-blocking kind may be quality-improving or optional")
            if self.blocking:
                raise ValueError("a non-blocking prerequisite never blocks")
        for value, label in ((self.resolution_id, "resolution id"), (self.goal_ref, "goal ref"),
                             (self.frame_ref, "frame ref"), (self.resolver_id, "resolver id"),
                             (self.resolver_version, "resolver version")):
            _nonblank(value, label)
        if not _KEY.match(self.key):
            raise ValueError("a requirement key is a registered dotted key")
        if (self.status, self.reason_code) not in ALLOWED_OUTCOMES[self.kind]:
            raise ValueError("the status and reason are not allowed for this requirement kind")
        if self.status == S.SATISFIED and self.blocking:
            raise ValueError("a satisfied prerequisite never blocks")
        if (self.kind in ARGUMENT_KINDS) != (self.argument_name is not None):
            raise ValueError("argument and referent prerequisites, and only they, name their argument")
        if self.argument_name is not None:
            _nonblank(self.argument_name, "argument name")
            if self.key != f"argument.{self.argument_name}":
                raise ValueError("an argument prerequisite is keyed by its argument")
        for label, values in (("subject refs", self.subject_refs), ("value refs", self.value_refs),
                              ("evidence refs", self.evidence_refs), ("policy refs", self.policy_refs),
                              ("capability refs", self.capability_refs)):
            _canonical_refs(values, label)
        if self.kind == K.SAFETY and self.status == S.PROHIBITED and not self.policy_refs:
            raise ValueError("a prohibited request cites the trusted safety decision")
        if self.kind == K.CAPABILITY and not self.capability_refs:
            raise ValueError("a capability prerequisite cites the capability it evaluated")
        return self

    @property
    def sort_key(self) -> tuple[str, str, int, str, str]:
        return (self.goal_ref, self.frame_ref, list(RequirementKind).index(self.kind), self.key, self.resolution_id)


class RequirementContentProvenance(FrozenModel):
    """Value-free producer provenance for one trusted-content prerequisite.

    Identities, versions, and the typed outcome only: the produced values stay
    with the producer result in the pipeline trace, never in this record.
    """

    resolution_ref: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    query_kind: str = Field(min_length=1)
    result_ref: str = Field(min_length=1)
    producer_status: ContentProducerStatus
    producer_reason: str | None = None
    authority_class: str | None = None
    producer_id: str | None = None
    producer_version: str | None = None
    producer_manifest_version: str = Field(min_length=1)
    fact_refs: tuple[str, ...] = ()
    withheld_fact_refs: tuple[str, ...] = ()
    conflicting_fact_refs: tuple[str, ...] = ()
    gap_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> RequirementContentProvenance:
        if not _KEY.match(self.query_kind):
            raise ValueError("a query kind is a dotted key")
        for label, values in (("fact refs", self.fact_refs), ("withheld fact refs", self.withheld_fact_refs),
                              ("conflicting fact refs", self.conflicting_fact_refs)):
            _canonical_refs(values, label)
        known = self.producer_status == ContentProducerStatus.KNOWN
        if known != bool(self.fact_refs) or known == (self.producer_reason is not None):
            raise ValueError("only a known outcome, and every one, carries facts and no reason")
        if (self.gap_ref is not None) != (self.producer_status in GAP_PRODUCER_STATUSES):
            raise ValueError("exactly an unavailable, stale, or unsupported answer records a knowledge gap")
        return self


class GoalReadiness(FrozenModel):
    """The readiness projection of one current request goal (never its terminal state)."""

    goal_ref: str = Field(min_length=1)
    readiness: Literal["ready", "blocked"]
    blocking_tier: int | None = Field(default=None, ge=0, le=9)
    blocking_resolution_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> GoalReadiness:
        _canonical_refs(self.blocking_resolution_refs, "blocking resolution refs")
        blocked = self.readiness == "blocked"
        if blocked != bool(self.blocking_resolution_refs) or blocked != (self.blocking_tier is not None):
            raise ValueError("a blocked goal, and only it, names its blocking tier and resolutions")
        return self


def requirement_snapshot_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON of every snapshot field except the digest itself."""

    fields = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class RequirementResolutionSnapshot(FrozenModel):
    """Every prerequisite resolution of one turn, in canonical order, with its digest."""

    snapshot_id: str = Field(min_length=1)
    contract_version: Literal[
        "requirement-resolution-contract-v0.1", "requirement-resolution-contract-v0.2",
    ] = REQUIREMENT_RESOLUTION_CONTRACT_VERSION
    engine_version: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    producer_manifest_version: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    interpretation_trace_ref: str = Field(min_length=1)
    goal_snapshot_ref: str = Field(min_length=1)
    runtime_snapshot_ref: str = Field(min_length=1)
    goal_refs: tuple[str, ...] = ()
    resolutions: tuple[RequirementResolution, ...] = ()
    content_queries: tuple[RequirementContentProvenance, ...] = ()
    goal_readiness: tuple[GoalReadiness, ...] = ()
    digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> RequirementResolutionSnapshot:
        _canonical_refs(self.goal_refs, "goal refs")
        ids = tuple(item.resolution_id for item in self.resolutions)
        if len(ids) != len(set(ids)):
            raise ValueError("resolution ids must be unique")
        keys = tuple(item.sort_key for item in self.resolutions)
        if keys != tuple(sorted(keys)):
            raise ValueError("resolutions must be in canonical order")
        declared = [(item.goal_ref, item.frame_ref, item.key) for item in self.resolutions]
        if len(declared) != len(set(declared)):
            raise ValueError("one resolution per declared prerequisite key")
        goals = set(self.goal_refs)
        for item in self.resolutions:
            if (item.cognitive_turn_id, item.conversation_ref) != (self.cognitive_turn_id, self.conversation_ref):
                raise ValueError("a resolution belongs to a different turn or conversation")
            if item.goal_ref not in goals:
                raise ValueError("a resolution cites a goal outside the snapshot's request goals")
        by_id = {item.resolution_id: item for item in self.resolutions}
        content = {ref for ref, item in by_id.items() if item.kind == K.TRUSTED_CONTENT}
        query_refs = tuple(item.resolution_ref for item in self.content_queries)
        if len(query_refs) != len(set(query_refs)) or set(query_refs) != content:
            raise ValueError("every trusted-content resolution, and only one, records its producer query")
        if query_refs != tuple(sorted(query_refs)):
            raise ValueError("content queries must be in canonical order")
        for record in self.content_queries:
            resolution = by_id[record.resolution_ref]
            if CONTENT_STATUS_BY_PRODUCER[record.producer_status] != resolution.status:
                raise ValueError("a trusted-content resolution misstates its producer outcome")
            if record.query_id not in resolution.evidence_refs:
                raise ValueError("a trusted-content resolution cites its producer query")
        readiness = tuple(item.goal_ref for item in self.goal_readiness)
        if readiness != self.goal_refs:
            raise ValueError("every request goal, and only they, carries a readiness projection")
        if self.contract_version in LEGACY_REQUIREMENT_RESOLUTION_CONTRACT_VERSIONS and any(
            item.criticality != RequirementCriticality.BLOCKING or item.kind in _V02_KINDS for item in self.resolutions
        ):
            raise ValueError("a v0.1 snapshot cannot carry contract v0.2 records")
        for projection in self.goal_readiness:
            own = [item for item in self.resolutions if item.goal_ref == projection.goal_ref]
            blocking = tuple(sorted(item.resolution_id for item in own if item.blocking))
            # Only blocking-criticality prerequisites gate readiness (v0.2).
            unsatisfied = [item for item in own if item.status != S.SATISFIED
                           and item.criticality == RequirementCriticality.BLOCKING]
            tier = min((item.tier for item in unsatisfied), default=None)
            expected = tuple(sorted(item.resolution_id for item in unsatisfied if item.tier == tier))
            if blocking != expected or projection.blocking_resolution_refs != blocking:
                raise ValueError("only a goal's lowest unsatisfied tier blocks it")
            if projection.blocking_tier != tier:
                raise ValueError("the readiness projection names a different blocking tier")
        if not _DIGEST.match(self.digest) or self.digest != requirement_snapshot_digest(self.model_dump(mode="json")):
            raise ValueError("the requirement snapshot digest does not match its contents")
        return self

    @classmethod
    def sealed(cls, **fields: Any) -> RequirementResolutionSnapshot:
        """Build a snapshot whose digest is computed from its canonical contents."""

        draft = cls.model_construct(**{**fields, "digest": "0" * 64})
        return cls(**{**fields, "digest": requirement_snapshot_digest(draft.model_dump(mode="json"))})

    def resolution(self, resolution_id: str) -> RequirementResolution | None:
        return next((item for item in self.resolutions if item.resolution_id == resolution_id), None)


# ---------------------------------------------------------------------------
# Resolution -> Action handoff
# ---------------------------------------------------------------------------

class RequirementActionBinding(FrozenModel):
    """Which Action index objects one resolution was projected into.

    ``target_refs`` are the exact index references a move must target to
    address a blocking resolution (empty when it does not block or nothing
    addressable exists, e.g. withheld content).  ``object_refs``,
    ``descriptor_refs``, and ``content_refs`` are what the projection created.
    """

    resolution_ref: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    blocking: bool
    target_refs: tuple[str, ...] = ()
    object_refs: tuple[str, ...] = ()
    descriptor_refs: tuple[str, ...] = ()
    content_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> RequirementActionBinding:
        for label, values in (("target refs", self.target_refs), ("object refs", self.object_refs),
                              ("descriptor refs", self.descriptor_refs), ("content refs", self.content_refs)):
            _canonical_refs(values, label)
        if self.target_refs and not self.blocking:
            raise ValueError("only a blocking resolution names blocker targets")
        return self


class RequirementActionHandoff(FrozenModel):
    """The typed readiness facts Action Selection consumes for one turn."""

    projection_version: Literal[
        "requirement-action-projection-v0.1", "requirement-action-projection-v0.2",
    ] = REQUIREMENT_ACTION_PROJECTION_VERSION
    snapshot: RequirementResolutionSnapshot
    bindings: tuple[RequirementActionBinding, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> RequirementActionHandoff:
        refs = tuple(item.resolution_ref for item in self.bindings)
        if refs != tuple(item.resolution_id for item in self.snapshot.resolutions):
            raise ValueError("every resolution, in snapshot order, has exactly one action binding")
        for binding, resolution in zip(self.bindings, self.snapshot.resolutions):
            if (binding.goal_ref, binding.blocking) != (resolution.goal_ref, resolution.blocking):
                raise ValueError("an action binding misstates its resolution")
        return self

    def binding(self, resolution_ref: str) -> RequirementActionBinding | None:
        return next((item for item in self.bindings if item.resolution_ref == resolution_ref), None)


# ---------------------------------------------------------------------------
# Cross-object validation
# ---------------------------------------------------------------------------

def permitted_references(
    *, interpretation, goal_snapshot, runtime_refs: Iterable[str], dialogue_refs: Iterable[str] = (),
    snapshot: RequirementResolutionSnapshot | None = None, workspace_refs: Iterable[str] = (),
) -> frozenset[str]:
    """Every reference a resolution of this turn may cite.

    Current-turn Interpretation identities (frames, every frame argument,
    unknowns, evidence, hypotheses), the current Goal snapshot's records and
    requirements, trusted runtime references of this turn's snapshot, the
    authorized dialogue context of this turn, and the snapshot's own producer
    queries and results.  Nothing else -- in particular no raw text.
    """

    refs: set[str] = {interpretation.interpretation_trace_id}
    for frame in interpretation.semantic_frames:
        refs.add(frame.frame_id)
        refs.update(f"{frame.frame_id}#{name}" for name in (*frame.arguments, *frame.unresolved_arguments))
    refs.update(item.unknown_id for item in interpretation.unknowns)
    refs.update(item.evidence_id for item in interpretation.evidence)
    refs.update(item.hypothesis_id for item in interpretation.hypotheses)
    refs.update(item.contradiction_id for item in interpretation.contradictions)
    refs.add(goal_snapshot.goal_snapshot_id)
    for record in goal_snapshot.records:
        refs.add(record.goal_id)
        refs.update(requirement.requirement_id for requirement in record.requirements)
    refs.update(runtime_refs)
    refs.update(dialogue_refs)
    refs.update(workspace_refs)
    if snapshot is not None:
        for record in snapshot.content_queries:
            refs.update((record.query_id, record.result_ref, *record.fact_refs))
            if record.gap_ref is not None:
                refs.add(record.gap_ref)
    return frozenset(refs)


def validate_requirement_snapshot(
    snapshot: RequirementResolutionSnapshot, *, interpretation, goal_snapshot, runtime_refs: Iterable[str],
    dialogue_refs: Iterable[str] = (), workspace_refs: Iterable[str] = (),
) -> None:
    """Lineage and reference integrity of a snapshot against the exact turn it describes."""

    if (snapshot.cognitive_turn_id, snapshot.conversation_ref) != (
        interpretation.cognitive_turn_id, interpretation.conversation_ref,
    ):
        raise ValueError("the requirement snapshot belongs to a different turn or conversation")
    if snapshot.interpretation_trace_ref != interpretation.interpretation_trace_id:
        raise ValueError("the requirement snapshot cites a different interpretation")
    if (snapshot.goal_snapshot_ref, snapshot.runtime_snapshot_ref) != (
        goal_snapshot.goal_snapshot_id, goal_snapshot.runtime_snapshot_ref,
    ):
        raise ValueError("the requirement snapshot cites a different goal or runtime snapshot")
    records = {record.goal_id: record for record in goal_snapshot.records}
    frames = {frame.frame_id for frame in interpretation.semantic_frames}
    for goal_ref in snapshot.goal_refs:
        record = records.get(goal_ref)
        if record is None or record.created_turn_ref != snapshot.cognitive_turn_id:
            raise ValueError("a requirement snapshot covers only goals formed this turn")
        if record.status.value != "active" or record.owner.value not in {"user", "shared"}:
            raise ValueError("a requirement snapshot covers only active user or shared request goals")
    permitted = permitted_references(interpretation=interpretation, goal_snapshot=goal_snapshot,
                                     runtime_refs=runtime_refs, dialogue_refs=dialogue_refs, snapshot=snapshot,
                                     workspace_refs=workspace_refs)
    for item in snapshot.resolutions:
        if item.frame_ref not in frames:
            raise ValueError("a resolution cites a frame outside this turn's interpretation")
        requirement_ids = {requirement.requirement_id for requirement in records[item.goal_ref].requirements}
        if item.goal_requirement_ref is not None and item.goal_requirement_ref not in requirement_ids:
            raise ValueError("a resolution cites a requirement of a different goal")
        for ref in (*item.subject_refs, *item.value_refs, *item.evidence_refs, *item.policy_refs):
            if ref not in permitted:
                raise ValueError("a resolution cites a reference outside the turn's permitted lineage")


_V02_KINDS = frozenset({K.ARTIFACT_TARGET, K.REQUESTED_CHANGE, K.REQUESTED_OUTCOME, K.INQUIRY_LINK, K.DOMAIN_CONTEXT})


__all__ = [
    "LEGACY_REQUIREMENT_RESOLUTION_CONTRACT_VERSIONS", "NON_BLOCKING_KINDS", "RequirementCriticality",
    "ALLOWED_OUTCOMES", "ARGUMENT_KINDS", "CONTENT_STATUS_BY_PRODUCER", "ContentProducerStatus",
    "GAP_PRODUCER_STATUSES", "GoalReadiness",
    "REQUIREMENT_ACTION_PROJECTION_VERSION", "REQUIREMENT_CAPABILITY_MAP_VERSION",
    "REQUIREMENT_RESOLUTION_CONTENT_PRODUCER", "REQUIREMENT_RESOLUTION_CONTRACT_VERSION",
    "REQUIREMENT_RESOLUTION_ENGINE_VERSION", "REQUIREMENT_RESOLUTION_MANIFEST_VERSION",
    "RequirementActionBinding", "RequirementActionHandoff", "RequirementContentProvenance", "RequirementKind",
    "RequirementReason", "RequirementResolution", "RequirementResolutionInputs", "RequirementResolutionSnapshot",
    "RequirementResolutionStatus", "RequirementResolutionVersions", "permitted_references", "requirement_snapshot_digest",
    "validate_requirement_snapshot",
]
