"""Trusted reconciliation evidence for Goal V0 (SH-EVAL-014, workstream A).

Typed outcome events become ``GoalEvidence`` only for exact targets and only
where the authority table allows; lifecycle revisions consume that evidence
without creating response obligations.
"""

from __future__ import annotations

import itertools

import pytest

from app.ai.Cognitive.system.v0.contracts.goals import (
    GoalEvidence,
    GoalEvidenceProvenance,
    GoalRequirementStatus,
    GoalStatus,
    GoalUpdateTrigger,
)
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.evidence_producer import (
    EVIDENCE_AUTHORITY,
    ArgumentGroundedEvent,
    ContradictionResolvedEvent,
    DeferralEvent,
    GoalEvidenceProductionError,
    GoalReplacementEvent,
    ResponseDeliveredEvent,
    ResumptionEvent,
    ScopeClosureEvent,
    ToolResultEvent,
    UserConfirmationEvent,
    UserWithdrawalEvent,
    VerifiedImpossibilityEvent,
    produce_goal_evidence,
    trigger_for,
)
from app.ai.Cognitive.system.v0.goal_v0.ontology import all_definitions
from goal_v0_support import (
    contradiction, frame, hyp, make_case, only, records, requirement, respond, revise, target, unknown,
)

RESPONSE = "interaction.discharge_response_obligation"
ANSWER = "interaction.answer_information_request"
TASK = "task.complete_authorized_request"
STOP = "task.prevent_or_stop_requested_action"
REPAIR = "interaction.repair_shared_understanding"
UNKNOWN = "epistemic.resolve_required_unknown"
CONTRADICTION = "epistemic.resolve_material_contradiction"
CONVERSATION = "goal-test-conversation"
TURN = "t1"


def _event(cls, goal, event_id="e1", **fields):
    return cls(event_id=event_id, producer="trusted-runtime", source_ref=f"source-{event_id}", conversation_ref=CONVERSATION,
               occurred_turn_ref=TURN, target_goal_ref=goal.goal_id,
               target_requirement_ref=requirement(goal).requirement_id, **fields)


def _answer_case():
    frames = [frame("f-ask", "information_request", topic_ref="protein in eggs", question_form="wh")]
    return make_case(TURN, frames=frames, hypotheses=[
        hyp("h-ask", "act.request_information", frames=("f-ask",)), hyp("h-obtain", "goal.obtain_information", frames=("f-ask",)),
    ])


def _task_case(*effects: str, negative: bool = False):
    frames = [frame(f"f-{index}", "action_request", polarity="negative" if negative else "positive",
                    requested_effect=effect, subject_ref="plan") for index, effect in enumerate(effects)]
    refs = tuple(item["frame_id"] for item in frames)
    goal_key = "goal.prevent_or_stop_action" if negative else "goal.cause_action"
    return make_case(TURN, frames=frames, task_ref="task-plan", hypotheses=[
        hyp("h-do", "act.request_action", frames=refs), hyp("h-goal", goal_key, frames=refs),
    ])


def _repair_case():
    frames = [frame("f-fix", "correction", corrected_attribute="servings", corrected_value="two")]
    return make_case(TURN, frames=frames, task_ref="task-plan", hypotheses=[
        hyp("h-fix", "act.correct", frames=("f-fix",)), hyp("h-repair", "goal.repair_shared_understanding", frames=("f-fix",)),
    ])


def _task(snapshot, effect):
    [item] = [goal for goal in records(snapshot, TASK) if requirement(goal).parameters["requested_effect"] == effect]
    return item


def _apply(case, snapshot, events):
    evidence = produce_goal_evidence(snapshot, events)
    return revise(case, snapshot, trigger_for(events), evidence), evidence


def _record(snapshot, goal):
    return next(item for item in snapshot.records if item.goal_id == goal.goal_id)


