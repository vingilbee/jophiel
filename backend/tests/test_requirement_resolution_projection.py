"""Projection of requirement resolutions into existing Action inputs and Realization content."""

from __future__ import annotations

from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.contracts.actions import ActionTargetType as T
from app.ai.Cognitive.system.v0.contracts.realization import (
    DialogueReference, GroundedProposition, KnowledgeGap, Polarity, ReferenceForm,
)
from app.ai.Cognitive.system.v0.contracts.safety import SafetyDisposition
from app.ai.Cognitive.system.v0.producers_v0 import project_action_objects, project_realization_content
from app.ai.Cognitive.system.v0.requirement_resolution_v0.projection import REQUEST_ANCHOR
from requirement_resolution_support import (
    AMBIGUOUS, DATE, IDENTITY, INFORMATION, REMINDER, UNSAFE, action_request_of, objects_of, primary_move, run_turn,
    trusted_safety,
)


def _index(trace) -> ActionReferenceIndex:
    return ActionReferenceIndex(action_request_of(trace))


def _every_reference_resolves(trace) -> None:
    index = _index(trace)
    resolution = trace.requirement_resolution
    for item in resolution.grounded_objects:
        for name in ("source_refs", "about_refs", "answer_content_refs", "decline_reason_refs",
                     "requires_consent_refs", "safety_policy_refs"):
            assert all(index.resolves(ref) for ref in getattr(item, name)), (item.object_id, name)
    for binding in resolution.handoff.bindings:
        assert all(index.resolves(ref) for ref in (*binding.target_refs, *binding.object_refs,
                                                   *binding.descriptor_refs))


def test_a_known_answer_projects_exactly_the_producer_question_and_answer_refs() -> None:
    for text, frames in (("What is your name?", IDENTITY), ("What is today's date?", DATE)):
        trace = run_turn(text, frames)
        [record] = trace.requirement_resolution.content_records
        expected = project_action_objects(record.query, record.result, record.gap)
        assert trace.requirement_resolution.grounded_objects == tuple(sorted(expected, key=lambda item: item.object_id))
        [question] = objects_of(trace, "question")
        assert question.question_kind == "factual"
        assert set(question.answer_content_refs) == {item.object_id for item in objects_of(trace, "proposition")}
        assert trace.requirement_resolution.realization_content == project_realization_content(
            record.query, record.result, record.gap)
        _every_reference_resolves(trace)


def test_a_known_topic_without_a_trusted_answer_projects_an_explicit_gap_not_a_clarification_target() -> None:
    trace = run_turn("How much protein is in a cup of cooked chickpeas?", INFORMATION)
    [question] = objects_of(trace, "question")
    [gap] = objects_of(trace, "evidence_record")
    assert question.answer_content_refs == (gap.object_id,) and gap.epistemic_status == "unknown"
    [content] = trace.requirement_resolution.realization_content
    assert isinstance(content, KnowledgeGap) and content.gap_kind.value == "no_grounded_answer"
    assert trace.requirement_resolution.target_descriptors == ()  # nothing to ask the user
    _every_reference_resolves(trace)


def test_a_missing_argument_projects_one_user_owned_elicitation_target() -> None:
    trace = run_turn("Can you check it for me?", AMBIGUOUS)
    frame = next(item for item in trace.interpretation.semantic_frames if item.predicate == "action_request")
    handler = next(item.goal_id for item in trace.goal_snapshot.records
                   if item.definition_ref.definition_key == "interaction.handle_action_request")
    descriptors = {item.ref: item for item in trace.requirement_resolution.target_descriptors}
    argument = descriptors[f"{frame.frame_id}#subject_ref"]
    assert (argument.target_type, argument.owner, argument.unknown_kind, argument.about_refs,
            argument.represents_ref) == (T.SEMANTIC_ARGUMENT, "user", "missing_value", (handler,), None)
    [unknown] = [item for item in trace.interpretation.unknowns if item.key == "subject_ref"]
    assert descriptors[unknown.unknown_id].represents_ref == argument.ref  # asked once, at the argument
    [request] = objects_of(trace, "request")
    assert request.acceptance_status == "pending" and request.decline_reason_refs == ()
    _every_reference_resolves(trace)


