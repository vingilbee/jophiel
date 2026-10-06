"""Gate 6: the required dinner conversation, uninterrupted, under simulated authority.

Every turn runs the real compiler and pipeline; only the semantic parser and
the collaboration provider are reviewed structured doubles.  Each selected
response is fed forward as an explicitly marked simulated delivery
(``conversation_workspace_v0.simulation``).  Hermetic: no ``.env``, no network.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.contracts.goals import GoalStatus
from app.ai.Cognitive.system.v0.contracts.realization import ArtifactPresentation, InquiryPrompt
from app.ai.Cognitive.system.v0.contracts.workspace import (
    ArtifactRevisionStatus, ConstraintReason, DeliveryChannel, DeliveryStatus, OpenInquiryStatus,
    ReconciliationState, WorkspaceAuthorityMode,
)
from app.ai.Cognitive.system.v0.conversation_workspace_v0 import WorkspaceError, reconcile
from app.ai.Cognitive.system.v0.conversation_workspace_v0.simulation import (
    SIMULATION_HARNESS_VERSION, next_workspace_inputs, simulated_delivery,
)
from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompileSkip
from simulated_authority_support import SIMULATION_REF, run_simulated_conversation

DELIVERABLE = "interaction.fulfill_conversational_deliverable"
ANSWER = "interaction.incorporate_inquiry_answer"


@pytest.fixture(scope="module")
def turns():
    return run_simulated_conversation()


def _moves(turn) -> list[str]:
    return [move.definition_ref.action_key for move in turn.trace.action_selection.plan.moves]


def _primary_goal(turn):
    snapshot = turn.trace.goal_snapshot
    focus = snapshot.focus_decision.primary_goal_ref
    return next(item for item in snapshot.records if item.goal_id == focus)


def _proposal(turn):
    [outcome] = turn.trace.collaboration.outcomes
    assert outcome.status == "proposed", outcome.failure
    return outcome.proposal


def _head(turn):
    [head] = turn.trace.collaboration.workspace.snapshot.heads
    return head


def test_every_turn_completes_and_realizes(turns) -> None:
    assert len(turns) == 8
    for turn in turns:
        assert not isinstance(turn.realization, RealizationCompileSkip), (turn.index, turn.realization)
        assert turn.trace.collaboration.workspace.snapshot.authority_mode == WorkspaceAuthorityMode.SIMULATED_AUTHORITY


def test_routing_never_needs_an_external_capability_or_an_acknowledgement(turns) -> None:
    assert _moves(turns[0]) == ["give_feedback.acknowledge", "manage_dialogue.greet"]
    for turn in turns[1:]:
        moves = _moves(turn)
        assert moves[0] == "collaborate.deliver_artifact", (turn.index, moves)
        assert not any(key.startswith(("commit_or_coordinate.", "give_feedback.")) for key in moves), (turn.index, moves)
        conditions = turn.trace.action_selection.planning_conditions
        assert not conditions, (turn.index, conditions)


def test_no_older_goal_captures_focus_from_a_new_request(turns) -> None:
    for turn in turns[1:]:
        primary = _primary_goal(turn)
        assert primary.created_turn_ref == turn.trace.cognitive_turn_id, turn.index
        assert primary.definition_ref.definition_key == (ANSWER if turn.index == 7 else DELIVERABLE)


def test_one_artifact_lineage_with_revisions_only_from_create_revise_and_answer(turns) -> None:
    operations = [_proposal(turn).operation.value for turn in turns[1:]]
    assert operations == ["create", "explain", "revise", "describe", "summarize", "revise", "evaluate"]
    artifact = _proposal(turns[1]).artifact_ref
    assert all(_proposal(turn).artifact_ref == artifact for turn in turns[1:])
    revisions = {turn.index: _proposal(turn).proposed_revision for turn in turns[1:]}
    created, revised, answered = revisions[2], revisions[4], revisions[7]
    assert [item.revision_number for item in (created, revised, answered)] == [1, 2, 3]
    assert revised.parent_revision_ref == created.revision_id and answered.parent_revision_ref == revised.revision_id
    assert all(revisions[index] is None for index in (3, 5, 6, 8))
    # Presentations cite the head at their turn: explain rev 1, describe/summarize rev 2, evaluate rev 3.
    for index, expected in ((3, created), (5, revised), (6, revised), (8, answered)):
        assert _proposal(turns[index - 1]).target_revision_ref == expected.revision_id, index
        assert _head(turns[index - 1]).head_revision_ref == expected.revision_id, index


def test_corrections_change_the_intended_revision_and_preserve_unrelated_constraints(turns) -> None:
    created = _proposal(turns[1]).proposed_revision.payload
    revised = _proposal(turns[3]).proposed_revision.payload
    answered = _proposal(turns[6]).proposed_revision.payload
    ids = lambda payload: {item.constraint_id for item in payload.constraints}  # noqa: E731
    assert ids(created) == {"vegetarian"}
    assert ids(revised) == {"vegetarian", "dairy-free"}
    assert (revised.servings.value, revised.time_limit_minutes.value) == (6, 30)
    assert "dairy" not in revised.main_dish.contains
    assert [dish.name for dish in revised.side_dishes] == ["steamed rice"]
    # The answer changes only the attributed reason of the dairy-free constraint.
    reasons = {item.constraint_id: item.reason for item in answered.constraints}
    assert reasons == {"vegetarian": ConstraintReason.UNSPECIFIED, "dairy-free": ConstraintReason.ALLERGY}
    assert (answered.main_dish, answered.side_dishes, answered.servings, answered.time_limit_minutes) == (
        revised.main_dish, revised.side_dishes, revised.servings, revised.time_limit_minutes)


def test_inquiry_follows_the_delivered_revision_and_binds_to_the_exact_answer(turns) -> None:
    revise = turns[3]
    assert _moves(revise) == ["collaborate.deliver_artifact", "seek_information.inquire"]
    [inquiry] = _proposal(revise).selected_inquiries
    assert inquiry.timing.value == "after_primary" and inquiry.criticality.value != "blocking"
    assert revise.outcome.inquiry_refs == (inquiry.inquiry_id,)
    prompts = [item for item in revise.realization.content_items if isinstance(item, InquiryPrompt)]
    assert [item.inquiry_ref for item in prompts] == [inquiry.inquiry_id]
    # Opened only by the delivered outcome, carried open, then answered at turn 7.
    for index, status in ((5, OpenInquiryStatus.OPEN), (6, OpenInquiryStatus.OPEN), (7, OpenInquiryStatus.ANSWERED)):
        [record] = turns[index - 1].trace.collaboration.workspace.snapshot.inquiries
        assert (record.inquiry_id, record.status) == (inquiry.inquiry_id, status), index
        assert record.elicitation_delivery_ref == revise.outcome.outcome_id
    [binding] = turns[6].trace.collaboration.workspace.answer_bindings
    assert (binding.inquiry_ref, binding.answer_kind, binding.answer_option) == (inquiry.inquiry_id, "option", "allergy")
    assert binding.goal_ref == _primary_goal(turns[6]).goal_id
    # No other turn asks anything.
    assert all("seek_information.inquire" not in _moves(turn) for turn in turns if turn.index != 4)


def test_every_delivered_response_reconciles_its_goal_and_revision(turns) -> None:
    for previous, turn in zip(turns, turns[1:]):
        records = turn.trace.collaboration.workspace.reconciliations
        if previous.index == 1:
            assert records == ()
            continue
        [record] = records
        expected = (ReconciliationState.DELIVERED_WAITING_FOR_ANSWER if previous.index == 4
                    else ReconciliationState.DELIVERED_AND_SATISFIED)
        assert (record.state, record.outcome_ref) == (expected, previous.outcome.outcome_id), previous.index
    for turn in turns[1:]:
        goal = _primary_goal(turn)
        live = next(item for item in turn.trace.goal_snapshot.records if item.goal_id == goal.goal_id)
        delivered = next(item for item in turn.delivered_goal_snapshot.records if item.goal_id == goal.goal_id)
        assert live.status == GoalStatus.ACTIVE, turn.index  # never satisfied from planning or generation
        assert delivered.status == GoalStatus.SATISFIED, turn.index  # only from the delivered outcome
    final = turns[-1].trace.collaboration.workspace.snapshot
    statuses = sorted(item.status for item in final.revisions)
    assert statuses == sorted([ArtifactRevisionStatus.SUPERSEDED, ArtifactRevisionStatus.SUPERSEDED,
                               ArtifactRevisionStatus.DELIVERED])


def test_every_injected_delivery_is_marked_simulated(turns) -> None:
    for turn in turns:
        outcome = turn.outcome
        assert outcome.delivery_channel == DeliveryChannel.SIMULATED and outcome.simulation_ref == SIMULATION_REF
        assert outcome.turn_ref == turn.trace.cognitive_turn_id
    events_producer = turns[1].delivered_goal_snapshot.records
    assert events_producer  # the outcome revision ran
    assert SIMULATION_HARNESS_VERSION.startswith("workspace-simulated-authority-harness")


def test_artifact_content_reaches_realization_with_value_fidelity(turns) -> None:
    created = [item for item in turns[1].realization.content_items if isinstance(item, ArtifactPresentation)]
    [presentation] = created
    assert presentation.operation == "create" and presentation.reports_change
    assert {(arg.value, arg.unit) for arg in presentation.arguments} == {("6", "serving"), ("25", "minute")}
    evaluate = [item for item in turns[7].realization.content_items if isinstance(item, ArtifactPresentation)]
    guidance = [item for item in evaluate if item.role == "safety_guidance"]
    assert guidance and all(item.requires_hedge and item.licenses_suggestion for item in guidance)


def test_a_failed_delivery_never_advances_the_head_or_satisfies_the_goal() -> None:
    from simulated_authority_support import DINNER

    turns = run_simulated_conversation(
        DINNER["turns"][:4], deliver=lambda index, trace: DeliveryStatus.FAILED if index == 2 else None)
    failed = turns[1].outcome
    assert failed.delivery_status == DeliveryStatus.FAILED
    assert failed.artifact_revision_refs == () and failed.inquiry_refs == ()
    [record] = turns[2].trace.collaboration.workspace.reconciliations
    assert (record.state, record.outcome_ref) == (ReconciliationState.DELIVERY_FAILED, failed.outcome_id)
    assert turns[2].trace.collaboration.workspace.snapshot.heads == ()
    goal = _primary_goal(turns[1])
    after = next(item for item in turns[1].delivered_goal_snapshot.records if item.goal_id == goal.goal_id)
    assert after.status == GoalStatus.ACTIVE
    # Later turns have no artifact to explain or revise: the target is not
    # found, the request is clarified, and no content is invented.
    for turn in turns[2:]:
        [outcome] = turn.trace.collaboration.outcomes
        assert (outcome.status, outcome.skip_reason.value) == ("skipped", "goal_blocked"), turn.index
        assert turn.trace.collaboration.workspace.targets[0].status.value == "not_found"
        assert _moves(turn) == ["seek_information.clarify"], turn.index


def test_an_unhedged_safety_suggestion_is_declined_without_fallback() -> None:
    from simulated_authority_support import dinner_script

    def script(payload):
        output = dinner_script(payload)
        if payload.operation.value == "evaluate":
            output["content_items"] = [
                {**item, "content_class": "generated_proposal"} if item["role"] == "safety_guidance" else item
                for item in output["content_items"]
            ]
        return output

    turns = run_simulated_conversation(script=script)
    [outcome] = turns[7].trace.collaboration.outcomes
    assert (outcome.status, outcome.failure.code.value) == ("failed", "sensitive_content_unauthorized")
    assert _moves(turns[7])[0] == "commit_or_coordinate.decline_request"
    assert turns[7].outcome.artifact_revision_refs == () and turns[7].outcome.delivered_content_refs == ()


def test_the_harness_is_rejected_outside_simulated_authority(turns) -> None:
    trace = turns[1].trace
    outcome = simulated_delivery(trace, simulation_ref=SIMULATION_REF, observed_at="2026-10-01T18:02:30+00:00",
                                 realization_ref="realization-x")
    snapshot = trace.collaboration.workspace.snapshot.model_copy(update={
        "authority_mode": WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW})
    with pytest.raises(WorkspaceError):
        reconcile(snapshot, record=trace.collaboration.turn_record, outcomes=(outcome,), turn_ref="next")
    observational = type(trace.collaboration.workspace).model_construct(
        **{**dict(trace.collaboration.workspace), "snapshot": snapshot})
    fake = type(trace.collaboration).model_construct(**{**dict(trace.collaboration), "workspace": observational})
    with pytest.raises(ValueError):
        next_workspace_inputs(type(trace).model_construct(**{**dict(trace), "collaboration": fake}), (outcome,))


def test_artifact_delivery_evidence_is_exact_about_operation_target_and_episode(turns) -> None:
    from pydantic import ValidationError

    from app.ai.Cognitive.system.v0.conversation_workspace_v0.simulation import delivery_events
    from app.ai.Cognitive.system.v0.goal_v0.evidence_producer import (
        ArtifactDeliveredEvent, GoalEvidenceProductionError, produce_goal_evidence,
    )

    trace = turns[1].trace
    [event] = delivery_events(trace, turns[1].outcome)
    assert event.operation == "create" and event.artifact_revision_ref == turns[1].outcome.artifact_revision_refs[0]
    [evidence] = produce_goal_evidence(trace.goal_snapshot, [event])
    assert not evidence.provenance.non_satisfying_observation
    # A different operation than the one requested is recorded but never satisfies.
    [other] = produce_goal_evidence(trace.goal_snapshot, [event.model_copy(update={
        "operation": "summarize", "event_id": "wrong-operation"})])
    assert other.provenance.non_satisfying_observation
    # A different target, or a delivery from another episode, produces nothing.
    for update in ({"addressed_target_ref": "another-request"}, {"delivered_turn_ref": "another-turn"}):
        with pytest.raises(GoalEvidenceProductionError):
            produce_goal_evidence(trace.goal_snapshot, [event.model_copy(update=update)])
    # A create or revise must name the delivered revision.
    with pytest.raises(ValidationError):
        ArtifactDeliveredEvent.model_validate({**event.model_dump(), "artifact_revision_ref": None})
    # A plan or a proposal alone has no event: an undelivered turn yields none.
    failed = turns[1].outcome.model_copy(update={"delivery_status": DeliveryStatus.FAILED,
                                                  "artifact_revision_refs": (), "inquiry_refs": ()})
    assert delivery_events(trace, failed) == ()


def test_quality_improving_context_never_blocks_the_primary_contribution(turns) -> None:
    for index in (2, 4):
        snapshot = turns[index - 1].trace.requirement_resolution.handoff.snapshot
        kinds = {item.kind.value: item for item in snapshot.resolutions}
        context = kinds["domain_context"]
        assert (context.status.value, context.criticality.value, context.blocking) == (
            "missing_user_input", "quality_improving", False)
        assert [item.readiness for item in snapshot.goal_readiness] == ["ready"]
        assert "collaborate.deliver_artifact" in _moves(turns[index - 1])
    # The answer turn needs only its exact target and inquiry link, both satisfied.
    snapshot = turns[6].trace.requirement_resolution.handoff.snapshot
    assert sorted(item.kind.value for item in snapshot.resolutions) == ["artifact_target", "inquiry_link"]
    assert all(item.status.value == "satisfied" for item in snapshot.resolutions)
    # No deliverable ever requires an external capability, authorization, consent, or safety prerequisite.
    for turn in turns[1:]:
        kinds = {item.kind.value for item in turn.trace.requirement_resolution.handoff.snapshot.resolutions}
        assert not kinds & {"capability", "authorization", "consent", "safety", "execution"}, turn.index


# ---------------------------------------------------------------------------
# Realization fidelity over the real compiled requests (frozen reviewed wordings)
# ---------------------------------------------------------------------------

REVISION = "I've made the chickpea and spinach stew dairy-free, and steamed rice is an inexpensive side."
ACCEPTED = [
    (2, "A chickpea and spinach stew serves 6 and takes about 25 minutes.", None),
    (2, "A chickpea and spinach stew serves six and takes about 25 minutes.", None),
    (3, "The chickpea and spinach stew has no meat, so it fits the vegetarian requirement, and it cooks in one pot "
        "in about 25 minutes, inside the 30 minute limit.", None),
    (4, f"{REVISION} Is dairy-free a preference or an allergy for you?",
     {1: REVISION, 2: "Is dairy-free a preference or an allergy for you?"}),
    (5, "It's a fragrant chickpea and spinach stew, served with fluffy steamed rice.", None),
    (6, "So far: a vegetarian, dairy-free chickpea and spinach stew with steamed rice for 6, under 30 minutes.", None),
    (7, "You said one guest has an allergy, so I've recorded the dairy-free requirement as an allergy.", None),
    (7, "As you mentioned, the dairy-free requirement is now recorded as a guest's allergy.", None),
    (8, "The stew and rice already avoid dairy, so the plan still fits the allergy. It may help to check packaged "
        "ingredients such as stock for hidden dairy.", None),
]
REJECTED = [
    (2, "A chickpea and spinach stew serves 8 and takes about 25 minutes.", None, "numeric_value_changed"),
    (4, f"{REVISION} Do you also want dessert?", {1: REVISION, 2: "Do you also want dessert?"},
     "content_alignment_missing"),
    (7, "Thanks. Since one guest has an allergy, the dairy-free requirement is now recorded as a guest's allergy.",
     None, "attribution_dropped"),
    (8, "The stew and rice already avoid dairy, so the plan still fits the allergy. You must check packaged "
        "ingredients such as stock for hidden dairy.", None, "required_uncertainty_dropped"),
]


@pytest.mark.parametrize(("index", "text", "moves"), ACCEPTED)
def test_reviewed_natural_wordings_survive_the_validator(turns, index, text, moves) -> None:
    from realization_v0_support import validate_text

    result = validate_text(turns[index - 1].realization, text, moves)
    assert result.status == "accepted_for_shadow_review", [item.code.value for item in result.violations]


@pytest.mark.parametrize(("index", "text", "moves", "code"), REJECTED)
def test_unfaithful_wordings_are_rejected(turns, index, text, moves, code) -> None:
    from realization_v0_support import validate_text

    result = validate_text(turns[index - 1].realization, text, moves)
    assert result.status == "rejected" and code in {item.code.value for item in result.violations}