def _assessment(snapshot, goal):
    return next(item for item in snapshot.requirement_assessments if item.goal_ref == goal.goal_id)


def _no_response_obligation_created(result):
    assert not any(item.definition_ref.definition_key == RESPONSE for item in result.proposals)
    assert not result.proposals


def test_authority_table_covers_every_definition() -> None:
    assert set(EVIDENCE_AUTHORITY) == {item.definition_key for item in all_definitions()}
    assert EVIDENCE_AUTHORITY[TASK]["satisfies"] == {"tool_result"}
    assert EVIDENCE_AUTHORITY[REPAIR]["satisfies"] == {"user_confirmation"}


def test_delivered_response_satisfies_only_the_matching_response_obligation() -> None:
    case = _answer_case()
    snapshot = respond(case).snapshot
    obligation, answer = only(snapshot, RESPONSE), only(snapshot, ANSWER)
    events = [
        _event(ResponseDeliveredEvent, obligation, "d-obligation", delivered_turn_ref=TURN, delivery_ref="delivery-1"),
        _event(ResponseDeliveredEvent, answer, "d-answer", delivered_turn_ref=TURN, delivery_ref="delivery-1",
               addressed_target_ref="protein in eggs"),
    ]
    result, evidence = _apply(case, snapshot, events)
    assert trigger_for(events) == GoalUpdateTrigger.REALIZATION_OR_OUTCOME
    assert _record(result.snapshot, obligation).status == GoalStatus.SATISFIED
    # Delivery without validated answer content is recorded, never success.
    assert _record(result.snapshot, answer).status == GoalStatus.ACTIVE
    assessment = _assessment(result.snapshot, answer)
    assert assessment.status == GoalRequirementStatus.OPEN and assessment.evidence_refs
    satisfy = next(item for item in result.transitions if item.goal_id == obligation.goal_id)
    assert satisfy.transition_kind.value == "satisfy" and set(satisfy.evidence_refs) <= {item.evidence_id for item in evidence}
    _no_response_obligation_created(result)
    # A terminal goal is never ranked in the update that ended it.
    assert obligation.goal_id not in result.snapshot.focal_goal_refs

    validated = [_event(ResponseDeliveredEvent, answer, "d-answer-2", delivered_turn_ref=TURN, delivery_ref="delivery-2",
                        addressed_target_ref="protein in eggs", validated_content_ref="realization-validation-1")]
    result, _ = _apply(case, snapshot, validated)
    assert _record(result.snapshot, answer).status == GoalStatus.SATISFIED


@pytest.mark.parametrize("mutation", ("other_turn", "wrong_topic", "no_topic"))
def test_delivery_for_another_turn_or_target_is_rejected(mutation: str) -> None:
    snapshot = respond(_answer_case()).snapshot
    answer = only(snapshot, ANSWER)
    fields = dict(delivered_turn_ref=TURN, delivery_ref="d", addressed_target_ref="protein in eggs")
    fields.update({"other_turn": {"delivered_turn_ref": "t0"}, "wrong_topic": {"addressed_target_ref": "fat in eggs"},
                   "no_topic": {"addressed_target_ref": None}}[mutation])
    with pytest.raises(GoalEvidenceProductionError):
        produce_goal_evidence(snapshot, [_event(ResponseDeliveredEvent, answer, **fields)])


