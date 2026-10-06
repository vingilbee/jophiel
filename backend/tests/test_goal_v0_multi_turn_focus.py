"""Ten uninterrupted turns: reconciliation, rollover, and focus precedence (SH-EVAL-014).

The evaluation found a task-scoped repair goal primary on six consecutive
turns, displacing every current-turn response obligation.  These traces run
one conversation through task creation, constraints, a correction, questions,
new requests, a topic shift, and closure, and check that focus always belongs
to the current turn while unresolved history stays active and visible.
"""

from __future__ import annotations

import uuid

from app.ai.Cognitive.system.v0.contracts.goals import GoalStatus, GoalUpdateTrigger
from app.ai.Cognitive.system.v0.goal_v0.evidence_producer import (
    ResponseDeliveredEvent,
    ScopeClosureEvent,
    produce_goal_evidence,
    trigger_for,
)
from app.ai.Cognitive.system.v0.live_shadow.models import TerminalStatus
from cognitive_shadow_support import make_capture
from goal_v0_support import frame, hyp, make_case, requirement, respond, revise, rollover_for
from test_cognitive_shadow_compiler import _as_predecessor, _observe

RESPONSE = "interaction.discharge_response_obligation"
ANSWER = "interaction.answer_information_request"
TASK = "task.complete_authorized_request"
REPAIR = "interaction.repair_shared_understanding"
PRECEDENCE = "goal-priority.current-explicit-request-over-compatible-history.v1"
ROLLOVER_RULE = "goal-lifecycle.expire-turn-scope-on-successor-turn.v1"
TASK_REF = "task-meal-plan"
CONVERSATION = "goal-test-conversation"


def _act(turn, predicate, act, goal, *, modality="modality.asserted", goal_support="strong", **arguments):
    fid = f"{turn}-frame"
    return make_case(turn, task_ref=TASK_REF, frames=[frame(fid, predicate, **arguments)], hypotheses=[
        hyp(f"{turn}-act", act, frames=(fid,)),
        hyp(f"{turn}-goal", goal, goal_support, frames=(fid,)),
        hyp(f"{turn}-modality", modality, frames=(fid,)),
    ])


# (label, case, definition the turn's explicit request forms, or None)
TURNS = (
    ("task creation", _act("turn-01", "action_request", "act.request_action", "goal.cause_action", modality="modality.requested",
                           requested_effect="create meal plan", subject_ref="this week"), TASK),
    ("added constraint", _act("turn-02", "constraint_statement", "act.inform", "goal.transfer_information",
                              constraint="under 30 minutes per meal", constraint_kind="time"), None),
    ("correction", _act("turn-03", "correction", "act.correct", "goal.repair_shared_understanding",
                        corrected_attribute="servings", corrected_value="two", prior_value="one"), REPAIR),
    ("question after correction", _act("turn-04", "information_request", "act.request_information", "goal.obtain_information",
                                       modality="modality.questioned", topic_ref="daily protein of the plan", question_form="wh"), ANSWER),
    ("new compatible request", _act("turn-05", "action_request", "act.request_action", "goal.cause_action", modality="modality.requested",
                                    requested_effect="add shopping list", subject_ref="meal plan"), TASK),
    ("additional constraint", _act("turn-06", "constraint_statement", "act.inform", "goal.transfer_information",
                                   constraint="no dairy", constraint_kind="dietary"), None),
    ("explicit action request", _act("turn-07", "action_request", "act.request_action", "goal.cause_action",
                                     modality="modality.requested", requested_effect="create reminder",
                                     subject_ref="Sunday shopping"), TASK),
    ("summary request", _act("turn-08", "elaboration_request", "act.request_information", "goal.obtain_information",
                             modality="modality.questioned", referent_phrase="the plan", elaboration_kind="summary"), ANSWER),
    ("topic shift", _act("turn-09", "information_request", "act.request_information", "goal.obtain_information",
                         modality="modality.questioned", topic_ref="capital of France", question_form="wh"), ANSWER),
    ("closure", _act("turn-10", "social_closing", "act.close", "goal.maintain_social_connection", goal_support="moderate",
                     future_contact="tomorrow"), None),
)


def _event(cls, goal, turn, event_id, **fields):
    return cls(event_id=event_id, producer="trusted-runtime", source_ref=f"source:{event_id}", conversation_ref=CONVERSATION,
               occurred_turn_ref=turn, target_goal_ref=goal.goal_id,
               target_requirement_ref=requirement(goal).requirement_id, **fields)


