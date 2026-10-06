"""Typed planning conditions for blocked user goals (``action-planner-v0.3``).

Two typed sources produce a closed, evidence-linked
:class:`ActionPlanningCondition`:

- a **rejected** current-turn user goal (SH-EVAL-006): its ``blocker_refs``
  are the unresolved unknowns and semantic arguments of the goal's own
  frames, or the active contradictions on them;
- (planner v0.3) the **blocking requirement resolutions** of an *adopted*
  current-turn request goal: its ``blocker_refs`` are exactly the Action
  objects the resolution was projected into (the unresolved argument to ask
  about, the question to answer with an explicit gap, or the request to
  decline with its grounded reason).

A candidate *addresses* a rejection-derived condition if its registered
effect describes clarification, limitation disclosure, or repair and one of
its principal targets is (or is one explicit hop from) a blocker.  A
resolution-derived condition is stricter: only the move kind that truthfully
resolves that exact blocker kind addresses it (elicitation for missing input,
an answer carrying the explicit gap for missing content, a scoped decline for
an execution blocker), and its principal target must be a blocker itself.  A
generic receipt acknowledgement never addresses any blocker.

Everything here reads typed Goal, Interpretation, and requirement records
only; nothing parses text, consults scores, or depends on personality.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..contracts.actions import (
    ActionPlanningCondition, ActionSelectionRequest, GroundedActionCandidate, PlanningConditionKind as K,
    TransitionDomain,
)
from ..contracts.goals import GoalOwner, GoalStatus, GoalTransitionKind
from ..contracts.requirement_resolution import (
    RequirementKind as RK, RequirementReason as RR, RequirementResolution, RequirementResolutionStatus as RS,
)
from .reference_index import ActionReferenceIndex, argument_ref
from .registry import ActionRegistry

MISSING_GROUNDING_RULE = "goal-reject.missing-required-grounding.v1"
HARD_GATE_RULE = "goal-reject.hard-gate-denial.v1"
BLOCKING_REJECTION_RULES = frozenset({MISSING_GROUNDING_RULE, HARD_GATE_RULE})
# The interpretation act a request goal is formed from (Goal formation v0.2).
REQUEST_ACT_FOR_GOAL = {
    "interaction.answer_information_request": "interpretation.act.request_information",
    "task.complete_authorized_request": "interpretation.act.request_action",
    "interaction.handle_action_request": "interpretation.act.request_action",
    "interaction.fulfill_conversational_deliverable": "interpretation.act.request_action",
    "interaction.incorporate_inquiry_answer": "interpretation.act.inform",
}
# Effects that clarify, disclose a limitation, or repair, beyond every
# unknown- and contradiction-handling effect.
ADDRESSING_EFFECTS = frozenset({
    "feedback.misunderstanding_disclosure", "information.uncertainty_qualification", "information.answer",
    "information.correction", "coordination.request_decline", "dialogue.repair",
})
ADDRESSING_DOMAINS = frozenset({TransitionDomain.UNKNOWN_HANDLING, TransitionDomain.CONTRADICTION_HANDLING})

# Planner v0.3: the only move kinds that truthfully address each kind of
# resolution-derived blocker.  Receipt (``feedback.receipt``) appears nowhere.
_DECLINE = frozenset({"coordination.request_decline"})
RESOLUTION_ADDRESSING: dict[K, tuple[frozenset[str], frozenset[TransitionDomain]]] = {
    K.MISSING_ARGUMENT: (frozenset(), frozenset({TransitionDomain.UNKNOWN_HANDLING})),
    K.MISSING_CONSENT: (frozenset(), frozenset({TransitionDomain.UNKNOWN_HANDLING})),
    K.MISSING_TRUSTED_FACT: (frozenset({"information.answer"}), frozenset()),
    K.TRUSTED_CONTENT_UNAVAILABLE: (frozenset({"information.answer"}), frozenset()),
    # Conflicting trusted records: verify or repair the contradiction, or
    # decline to give an unreliable answer (the conversational request).
    K.CONFLICTING_FACT: (frozenset({"elicitation.verification", "coordination.request_decline"}),
                         frozenset({TransitionDomain.CONTRADICTION_HANDLING})),
    K.MISSING_CAPABILITY: (_DECLINE, frozenset()),
    K.CAPABILITY_UNAVAILABLE: (_DECLINE, frozenset()),
    K.MISSING_AUTHORIZATION: (_DECLINE, frozenset()),
    K.SAFETY_PROHIBITED: (_DECLINE, frozenset()),
    K.SAFETY_UNAVAILABLE: (_DECLINE, frozenset()),
    K.TEMPORARY_FAILURE: (_DECLINE, frozenset()),
    K.UNSUPPORTED_DOMAIN: (frozenset(), frozenset({TransitionDomain.UNKNOWN_HANDLING})),
    # Planner v0.4: a ready deliverable whose content could not be produced is
    # truthfully declined (never acknowledged, never answered from nothing).
    K.CONTENT_UNAVAILABLE: (_DECLINE, frozenset()),
}
if set(RESOLUTION_ADDRESSING) != set(K):
    raise RuntimeError("every planning-condition kind declares its resolution addressing")
if any("feedback.receipt" in effects for effects, _ in RESOLUTION_ADDRESSING.values()):
    raise RuntimeError("a receipt can never address a blocker")


def resolution_condition_kind(resolution: RequirementResolution) -> K | None:
    """The planning-condition kind a blocking requirement resolution imposes (``None``: not a blocker)."""

    kind, status, reason = resolution.kind, resolution.status, resolution.reason_code
    if not resolution.blocking or status == RS.SATISFIED:
        return None
    if kind in {RK.REQUIRED_ARGUMENT, RK.DIALOGUE_REFERENT, RK.ARTIFACT_TARGET, RK.REQUESTED_CHANGE,
                RK.REQUESTED_OUTCOME, RK.INQUIRY_LINK}:
        # Every conversational-deliverable blocker is something the user supplies.
        return K.MISSING_ARGUMENT
    if kind == RK.DOMAIN_CONTEXT:  # pragma: no cover - never blocking by contract
        return None
    if kind == RK.TRUSTED_CONTENT:
        if status == RS.UNAUTHORIZED:
            return K.MISSING_AUTHORIZATION
        if status == RS.CONFLICTING:
            return K.CONFLICTING_FACT
        # A known topic whose fact is absent or outdated, versus a query no
        # installed producer may answer.  Neither is ever user ambiguity.
        if reason in {RR.PRODUCER_NO_MATCHING_RECORD, RR.PRODUCER_STALE_OBSERVATION}:
            return K.MISSING_TRUSTED_FACT
        return K.TRUSTED_CONTENT_UNAVAILABLE
    if kind == RK.CAPABILITY:
        return K.MISSING_CAPABILITY if reason == RR.CAPABILITY_UNREGISTERED else K.CAPABILITY_UNAVAILABLE
    if kind == RK.AUTHORIZATION:
        return K.MISSING_AUTHORIZATION
    if kind == RK.CONSENT:
        return K.MISSING_CONSENT
    if kind == RK.SAFETY:
        return K.SAFETY_PROHIBITED if status == RS.PROHIBITED else K.SAFETY_UNAVAILABLE
    if kind == RK.TOOL_OUTCOME:
        return K.TEMPORARY_FAILURE if status == RS.TEMPORARY_FAILURE else K.CAPABILITY_UNAVAILABLE
    raise ValueError("unregistered requirement kind")


def _condition_id(turn: str, goal: str, kind: K, refs: Iterable[str]) -> str:
    digest = hashlib.sha256("|".join((turn, goal, kind.value, *refs)).encode()).hexdigest()[:20]
    return f"planning-condition-{digest}"


def derive_planning_conditions(request: ActionSelectionRequest) -> tuple[ActionPlanningCondition, ...]:
    """Every blocked current-turn user goal, as typed conditions in canonical order."""

    snapshot = request.goal_snapshot
    interpretation = request.interpretation
    records = {record.goal_id: record for record in snapshot.records}
    frames = {frame.frame_id: frame for frame in interpretation.semantic_frames}
    hypotheses = {item.hypothesis_id: item for item in interpretation.hypotheses}
    leading = next((item for item in interpretation.scenarios if item.disposition.value == "leading"), None)
    leading_ids = {ref for refs in (leading.hypothesis_refs.values() if leading else ()) for ref in refs}
    conditions: list[ActionPlanningCondition] = []
    for transition in snapshot.transitions:
        if (
            transition.resulting_snapshot_ref != snapshot.goal_snapshot_id
            or transition.transition_kind != GoalTransitionKind.REJECT
            or transition.rule_id not in BLOCKING_REJECTION_RULES
        ):
            continue
        goal = records.get(transition.goal_id)
        if goal is None or goal.owner not in {GoalOwner.USER, GoalOwner.SHARED}:
            continue
        goal_frames = sorted({
            target.ref for requirement in goal.requirements for target in requirement.target_refs
            if target.ref in frames
        })
        if not goal_frames:
            act = REQUEST_ACT_FOR_GOAL.get(goal.definition_ref.definition_key)
            goal_frames = sorted({
                ref for hid in leading_ids if hid in hypotheses and hypotheses[hid].hypothesis_key == act
                for ref in hypotheses[hid].semantic_frame_refs if ref in frames
            })
        grouped: dict[K, set[str]] = {}
        evidence: set[str] = set()
        for frame_id in goal_frames:
            frame = frames[frame_id]
            evidence.update(frame.evidence_refs)
            for argument in frame.unresolved_arguments:
                if transition.rule_id == HARD_GATE_RULE:
                    if argument != "authorization":
                        continue
                    kind = K.MISSING_AUTHORIZATION
                elif frame.predicate == "unknown":
                    kind = K.UNSUPPORTED_DOMAIN
                else:
                    # An unresolved topic is a user-input problem (planner
                    # v0.3); a known topic without a trusted answer is a
                    # grounding problem and comes only from a resolution.
                    kind = K.MISSING_ARGUMENT
                refs = grouped.setdefault(kind, set())
                refs.add(argument_ref(frame_id, argument))
                refs.update(
                    unknown.unknown_id for unknown in interpretation.unknowns
                    if unknown.key == argument and set(unknown.evidence_refs) == set(frame.evidence_refs)
                )
            if frame.predicate == "unknown" and transition.rule_id == MISSING_GROUNDING_RULE:
                grouped.setdefault(K.UNSUPPORTED_DOMAIN, set()).update(
                    unknown.unknown_id for unknown in interpretation.unknowns
                    if unknown.key == "meaning" and set(unknown.evidence_refs) == set(frame.evidence_refs)
                )
            contradictions = {
                item.contradiction_id for item in interpretation.contradictions
                if item.status == "active" and set(item.evidence_refs) & set(frame.evidence_refs)
            }
            if contradictions:
                grouped.setdefault(K.CONFLICTING_FACT, set()).update(contradictions)
        if transition.rule_id == HARD_GATE_RULE and K.MISSING_AUTHORIZATION not in grouped:
            grouped[K.MISSING_AUTHORIZATION] = set()
        for kind in sorted(grouped, key=lambda item: item.value):
            refs = tuple(sorted(grouped[kind]))
            conditions.append(ActionPlanningCondition(
                condition_id=_condition_id(request.cognitive_turn_id, goal.goal_id, kind, refs),
                kind=kind, blocked_goal_ref=goal.goal_id,
                blocked_goal_definition=goal.definition_ref.definition_key,
                rejection_rule_id=transition.rule_id, blocker_refs=refs, frame_refs=tuple(goal_frames),
                evidence_refs=tuple(sorted(evidence)),
            ))
    conditions.extend(_resolution_conditions(request))
    conditions.extend(_collaboration_conditions(request))
    return tuple(sorted(conditions, key=lambda item: item.condition_id))


def _collaboration_conditions(request: ActionSelectionRequest) -> list[ActionPlanningCondition]:
    """A ready deliverable whose proposal failed is a typed, declinable blocker (planner v0.4)."""

    handoff = getattr(request, "collaboration_handoff", None)
    if handoff is None:
        return []
    records = {record.goal_id: record for record in request.goal_snapshot.records}
    frames = {frame.frame_id: frame for frame in request.interpretation.semantic_frames}
    out = []
    for binding in handoff.bindings:
        if binding.status != "failed":
            continue
        goal = records[binding.goal_ref]
        frame_refs = sorted({target.ref for requirement in goal.requirements for target in requirement.target_refs
                             if target.ref in frames})
        evidence = sorted({ref for frame_ref in frame_refs for ref in frames[frame_ref].evidence_refs})
        out.append(ActionPlanningCondition(
            condition_id=_condition_id(request.cognitive_turn_id, goal.goal_id, K.CONTENT_UNAVAILABLE,
                                       binding.blocker_refs),
            kind=K.CONTENT_UNAVAILABLE, blocked_goal_ref=goal.goal_id,
            blocked_goal_definition=goal.definition_ref.definition_key, blocker_refs=binding.blocker_refs,
            frame_refs=tuple(frame_refs), evidence_refs=tuple(evidence),
            collaboration_failure=binding.failure_code.value,
        ))
    return out


def _resolution_conditions(request: ActionSelectionRequest) -> list[ActionPlanningCondition]:
    """Blocking resolutions of active current-turn request goals, one condition per (goal, kind)."""

    handoff = getattr(request, "requirement_handoff", None)
    if handoff is None:
        return []
    records = {record.goal_id: record for record in request.goal_snapshot.records}
    frames = {frame.frame_id: frame for frame in request.interpretation.semantic_frames}
    grouped: dict[tuple[str, K], list[RequirementResolution]] = {}
    for resolution in handoff.snapshot.resolutions:
        kind = resolution_condition_kind(resolution)
        goal = records.get(resolution.goal_ref)
        if (kind is None or goal is None or goal.status != GoalStatus.ACTIVE
                or goal.created_turn_ref != request.cognitive_turn_id
                or goal.owner not in {GoalOwner.USER, GoalOwner.SHARED}):
            continue
        grouped.setdefault((goal.goal_id, kind), []).append(resolution)
    out = []
    for (goal_ref, kind), resolutions in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1].value)):
        blockers = sorted({ref for item in resolutions for ref in handoff.binding(item.resolution_id).target_refs})
        frame_refs = sorted({item.frame_ref for item in resolutions})
        evidence = sorted({ref for frame_ref in frame_refs if frame_ref in frames
                           for ref in frames[frame_ref].evidence_refs})
        out.append(ActionPlanningCondition(
            condition_id=_condition_id(request.cognitive_turn_id, goal_ref, kind, blockers),
            kind=kind, blocked_goal_ref=goal_ref,
            blocked_goal_definition=records[goal_ref].definition_ref.definition_key,
            blocker_refs=tuple(blockers), frame_refs=tuple(frame_refs), evidence_refs=tuple(evidence),
            resolution_refs=tuple(sorted(item.resolution_id for item in resolutions)),
        ))
    return out


def addresses(
    candidate: GroundedActionCandidate, condition: ActionPlanningCondition,
    registry: ActionRegistry, index: ActionReferenceIndex,
) -> bool:
    """Whether ``candidate`` truthfully targets one of the condition's blockers."""

    if not condition.blocker_refs:
        return False
    definition = registry.get(candidate.definition_ref)
    if definition is None:
        return False
    effect = definition.effect
    targets = {item.ref for item in candidate.target_refs if item.role != "support"}
    blockers = set(condition.blocker_refs)
    if condition.from_resolution:
        effects, domains = RESOLUTION_ADDRESSING[condition.kind]
        if effect.effect_key not in effects and effect.transition_domain not in domains:
            return False
        return bool(targets & blockers)
    if effect.transition_domain not in ADDRESSING_DOMAINS and effect.effect_key not in ADDRESSING_EFFECTS:
        return False
    return bool(targets & blockers) or any(
        index.relates(target, blocker) for target in sorted(targets) for blocker in sorted(blockers)
    )


def addresses_any(
    candidate: GroundedActionCandidate, conditions: Iterable[ActionPlanningCondition],
    registry: ActionRegistry, index: ActionReferenceIndex,
) -> bool:
    return any(addresses(candidate, condition, registry, index) for condition in conditions)


__all__ = [
    "ADDRESSING_EFFECTS", "BLOCKING_REJECTION_RULES", "RESOLUTION_ADDRESSING", "addresses", "addresses_any",
    "derive_planning_conditions", "resolution_condition_kind",
]