def test_delivery_can_only_address_a_goal_its_episode_focused_on() -> None:
    case = _task_case("make plan")
    first = respond(case).snapshot
    # A second turn whose focus is its own request; the turn-1 task is history.
    second_case = make_case("t2", frames=[frame("f-q", "information_request", topic_ref="oats", question_form="wh")],
                            hypotheses=[hyp("h-q", "act.request_information", frames=("f-q",)),
                                        hyp("h-o", "goal.obtain_information", frames=("f-q",))], task_ref="task-plan")
    from goal_v0_support import respond as respond_turn
    second = respond_turn(second_case, first).snapshot
    old_task = only(second, TASK)
    assert old_task.goal_id not in second.focal_goal_refs[:1]
    obligation = next(item for item in records(second, RESPONSE) if item.scope_ref == "t2")
    event = ResponseDeliveredEvent(event_id="e", producer="p", source_ref="s", conversation_ref=CONVERSATION,
                                   occurred_turn_ref="t2", target_goal_ref=obligation.goal_id,
                                   target_requirement_ref=requirement(obligation).requirement_id,
                                   delivered_turn_ref=TURN, delivery_ref="d")
    with pytest.raises(GoalEvidenceProductionError, match="predecessor"):
        produce_goal_evidence(second, [event])


def test_an_assistant_claim_cannot_satisfy_a_task_goal() -> None:
    case = _task_case("make plan")
    snapshot = respond(case).snapshot
    task = only(snapshot, TASK)
    for event in (
        _event(ResponseDeliveredEvent, task, delivered_turn_ref=TURN, delivery_ref="d", addressed_target_ref="task-plan",
               validated_content_ref="v"),
        _event(UserConfirmationEvent, task, confirmed_target_ref="task-plan", user_message_ref="m"),
    ):
        with pytest.raises(GoalEvidenceProductionError, match="not authorized"):
            produce_goal_evidence(snapshot, [event])
    # Hand-built confirmation evidence reaching the engine directly stays open.
    claim = GoalEvidence(evidence_id="claim", evidence_kind="explicit_user_confirmation", source_type="assistant_text",
                         source_ref="reply", episode_or_turn_ref=TURN, target_goal_ref=task.goal_id,
                         target_requirement_ref=requirement(task).requirement_id,
                         provenance=GoalEvidenceProvenance(producer="assistant", review_state="unreviewed"), finality="final")
    result = revise(case, snapshot, GoalUpdateTrigger.REALIZATION_OR_OUTCOME, [claim])
    assert _record(result.snapshot, task).status == GoalStatus.ACTIVE


def test_finalized_matching_tool_result_satisfies_the_task_and_nothing_else_does() -> None:
    case = _task_case("make plan")
    snapshot = respond(case).snapshot
    task = only(snapshot, TASK)
    base = dict(outcome_id="tool-outcome", status="succeeded", final=True, authorized=True, effect_ref="make plan")
    for override, status in (
        ({}, GoalStatus.SATISFIED),
        ({"effect_ref": "other effect"}, GoalStatus.ACTIVE),
        ({"authorized": False}, GoalStatus.ACTIVE),
        ({"final": False}, GoalStatus.ACTIVE),
        ({"status": "unavailable"}, GoalStatus.ACTIVE),
    ):
        result, _ = _apply(case, snapshot, [_event(ToolResultEvent, task, **{**base, **override})])
        assert _record(result.snapshot, task).status == status, override
        _no_response_obligation_created(result)


def test_failed_outcomes_never_satisfy() -> None:
    case = _task_case("make plan")
    snapshot = respond(case).snapshot
    task = only(snapshot, TASK)
    failed = dict(outcome_id="tool-outcome", status="failed", final=True, authorized=True, effect_ref="make plan")
    # An approved alternate route keeps the goal open.
    result, _ = _apply(case, snapshot, [_event(ToolResultEvent, task, alternate_route_available=True, **failed)])
    assert _record(result.snapshot, task).status == GoalStatus.ACTIVE
    assert _assessment(result.snapshot, task).status == GoalRequirementStatus.OPEN
    # A plain failure is not success either (lifecycle v0.3).
    result, _ = _apply(case, snapshot, [_event(ToolResultEvent, task, **failed)])
    assert _record(result.snapshot, task).status == GoalStatus.ACTIVE
    # Verified impossibility with no alternate route fails the goal.
    result, _ = _apply(case, snapshot, [_event(ToolResultEvent, task, verified_impossible=True, **failed)])
    assert _record(result.snapshot, task).status == GoalStatus.FAILED
    result, _ = _apply(case, snapshot, [_event(VerifiedImpossibilityEvent, task, capability_ref="capability-removed")])
    assert _record(result.snapshot, task).status == GoalStatus.FAILED
    with pytest.raises(ValueError):
        _event(ToolResultEvent, task, verified_impossible=True, alternate_route_available=True, **failed)


