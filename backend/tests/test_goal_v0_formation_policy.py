"""Frame-driven Goal V0 candidate formation (formation policy v0.3).

Each candidate is bound to the frames of its own act; compound contributions
keep independent candidates, and an unresolved argument forms an epistemic
subgoal only for its exact parent and argument.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.contracts.goals import GoalStatus
from app.ai.Cognitive.system.v0.goal_v0.formation_policy import (
    FORMATION_ENTRIES,
    GoalFormationEntry,
    validate_formation_manifest,
)
from app.ai.Cognitive.system.v0.goal_v0.ontology import all_definitions
from goal_v0_support import frame, hyp, make_case, only, records, rejection, respond, scenario, target, unknown

ANSWER = "interaction.answer_information_request"
TASK = "task.complete_authorized_request"
STOP = "task.prevent_or_stop_requested_action"
REPAIR = "interaction.repair_shared_understanding"
SOCIAL = "interaction.respond_to_social_contact"
UNKNOWN = "epistemic.resolve_required_unknown"
RESPONSE = "interaction.discharge_response_obligation"
USER_KEYS = {ANSWER, TASK, STOP, REPAIR, SOCIAL, "interaction.acknowledge_relevant_information"}


def _user_goals(snapshot):
    return [item for item in snapshot.records if item.definition_ref.definition_key in USER_KEYS]


def test_manifest_is_closed_over_the_installed_definitions() -> None:
    assert {item.definition_key for item in FORMATION_ENTRIES} == {item.definition_key for item in all_definitions()}
    assert validate_formation_manifest() == FORMATION_ENTRIES
    with pytest.raises(ValueError):
        validate_formation_manifest(FORMATION_ENTRIES[1:])
    with pytest.raises(ValueError, match="act hypothesis"):
        GoalFormationEntry.model_validate({**FORMATION_ENTRIES[1].model_dump(), "act_hypothesis_key": None})
    with pytest.raises(ValueError, match="target roles"):
        validate_formation_manifest(tuple(
            item.model_copy(update={"target_bindings": ()}) if item.definition_key == ANSWER else item
            for item in FORMATION_ENTRIES
        ))


def test_information_request_binds_its_own_topic() -> None:
    frames = [frame("f-ask", "information_request", topic_ref="protein in two eggs", question_form="wh")]
    hypotheses = [hyp("h-ask", "act.request_information", frames=("f-ask",)),
                  hyp("h-obtain", "goal.obtain_information", frames=("f-ask",))]
    answer = only(respond(make_case("ask-turn", frames=frames, hypotheses=hypotheses)).snapshot, ANSWER)
    assert answer.status == GoalStatus.ACTIVE
    assert target(answer, "topic_ref") == "protein in two eggs"


def test_unresolved_topic_is_not_invented() -> None:
    frames = [frame("f-ask", "information_request", unresolved=("topic_ref",), question_form="wh")]
    hypotheses = [hyp("h-ask", "act.request_information", frames=("f-ask",)),
                  hyp("h-obtain", "goal.obtain_information", frames=("f-ask",))]
    result = respond(make_case("ask-turn", frames=frames, hypotheses=hypotheses))
    answer = only(result.snapshot, ANSWER)
    assert answer.status == GoalStatus.REJECTED
    # The frame itself is the reference; no topic text is fabricated.
    assert target(answer, "topic_ref") == "f-ask"
    assert rejection(result, answer.goal_id).rejection_detail.argument_name == "topic_ref"


def test_correction_forms_a_repair_bound_to_the_correction_frame() -> None:
    frames = [frame("f-fix", "correction", corrected_attribute="servings", corrected_value="two")]
    hypotheses = [hyp("h-fix", "act.correct", frames=("f-fix",)),
                  hyp("h-repair", "goal.repair_shared_understanding", frames=("f-fix",))]
    repair = only(respond(make_case("fix-turn", frames=frames, hypotheses=hypotheses, task_ref="task-plan")).snapshot, REPAIR)
    assert repair.status == GoalStatus.ACTIVE
    assert (target(repair, "proposition_ref"), repair.scope.value, repair.scope_ref) == ("f-fix", "task", "task-plan")


def test_greeting_plus_request_keeps_independent_candidates() -> None:
    frames = [frame("f-hello", "social_opening"),
              frame("f-ask", "information_request", topic_ref="calories in rice", question_form="wh")]
    hypotheses = [
        hyp("h-greet", "act.greet", frames=("f-hello",)),
        hyp("h-social", "goal.maintain_social_connection", "moderate", frames=("f-hello",)),
        hyp("h-ask", "act.request_information", frames=("f-ask",)),
        hyp("h-obtain", "goal.obtain_information", frames=("f-ask",)),
    ]
    snapshot = respond(make_case("compound-turn", frames=frames, hypotheses=hypotheses)).snapshot
    answer, social = only(snapshot, ANSWER), only(snapshot, SOCIAL)
    assert answer.status == social.status == GoalStatus.ACTIVE
    assert target(answer, "topic_ref") == "calories in rice"
    assert snapshot.focus_decision.primary_goal_ref == answer.goal_id


def test_two_independent_acts_own_separate_frames() -> None:
    frames = [frame("f-hello", "social_opening", addressee="Columbina"),
              frame("f-fix", "correction", corrected_attribute="day", corrected_value="Sunday")]
    hypotheses = [
        hyp("h-greet", "act.greet", frames=("f-hello",)),
        hyp("h-social", "goal.maintain_social_connection", "moderate", frames=("f-hello",)),
        hyp("h-fix", "act.correct", frames=("f-fix",)),
        hyp("h-repair", "goal.repair_shared_understanding", frames=("f-fix",)),
    ]
    snapshot = respond(make_case("two-acts-turn", frames=frames, hypotheses=hypotheses)).snapshot
    repair, social = only(snapshot, REPAIR), only(snapshot, SOCIAL)
    assert target(repair, "proposition_ref") == "f-fix"
    assert target(social, "dialogue_scope_ref") == "goal-test-conversation"
    assert len(_user_goals(snapshot)) == 2


def test_two_questions_form_two_answers_with_their_own_topics() -> None:
    frames = [frame("f-one", "information_request", topic_ref="fat in avocado", question_form="wh"),
              frame("f-two", "information_request", topic_ref="carbs in rice", question_form="wh")]
    hypotheses = [hyp("h-ask", "act.request_information", frames=("f-one", "f-two")),
                  hyp("h-obtain", "goal.obtain_information", frames=("f-one", "f-two"))]
    answers = records(respond(make_case("two-q-turn", frames=frames, hypotheses=hypotheses)).snapshot, ANSWER)
    assert sorted(target(item, "topic_ref") for item in answers) == ["carbs in rice", "fat in avocado"]
    assert all(item.status == GoalStatus.ACTIVE for item in answers)


def test_grounded_action_request_binds_its_requested_effect() -> None:
    frames = [frame("f-do", "action_request", requested_effect="create reminder", subject_ref="Sunday shopping")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-do",)), hyp("h-cause", "goal.cause_action", frames=("f-do",))]
    task = only(respond(make_case("do-turn", frames=frames, hypotheses=hypotheses, task_ref="task-plan")).snapshot, TASK)
    assert task.status == GoalStatus.ACTIVE
    assert task.requirements[0].parameters == {"requested_effect": "create reminder"}
    assert (task.scope.value, task.scope_ref) == ("task", "task-plan")


def test_an_action_request_without_a_trusted_task_forms_no_task_goal() -> None:
    frames = [frame("f-do", "action_request", requested_effect="create reminder", subject_ref="it")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-do",)), hyp("h-cause", "goal.cause_action", frames=("f-do",))]
    assert not records(respond(make_case("do-turn", frames=frames, hypotheses=hypotheses)).snapshot, TASK)


def test_unresolved_action_argument_forms_the_exact_epistemic_subgoal() -> None:
    frames = [frame("f-send", "action_request", unresolved=("recipient",), requested_effect="send summary",
                    subject_ref="meal plan")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-send",)), hyp("h-cause", "goal.cause_action", frames=("f-send",))]
    unknowns = [unknown("u-recipient", "recipient"), unknown("u-other", "time_zone")]
    # An unrelated older task goal stays active across turns; it must never
    # become the parent (the removed rule took the first active goal).
    older = respond(make_case("older-turn", frames=[frame("f-a", "action_request", requested_effect="make plan", subject_ref="week")],
                              hypotheses=[hyp("h-a", "act.request_action", frames=("f-a",)),
                                          hyp("h-c", "goal.cause_action", frames=("f-a",))],
                              task_ref="task-plan"))
    older_task = only(older.snapshot, TASK)
    case = make_case("send-turn", frames=frames, hypotheses=hypotheses, unknowns=unknowns, task_ref="task-plan")
    result = respond(case, older.snapshot)
    assert next(item for item in result.snapshot.records if item.goal_id == older_task.goal_id).status == GoalStatus.ACTIVE
    [parent] = [item for item in records(result.snapshot, TASK) if item.created_turn_ref == "send-turn"]
    subgoal = only(result.snapshot, UNKNOWN)
    assert parent.status == subgoal.status == GoalStatus.ACTIVE
    assert target(subgoal, "parent_goal_ref") == parent.goal_id
    assert target(subgoal, "argument_ref") == "u-recipient"
    assert subgoal.requirements[0].parameters == {"argument_name": "recipient"}
    # It blocks the current request, so it is focused first.
    assert result.focus_decision.primary_goal_ref == subgoal.goal_id
    assert "goal-priority.blocking-required-subgoal.v1" in result.focus_decision.rule_ids


def test_an_ambiguous_unknown_is_never_guessed() -> None:
    frames = [frame("f-send", "action_request", unresolved=("recipient",), requested_effect="send summary",
                    subject_ref="meal plan")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-send",)), hyp("h-cause", "goal.cause_action", frames=("f-send",))]
    unknowns = [unknown("u-one", "recipient"), unknown("u-two", "recipient")]
    result = respond(make_case("send-turn", frames=frames, hypotheses=hypotheses, unknowns=unknowns, task_ref="task-plan"))
    task = only(result.snapshot, TASK)
    assert task.status == GoalStatus.REJECTED
    assert rejection(result, task.goal_id).rule_id == "goal-reject.missing-required-grounding.v1"
    assert not records(result.snapshot, UNKNOWN)


def test_stop_request_forms_a_prevent_goal_and_negative_scope_does_not_split_a_request() -> None:
    stop_frames = [frame("f-stop", "action_request", polarity="negative", requested_effect="send email", subject_ref="the draft")]
    stop = [hyp("h-do", "act.request_action", frames=("f-stop",)), hyp("h-stop", "goal.prevent_or_stop_action", frames=("f-stop",))]
    snapshot = respond(make_case("stop-turn", frames=stop_frames, hypotheses=stop, task_ref="task-mail")).snapshot
    assert only(snapshot, STOP).status == GoalStatus.ACTIVE
    assert not records(snapshot, TASK)

    scoped_frames = [frame("f-send", "action_request", requested_effect="send plan", subject_ref="meal plan"),
                     frame("f-not", "action_request", polarity="negative", requested_effect="include dairy", subject_ref="plan")]
    scoped = [hyp("h-do", "act.request_action", frames=("f-send", "f-not")),
              hyp("h-cause", "goal.cause_action", frames=("f-send", "f-not"))]
    snapshot = respond(make_case("scoped-turn", frames=scoped_frames, hypotheses=scoped, task_ref="task-mail")).snapshot
    [task] = records(snapshot, TASK)
    assert task.requirements[0].parameters == {"requested_effect": "send plan"}
    assert not records(snapshot, STOP)


def test_unknown_predicate_and_mismatched_act_family_form_no_adopted_user_goal() -> None:
    # A cue-only request over an unknown frame is formed and rejected as
    # ungrounded, so Action still sees the typed blocker.
    frames = [frame("f-x", "unknown", unresolved=("predicate",), surface_action="zorbulate")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-x",)), hyp("h-cause", "goal.cause_action", frames=("f-x",))]
    result = respond(make_case("unknown-turn", frames=frames, hypotheses=hypotheses, task_ref="task-x"))
    task = only(result.snapshot, TASK)
    assert task.status == GoalStatus.REJECTED
    assert rejection(result, task.goal_id).rejection_detail.argument_name == "predicate"
    # A request act citing a frame whose registered predicate grounds another
    # act (information_request) binds nothing.
    frames = [frame("f-q", "information_request", topic_ref="a plan", question_form="polar")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-q",)), hyp("h-cause", "goal.cause_action", frames=("f-q",))]
    snapshot = respond(make_case("family-turn", frames=frames, hypotheses=hypotheses, task_ref="task-x")).snapshot
    assert not _user_goals(snapshot)
    assert [item.definition_ref.definition_key for item in snapshot.records] == [RESPONSE]


def test_quoted_instruction_never_becomes_the_speakers_action_goal() -> None:
    # Relayed content alone: the act is ``inform``; no action goal forms at all.
    frames = [frame("f-quote", "action_request", requested_effect="delete database", subject_ref="prod", embedding="quoted")]
    hypotheses = [hyp("h-inform", "act.inform", "moderate", frames=("f-quote",)),
                  hyp("h-transfer", "goal.transfer_information", "moderate", frames=("f-quote",)),
                  hyp("h-quoted", "modality.quoted", frames=("f-quote",))]
    snapshot = respond(make_case("quote-turn", frames=frames, hypotheses=hypotheses, task_ref="task-x")).snapshot
    assert not _user_goals(snapshot)
    # Beside a direct request, the quotation is scoped to its own frame.
    frames = [frame("f-do", "action_request", requested_effect="summarize note", subject_ref="the note"),
              frame("f-quote", "action_request", requested_effect="delete database", subject_ref="prod", embedding="quoted")]
    hypotheses = [hyp("h-do", "act.request_action", frames=("f-do",)), hyp("h-cause", "goal.cause_action", frames=("f-do",)),
                  hyp("h-quoted", "modality.quoted", frames=("f-quote",))]
    [task] = records(respond(make_case("mixed-turn", frames=frames, hypotheses=hypotheses, task_ref="task-x")).snapshot, TASK)
    assert task.status == GoalStatus.ACTIVE
    assert task.requirements[0].parameters == {"requested_effect": "summarize note"}


def test_duplicate_scenarios_do_not_duplicate_a_candidate() -> None:
    frames = [frame("f-ask", "information_request", topic_ref="oat fiber", question_form="wh")]
    leading = [hyp("h-ask", "act.request_information", frames=("f-ask",)), hyp("h-obtain", "goal.obtain_information", frames=("f-ask",))]
    alternative = hyp("h-tentative", "modality.tentative", "weak", frames=("f-ask",), disposition="retained_alternative")
    scenarios = [scenario("s-lead", leading, frames),
                 scenario("s-alt", [*leading, alternative], frames, disposition="retained_alternative")]
    result = respond(make_case("dup-turn", frames=frames, hypotheses=[*leading, alternative], scenarios=scenarios))
    assert len(records(result.snapshot, ANSWER)) == 1
    assert len([item for item in result.proposals if item.definition_ref.definition_key == ANSWER]) == 1
