"""Definition-specific Goal V0 adoption policies (SH-EVAL-013).

Unit tests evaluate each installed policy directly; the engine tests run the
same policies through formation, adoption, and snapshot validation.
"""

from __future__ import annotations

import itertools
import random

import pytest

from app.ai.Cognitive.system.v0.contracts.goals import (
    GOAL_ADOPTION_POLICY_VERSION,
    GoalRejectionCheck,
    GoalRejectionDetail,
    GoalStatus,
    GoalTransition,
)
from app.ai.Cognitive.system.v0.contracts.interpretation import (
    HYPOTHESIS_DIMENSION_BY_KEY,
    HypothesisDisposition,
    HypothesisSupport,
    InterpretationHypothesis,
)
from app.ai.Cognitive.system.v0.goal_v0.adoption_policy import (
    ADOPTION_POLICIES,
    AdoptionContext,
    GoalAdoptionPolicy,
    GoalHypothesisRequirement,
    UnresolvedArgument,
    adoption_policy,
    evaluate_adoption,
    validate_adoption_manifest,
)
from app.ai.Cognitive.system.v0.goal_v0.ontology import all_definitions
from goal_v0_support import frame, hyp, make_case, only, rejection, respond

S = HypothesisSupport
D = HypothesisDisposition
BANDS = (S.UNSUPPORTED, S.WEAK, S.MODERATE, S.STRONG)
SEMANTIC_POLICIES = tuple(
    key for key, policy in ADOPTION_POLICIES.items() if policy.hypothesis_requirements
)
TARGETS = {
    "interaction.discharge_response_obligation": {"turn_ref": "turn"},
    "interaction.answer_information_request": {"topic_ref": "topic"},
    "interaction.acknowledge_relevant_information": {"proposition_ref": "proposition"},
    "interaction.repair_shared_understanding": {"proposition_ref": "proposition"},
    "interaction.respond_to_social_contact": {"dialogue_scope_ref": "conversation"},
    "epistemic.resolve_required_unknown": {"parent_goal_ref": "parent", "argument_ref": "unknown"},
    "epistemic.resolve_material_contradiction": {"contradiction_ref": "contradiction"},
    "task.complete_authorized_request": {"task_ref": "task"},
    "task.prevent_or_stop_requested_action": {"task_ref": "task"},
    "interaction.explain_unavailable_or_failed_operation": {"operation_ref": "tool", "failure_ref": "failure"},
    "interaction.handle_action_request": {"request_ref": "request"},
    "interaction.fulfill_conversational_deliverable": {"request_ref": "request"},
    "interaction.incorporate_inquiry_answer": {"answer_ref": "answer"},
}


def _hypothesis(key: str, support: S = S.STRONG, disposition: D = D.LEADING, *, ident: str | None = None,
                contradictions: tuple[str, ...] = ()) -> InterpretationHypothesis:
    return InterpretationHypothesis(
        hypothesis_id=ident or f"h:{key}", hypothesis_key=key, definition_version=1,
        dimension=HYPOTHESIS_DIMENSION_BY_KEY[key], base_support=support, support=support,
        disposition=disposition, semantic_frame_refs=("frame",),
        supporting_evidence_refs=("ev",) if support != S.UNSUPPORTED else (),
        contradiction_refs=contradictions, rule_ids=("test.v1",),
    )


def _minimal(policy: GoalAdoptionPolicy) -> list[InterpretationHypothesis]:
    """Every required hypothesis exactly at its minimum support and first allowed disposition."""

    return [
        _hypothesis(item.hypothesis_key, item.minimum_support, item.allowed_dispositions[0])
        for item in policy.hypothesis_requirements if item.required
    ]


def _context(key: str, hypotheses, **overrides) -> AdoptionContext:
    policy = adoption_policy(key)
    act_key = next((item.hypothesis_key for item in policy.hypothesis_requirements if item.role == "act"), None)
    fields = dict(
        definition_key=key, is_system_owned=key == "interaction.discharge_response_obligation",
        has_source_refs=True, bound_targets=TARGETS[key],
        act_hypothesis=next((item for item in hypotheses if item.hypothesis_key == act_key), None),
        linked_hypotheses=tuple(hypotheses), frame_refs=("frame",), frame_predicates=("reviewed_predicate",),
        parent_adopted=True if key == "epistemic.resolve_required_unknown" else None,
    )
    fields.update(overrides)
    return AdoptionContext(**fields)


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def test_manifest_is_closed_and_versioned_over_every_installed_definition() -> None:
    assert set(ADOPTION_POLICIES) == {item.definition_key for item in all_definitions()}
    assert len(ADOPTION_POLICIES) == 13
    assert {item.policy_version for item in ADOPTION_POLICIES.values()} == {GOAL_ADOPTION_POLICY_VERSION}
    assert validate_adoption_manifest() == tuple(ADOPTION_POLICIES.values())


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "version", "target"))
def test_manifest_validation_fails_closed(mutation: str) -> None:
    policies = list(ADOPTION_POLICIES.values())
    if mutation == "missing":
        policies = policies[1:]
    elif mutation == "duplicate":
        policies.append(policies[0])
    elif mutation == "version":
        policies[0] = policies[0].model_copy(update={"policy_version": "other"})
    else:
        policies[0] = policies[0].model_copy(update={"required_bound_targets": ("not_a_role",)})
    with pytest.raises(ValueError):
        validate_adoption_manifest(tuple(policies))