def test_explicit_withdrawal_abandons_only_its_target() -> None:
    case = _task_case("make plan", "add shopping list")
    snapshot = respond(case).snapshot
    first, second = _task(snapshot, "make plan"), _task(snapshot, "add shopping list")
    result, _ = _apply(case, snapshot, [_event(UserWithdrawalEvent, first, user_message_ref="m-withdraw")])
    assert trigger_for([_event(UserWithdrawalEvent, first, user_message_ref="m")]) == GoalUpdateTrigger.EXPLICIT_CORRECTION
    assert _record(result.snapshot, first).status == GoalStatus.ABANDONED
    assert _record(result.snapshot, second).status == GoalStatus.ACTIVE
    _no_response_obligation_created(result)
    with pytest.raises(GoalEvidenceProductionError, match="user-requested"):
        produce_goal_evidence(snapshot, [_event(UserWithdrawalEvent, only(snapshot, RESPONSE), user_message_ref="m")])


def test_typed_replacement_supersedes_and_names_the_replacement() -> None:
    case = _task_case("make plan", "make a vegetarian plan")
    snapshot = respond(case).snapshot
    old, new = _task(snapshot, "make plan"), _task(snapshot, "make a vegetarian plan")
    result, _ = _apply(case, snapshot, [_event(GoalReplacementEvent, old, replacement_goal_ref=new.goal_id, user_message_ref="m")])
    assert _record(result.snapshot, old).status == GoalStatus.SUPERSEDED
    supersede = next(item for item in result.transitions if item.goal_id == old.goal_id)
    assert supersede.replacement_goal_ref == new.goal_id
    assert _record(result.snapshot, new).status == GoalStatus.ACTIVE
    _no_response_obligation_created(result)


def test_deferral_suspends_and_resumption_resumes() -> None:
    case = _task_case("make plan")
    snapshot = respond(case).snapshot
    task = only(snapshot, TASK)
    deferral = [_event(DeferralEvent, task, cause="missing_temporary_prerequisite")]
    assert trigger_for(deferral) == GoalUpdateTrigger.TRUSTED_RUNTIME_CHANGE
    suspended, _ = _apply(case, snapshot, deferral)
    assert _record(suspended.snapshot, task).status == GoalStatus.SUSPENDED
    resumed, _ = _apply(case, suspended.snapshot, [_event(ResumptionEvent, task, "e2", resolved_blocker_ref="prerequisite-back")])
    assert _record(resumed.snapshot, task).status == GoalStatus.ACTIVE
    with pytest.raises(GoalEvidenceProductionError, match="suspended"):
        produce_goal_evidence(snapshot, [_event(ResumptionEvent, task, resolved_blocker_ref="x")])


def test_scope_closure_expires_rather_than_satisfies() -> None:
    case = _task_case("make plan")
    snapshot = respond(case).snapshot
    task = only(snapshot, TASK)
    closure = [_event(ScopeClosureEvent, task, closed_scope_ref="task-plan")]
    assert trigger_for(closure) == GoalUpdateTrigger.TRUSTED_SCOPE_CLOSURE
    result, _ = _apply(case, snapshot, closure)
    assert _record(result.snapshot, task).status == GoalStatus.EXPIRED
    expire = next(item for item in result.transitions if item.goal_id == task.goal_id)
    assert expire.rule_id == "goal-lifecycle.expire-task-scope.v1" and expire.evidence_refs
    with pytest.raises(GoalEvidenceProductionError, match="avoid-mode"):
        produce_goal_evidence(snapshot, [_event(ScopeClosureEvent, task, closed_scope_ref="task-plan",
                                                prevention_monitoring_adequate=True)])
    with pytest.raises(GoalEvidenceProductionError, match="scope"):
        produce_goal_evidence(snapshot, [_event(ScopeClosureEvent, task, closed_scope_ref="another-task")])


