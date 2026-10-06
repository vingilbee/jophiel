"""The closed, versioned application safety decision.

Application safety is decided outside the cognitive stages by a trusted
application policy.  Cognition only consumes the decision: it never produces,
weakens, or reinterprets one.  This module owns the one typed shape of that
decision and the one pure function that turns it into an action restriction.

Fail-closed rule: operational commitments (accepting, promising, offering, or
proposing to carry something out) require an explicit ``allow`` from a
supported policy version.  A missing decision, an ``unknown`` or
``unavailable`` disposition, or a decision produced under an unsupported
policy version restricts commitments.  A ``block_response`` restricts every
conversational move and is honoured under any policy version, because a block
is never weakened by the consumer failing to recognise its producer.

Unrecognised dispositions, provenance kinds, and decision-schema versions are
not representable: they are rejected when the runtime snapshot is validated.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .interpretation import FrozenModel

APPLICATION_SAFETY_DECISION_SCHEMA_VERSION = "application-safety-decision-v0.1"
APPLICATION_SAFETY_POLICY_VERSION = "application-safety-policy-v0.1"
SUPPORTED_SAFETY_POLICY_VERSIONS: frozenset[str] = frozenset({APPLICATION_SAFETY_POLICY_VERSION})


class SafetyDisposition(StrEnum):
    ALLOW = "allow"
    BLOCK_COMMITMENTS = "block_commitments"
    BLOCK_RESPONSE = "block_response"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class SafetyDecisionProvenance(StrEnum):
    """Who produced the decision.

    ``application_safety_policy`` is the trusted application policy evaluator.
    ``runtime_unavailable`` records that the runtime could not obtain a decision;
    it may only carry the ``unavailable`` disposition.
    """

    APPLICATION_SAFETY_POLICY = "application_safety_policy"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"


class SafetyRestriction(StrEnum):
    NONE = "none"
    COMMITMENTS = "commitments"
    RESPONSE = "response"


class SafetyRestrictionReason(StrEnum):
    """Stable reason codes for the restriction a decision produces."""

    ALLOWED = "allowed"
    BLOCKS_COMMITMENTS = "blocks_commitments"
    BLOCKS_RESPONSE = "blocks_response"
    DECISION_MISSING = "decision_missing"
    DECISION_UNKNOWN = "decision_unknown"
    DECISION_UNAVAILABLE = "decision_unavailable"
    POLICY_VERSION_UNSUPPORTED = "policy_version_unsupported"


_DECIDING = frozenset({SafetyDisposition.ALLOW, SafetyDisposition.BLOCK_COMMITMENTS, SafetyDisposition.BLOCK_RESPONSE})


class ApplicationSafetyDecision(FrozenModel):
    schema_version: Literal["application-safety-decision-v0.1"] = APPLICATION_SAFETY_DECISION_SCHEMA_VERSION
    decision_id: str = Field(min_length=1)
    # Deliberately an open string: an unsupported producer version must be
    # representable so the consumer can fail closed on it rather than crash.
    policy_version: str = Field(min_length=1)
    disposition: SafetyDisposition
    provenance: SafetyDecisionProvenance
    producer_ref: str = Field(min_length=1)
    runtime_snapshot_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ApplicationSafetyDecision:
        for value in (self.decision_id, self.policy_version, self.producer_ref, self.runtime_snapshot_ref):
            if not value.strip():
                raise ValueError("safety decision identifiers must not be blank")
        if self.disposition in _DECIDING and self.provenance != SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY:
            raise ValueError("only the trusted application safety policy can allow or block")
        if self.provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE and self.disposition != SafetyDisposition.UNAVAILABLE:
            raise ValueError("a runtime-unavailable record can only carry the unavailable disposition")
        return self


def safety_restriction(decision: ApplicationSafetyDecision | None) -> tuple[SafetyRestriction, SafetyRestrictionReason]:
    """The single pure mapping from a decision (or its absence) to a restriction."""

    if decision is None:
        return SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_MISSING
    if decision.disposition == SafetyDisposition.BLOCK_RESPONSE:
        return SafetyRestriction.RESPONSE, SafetyRestrictionReason.BLOCKS_RESPONSE
    if decision.policy_version not in SUPPORTED_SAFETY_POLICY_VERSIONS:
        return SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED
    if decision.disposition == SafetyDisposition.BLOCK_COMMITMENTS:
        return SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.BLOCKS_COMMITMENTS
    if decision.disposition == SafetyDisposition.UNKNOWN:
        return SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_UNKNOWN
    if decision.disposition == SafetyDisposition.UNAVAILABLE:
        return SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_UNAVAILABLE
    return SafetyRestriction.NONE, SafetyRestrictionReason.ALLOWED


def legacy_safety_label(decision: ApplicationSafetyDecision | None) -> str | None:
    """Project the typed decision onto the pre-typed upstream runtime string.

    Interpretation, Goal, and Appraisal still consume ``safety_decision: str``
    and only distinguish ``"blocked"``.  Both blocking dispositions project to
    ``"blocked"``, ``unknown`` to ``"unknown"``, and everything that does not
    block (including an absent decision) to ``None``.  The projection is
    deterministic, so upstream and action inputs cannot disagree.
    """

    if decision is None:
        return None
    if decision.disposition in {SafetyDisposition.BLOCK_COMMITMENTS, SafetyDisposition.BLOCK_RESPONSE}:
        return "blocked"
    if decision.disposition == SafetyDisposition.UNKNOWN:
        return "unknown"
    return None


__all__ = [
    "APPLICATION_SAFETY_DECISION_SCHEMA_VERSION", "APPLICATION_SAFETY_POLICY_VERSION", "ApplicationSafetyDecision",
    "SUPPORTED_SAFETY_POLICY_VERSIONS", "SafetyDecisionProvenance", "SafetyDisposition", "SafetyRestriction",
    "SafetyRestrictionReason", "legacy_safety_label", "safety_restriction",
]