def _run(state=None):
    """The complete trace: ten response turns, one delivery, one closure."""

    steps, previous = [], None
    for label, case, formed in TURNS:
        turn = case["turn_context"]["cognitive_turn_id"]
        rollover = rollover_for(previous, case)
        result = respond(case, previous, state=state)
        steps.append({"label": label, "turn": turn, "formed": formed, "result": result, "rollover": rollover})
        previous = result.snapshot
        if label == "correction":
            # The repair reply was delivered: observed against the repair,
            # which still needs the user's confirmation to be satisfied.
            repair = next(item for item in previous.records if item.definition_ref.definition_key == REPAIR)
            events = [_event(ResponseDeliveredEvent, repair, turn, "delivery-turn-03", delivered_turn_ref=turn,
                             delivery_ref="channel-delivery-03", addressed_target_ref="turn-03-frame")]
            previous = revise(case, previous, trigger_for(events), produce_goal_evidence(previous, events), state=state).snapshot
            steps[-1]["delivered"] = previous
    last_case = TURNS[-1][1]
    open_task_goals = [item for item in previous.records if item.scope.value == "task" and item.status == GoalStatus.ACTIVE]
    events = [_event(ScopeClosureEvent, item, "turn-10", f"closure-{index}", closed_scope_ref=TASK_REF)
              for index, item in enumerate(open_task_goals)]
    closure = revise(last_case, previous, trigger_for(events), produce_goal_evidence(previous, events), state=state)
    return steps, closure, open_task_goals


def _by_id(snapshot):
    return {item.goal_id: item for item in snapshot.records}


def test_ten_uninterrupted_turns_keep_focus_on_the_current_turn() -> None:
    steps, closure, open_task_goals = _run()
    repair_id = None
    older_tasks: list[str] = []
    for index, step in enumerate(steps):
        snapshot = step["result"].snapshot
        focus = snapshot.focus_decision
        records = _by_id(snapshot)
        primary = records[focus.primary_goal_ref]
        turn = step["turn"]

        # SH-EVAL-014: in a response turn, focus always belongs to this turn.
        assert primary.created_turn_ref == turn, step["label"]
        if step["formed"] is not None:
            assert primary.definition_ref.definition_key == step["formed"], step["label"]
        else:
            assert primary.definition_ref.definition_key == RESPONSE, step["label"]
        assert len(focus.supporting_goal_refs) <= 2

        # The floor is this turn's obligation, and every focus evidence ref is
        # owned by an exact focal or floor record.
        floor = records[focus.coordination_floor_goal_ref]
        assert (floor.definition_ref.definition_key, floor.scope_ref, floor.status) == (RESPONSE, turn, GoalStatus.ACTIVE)
        owned = {ref for ref in (focus.primary_goal_ref, *focus.supporting_goal_refs, floor.goal_id) for ref in records[ref].source_refs}
        assert set(focus.evidence_refs) <= owned and focus.evidence_refs == floor.source_refs

        # Earlier obligations expired through the trusted rollover, never satisfied.
        earlier = [item for item in snapshot.records if item.definition_ref.definition_key == RESPONSE and item.scope_ref != turn]
        assert len(earlier) == index
        assert all(item.status == GoalStatus.EXPIRED for item in earlier)
        if index:
            rollover = step["rollover"]
            expired = [item for item in step["result"].transitions if item.rule_id == ROLLOVER_RULE]
            assert expired and all(item.evidence_refs == (rollover.rollover_id,) for item in expired)
            assert {item.goal_id for item in expired} >= {
                item.goal_id for item in steps[index - 1]["result"].snapshot.records
                if item.definition_ref.definition_key == RESPONSE and item.scope_ref == steps[index - 1]["turn"]
            }

        # Unresolved history stays active (never silently deleted) and is
        # ranked below the current turn, citing the precedence rule.
        for goal_id in older_tasks + ([repair_id] if repair_id else []):
            assert records[goal_id].status == GoalStatus.ACTIVE, (step["label"], goal_id)
            assert goal_id != focus.primary_goal_ref
        if older_tasks or repair_id:
            assert PRECEDENCE in focus.rule_ids
        if step["formed"] == TASK:
            older_tasks.append(primary.goal_id)
        if step["formed"] == REPAIR:
            repair_id = primary.goal_id

    # The repair was primary exactly once: on the turn that formed it.
    assert [step["label"] for step in steps if step["result"].snapshot.focus_decision.primary_goal_ref == repair_id] == ["correction"]
    delivered = next(step["delivered"] for step in steps if "delivered" in step)
    assessment = next(item for item in delivered.requirement_assessments if item.goal_ref == repair_id)
    assert assessment.evidence_refs and _by_id(delivered)[repair_id].status == GoalStatus.ACTIVE

    # Explicit closure expires the unresolved task-scoped goals; it satisfies nothing
    # and creates no response obligation.
    assert {item.goal_id for item in open_task_goals} == {*older_tasks, repair_id}
    final = _by_id(closure.snapshot)
    assert all(final[item.goal_id].status == GoalStatus.EXPIRED for item in open_task_goals)
    assert not closure.proposals
    assert not any(item.transition_kind.value == "satisfy" for item in closure.transitions)
    assert all(item.rule_id == "goal-lifecycle.expire-task-scope.v1" and item.evidence_refs for item in closure.transitions)
    assert closure.snapshot.focus_decision.primary_goal_ref == steps[-1]["result"].snapshot.focus_decision.primary_goal_ref