def test_adequate_prevention_monitoring_satisfies_an_avoid_goal_at_closure() -> None:
    case = _task_case("send email", negative=True)
    snapshot = respond(case).snapshot
    stop = only(snapshot, STOP)
    adequate, _ = _apply(case, snapshot, [_event(ScopeClosureEvent, stop, closed_scope_ref="task-plan",
                                                 prevention_monitoring_adequate=True)])
    assert _record(adequate.snapshot, stop).status == GoalStatus.SATISFIED
    inadequate, _ = _apply(case, snapshot, [_event(ScopeClosureEvent, stop, closed_scope_ref="task-plan",
                                                   prevention_monitoring_adequate=False)])
    assert _record(inadequate.snapshot, stop).status == GoalStatus.EXPIRED


def test_repair_is_observed_on_delivery_and_satisfied_only_by_explicit_confirmation() -> None:
    case = _repair_case()
    snapshot = respond(case).snapshot
    repair = only(snapshot, REPAIR)
    assert snapshot.focus_decision.primary_goal_ref == repair.goal_id
    delivered, _ = _apply(case, snapshot, [_event(ResponseDeliveredEvent, repair, delivered_turn_ref=TURN,
                                                  delivery_ref="d", addressed_target_ref="f-fix",
                                                  validated_content_ref="v")])
    assert _record(delivered.snapshot, repair).status == GoalStatus.ACTIVE
    assert _assessment(delivered.snapshot, repair).evidence_refs
    confirmed, _ = _apply(case, delivered.snapshot, [_event(UserConfirmationEvent, repair, "e2",
                                                            confirmed_target_ref="f-fix", user_message_ref="m")])
    assert _record(confirmed.snapshot, repair).status == GoalStatus.SATISFIED
    with pytest.raises(GoalEvidenceProductionError, match="different"):
        produce_goal_evidence(snapshot, [_event(UserConfirmationEvent, repair, confirmed_target_ref="f-other",
                                                user_message_ref="m")])


def test_grounding_and_contradiction_resolution_must_name_their_exact_target() -> None:
    frames = [frame("f-send", "action_request", unresolved=("recipient",), requested_effect="send plan", subject_ref="plan")]
    case = make_case(TURN, frames=frames, task_ref="task-plan", unknowns=[unknown("u-recipient", "recipient")],
                     contradictions=[contradiction("c-days")], hypotheses=[
                         hyp("h-do", "act.request_action", frames=("f-send",)),
                         hyp("h-cause", "goal.cause_action", frames=("f-send",))])
    snapshot = respond(case).snapshot
    subgoal, conflict = only(snapshot, UNKNOWN), only(snapshot, CONTRADICTION)
    with pytest.raises(GoalEvidenceProductionError, match="different argument"):
        produce_goal_evidence(snapshot, [_event(ArgumentGroundedEvent, subgoal, argument_ref="u-other", grounding_ref="g")])
    grounded, _ = _apply(case, snapshot, [_event(ArgumentGroundedEvent, subgoal, argument_ref="u-recipient", grounding_ref="g")])
    assert _record(grounded.snapshot, subgoal).status == GoalStatus.SATISFIED
    assert target(subgoal, "argument_ref") == "u-recipient"
    with pytest.raises(GoalEvidenceProductionError, match="different contradiction"):
        produce_goal_evidence(snapshot, [_event(ContradictionResolvedEvent, conflict, contradiction_ref="c-x", outcome_id="r")])
    resolved, _ = _apply(case, snapshot, [_event(ContradictionResolvedEvent, conflict, contradiction_ref="c-days", outcome_id="r")])
    assert _record(resolved.snapshot, conflict).status == GoalStatus.SATISFIED


