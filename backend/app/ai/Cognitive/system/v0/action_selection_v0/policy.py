"""``action-policy-v0.1``: the accepted marginal-utility formula and tie policy.

All arithmetic uses :class:`decimal.Decimal` built from canonical six-place
strings.  Scores within ``ACTION_SCORE_TOLERANCE`` are tied.
"""

from __future__ import annotations

from decimal import Decimal

from ..contracts.actions import (
    ACTION_POLICY_VERSION,
    COST_WEIGHTS,
    MULTIPLIER_WEIGHTS,
    NEED_WEIGHTS,
    SHARED_MOVE_OVERHEAD,
    ActionScoreBreakdown,
    ActionScoreContribution,
    ActionTieResolution,
    GroundedActionCandidate,
    ScoredCandidate,
    TieBreakField,
    canonical_decimal,
    decimal_from,
    deterministic_selection,
)
from ..contracts.actions import (
    FeatureKey as F,
)

POLICY_VERSION = ACTION_POLICY_VERSION
MULTIPLIER_BOUNDS = (Decimal("0.00"), Decimal("1.75"))
STOP_SCORE = "0.000000"


def _contribution(term: str, key: F | None, base: str, weight: str) -> ActionScoreContribution:
    weight_text = canonical_decimal(Decimal(weight))
    return ActionScoreContribution(
        term=term, feature_key=key, base_value=base, weight=weight_text,
        signed_contribution=canonical_decimal(decimal_from(base) * decimal_from(weight_text)),
    )


def score_candidate(candidate: GroundedActionCandidate, features) -> ScoredCandidate:
    values = {item.feature_key: item.value for item in features}
    need = tuple(_contribution(f"need.{key.value}", key, values[key], weight) for key, weight in NEED_WEIGHTS)
    remaining = canonical_decimal(sum((decimal_from(item.signed_contribution) for item in need), Decimal(0)))
    multiplier_terms = tuple(_contribution(f"multiplier.{key.value}", key, values[key], weight) for key, weight in MULTIPLIER_WEIGHTS)
    unclamped = Decimal(1) + sum((decimal_from(item.signed_contribution) for item in multiplier_terms), Decimal(0))
    clamped = min(MULTIPLIER_BOUNDS[1], max(MULTIPLIER_BOUNDS[0], unclamped))
    fit = values[F.SEMANTIC_FIT]
    positive = canonical_decimal(decimal_from(fit) * decimal_from(remaining) * Decimal(canonical_decimal(clamped)))
    costs = tuple(_contribution(f"cost.{key.value}", key, values[key], weight) for key, weight in COST_WEIGHTS)
    total = decimal_from(positive) + sum((decimal_from(item.signed_contribution) for item in costs), Decimal(0)) + decimal_from(SHARED_MOVE_OVERHEAD)
    breakdown = ActionScoreBreakdown(
        candidate_ref=candidate.candidate_id, policy_version=POLICY_VERSION, need_contributions=need,
        remaining_need=remaining, multiplier_contributions=multiplier_terms,
        context_multiplier_unclamped=canonical_decimal(unclamped), context_multiplier=canonical_decimal(clamped),
        semantic_fit=fit, positive_marginal_value=positive, cost_contributions=costs,
        shared_move_overhead=SHARED_MOVE_OVERHEAD, score=canonical_decimal(total),
    )
    return ScoredCandidate(candidate_ref=candidate.candidate_id, features=tuple(features), score=breakdown)


def resolve_conflict(scored: tuple[ScoredCandidate, ...], candidates: dict[str, GroundedActionCandidate]) -> tuple[ScoredCandidate | None, ActionTieResolution | None]:
    """Deterministic argmax against the zero ``STOP`` baseline (the contract's single rule)."""

    identities = {ref: candidate.lexical_identity for ref, candidate in candidates.items()}
    winner_ref, tie_set, resolved_by = deterministic_selection(scored, identities)
    if winner_ref is None:
        return None, None
    winner = next(item for item in scored if item.candidate_ref == winner_ref)
    if not tie_set:
        return winner, None
    return winner, ActionTieResolution(
        tied_candidate_refs=tie_set, resolved_by=resolved_by, selected_candidate_ref=winner_ref,
        preference_evidence=resolved_by != TieBreakField.LEXICAL_IDENTITY,
    )


__all__ = ["COST_WEIGHTS", "MULTIPLIER_WEIGHTS", "NEED_WEIGHTS", "POLICY_VERSION", "SHARED_MOVE_OVERHEAD",
           "STOP_SCORE", "resolve_conflict", "score_candidate"]