def test_requirement_and_policy_contracts_reject_incoherent_declarations() -> None:
    with pytest.raises(ValueError, match="dimension"):
        GoalHypothesisRequirement(role="act", dimension="speaker_goal", hypothesis_key="interpretation.act.greet",
                                  minimum_support=S.STRONG, allowed_dispositions=(D.LEADING,))
    with pytest.raises(ValueError, match="unsupported"):
        GoalHypothesisRequirement(role="act", dimension="communicative_act", hypothesis_key="interpretation.act.greet",
                                  minimum_support=S.UNSUPPORTED, allowed_dispositions=(D.LEADING,))
    with pytest.raises(ValueError, match="retained"):
        GoalHypothesisRequirement(role="act", dimension="communicative_act", hypothesis_key="interpretation.act.greet",
                                  minimum_support=S.STRONG, allowed_dispositions=(D.REJECTED,))
    with pytest.raises(ValueError, match="adoption rule"):
        GoalAdoptionPolicy.model_validate({
            **adoption_policy("interaction.respond_to_social_contact").model_dump(), "adoption_rule_id": "x",
        })
    with pytest.raises(ValueError, match="resolvable"):
        GoalAdoptionPolicy.model_validate({
            **adoption_policy("interaction.repair_shared_understanding").model_dump(),
            "resolvable_arguments": ("x",),
        })


def test_rejection_detail_is_only_carried_by_a_reject_transition() -> None:
    detail = GoalRejectionDetail(policy_key="k", policy_version="v", failed_check=GoalRejectionCheck.AUTHORIZATION)
    with pytest.raises(ValueError, match="reject"):
        GoalTransition(transition_id="t", goal_id="g", sequence=1, from_status="candidate", to_status="active",
                       transition_kind="adopt", rule_id="goal-adopt.explicit-grounded-user-request.v1",
                       cognitive_turn_id="turn", resulting_snapshot_ref="s", reason_code="adopt",
                       rejection_detail=detail)
    with pytest.raises(ValueError, match="minimum and observed"):
        GoalRejectionDetail(policy_key="k", policy_version="v", failed_check=GoalRejectionCheck.HYPOTHESIS_SUPPORT,
                            hypothesis_key="interpretation.act.greet")


# ---------------------------------------------------------------------------
# Every installed definition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(ADOPTION_POLICIES))
def test_minimum_accepted_support_adopts(key: str) -> None:
    decision = evaluate_adoption(adoption_policy(key), _context(key, _minimal(adoption_policy(key))))
    assert decision.adopted and decision.detail is None


@pytest.mark.parametrize("key,index", [
    (key, index) for key in SEMANTIC_POLICIES
    for index, item in enumerate(ADOPTION_POLICIES[key].hypothesis_requirements) if item.required
])
def test_one_band_below_rejects_with_the_exact_failed_requirement(key: str, index: int) -> None:
    policy = adoption_policy(key)
    requirement = policy.hypothesis_requirements[index]
    below = BANDS[BANDS.index(requirement.minimum_support) - 1]
    hypotheses = [
        _hypothesis(item.hypothesis_key, below if item is requirement else item.minimum_support)
        for item in policy.hypothesis_requirements if item.required
    ]
    decision = evaluate_adoption(policy, _context(key, hypotheses))
    assert not decision.adopted
    assert decision.rejection_rule_id == "goal-reject.insufficient-interpretation-support.v1"
    detail = decision.detail
    assert detail.failed_check == GoalRejectionCheck.HYPOTHESIS_SUPPORT
    assert (detail.policy_key, detail.policy_version) == (key, GOAL_ADOPTION_POLICY_VERSION)
    assert (detail.hypothesis_key, detail.minimum_support, detail.observed_support) == (
        requirement.hypothesis_key, requirement.minimum_support, below,
    )