def test_dynamic_state_does_not_influence_any_goal_decision() -> None:
    def excited(state):
        values = tuple(item.model_copy(update={"score": 90 if item.score is not None else None}) for item in state.values)
        return state.model_copy(update={"values": values})

    def neutral_dump(snapshot):
        data = snapshot.model_dump(mode="json")
        data.pop("previous_dynamic_state_ref")
        return data

    baseline, baseline_closure, _ = _run()
    varied, varied_closure, _ = _run(state=excited)
    for left, right in zip(baseline, varied):
        assert neutral_dump(left["result"].snapshot) == neutral_dump(right["result"].snapshot)
    assert neutral_dump(baseline_closure.snapshot) == neutral_dump(varied_closure.snapshot)


# ---------------------------------------------------------------------------
# The same ten messages through the durable live-shadow observation path
# ---------------------------------------------------------------------------

NAMESPACE = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
MESSAGES = (
    "Can you make me a meal plan for this week?",
    "It must take under 30 minutes per meal.",
    "Actually, make it for two people instead of one.",
    "How much protein will that give me per day?",
    "Can you also add a shopping list?",
    "No dairy, please.",
    "Please create a reminder for Sunday to go shopping.",
    "Can you summarize the plan?",
    "What's the capital of France?",
    "Yes, that's right, thanks.",
)


def _fixed(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(parts)))


def test_live_shadow_ten_turn_trace_never_locks_focus_on_history() -> None:
    contents: list[tuple[str, str]] = []
    message_ids: list[str] = []
    previous_record = previous_capture = None
    for index, content in enumerate(MESSAGES):
        if index:
            contents.append(("assistant", f"reply {index}"))
            message_ids.append(_fixed("a", str(index)))
        contents.append(("user", content))
        message_ids.append(_fixed("m", str(index)))
        capture = make_capture(
            user_ref=_fixed("u"), conversation_ref=_fixed("c"), turn_id=_fixed("t", str(index)),
            contents=tuple(contents), message_ids=tuple(message_ids),
            previous_user_message_id=previous_capture.focus_message_id if previous_capture else None,
            chat_job_ref=_fixed("j", str(index)), captured_at="2026-09-26T00:00:00+00:00",
        )
        predecessor = _as_predecessor(previous_record, previous_capture) if previous_record else None
        record = _observe(capture, predecessor)
        assert record.status == TerminalStatus.COMPLETED, record.failure
        if index:
            assert record.segment_reset_reason is None and record.segment_id == previous_record.segment_id
        goals = record.pipeline_trace.goal_snapshot
        records = _by_id(goals)
        focus = goals.focus_decision
        assert records[focus.primary_goal_ref].created_turn_ref == capture.turn_id
        assert all(records[ref].status == GoalStatus.ACTIVE for ref in goals.focal_goal_refs)
        floor = records[focus.coordination_floor_goal_ref]
        assert floor.scope_ref == capture.turn_id and focus.evidence_refs == floor.source_refs
        assert [
            item.scope_ref for item in goals.records
            if item.definition_ref.definition_key == RESPONSE and item.status == GoalStatus.ACTIVE
        ] == [capture.turn_id]
        previous_record, previous_capture = record, capture
