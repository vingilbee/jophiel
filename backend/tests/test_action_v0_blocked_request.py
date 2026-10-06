"""Blocked user goals reach Action as typed planning conditions (SH-EVAL-006).

When the leading request's user goal is rejected for a typed missing input,
the primary move must target that exact blocker (targeted clarification,
limitation disclosure, or repair).  A generic receipt acknowledgement never
earns direct coverage of the blocked request, but stays selectable for
genuine receipt acts, and answers stay preferred when grounding exists.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from action_v0_support import result_for
from app.ai.Cognitive.system.v0.action_selection_v0.planning_conditions import derive_planning_conditions
from app.ai.Cognitive.system.v0.contracts.actions import ActionPersonalityProfile, PlanningConditionKind as K
from app.ai.Cognitive.system.v0.contracts.interpretation import Contradiction
from app.ai.Cognitive.system.v0.live_shadow.compiler import compile_request, resolve_lineage
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from cognitive_shadow_support import make_capture


def _trace(content: str, *, prior: str | None = None, runtime: dict | None = None, personality: dict | None = None,
           capture=None):
    contents = ((("assistant", prior),) if prior else ()) + (("user", content),)
    capture = capture or make_capture(contents=contents)
    request = compile_request(capture, resolve_lineage(capture, None))
    if runtime:
        request = request.model_copy(update={"runtime": request.runtime.model_copy(update=runtime)})
    if personality:
        profile = ActionPersonalityProfile.model_validate({
            **request.action_personality.model_dump(mode="python"), "status": "known", **personality,
        })
        request = request.model_copy(update={"action_personality": profile})
    return build_pipeline(capture.turn_id).run(request)


def _primary(selection):
    return next(move for move in selection.plan.moves if move.move_id == selection.plan.primary_move_ref)


def _assert_primary_targets_blocker(selection, kind: K) -> None:
    [condition] = [item for item in selection.planning_conditions if item.kind == kind]
    primary = _primary(selection)
    assert {target.ref for target in primary.target_refs} & set(condition.blocker_refs)
    assert primary.definition_ref.action_key != "give_feedback.acknowledge"


def test_unresolved_topic_is_a_user_input_blocker_addressed_by_the_primary_move() -> None:
    # The rule grounder leaves the topic unresolved.  An unknown topic is a
    # user-input problem (planner v0.3); only a *known* topic without a
    # trusted answer is a missing trusted fact, and that comes solely from
    # requirement resolution -- the two are never conflated.
    selection = _trace("How much protein is in two eggs?").action_selection
    [condition] = selection.planning_conditions
    assert condition.kind == K.MISSING_ARGUMENT
    assert condition.blocked_goal_definition == "interaction.answer_information_request"
    assert condition.rejection_rule_id == "goal-reject.missing-required-grounding.v1"
    assert condition.evidence_refs and condition.frame_refs
    _assert_primary_targets_blocker(selection, K.MISSING_ARGUMENT)
    first = selection.steps[0]
    by_id = {item.candidate_id: item for item in first.constructed_candidates}
    restricted = {by_id[ref].definition_ref.action_key for ref in first.condition_restricted_candidate_refs}
    assert "give_feedback.acknowledge" in restricted
    # Receipt earns only supporting coverage of the blocked request.
    ack = next(item for item in first.scored_candidates
               if by_id[item.candidate_ref].definition_ref.action_key == "give_feedback.acknowledge")
    selected = next(item for item in first.scored_candidates if item.candidate_ref == first.selected_candidate_ref)
    assert ack.feature("expected_goal_progress") == Decimal("0.5") and ack.feature("goal_fit") == Decimal("0.5")
    assert selected.feature("goal_fit") == Decimal("1")


def test_missing_argument_is_clarified_at_the_exact_argument() -> None:
    selection = _trace("What time does the museum close?").action_selection
    [condition] = selection.planning_conditions
    assert condition.kind == K.MISSING_ARGUMENT
    assert any(ref.endswith("#museum") for ref in condition.blocker_refs)
    _assert_primary_targets_blocker(selection, K.MISSING_ARGUMENT)
    assert _primary(selection).definition_ref.action_key == "seek_information.clarify"


def test_unsupported_domain_request_targets_the_unresolved_predicate() -> None:
    selection = _trace("Zorbulate this.", runtime={"task_ref": "task:shadow-test"}).action_selection
    kinds = {item.kind for item in selection.planning_conditions}
    assert kinds == {K.UNSUPPORTED_DOMAIN}
    _assert_primary_targets_blocker(selection, K.UNSUPPORTED_DOMAIN)


def test_missing_authorization_is_recorded_and_denies_receipt_full_credit() -> None:
    trace = _trace("Could you send the report to Alex?",
                   runtime={"task_ref": "task:shadow-test", "authorization": "denied"})
    selection = trace.action_selection
    [condition] = selection.planning_conditions
    assert condition.kind == K.MISSING_AUTHORIZATION
    assert condition.rejection_rule_id == "goal-reject.hard-gate-denial.v1"
    # No move can target an absent authorization argument, so nothing is
    # restricted -- but no generic move earns direct coverage either.
    assert not any(step.condition_restricted_candidate_refs for step in selection.steps)
    rules = {link.rule_id for step in selection.steps if step.transition_basis
             for link in step.transition_basis.goal_links}
    assert "link.response-obligation.any-move.v1" not in rules


def test_conflicting_fact_on_a_blocked_goal_becomes_its_own_condition() -> None:
    trace = _trace("How much protein is in two eggs?")
    request = trace.cognitive_turn.input_snapshot
    # The derivation reads only the typed Goal and Interpretation records.
    action_request = SimpleNamespace(
        cognitive_turn_id=request.cognitive_turn_id, interpretation=request.interpretation,
        goal_snapshot=request.goal_snapshot,
    )
    [frame_id] = derive_planning_conditions(action_request)[0].frame_refs
    frame = next(item for item in request.interpretation.semantic_frames if item.frame_id == frame_id)
    contradiction = Contradiction(
        contradiction_id="interpretation-contradiction-test", status="active",
        description="A trusted record contradicts the question's premise.", evidence_refs=frame.evidence_refs,
    )
    conflicted = SimpleNamespace(
        cognitive_turn_id=request.cognitive_turn_id, goal_snapshot=request.goal_snapshot,
        interpretation=request.interpretation.model_copy(update={"contradictions": (contradiction,)}),
    )
    kinds = {item.kind: item for item in derive_planning_conditions(conflicted)}
    assert set(kinds) == {K.MISSING_ARGUMENT, K.CONFLICTING_FACT}
    assert kinds[K.CONFLICTING_FACT].blocker_refs == ("interpretation-contradiction-test",)


def test_genuine_receipt_is_still_acknowledged() -> None:
    selection = _trace("Okay.", prior="Eggs have about six grams of protein each.").action_selection
    assert selection.planning_conditions == ()
    assert [move.definition_ref.action_key for move in selection.plan.moves] == ["give_feedback.acknowledge"]
    assert not any(step.condition_restricted_candidate_refs for step in selection.steps)


def test_answer_remains_preferred_when_grounding_exists() -> None:
    result = result_for("action-selection-prior-failure")
    assert result.planning_conditions == ()
    primary = next(move for move in result.plan.moves if move.move_id == result.plan.primary_move_ref)
    assert primary.definition_ref.action_key == "provide_information.answer"


@pytest.mark.parametrize("personality", [
    {"curiosity": 0.05, "warmth": 0.95, "playfulness": 0.95, "directness": 0.05},
    {"curiosity": 0.95, "warmth": 0.05, "playfulness": 0.05, "directness": 0.95},
    {"curiosity": 0.5, "warmth": 0.5, "playfulness": 0.5, "directness": 0.5},
])
def test_personality_changes_utility_but_never_hard_eligibility(personality: dict) -> None:
    capture = make_capture(contents=(("user", "How much protein is in two eggs?"),))
    baseline = _trace("", capture=capture).action_selection
    varied = _trace("", capture=capture, personality=personality).action_selection
    assert varied.planning_conditions == baseline.planning_conditions

    def restricted_keys(selection):
        first = selection.steps[0]
        by_id = {item.candidate_id: item for item in first.constructed_candidates}
        return sorted(by_id[ref].lexical_identity for ref in first.condition_restricted_candidate_refs)

    assert restricted_keys(varied) == restricted_keys(baseline)
    _assert_primary_targets_blocker(varied, K.MISSING_ARGUMENT)


def test_restriction_is_part_of_the_validated_step_contract() -> None:
    selection = _trace("How much protein is in two eggs?").action_selection
    first = selection.steps[0]
    payload = first.model_dump(mode="python")
    # Selecting a restricted candidate is not the recorded policy argmax.
    payload["condition_restricted_candidate_refs"] = tuple(
        item.candidate_ref for item in first.scored_candidates if item.candidate_ref == first.selected_candidate_ref
    )
    with pytest.raises(ValueError):
        type(first).model_validate(payload)
    # Engine output survives a JSON round trip with its conditions.
    assert type(selection).model_validate_json(selection.model_dump_json()) == selection