@pytest.mark.parametrize("key", SEMANTIC_POLICIES)
def test_a_missing_required_role_rejects(key: str) -> None:
    policy = adoption_policy(key)
    speaker_goal = next(item for item in policy.hypothesis_requirements if item.role == "speaker_goal")
    hypotheses = [item for item in _minimal(policy) if item.hypothesis_key != speaker_goal.hypothesis_key]
    detail = evaluate_adoption(policy, _context(key, hypotheses)).detail
    assert detail.failed_check == GoalRejectionCheck.HYPOTHESIS_MISSING
    assert detail.hypothesis_key == speaker_goal.hypothesis_key


@pytest.mark.parametrize("key", [key for key in SEMANTIC_POLICIES if any(
    not item.required for item in ADOPTION_POLICIES[key].hypothesis_requirements)])
def test_optional_corroboration_never_vetoes(key: str) -> None:
    policy = adoption_policy(key)
    optional = next(item for item in policy.hypothesis_requirements if not item.required)
    for present in ((), (_hypothesis(optional.hypothesis_key, S.WEAK),)):
        assert evaluate_adoption(policy, _context(key, [*_minimal(policy), *present])).adopted


@pytest.mark.parametrize("key", SEMANTIC_POLICIES)
def test_a_disallowed_disposition_rejects(key: str) -> None:
    policy = adoption_policy(key)
    act = next(item for item in policy.hypothesis_requirements if item.role == "act")
    assert act.allowed_dispositions == (D.LEADING,)
    hypotheses = [
        _hypothesis(item.hypothesis_key, S.STRONG, D.RETAINED_ALTERNATIVE if item is act else D.LEADING)
        for item in policy.hypothesis_requirements if item.required
    ]
    detail = evaluate_adoption(policy, _context(key, hypotheses)).detail
    assert detail.failed_check == GoalRejectionCheck.HYPOTHESIS_DISPOSITION
    assert detail.observed_disposition == D.RETAINED_ALTERNATIVE


@pytest.mark.parametrize("key", SEMANTIC_POLICIES)
def test_quoted_or_hypothetical_modality_rejects_every_user_act(key: str) -> None:
    policy = adoption_policy(key)
    for modality in ("interpretation.modality.quoted", "interpretation.modality.hypothetical"):
        for disposition in (D.LEADING, D.RETAINED_ALTERNATIVE):
            hypotheses = [*_minimal(policy), _hypothesis(modality, S.MODERATE, disposition)]
            decision = evaluate_adoption(policy, _context(key, hypotheses))
            assert decision.rejection_rule_id == "goal-reject.quoted-or-hypothetical-request.v1"
            assert decision.detail.hypothesis_key == modality


@pytest.mark.parametrize("key", [key for key in ADOPTION_POLICIES if key not in SEMANTIC_POLICIES])
def test_non_user_goals_have_no_modality_or_hypothesis_gate(key: str) -> None:
    hypotheses = [_hypothesis("interpretation.modality.quoted"), _hypothesis("interpretation.act.greet", S.WEAK)]
    assert evaluate_adoption(adoption_policy(key), _context(key, hypotheses)).adopted


@pytest.mark.parametrize("key", SEMANTIC_POLICIES)
def test_an_unknown_frame_is_ungrounded_for_every_user_act(key: str) -> None:
    policy = adoption_policy(key)
    decision = evaluate_adoption(policy, _context(key, _minimal(policy), frame_predicates=("unknown",)))
    assert decision.rejection_rule_id == "goal-reject.missing-required-grounding.v1"
    assert decision.detail.argument_name == "predicate"


