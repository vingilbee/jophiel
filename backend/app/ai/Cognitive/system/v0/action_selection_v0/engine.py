"""Shadow Action Selection V0: deterministic construction, gates and transitions.

``construct -> gate -> features -> score -> deterministic argmax`` repeats
until ``STOP`` (the zero baseline) wins.  Every failure is typed by phase and
fails closed: no partial plan is ever returned. The optional versioned proposal
selector chooses among actual eligible candidates while retaining real scores.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from ..contracts.actions import (
    ActionFailurePhase as P, ActionPlan, ActionPlanningStep, ActionSelectionRequest, ActionSelectionResult,
    ActionVersions, GroundedActionMove, PRIMARY_RULE_ID, PlanTerminationKind, PrimaryDesignation,
    designate_primary, primary_comparison, ModelActionChoice, MODEL_ACTION_PLANNER_VERSION,
    MODEL_POLICY_PLANNER_VERSION, MODEL_PRIMARY_RULE_ID, MODEL_PRIMARY_CHOICE_RULE_ID, ActionFamily, PlanningConditionKind,
)
from .construction import CandidateConstructor, PlanView, recipe_for
from .errors import ActionSelectionError, CycleGuardError, NoViablePlanError
from .features import CandidateAnalysis, FeatureCalculator
from .gates import HardGate
from .planning_conditions import addresses, addresses_any, derive_planning_conditions
from .policy import resolve_conflict, score_candidate
from .reference_index import ActionReferenceIndex
from .registry import DEFAULT_REGISTRY, SHADOW_KEYS, ActionRegistry
from .transition import apply_transition, empty_state

DEFAULT_MAX_STEPS = 64


def _digest(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


_DEFERRABLE_BY_ASKING = frozenset(PlanningConditionKind) - {
    PlanningConditionKind.SAFETY_PROHIBITED, PlanningConditionKind.SAFETY_UNAVAILABLE,
}


def _may_stop(conditions, addressed: set[str], moves, model_policy: bool) -> bool:
    """A model STOP is accepted once every planning condition is addressed.

    Under the model policy a reply that already asks the user something may
    also stop with non-safety conditions still open: asking first defers the
    gap answer or decline to the next turn and claims nothing.  Safety
    conditions are never deferred.
    """
    open_conditions = [item for item in conditions if item.condition_id not in addressed]
    if not open_conditions:
        return True
    return (model_policy and any(move.family == ActionFamily.SEEK_INFORMATION for move in moves)
            and all(item.kind in _DEFERRABLE_BY_ASKING for item in open_conditions))


class ActionSelectionEngine:
    def __init__(
        self,
        *,
        registry: ActionRegistry | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        constructor_factory: Callable[[ActionRegistry], CandidateConstructor] = CandidateConstructor,
        gate_factory: Callable[[ActionRegistry], HardGate] = HardGate,
        feature_factory: Callable[[ActionRegistry, ActionReferenceIndex], FeatureCalculator] = FeatureCalculator,
    ) -> None:
        if max_steps < 1:
            raise ValueError("the defensive guard must allow at least one step")
        self.registry = registry or DEFAULT_REGISTRY
        self.max_steps = max_steps
        self.constructor_factory = constructor_factory
        self.gate_factory = gate_factory
        self.feature_factory = feature_factory

    def select(self, request: ActionSelectionRequest, *, proposal_selector=None, source_call_digest=None,
               primary_selector=None, planner_version=None, primary_rule_id=MODEL_PRIMARY_RULE_ID) -> ActionSelectionResult:
        """``planner_version`` model-policy-v1 also takes the model's primary move
        (``primary_selector``) and records scores without using them as a gate."""
        turn = getattr(request, "cognitive_turn_id", "unknown-turn")
        phase = P.REQUEST_VALIDATION
        code = "invalid_request"
        try:
            model_policy = planner_version == MODEL_POLICY_PLANNER_VERSION
            if planner_version not in {None, MODEL_POLICY_PLANNER_VERSION} or (model_policy != (primary_selector is not None)):
                raise ValueError('model policy planning requires its primary selector, and only it')
            if model_policy and proposal_selector is None:
                raise ValueError('model policy planning requires a proposal selector')
            if primary_rule_id not in {MODEL_PRIMARY_RULE_ID, MODEL_PRIMARY_CHOICE_RULE_ID} or (
                    primary_rule_id != MODEL_PRIMARY_RULE_ID and not model_policy):
                raise ValueError('model primary choice rule requires model policy planning')
            versions = ActionVersions(planner=planner_version or MODEL_ACTION_PLANNER_VERSION) if proposal_selector else ActionVersions()
            if proposal_selector and not source_call_digest:
                raise ValueError('model selection requires captured proposal provenance')
            validated = ActionSelectionRequest.model_validate(request.model_dump(mode="python"))
            if validated != request:
                raise ValueError("action request changed during validation")
            canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            request_digest = hashlib.sha256(canonical.encode()).hexdigest()
            context_ref = f"action-context-{request_digest[:20]}"
            index = ActionReferenceIndex(request)

            phase, code = P.REGISTRY_RESOLUTION, "registry_unresolvable"
            if self.registry.version != request.versions.registry:
                raise ValueError("installed registry differs from the requested registry version")
            shadow = {item.definition_ref.action_key for item in self.registry.definitions if item.lifecycle.value == "shadow"}
            if shadow != SHADOW_KEYS or any(item.lifecycle.value == "approved" for item in self.registry.definitions):
                raise ValueError("installed lifecycle split differs from the accepted shadow subset")
            for key in SHADOW_KEYS:
                if recipe_for(key) is None or self.registry.by_key(key) is None:
                    raise ValueError("every shadow definition requires a binding recipe")
            constructor = self.constructor_factory(self.registry)
            gate = self.gate_factory(self.registry)
            calculator = self.feature_factory(self.registry, index)

            phase, code = P.CANDIDATE_CONSTRUCTION, "planning_conditions_invalid"
            conditions = derive_planning_conditions(request)
            addressed: set[str] = set()

            plan_id = f"action-plan-{_digest(context_ref)}"
            state = empty_state(plan_id)
            moves: list[GroundedActionMove] = []
            effects = []
            analyses_by_move: dict[str, CandidateAnalysis] = {}
            steps: list[ActionPlanningStep] = []
            seen = {state.state_signature}
            termination: PlanTerminationKind | None = None
            while termination is None:
                step_index = len(steps)
                if step_index >= self.max_steps:
                    phase, code = P.PLAN_TRANSITION, "cycle_guard"
                    raise CycleGuardError("defensive planning guard exhausted")
                phase, code = P.CANDIDATE_CONSTRUCTION, "construction_failed"
                candidates, uninstantiable = constructor.construct(index, PlanView(tuple(moves), tuple(effects)), context_ref)
                by_id = {item.candidate_id: item for item in candidates}
                phase, code = P.HARD_GATE, "gate_failed"
                gate_results = tuple(gate.evaluate(item, index) for item in candidates)
                eligible = [item for item, result in zip(candidates, gate_results) if result.eligible_in_shadow]
                constraints = {result.candidate_ref: result.realization_constraints for result in gate_results}
                phase, code = P.FEATURE_EXTRACTION, "feature_failed"
                analyses = {item.candidate_id: calculator.analyze(item, state) for item in eligible}
                features = {
                    item.candidate_id: calculator.compute(analyses[item.candidate_id], state, tuple(moves), tuple(effects), analyses_by_move)
                    for item in eligible
                }
                phase, code = P.SCORING, "scoring_failed"
                scored = tuple(score_candidate(item, features[item.candidate_id]) for item in eligible)
                phase, code = P.CONFLICT_RESOLUTION, "conflict_unresolved"
                # Blocker-first eligibility (planner v0.2): while a blocked
                # user goal is unaddressed and some eligible candidate
                # addresses it, only those candidates may be selected.  This
                # is a hard restriction applied before utility, never a score.
                open_conditions = tuple(
                    item for item in conditions if item.condition_id not in addressed and item.blocker_refs
                )
                addressing = {
                    item.candidate_id for item in eligible
                    if addresses_any(item, open_conditions, self.registry, index)
                } if open_conditions else set()
                restricted = tuple(
                    item.candidate_ref for item in scored if item.candidate_ref not in addressing
                ) if addressing else ()
                winner, tie = resolve_conflict(
                    tuple(item for item in scored if item.candidate_ref not in set(restricted)), by_id,
                )
                model_choice = None
                if proposal_selector is not None:
                    chosen = proposal_selector(step_index, candidates, tuple(eligible), restricted, plan_id, tuple(moves), analyses)
                    if chosen is not None and (chosen not in {c.candidate_id for c in eligible} or chosen in set(restricted)):
                        raise ValueError('model choice is not an eligible unrestricted candidate')
                    if chosen is None and not _may_stop(conditions, addressed, moves, model_policy):
                        raise ValueError('model STOP leaves an unresolved planning condition')
                    winner = next((s for s in scored if s.candidate_ref == chosen), None)
                    tie = None
                    model_choice = ModelActionChoice(source_call_digest=source_call_digest,
                        proposal_index=step_index, candidate_ref=chosen)
                if winner is None:
                    termination = PlanTerminationKind.STOP
                    steps.append(ActionPlanningStep(
                        step_id=f"action-step-{_digest(context_ref + str(step_index))}", step_index=step_index,
                        context_ref=context_ref, partial_state_before=state, constructed_candidates=candidates,
                        uninstantiable=uninstantiable, gate_results=gate_results, scored_candidates=scored,
                        partial_state_after=state, termination=termination, model_choice=model_choice,
                        condition_restricted_candidate_refs=restricted,
                    ))
                    break
                phase, code = P.PLAN_TRANSITION, "transition_failed"
                analysis = analyses[winner.candidate_ref]
                after, move, move_effects, delta, transition_basis = apply_transition(
                    state, analysis, winner, step_index=step_index, context_ref=context_ref,
                    realization_constraints=constraints[winner.candidate_ref],
                    selection_policy="model_policy" if model_policy else "argmax",
                )
                if after.state_signature in seen:
                    code = "cycle_guard"
                    raise CycleGuardError("repeated canonical partial-plan state")
                seen.add(after.state_signature)
                steps.append(ActionPlanningStep(
                    step_id=f"action-step-{_digest(context_ref + str(step_index))}", step_index=step_index,
                    context_ref=context_ref, partial_state_before=state, constructed_candidates=candidates,
                    uninstantiable=uninstantiable, gate_results=gate_results, scored_candidates=scored,
                    selected_candidate_ref=winner.candidate_ref, tie_resolution=tie, model_choice=model_choice, applied_effects=move_effects,
                    transition_basis=transition_basis, partial_state_delta=delta, partial_state_after=after,
                    condition_restricted_candidate_refs=restricted,
                ))
                addressed.update(
                    item.condition_id for item in conditions
                    if addresses(by_id[winner.candidate_ref], item, self.registry, index)
                )
                moves.append(move); effects.extend(move_effects); analyses_by_move[move.move_id] = analysis
                state = after

            phase, code = P.PLAN_VALIDATION, "plan_invalid"
            if not moves and request.runtime.response_obligation == "required":
                code = "no_viable_plan"
                raise NoViablePlanError("a required response has no positively valued eligible move")
            designation = self._primary(request, steps, moves, primary_selector, primary_rule_id) if moves else None
            primary = designation.primary_move_ref if designation else None
            plan = ActionPlan(
                plan_id=plan_id, cognitive_turn_id=request.cognitive_turn_id, moves=tuple(moves),
                primary_move_ref=primary, supporting_move_refs=tuple(item.move_id for item in moves if item.move_id != primary),
                move_priorities=tuple({"move_ref": item.move_id, "priority": "primary" if item.move_id == primary else "supporting"} for item in moves),
                primary_designation=designation, final_partial_state_ref=state.state_signature,
                termination=PlanTerminationKind.STOP, response_obligation=request.runtime.response_obligation,
                versions=versions,
            )

            phase, code = P.TRACE_VALIDATION, "trace_invalid"
            result = ActionSelectionResult(
                result_id=f"action-result-{_digest(context_ref + plan_id)}", cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref, request_digest=request_digest, context_ref=context_ref,
                plan=plan, steps=tuple(steps), moves=tuple(moves), planned_effects=tuple(effects),
                safety=request.runtime.safety_basis, versions=versions, planning_conditions=conditions,
            )
            round_trip = ActionSelectionResult.model_validate_json(result.model_dump_json())
            if round_trip != result:
                raise ValueError("action result does not survive JSON round-trip")
            return result
        except ActionSelectionError:
            raise
        except Exception as exc:
            raise ActionSelectionError(cognitive_turn_id=turn, failed_phase=phase, code=code) from exc

    def _primary(self, request: ActionSelectionRequest, steps: list[ActionPlanningStep], moves: list[GroundedActionMove],
                 primary_selector=None, primary_rule_id=MODEL_PRIMARY_RULE_ID) -> PrimaryDesignation:
        focal_primary = request.goal_snapshot.focus_decision.primary_goal_ref
        compared = [primary_comparison(step, move.move_id, focal_primary) for step, move in zip(steps, moves)]
        if primary_selector is None:
            return PrimaryDesignation(rule_id=PRIMARY_RULE_ID, focal_primary_goal_ref=focal_primary,
                                      primary_move_ref=designate_primary(compared), compared_moves=tuple(compared))
        # The selector validates the model's choice against the coverage rule.
        return PrimaryDesignation(rule_id=primary_rule_id, focal_primary_goal_ref=focal_primary,
                                  primary_move_ref=primary_selector(tuple(moves), tuple(compared)),
                                  compared_moves=tuple(compared))


__all__ = ["ActionSelectionEngine", "DEFAULT_MAX_STEPS", "PRIMARY_RULE_ID"]
