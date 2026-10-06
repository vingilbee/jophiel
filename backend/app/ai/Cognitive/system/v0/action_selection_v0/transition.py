"""Immutable partial-plan states and the fixed V0 transition order.

The transition is bookkeeping over declared planned effects.  It never marks
an unknown known, a contradiction repaired, a goal satisfied, or a user
reassured; every ledger label ends in ``_planned``.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from ..contracts.actions import (
    CountEntry, FeatureKey as F, GroundedActionMove, LedgerEntry, PartialPlanDelta, PartialPlanState,
    PlannedActionEffect, ScoredCandidate, StateLabel, TransitionBasis, TransitionDomain, TransitionGoalLink,
    canonical_decimal, decimal_from,
)
from .features import CandidateAnalysis

ONE = Decimal(1)


def empty_state(plan_id: str) -> PartialPlanState:
    return PartialPlanState.signed(plan_id=plan_id, step_index=0)


def _ledger(values: dict[str, Decimal]) -> tuple[LedgerEntry, ...]:
    return tuple(LedgerEntry(key=key, value=canonical_decimal(values[key])) for key in sorted(values))


def _labels(values: dict[str, str]) -> tuple[StateLabel, ...]:
    return tuple(StateLabel(key=key, state=values[key]) for key in sorted(values))


def _counts(values: dict[str, int]) -> tuple[CountEntry, ...]:
    return tuple(CountEntry(key=key, count=values[key]) for key in sorted(values))


def apply_transition(state: PartialPlanState, analysis: CandidateAnalysis, scored: ScoredCandidate,
                     *, step_index: int, context_ref: str, realization_constraints,
                     selection_policy: str = "argmax") -> tuple[PartialPlanState, GroundedActionMove, tuple[PlannedActionEffect, ...], PartialPlanDelta, TransitionBasis]:
    candidate = analysis.candidate
    # 1. the selected candidate is exactly the scored instance
    if scored.candidate_ref != candidate.candidate_id:
        raise ValueError("transition received a candidate different from the scored one")
    if state.step_index != step_index:
        raise ValueError("transition step does not follow the partial plan")
    digest = hashlib.sha256(f"{context_ref}|{step_index}|{candidate.candidate_id}".encode()).hexdigest()[:16]
    move_id = f"move-{step_index + 1}-{digest}"
    if move_id in state.selected_moves:
        raise ValueError("a move can be appended only once")
    # 3. copy declared effects
    strength = Decimal("1.000000")
    effects: list[PlannedActionEffect] = []
    for position, (ledger_key, targets) in enumerate(analysis.ledger_keys):
        effects.append(PlannedActionEffect(
            effect_id=f"effect-{digest}-{position}", move_ref=move_id, effect_key=analysis.effect_key,
            effect_version=analysis.effect_version, transition_domain=analysis.domain,
            planned_state=analysis.planned_state, binding_mode=analysis.binding_mode, target_refs=tuple(targets),
            support_refs=candidate.targets_for("support"), effect_strength=canonical_decimal(strength), ledger_key=ledger_key,
        ))
    # 4. exact target-effect coverage and residual need
    addressed = state.ledger("addressed_target_effects")
    residual = state.ledger("residual_target_needs")
    target_updates: dict[str, Decimal] = {}
    for effect in effects:
        coverage = max(addressed.get(effect.ledger_key, Decimal(0)), decimal_from(effect.effect_strength))
        addressed[effect.ledger_key] = coverage
        residual[effect.ledger_key] = max(Decimal(0), ONE - coverage)
        target_updates[effect.ledger_key] = coverage
    # 5. planned goal coverage only through explicit links
    goal_coverage = state.ledger("planned_goal_coverage")
    goal_updates: dict[str, Decimal] = {}
    for link in analysis.goal_links:
        updated = max(goal_coverage.get(link.coverage_key, Decimal(0)), link.strength * strength)
        if updated != goal_coverage.get(link.coverage_key):
            goal_updates[link.coverage_key] = updated
        goal_coverage[link.coverage_key] = updated
    # 6. unknowns are only ever planned for handling
    unknowns = state.labels("planned_unknown_handling")
    unknown_updates: dict[str, str] = {}
    if analysis.unknown_refs:
        label = "qualification_planned" if analysis.effect_key == "information.uncertainty_qualification" else analysis.planned_state
        if label not in {"elicitation_planned", "verification_planned", "qualification_planned"}:
            raise ValueError("unknown handling may only be planned")
        for ref in analysis.unknown_refs:
            if unknowns.get(ref) in {"elicitation_planned", "verification_planned"} and label == "qualification_planned":
                continue
            unknowns[ref] = label; unknown_updates[ref] = label
    # 7. contradictions are only ever planned for repair
    repairs = state.labels("planned_contradiction_repairs")
    repair_updates: dict[str, str] = {}
    if analysis.contradiction_refs:
        label = "correction_planned" if analysis.domain == TransitionDomain.CONTRADICTION_HANDLING else "exposure_planned"
        for ref in analysis.contradiction_refs:
            repairs[ref] = label; repair_updates[ref] = label
    # uptake of the focus contribution is recorded per addressed member
    uptake = state.ledger("uptake_coverage")
    uptake_updates: dict[str, Decimal] = {}
    if analysis.is_uptake:
        for ref in analysis.uptake_targets:
            uptake[ref] = ONE; uptake_updates[ref] = ONE
    # 8. counts and previous move
    actions = {item.key: item.count for item in state.action_counts}
    families = {item.key: item.count for item in state.family_counts}
    key = candidate.definition_ref.action_key
    actions[key] = actions.get(key, 0) + 1
    family = key.partition(".")[0]
    families[family] = families.get(family, 0) + 1
    # 9. accumulated marginal response cost and soft risk
    response_cost = scored.feature(F.RESPONSE_COST)
    soft_risk = scored.feature(F.SOFT_RISK_COST)
    after = PartialPlanState.signed(
        plan_id=state.plan_id, step_index=state.step_index + 1,
        selected_moves=(*state.selected_moves, move_id),
        represented_effects=(*state.represented_effects, *(item.effect_id for item in effects)),
        addressed_target_effects=_ledger(addressed), residual_target_needs=_ledger(residual),
        planned_goal_coverage=_ledger(goal_coverage), planned_unknown_handling=_labels(unknowns),
        planned_contradiction_repairs=_labels(repairs), uptake_coverage=_ledger(uptake),
        action_counts=_counts(actions), family_counts=_counts(families), previous_move_ref=move_id,
        accumulated_response_cost=canonical_decimal(decimal_from(state.accumulated_response_cost) + response_cost),
        accumulated_soft_risk=canonical_decimal(decimal_from(state.accumulated_soft_risk) + soft_risk),
    )
    # 11. the canonical state signature is computed by PartialPlanState.signed;
    # 10, rebinding and re-gating, happens in the next planning step.
    move = GroundedActionMove(
        move_id=move_id, step_index=step_index, candidate_ref=candidate.candidate_id,
        definition_ref=candidate.definition_ref, family=family, target_refs=candidate.target_refs,
        parameters=candidate.parameters, supporting_evidence_refs=candidate.supporting_evidence_refs,
        realization_constraints=realization_constraints, planned_effect_refs=tuple(item.effect_id for item in effects),
        selection_score=scored.score.score, selection_policy=selection_policy,
    )
    delta = PartialPlanDelta(
        added_move_ref=move_id, added_effect_refs=tuple(item.effect_id for item in effects),
        target_effect_updates=_ledger(target_updates), goal_coverage_updates=_ledger(goal_updates),
        unknown_handling_updates=_labels(unknown_updates), contradiction_updates=_labels(repair_updates),
        uptake_updates=_ledger(uptake_updates), added_response_cost=canonical_decimal(response_cost),
        added_soft_risk=canonical_decimal(soft_risk),
    )
    basis = TransitionBasis(
        candidate_ref=candidate.candidate_id,
        goal_links=tuple(sorted((TransitionGoalLink(
            coverage_key=item.coverage_key, goal_ref=item.goal_ref, requirement_ref=item.requirement_ref,
            strength=canonical_decimal(item.strength), rule_id=item.rule_id,
        ) for item in analysis.goal_links), key=lambda item: item.sort_key)),
        unknown_refs=tuple(sorted(analysis.unknown_refs)),
        contradiction_refs=tuple(sorted(analysis.contradiction_refs)),
        uptake_targets=tuple(sorted(analysis.uptake_targets)) if analysis.is_uptake else (),
    )
    return after, move, tuple(effects), delta, basis


__all__ = ["apply_transition", "empty_state"]