def test_unresolved_arguments_follow_each_policy() -> None:
    answer = adoption_policy("interaction.answer_information_request")
    unresolved = (UnresolvedArgument("topic_ref", "frame", "unknown-topic"),)
    detail = evaluate_adoption(answer, _context(answer.goal_definition_key, _minimal(answer), unresolved=unresolved)).detail
    assert (detail.failed_check, detail.argument_name) == (GoalRejectionCheck.UNRESOLVED_ARGUMENT, "topic_ref")

    task = adoption_policy("task.complete_authorized_request")
    exact = (UnresolvedArgument("recipient", "frame", "unknown-recipient"),)
    assert evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), unresolved=exact)).adopted
    inexact = (UnresolvedArgument("recipient", "frame", None),)
    assert not evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), unresolved=inexact)).adopted
    other = (UnresolvedArgument("parameters", "frame", "unknown-parameters"),)
    assert evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), unresolved=other)).detail.argument_name == "parameters"

    repair = adoption_policy("interaction.repair_shared_understanding")
    loose = (UnresolvedArgument("prior_value", "frame", None),)
    assert evaluate_adoption(repair, _context(repair.goal_definition_key, _minimal(repair), unresolved=loose)).adopted

    # A follow-up's pronoun referent is readiness, resolved downstream: it never
    # rejects the answer goal and never forms a subgoal; other arguments still do.
    deferred = (UnresolvedArgument("referent_phrase", "frame", None),)
    assert evaluate_adoption(answer, _context(answer.goal_definition_key, _minimal(answer), unresolved=deferred)).adopted
    assert answer.deferred_arguments == ("referent_phrase",) and "referent_phrase" not in answer.resolvable_arguments

    # The non-committing request handler keeps the user's request whatever is
    # missing: arguments, capability, authorization, and safety are readiness.
    handler = adoption_policy("interaction.handle_action_request")
    missing = (UnresolvedArgument("subject_ref", "frame", None), UnresolvedArgument("requested_effect", "frame", None))
    for authorization, safety in ((None, None), ("denied", "blocked")):
        assert evaluate_adoption(handler, _context(handler.goal_definition_key, _minimal(handler), unresolved=missing,
                                                   authorization=authorization, safety_decision=safety)).adopted
    assert (handler.requires_authorization, handler.reject_on_safety_block, handler.requires_safety_allow) == (
        False, False, False,
    )

    for key in ADOPTION_POLICIES:
        policy = adoption_policy(key)
        if not policy.required_bound_targets:
            continue
        blank = {**TARGETS[key], policy.required_bound_targets[0]: " "}
        detail = evaluate_adoption(policy, _context(key, _minimal(policy), bound_targets=blank)).detail
        assert detail.argument_name == policy.required_bound_targets[0]


def test_contradiction_gate_applies_only_where_declared() -> None:
    for key, declared in (("task.complete_authorized_request", True), ("interaction.answer_information_request", False)):
        policy = adoption_policy(key)
        hypotheses = [*_minimal(policy)]
        hypotheses[0] = _hypothesis(hypotheses[0].hypothesis_key, contradictions=("c1",))
        active = evaluate_adoption(policy, _context(key, hypotheses, active_contradiction_refs=frozenset({"c1"})))
        resolved = evaluate_adoption(policy, _context(key, hypotheses, active_contradiction_refs=frozenset()))
        assert resolved.adopted
        if declared:
            assert active.rejection_rule_id == "goal-reject.active-contradiction.v1"
            assert active.detail.related_refs == ("c1",)
        else:
            assert active.adopted


def test_authorization_and_safety_gates() -> None:
    task = adoption_policy("task.complete_authorized_request")
    for value in (None, "not_required", "authorized", "granted"):
        assert evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), authorization=value)).adopted
    for value in ("denied", "missing"):
        decision = evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), authorization=value))
        assert decision.rejection_rule_id == "goal-reject.hard-gate-denial.v1"
        assert (decision.detail.failed_check, decision.detail.runtime_value) == (GoalRejectionCheck.AUTHORIZATION, value)
    blocked = evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), safety_decision="blocked"))
    assert blocked.detail.failed_check == GoalRejectionCheck.SAFETY
    assert evaluate_adoption(task, _context(task.goal_definition_key, _minimal(task), safety_decision="unknown")).adopted
    # Preventing an action is never denied by a missing authorization or a block.
    stop = adoption_policy("task.prevent_or_stop_requested_action")
    assert evaluate_adoption(stop, _context(stop.goal_definition_key, _minimal(stop), authorization="denied",
                                            safety_decision="blocked")).adopted
    # No installed policy requires an explicit safety allow (live safety is ``unknown``).
    assert not any(item.requires_safety_allow for item in ADOPTION_POLICIES.values())


def test_a_required_subgoal_needs_its_adopted_parent() -> None:
    key = "epistemic.resolve_required_unknown"
    decision = evaluate_adoption(adoption_policy(key), _context(key, [], parent_adopted=False))
    assert decision.rejection_rule_id == "goal-reject.parent-goal-not-adopted.v1"
    assert decision.detail.related_refs == ("parent",)


@pytest.mark.parametrize("key", SEMANTIC_POLICIES)
def test_decisions_are_invariant_under_hypothesis_ordering(key: str) -> None:
    policy = adoption_policy(key)
    extra = [_hypothesis("interpretation.pragmatic.literal"), _hypothesis("interpretation.modality.asserted")]
    for weaken in (False, True):
        base = _minimal(policy)
        if weaken:
            base[-1] = _hypothesis(base[-1].hypothesis_key, BANDS[BANDS.index(base[-1].support) - 1])
        outcomes = {
            repr(evaluate_adoption(policy, _context(key, list(order))))
            for order in itertools.permutations([*base, *extra])
        }
        assert len(outcomes) == 1