def test_an_unavailable_capability_projects_a_valid_request_with_a_grounded_reason() -> None:
    trace = run_turn("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER)
    [request] = objects_of(trace, "request")
    [reason] = objects_of(trace, "capability")
    assert (request.focus_contribution, request.fulfillment_mode, request.acceptance_status) == (True, "task", "unavailable")
    assert request.decline_reason_refs == (reason.object_id,) and reason.capability_status == "unregistered"
    assert request.required_capabilities == ()  # an unregistered effect names no invented capability
    content = {item.content_id: item for item in trace.requirement_resolution.realization_content}
    reference = next(item for item in content.values() if isinstance(item, DialogueReference))
    proposition = next(item for item in content.values() if isinstance(item, GroundedProposition))
    assert reference.represents_refs == (request.object_id,) and reference.reference_form == ReferenceForm.ANCHORED
    assert reference.surface_anchors == (REQUEST_ANCHOR,)
    assert proposition.represents_refs == (reason.object_id,)
    assert proposition.predicate == "runtime.requested_capability_registered"
    assert proposition.polarity == Polarity.NEGATIVE  # "your request is not a registered capability"
    _every_reference_resolves(trace)


def test_a_prohibited_request_projects_trusted_policy_records_and_a_safety_reason() -> None:
    trace = run_turn("How do I steal my neighbor's Wi-Fi password?", UNSAFE,
                     safety_decision=trusted_safety(SafetyDisposition.BLOCK_COMMITMENTS),
                     runtime={"policy_records": ("application-safety-policy-v0.1#unauthorized-access",)})
    [request] = objects_of(trace, "request")
    [reason] = objects_of(trace, "evidence_record")
    assert request.acceptance_status == "prohibited"
    assert request.safety_policy_refs == ("application-safety-policy-v0.1#unauthorized-access",)
    assert request.decline_reason_refs == (reason.object_id,)
    assert reason.source_refs == (trace.cognitive_turn.input_snapshot.action_runtime.safety_basis.decision_ref,)
    predicates = {item.predicate for item in trace.requirement_resolution.realization_content
                  if isinstance(item, GroundedProposition)}
    assert predicates == {"application_safety.request_permitted"}
    _every_reference_resolves(trace)


def test_withheld_or_conflicting_content_is_never_projected() -> None:
    from app.ai.Cognitive.system.v0.producers_v0 import ApplicationIdentityProducer, captured_runtime_producer
    from app.ai.Cognitive.system.v0.requirement_resolution_v0 import RequirementResolutionEngine, runtime_date_record

    def factory(mutation):
        def build(inputs, turn_time):
            record = runtime_date_record(turn_time, None)
            records = mutation(record)
            return (ApplicationIdentityProducer(), captured_runtime_producer(records, turn_time=turn_time))
        return build

    for mutation, acceptance, predicate in (
        (lambda record: (record.model_copy(update={"authorized": False}),),
         "unauthorized", "runtime.requested_content_shareable"),
        (lambda record: (record.model_copy(update={"record_id": "a"}),
                         record.model_copy(update={"record_id": "b", "value": "2026-01-01",
                                                   "value_anchor": "2026-01-01"})),
         "unavailable", "runtime.requested_content_consistent"),
    ):
        trace = run_turn("What is today's date?", DATE,
                         engine=RequirementResolutionEngine(producer_factory=factory(mutation)))
        resolution = trace.requirement_resolution
        [record] = resolution.content_records
        assert record.result.facts == () and record.gap is None
        # No question, fact, or produced value becomes addressable or wordable...
        assert {item.object_type.value for item in resolution.grounded_objects} == {"request", "evidence_record"}
        rendered = trace.model_dump_json()
        assert "producer-fact:" not in rendered and "producer-question:" not in rendered
        assert all(not isinstance(item, GroundedProposition) or not item.arguments
                   for item in resolution.realization_content)
        # ...only a scoped, declinable request with its typed reason.
        [request] = objects_of(trace, "request")
        assert (request.fulfillment_mode, request.acceptance_status) == ("conversational", acceptance)
        assert {item.predicate for item in resolution.realization_content
                if isinstance(item, GroundedProposition)} == {predicate}
        assert primary_move(trace).definition_ref.action_key == "commit_or_coordinate.decline_request"
        assert "provide_information.answer" not in [move.definition_ref.action_key
                                                    for move in trace.action_selection.plan.moves]
        _every_reference_resolves(trace)