def test_evidence_for_another_goal_requirement_conversation_or_terminal_goal_is_rejected() -> None:
    case = _task_case("make plan", "add list")
    snapshot = respond(case).snapshot
    first, second = _task(snapshot, "make plan"), _task(snapshot, "add list")
    good = dict(outcome_id="o", status="succeeded", final=True, authorized=True, effect_ref="make plan")
    with pytest.raises(GoalEvidenceProductionError, match="unknown goal"):
        produce_goal_evidence(snapshot, [_event(ToolResultEvent, first, **good).model_copy(update={"target_goal_ref": "missing"})])
    with pytest.raises(GoalEvidenceProductionError, match="requirement of another goal"):
        produce_goal_evidence(snapshot, [_event(ToolResultEvent, first, **good).model_copy(
            update={"target_requirement_ref": requirement(second).requirement_id})])
    with pytest.raises(GoalEvidenceProductionError, match="another conversation"):
        produce_goal_evidence(snapshot, [_event(ToolResultEvent, first, **good).model_copy(update={"conversation_ref": "other"})])
    with pytest.raises(GoalEvidenceProductionError, match="unique"):
        produce_goal_evidence(snapshot, [_event(ToolResultEvent, first, **good), _event(ToolResultEvent, second, **good)])
    satisfied, _ = _apply(case, snapshot, [_event(ToolResultEvent, first, **good)])
    with pytest.raises(GoalEvidenceProductionError, match="not active or suspended"):
        produce_goal_evidence(satisfied.snapshot, [_event(ToolResultEvent, first, "e9", **good)])


def test_mixed_triggers_cannot_share_one_revision() -> None:
    snapshot = respond(_task_case("make plan")).snapshot
    task = only(snapshot, TASK)
    with pytest.raises(GoalEvidenceProductionError, match="one trigger"):
        trigger_for([_event(UserWithdrawalEvent, task, user_message_ref="m"),
                     _event(ScopeClosureEvent, task, "e2", closed_scope_ref="task-plan")])


def test_reconciliation_is_invariant_under_evidence_ordering() -> None:
    case = _task_case("make plan", "add list")
    snapshot = respond(case).snapshot
    obligation = only(snapshot, RESPONSE)
    first, second = _task(snapshot, "make plan"), _task(snapshot, "add list")
    tool = dict(status="succeeded", final=True, authorized=True)
    events = [
        _event(ResponseDeliveredEvent, obligation, "d", delivered_turn_ref=TURN, delivery_ref="delivery"),
        _event(ToolResultEvent, first, "t-wrong", outcome_id="o1", effect_ref="unrelated", **tool),
        _event(ToolResultEvent, first, "t-right", outcome_id="o2", effect_ref="make plan", **tool),
        _event(ToolResultEvent, second, "t-second", outcome_id="o3", effect_ref="add list", alternate_route_available=True,
               **{**tool, "status": "failed"}),
    ]
    produced = {produce_goal_evidence(snapshot, list(order)) for order in itertools.permutations(events)}
    assert len(produced) == 1
    [evidence] = produced
    snapshots = set()
    for order in itertools.permutations(evidence):
        result = revise(case, snapshot, GoalUpdateTrigger.REALIZATION_OR_OUTCOME, list(order), engine=GoalEngine())
        snapshots.add(result.snapshot.model_dump_json())
    assert len(snapshots) == 1
    final = revise(case, snapshot, GoalUpdateTrigger.REALIZATION_OR_OUTCOME, evidence).snapshot
    # The observation for the other effect never reopens the satisfied requirement.
    assert _record(final, first).status == GoalStatus.SATISFIED
    assert _record(final, second).status == GoalStatus.ACTIVE
    assert _record(final, obligation).status == GoalStatus.SATISFIED