# ---------------------------------------------------------------------------
# Through the engine
# ---------------------------------------------------------------------------

GREETING = "interaction.respond_to_social_contact"
ANSWER = "interaction.answer_information_request"


def _greeting(social_support: str, *, order: int = 0):
    frames = [frame("f-hello", "social_opening")]
    hypotheses = [
        hyp("h-greet", "act.greet", "strong", frames=("f-hello",)),
        hyp("h-social", "goal.maintain_social_connection", social_support, frames=("f-hello",)),
    ]
    if order:
        hypotheses.reverse()
    return make_case("greet-turn", frames=frames, hypotheses=hypotheses, text="Hi there!")


def test_strong_greeting_with_moderate_social_goal_is_adopted() -> None:
    """SH-EVAL-013: the reviewed moderate social-connection goal is enough."""

    result = respond(_greeting("moderate"))
    social = only(result.snapshot, GREETING)
    assert social.status == GoalStatus.ACTIVE
    assert social.adoption_basis.rule_id == "goal-adopt.explicit-shared-commitment.v1"
    assert result.focus_decision.primary_goal_ref == social.goal_id
    assert not any(item.rejection_detail for item in result.transitions)


def test_one_band_below_the_social_goal_minimum_is_rejected_exactly() -> None:
    result = respond(_greeting("weak"))
    social = only(result.snapshot, GREETING)
    assert social.status == GoalStatus.REJECTED
    detail = rejection(result, social.goal_id).rejection_detail
    assert detail.model_dump(exclude_none=True, mode="json") == {
        "policy_key": GREETING, "policy_version": GOAL_ADOPTION_POLICY_VERSION,
        "failed_check": "hypothesis_support", "hypothesis_key": "interpretation.goal.maintain_social_connection",
        "hypothesis_ref": "h-social", "minimum_support": "moderate", "observed_support": "weak",
        "allowed_dispositions": ["leading"], "observed_disposition": "leading",
        "related_refs": [], "frame_refs": ["f-hello"],
    }


def test_a_moderate_compound_greeting_cannot_reject_the_request_beside_it() -> None:
    frames = [frame("f-hello", "social_opening"), frame("f-ask", "information_request", topic_ref="oat fiber", question_form="wh")]
    hypotheses = [
        hyp("h-greet", "act.greet", "moderate", frames=("f-hello",)),
        hyp("h-social", "goal.maintain_social_connection", "moderate", frames=("f-hello",)),
        hyp("h-ask", "act.request_information", frames=("f-ask",)),
        hyp("h-obtain", "goal.obtain_information", frames=("f-ask",)),
        hyp("h-question", "modality.questioned", frames=("f-ask",)),
    ]
    result = respond(make_case("compound-turn", frames=frames, hypotheses=hypotheses, text="Hey, how much fiber is in oats?"))
    answer = only(result.snapshot, ANSWER)
    assert answer.status == GoalStatus.ACTIVE
    social = only(result.snapshot, GREETING)
    assert social.status == GoalStatus.REJECTED
    detail = rejection(result, social.goal_id).rejection_detail
    assert (detail.hypothesis_key, detail.observed_support, detail.frame_refs) == (
        "interpretation.act.greet", S.MODERATE, ("f-hello",),
    )


def test_engine_adoption_is_invariant_under_hypothesis_and_frame_ordering() -> None:
    frames = [frame("f-hello", "social_opening"), frame("f-ask", "information_request", topic_ref="oat fiber", question_form="wh")]
    hypotheses = [
        hyp("h-greet", "act.greet", frames=("f-hello",)),
        hyp("h-social", "goal.maintain_social_connection", "moderate", frames=("f-hello",)),
        hyp("h-ask", "act.request_information", frames=("f-ask",)),
        hyp("h-obtain", "goal.obtain_information", frames=("f-ask",)),
    ]
    baseline = respond(make_case("order-turn", frames=frames, hypotheses=hypotheses)).snapshot
    rng = random.Random(7)
    for _ in range(6):
        shuffled_frames, shuffled_hypotheses = frames[:], hypotheses[:]
        rng.shuffle(shuffled_frames); rng.shuffle(shuffled_hypotheses)
        snapshot = respond(make_case("order-turn", frames=shuffled_frames, hypotheses=shuffled_hypotheses)).snapshot
        assert {item.goal_id: item.status for item in snapshot.records} == {item.goal_id: item.status for item in baseline.records}
        assert snapshot.focus_decision == baseline.focus_decision
