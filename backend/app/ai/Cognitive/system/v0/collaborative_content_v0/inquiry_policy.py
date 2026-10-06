"""Deterministic inquiry timing, suppression, and personality policy (``inquiry-timing-policy-v0.1``).

A proposal may *suggest* questions; code decides whether and when one is
asked (Section 11.2).  Requirement resolution owns every blocking question,
so a proposed inquiry is at most quality-improving and is asked after the
primary contribution (or at a checkpoint before a consequential boundary).
It is suppressed when it is unrelated, too sensitive for its expected value,
already answered, recently asked, unlikely to change behavior, below the
curiosity threshold, or beyond the per-turn question budget.

Personality may only move an already valid optional inquiry's score
(Section 11.3): it never creates an inquiry, a blocker, or a sensitive
question, and never overrides a suppression.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..contracts.collaboration import INQUIRY_POLICY_VERSION, InquirySuppressionReason as S
from ..contracts.workspace import (
    InquiryCriticality, InquiryExpectedUse, InquiryPurpose, InquirySensitivity, InquiryTargetKind, InquiryTiming,
    OpenInquiryRecord, OpenInquiryStatus,
)

POLICY_VERSION = INQUIRY_POLICY_VERSION
BASE_SCORES = {InquiryCriticality.QUALITY_IMPROVING: Decimal("0.700000"),
               InquiryCriticality.OPTIONAL_CURIOSITY: Decimal("0.400000")}
CURIOSITY_WEIGHT = Decimal("0.400000")   # score shift = (curiosity - 0.5) * weight, so at most +/- 0.2
SELECTION_THRESHOLD = Decimal("0.500000")
DEFAULT_BUDGET = 1
HIGH_CURIOSITY_BUDGET = 2
HIGH_CURIOSITY = Decimal("0.850000")
NEUTRAL_CURIOSITY = Decimal("0.500000")
# A health-sensitive question is worth asking only to keep a safety-relevant
# requirement correct, and only about a requirement the user stated.
HEALTH_PURPOSES = frozenset({InquiryPurpose.RESOLVE_SAFETY_RELEVANT_CONTEXT})


def _q(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001')):f}"


@dataclass(frozen=True)
class InquiryDraft:
    """One structurally valid candidate, before policy."""

    inquiry_id: str
    target_kind: InquiryTargetKind
    target_ref: str
    target_known: bool
    purpose: InquiryPurpose
    expected_use: InquiryExpectedUse
    criticality: InquiryCriticality
    sensitivity: InquirySensitivity
    answer_options: tuple[str, ...]
    reason_already_known: bool = False


@dataclass(frozen=True)
class InquiryDecision:
    timing: InquiryTiming
    selected: bool
    suppression: S | None
    score: str
    rule_ids: tuple[str, ...]


def timing_for(purpose: InquiryPurpose) -> InquiryTiming:
    if purpose == InquiryPurpose.CONFIRM_CONSEQUENTIAL_EFFECT:
        return InquiryTiming.CHECKPOINT
    return InquiryTiming.AFTER_PRIMARY


def decide(drafts: tuple[InquiryDraft, ...], *, curiosity: float | None,
           history: tuple[OpenInquiryRecord, ...]) -> dict[str, InquiryDecision]:
    """Policy decisions for every draft, keyed by inquiry id (order-independent)."""

    affinity = Decimal(str(curiosity)) if curiosity is not None else NEUTRAL_CURIOSITY
    shift = (affinity - NEUTRAL_CURIOSITY) * CURIOSITY_WEIGHT
    budget = HIGH_CURIOSITY_BUDGET if affinity >= HIGH_CURIOSITY else DEFAULT_BUDGET
    decisions: dict[str, InquiryDecision] = {}
    eligible: list[tuple[Decimal, str]] = []
    for draft in sorted(drafts, key=lambda item: item.inquiry_id):
        rules = [f"inquiry.timing.{timing_for(draft.purpose).value}.v1"]
        base = BASE_SCORES[draft.criticality]
        score = base + (shift if draft.criticality == InquiryCriticality.OPTIONAL_CURIOSITY else shift / 2)
        suppression = _suppression(draft, history)
        if suppression is None and score < SELECTION_THRESHOLD:
            suppression = S.BELOW_CURIOSITY_THRESHOLD
        if suppression is not None:
            rules.append(f"inquiry.suppress.{suppression.value}.v1")
        else:
            rules.append("inquiry.personality-scored.v1")
            eligible.append((score, draft.inquiry_id))
        decisions[draft.inquiry_id] = InquiryDecision(
            timing=timing_for(draft.purpose), selected=False, suppression=suppression, score=_q(score),
            rule_ids=tuple(rules),
        )
    ranked = sorted(eligible, key=lambda item: (-item[0], item[1]))
    for position, (_, inquiry_id) in enumerate(ranked):
        decision = decisions[inquiry_id]
        if position < budget:
            decisions[inquiry_id] = InquiryDecision(decision.timing, True, None, decision.score,
                                                    (*decision.rule_ids, "inquiry.select.v1"))
        else:
            decisions[inquiry_id] = InquiryDecision(
                decision.timing, False, S.QUESTION_BUDGET_EXHAUSTED, decision.score,
                (*decision.rule_ids, f"inquiry.suppress.{S.QUESTION_BUDGET_EXHAUSTED.value}.v1"),
            )
    return decisions


def _suppression(draft: InquiryDraft, history: tuple[OpenInquiryRecord, ...]) -> S | None:
    if not draft.target_known:
        return S.UNRELATED_TARGET
    if draft.sensitivity == InquirySensitivity.HEALTH and (
        draft.purpose not in HEALTH_PURPOSES or draft.target_kind != InquiryTargetKind.ARTIFACT_CONSTRAINT
    ):
        return S.SENSITIVITY_EXCEEDS_VALUE
    same = [item for item in history if item.target_ref == draft.target_ref and item.purpose == draft.purpose]
    if any(item.status in {OpenInquiryStatus.ANSWERED, OpenInquiryStatus.DECLINED} for item in same):
        return S.ALREADY_ANSWERED
    if any(item.status in {OpenInquiryStatus.OPEN, OpenInquiryStatus.EXPIRED} for item in same):
        return S.RECENTLY_ASKED
    if draft.expected_use == InquiryExpectedUse.SELECT_OPTION and len(draft.answer_options) < 2:
        return S.UNLIKELY_TO_CHANGE_BEHAVIOR
    if draft.expected_use == InquiryExpectedUse.SET_CONSTRAINT_REASON and draft.reason_already_known:
        return S.UNLIKELY_TO_CHANGE_BEHAVIOR
    return None


__all__ = [
    "BASE_SCORES", "CURIOSITY_WEIGHT", "DEFAULT_BUDGET", "HIGH_CURIOSITY_BUDGET", "InquiryDecision", "InquiryDraft",
    "POLICY_VERSION", "SELECTION_THRESHOLD", "decide", "timing_for",
]
