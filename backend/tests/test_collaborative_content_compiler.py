"""Gate 3: collaborative-content provider protocol, compiler, and inquiry policy.

Reviewed structured model doubles stand in for the provider; nothing here
depends on a developer ``.env`` or makes a network call.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.collaborative_content_v0 import (
    CollaborationCompileError, CollaborationCompilerV0, CollaborationModelOutput, CollaborationProviderError,
    CompileContext, response_json_schema,
)
from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import empty_output, inquiry, item, operation
from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import (
    CollaborationModelPayload, CollaborationSource, SourceKind, vocabulary_for,
)
from app.ai.Cognitive.system.v0.collaborative_content_v0.sources import SourceSet, element_ref
from app.ai.Cognitive.system.v0.contracts.collaboration import (
    CollaborationFailureCode as F, InquiryDisposition, InquirySuppressionReason, WorkspaceTargetBinding,
)
from app.ai.Cognitive.system.v0.contracts.workspace import (
    ArtifactOperation, ArtifactRevisionStatus, ArtifactType, ContentClass, InquiryPurpose, OpenInquiryRecord,
    OpenInquiryStatus,
)
from workspace_support import CONVERSATION, created, delivered, revised

REQUEST = "frame-turn4-request"
DAIRY = "frame-turn4-request#constraints.0"
SIDE = "frame-turn4-request#constraints.1"
CREATE_REQUEST = "frame-turn2-request"
VEG = "frame-turn2-request#constraints.0"
SIX = "frame-turn2-request#constraints.1"
FAST = "frame-turn2-request#constraints.2"


def _source(ref, kind, text, *, speaker="user", current=True) -> CollaborationSource:
    return CollaborationSource(ref=ref, kind=kind, speaker=speaker, text=text, current_turn=current)


def _source_set(sources, *, required=(), withdrawals=()) -> SourceSet:
    sources = tuple(sorted(sources, key=lambda value: value.ref))
    return SourceSet(sources=sources, required_refs=frozenset(required),
                     user_refs=frozenset(item.ref for item in sources if item.speaker == "user"),
                     withdrawal_refs=frozenset(withdrawals))


def _create_context(**overrides) -> CompileContext:
    sources = _source_set((
        _source(CREATE_REQUEST, SourceKind.USER_REQUEST, "create meal_plan"),
        _source(VEG, SourceKind.USER_REQUIREMENT, "vegetarian"),
        _source(SIX, SourceKind.USER_REQUIREMENT, "six people"),
        _source(FAST, SourceKind.USER_REQUIREMENT, "under 30 minutes"),
    ), required=(VEG, SIX, FAST))
    values = dict(
        cognitive_turn_id="turn-2", conversation_ref=CONVERSATION, goal_ref="goal-create", frame_ref=CREATE_REQUEST,
        operation=ArtifactOperation.CREATE, artifact_type=ArtifactType.MEAL_PLAN,
        target=WorkspaceTargetBinding(goal_ref="goal-create", frame_ref=CREATE_REQUEST, operation="create",
                                      artifact_type="meal_plan", status="not_required", rule_id="test"),
        base=None, sources=sources, inquiry_history=(), curiosity=0.5, max_inquiries=1,
    )
    values.update(overrides)
    return CompileContext(**values)


def _dish(dish_id, name, *, contains=(), traits=(), minutes=None, content_class="generated_proposal"):
    return {"dish_id": dish_id, "name": name, "content_class": content_class, "contains": list(contains),
            "traits": list(traits), "prep_time_minutes": minutes}


def _constraint(constraint_id, kind, value, *, basis="user_supplied_constraint", reason="unspecified"):
    return {"constraint_id": constraint_id, "kind": kind, "value": value, "basis": basis, "reason": reason}


def create_candidate(**overrides) -> dict:
    output = empty_output(
        title="Vegetarian dinner for six",
        operations=[
            operation("set_servings", integer_value=6, integer_class="user_supplied_constraint", source_refs=[SIX]),
            operation("set_time_limit", integer_value=30, integer_class="user_supplied_constraint", source_refs=[FAST]),
            operation("add_constraint", constraint=_constraint("vegetarian", "dietary", "vegetarian"), source_refs=[VEG]),
            operation("set_main_dish", dish=_dish("main", "chickpea and spinach stew", contains=["dairy"],
                                                  traits=["one_pot", "quick"], minutes=25),
                      source_refs=[CREATE_REQUEST]),
        ],
        content_items=[
            item("change", "A chickpea and spinach stew serves 6 and takes about 25 minutes.",
                 ["chickpea and spinach stew"], subject_ids=["main", "servings"], source_refs=[CREATE_REQUEST],
                 numbers=[6, 25]),
        ],
    )
    output.update(overrides)
    return output


def _compile(context, candidate):
    output = CollaborationModelOutput.model_validate(candidate)
    return CollaborationCompilerV0().compile(context, output, candidate)


def _code(context, candidate) -> F:
    with pytest.raises(CollaborationCompileError) as caught:
        _compile(context, candidate)
    return caught.value.code


def _revise_context(**overrides) -> CompileContext:
    base = delivered(created())
    sources = _source_set((
        _source(REQUEST, SourceKind.USER_REQUEST, "revise meal_plan our idea"),
        _source(DAIRY, SourceKind.USER_REQUIREMENT, "dairy-free"),
        _source(SIDE, SourceKind.USER_REQUIREMENT, "add an inexpensive side"),
        *(_source(element_ref(base, element), SourceKind.ARTIFACT_ELEMENT, element, speaker="workspace", current=False)
          for element in ("main", "servings", "time_limit", "vegetarian")),
    ), required=(DAIRY, SIDE))
    values = dict(
        cognitive_turn_id="turn-4", conversation_ref=CONVERSATION, goal_ref="goal-revise", frame_ref=REQUEST,
        operation=ArtifactOperation.REVISE, artifact_type=ArtifactType.MEAL_PLAN,
        target=WorkspaceTargetBinding(goal_ref="goal-revise", frame_ref=REQUEST, operation="revise",
                                      artifact_type="meal_plan", status="resolved_artifact",
                                      artifact_ref=base.artifact_id, base_revision_ref=base.revision_id, rule_id="test"),
        base=base, sources=sources, inquiry_history=(), curiosity=0.5, max_inquiries=1,
    )
    values.update(overrides)
    return CompileContext(**values)


def revise_candidate(**overrides) -> dict:
    output = empty_output(
        operations=[
            operation("add_constraint", constraint=_constraint("dairy-free", "dietary", "dairy_free"), source_refs=[DAIRY]),
            operation("update_dish", dish=_dish("main", "chickpea and spinach stew", traits=["one_pot", "quick"],
                                                minutes=25), source_refs=[DAIRY]),
            operation("add_side_dish", dish=_dish("side-rice", "steamed rice", traits=["inexpensive"], minutes=20),
                      source_refs=[SIDE]),
        ],
        content_items=[
            item("change", "The chickpea and spinach stew is now dairy-free.", ["chickpea and spinach stew", "dairy-free"],
                 subject_ids=["dairy-free", "main"], source_refs=[DAIRY]),
            item("change", "Steamed rice is added as an inexpensive side.", ["steamed rice", "inexpensive side"],
                 subject_ids=["side-rice"], source_refs=[SIDE]),
        ],
        inquiries=[
            inquiry("dairy-free", asked_about="whether dairy-free is a preference or an allergy",
                    options=["preference", "allergy"], purpose="resolve_safety_relevant_context",
                    expected_use="set_constraint_reason", sensitivity="health", source_refs=[DAIRY]),
        ],
    )
    output.update(overrides)
    return output


# ---------------------------------------------------------------------------
# Valid proposals
# ---------------------------------------------------------------------------

def test_a_valid_create_proposal_compiles_to_a_proposed_first_revision() -> None:
    proposal = _compile(_create_context(), create_candidate())
    revision = proposal.proposed_revision
    assert proposal.operation == ArtifactOperation.CREATE and revision.status == ArtifactRevisionStatus.PROPOSED
    assert revision.revision_number == 1 and revision.payload.servings.value == 6
    assert proposal.artifact_ref == revision.artifact_id and proposal.artifact_ref.startswith("artifact-")
    [content] = proposal.content_items
    assert content.content_class == ContentClass.GENERATED_PROPOSAL and content.numbers == (6, 25)


def test_a_valid_revision_applies_against_the_exact_base_and_selects_its_inquiry() -> None:
    context = _revise_context()
    proposal = _compile(context, revise_candidate())
    revision = proposal.proposed_revision
    assert revision.parent_revision_ref == context.base.revision_id and revision.revision_number == 2
    assert {item.value for item in revision.payload.dietary_constraints} == {"vegetarian", "dairy_free"}
    [question] = proposal.inquiry_opportunities
    assert question.disposition == InquiryDisposition.SELECTED and question.timing.value == "after_primary"
    assert question.answer_options == ("preference", "allergy")


def test_a_presentation_proposal_targets_the_exact_revision_and_carries_no_patch() -> None:
    base = delivered(revised(delivered(created())))
    sources = _source_set((
        _source("frame-turn3", SourceKind.USER_REQUEST, "explain meal_plan"),
        *(_source(element_ref(base, element), SourceKind.ARTIFACT_ELEMENT, element, speaker="workspace",
                  current=False) for element in ("main", "vegetarian", "time_limit", "servings")),
    ))
    context = _revise_context(
        operation=ArtifactOperation.EXPLAIN, frame_ref="frame-turn3", goal_ref="goal-explain", sources=sources,
        base=base, target=WorkspaceTargetBinding(
            goal_ref="goal-explain", frame_ref="frame-turn3", operation="explain", artifact_type="meal_plan",
            status="resolved_artifact", artifact_ref=base.artifact_id, base_revision_ref=base.revision_id,
            rule_id="test"),
    )
    candidate = empty_output(content_items=[
        item("explanation", "The chickpea and spinach stew has no meat, so it fits a vegetarian dinner.",
             ["chickpea and spinach stew", "vegetarian"], content_class="reasoned_inference",
             claim_kind="constraint_fit", subject_ids=["main", "vegetarian"],
             source_refs=[element_ref(base, "main"), element_ref(base, "vegetarian")]),
    ])
    proposal = _compile(context, candidate)
    assert proposal.patch is None and proposal.target_revision_ref == base.revision_id
    assert proposal.uncertainty_item_refs == (proposal.content_items[0].item_id,)  # inference keeps its hedge
    assert _code(context, {**candidate, "operations": revise_candidate()["operations"]}) == F.UNEXPECTED_PATCH


# ---------------------------------------------------------------------------
# Rejections (Section 9.4)
# ---------------------------------------------------------------------------

def test_unsupported_verified_claims_and_fabricated_outcomes_are_rejected() -> None:
    nutrient = create_candidate(content_items=[item(
        "explanation", "The stew has 18 grams of protein.", ["stew"], claim_kind="nutrient_amount",
        source_refs=[CREATE_REQUEST], numbers=[18])])
    assert _code(_create_context(), nutrient) == F.UNSUPPORTED_VERIFIED_CLAIM
    sent = create_candidate(content_items=[item(
        "change", "I have emailed the plan to Sam.", ["plan"], claim_kind="tool_outcome", subject_ids=["main"],
        source_refs=[CREATE_REQUEST])])
    assert _code(_create_context(), sent) == F.FABRICATED_OUTCOME
    # A change item may only describe what the patch actually did.
    invented = revise_candidate(content_items=[item(
        "change", "The time limit is now 25 minutes.", ["time limit"], subject_ids=["time_limit"],
        source_refs=[DAIRY], numbers=[25])])
    assert _code(_revise_context(), invented) == F.FABRICATED_OUTCOME


def test_a_proposal_cannot_claim_verified_or_tool_authority() -> None:
    verified = create_candidate(content_items=[item(
        "change", "The stew is ready.", ["stew"], content_class="verified_fact", subject_ids=["main"],
        source_refs=[CREATE_REQUEST])])
    assert _code(_create_context(), verified) == F.UNSUPPORTED_CONTENT_CLASS
    attributed = create_candidate(content_items=[item(
        "change", "The stew is ready.", ["stew"], content_class="attributed_user_claim", subject_ids=["main"],
        source_refs=["frame-turn2-request"])])
    # User-classed content must cite the user's own contribution (the request frame is the user's).
    assert _compile(_create_context(), attributed).content_items[0].content_class == ContentClass.ATTRIBUTED_USER_CLAIM


def test_foreign_source_and_subject_refs_are_rejected() -> None:
    foreign = create_candidate(content_items=[item("change", "The stew serves 6.", ["stew"], subject_ids=["main"],
                                                   source_refs=["frame-from-another-turn"], numbers=[6])])
    assert _code(_create_context(), foreign) == F.FOREIGN_SOURCE_REF
    subject = create_candidate(content_items=[item("change", "The salad is crunchy.", ["salad"],
                                                   subject_ids=["side-salad"], source_refs=[CREATE_REQUEST])])
    assert _code(_create_context(), subject) == F.FOREIGN_SUBJECT_REF


def test_explicit_user_constraints_must_be_incorporated_and_never_silently_removed() -> None:
    ignored = revise_candidate(operations=revise_candidate()["operations"][:2])
    assert _code(_revise_context(), ignored) == F.USER_CONSTRAINT_IGNORED
    removal = revise_candidate(operations=[
        *revise_candidate()["operations"],
        operation("remove_constraint", element_id="vegetarian", source_refs=[DAIRY]),
    ])
    assert _code(_revise_context(), removal) == F.PATCH_REJECTED
    # A patch inconsistent with an explicit constraint (dairy kept with dairy-free) is rejected.
    conflicting = revise_candidate(operations=[
        revise_candidate()["operations"][0], revise_candidate()["operations"][2],
    ])
    assert _code(_revise_context(), conflicting) == F.PATCH_REJECTED


def test_numbers_must_come_from_typed_artifact_content() -> None:
    wrong = create_candidate(content_items=[item("change", "It serves 8 people.", ["serves"],
                                                 subject_ids=["servings"], source_refs=[SIX], numbers=[8])])
    assert _code(_create_context(), wrong) == F.UNSUPPORTED_NUMBER
    undeclared = create_candidate(content_items=[item("change", "It serves 6 people.", ["serves"],
                                                      subject_ids=["servings"], source_refs=[SIX])])
    assert _code(_create_context(), undeclared) == F.UNSUPPORTED_NUMBER


def test_safety_guidance_is_cautious_and_tied_to_a_stated_requirement() -> None:
    reckless = revise_candidate(content_items=[*revise_candidate()["content_items"], item(
        "safety_guidance", "Dairy allergies are never serious.", ["dairy"], content_class="generated_proposal",
        claim_kind="general_practice", subject_ids=["dairy-free"], source_refs=[DAIRY])])
    assert _code(_revise_context(), reckless) == F.SENSITIVE_CONTENT_UNAUTHORIZED
    untied = revise_candidate(content_items=[*revise_candidate()["content_items"], item(
        "safety_guidance", "Check labels for hidden dairy.", ["labels"], content_class="reasoned_inference",
        claim_kind="general_practice", subject_ids=["side-rice"], source_refs=[DAIRY])])
    assert _code(_revise_context(), untied) == F.SENSITIVE_CONTENT_UNAUTHORIZED
    cautious = revise_candidate(content_items=[*revise_candidate()["content_items"], item(
        "safety_guidance", "It may help to check labels for hidden dairy.", ["labels", "hidden dairy"],
        content_class="reasoned_inference", claim_kind="general_practice", subject_ids=["dairy-free"],
        source_refs=[DAIRY])])
    proposal = _compile(_revise_context(), cautious)
    assert proposal.uncertainty_item_refs  # the inference stays visibly uncertain


def test_roles_are_closed_per_operation_and_a_patch_is_required_for_revisions() -> None:
    assert _code(_create_context(), create_candidate(operations=[])) == F.MISSING_PATCH
    assert _code(_create_context(), create_candidate(content_items=[])) == F.MISSING_CONTENT
    summary = create_candidate(content_items=[item("summary", "A stew for 6.", ["stew"], subject_ids=["main"],
                                                   source_refs=[CREATE_REQUEST], numbers=[6])])
    assert _code(_create_context(), summary) == F.ROLE_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Inquiry policy (Section 11)
# ---------------------------------------------------------------------------

def _question(**overrides) -> dict:
    values = dict(asked_about="whether dairy-free is a preference or an allergy", options=["preference", "allergy"],
                  purpose="resolve_safety_relevant_context", expected_use="set_constraint_reason",
                  sensitivity="health", source_refs=[DAIRY])
    values.update(overrides)
    target = values.pop("target", "dairy-free")
    return inquiry(target, **values)


def test_blocking_inquiries_and_request_arguments_never_come_from_a_proposal() -> None:
    for question in (_question(criticality="blocking"), _question(target_kind="request_argument")):
        assert _code(_revise_context(), revise_candidate(inquiries=[question])) == F.INQUIRY_INVALID
    assert _code(_revise_context(), revise_candidate(inquiries=[_question(options=["Preference!", "allergy"])])) \
        == F.INQUIRY_INVALID


@pytest.mark.parametrize("question, reason", [
    (_question(target="side-rice"), InquirySuppressionReason.UNRELATED_TARGET),
    (_question(purpose="improve_personalization"), InquirySuppressionReason.SENSITIVITY_EXCEEDS_VALUE),
    (_question(expected_use="select_option", options=[]), InquirySuppressionReason.UNLIKELY_TO_CHANGE_BEHAVIOR),
])
def test_unrelated_oversensitive_or_useless_questions_are_suppressed(question, reason) -> None:
    proposal = _compile(_revise_context(), revise_candidate(inquiries=[question]))
    [result] = proposal.inquiry_opportunities
    assert result.disposition == InquiryDisposition.SUPPRESSED and result.suppression_reason == reason
    assert proposal.selected_inquiries == ()


def _history(status: OpenInquiryStatus) -> OpenInquiryRecord:
    proposal = _compile(_revise_context(), revise_candidate())
    [question] = proposal.inquiry_opportunities
    return OpenInquiryRecord(
        inquiry_id=question.inquiry_id, record_kind="optional_inquiry", conversation_ref=CONVERSATION,
        parent_goal_ref="goal-earlier", originating_frame_ref=REQUEST, target_kind=question.target_kind,
        target_ref=question.target_ref, purpose=question.purpose, expected_use=question.expected_use,
        criticality=question.criticality, blocking=False, asked_about=question.asked_about,
        answer_options=question.answer_options, elicitation_move_ref="move", elicitation_delivery_ref="outcome",
        created_turn_ref="turn-earlier", expires_after_user_turns=2, status=status,
        resolution_ref=None if status == OpenInquiryStatus.OPEN else "frame-answer",
    )


@pytest.mark.parametrize("status, reason", [
    (OpenInquiryStatus.ANSWERED, InquirySuppressionReason.ALREADY_ANSWERED),
    (OpenInquiryStatus.OPEN, InquirySuppressionReason.RECENTLY_ASKED),
])
def test_answered_or_recently_asked_questions_are_not_repeated(status, reason) -> None:
    proposal = _compile(_revise_context(inquiry_history=(_history(status),)), revise_candidate())
    [question] = proposal.inquiry_opportunities
    assert question.suppression_reason == reason


def test_personality_only_moves_an_optional_questions_score() -> None:
    curious = _question(purpose="improve_personalization", expected_use="refine_artifact", sensitivity="ordinary",
                        criticality="optional_curiosity", target="main", target_kind="artifact_field",
                        asked_about="whether there is a favorite spice level", options=["mild", "spicy"])
    low = _compile(_revise_context(curiosity=0.2), revise_candidate(inquiries=[curious]))
    high = _compile(_revise_context(curiosity=0.95), revise_candidate(inquiries=[curious]))
    assert low.inquiry_opportunities[0].suppression_reason == InquirySuppressionReason.BELOW_CURIOSITY_THRESHOLD
    assert high.inquiry_opportunities[0].disposition == InquiryDisposition.SELECTED
    # Personality never rescues a suppressed question or creates a blocker.
    sensitive = {**curious, "sensitivity": "health"}
    assert _compile(_revise_context(curiosity=1.0), revise_candidate(inquiries=[sensitive])) \
        .inquiry_opportunities[0].suppression_reason == InquirySuppressionReason.SENSITIVITY_EXCEEDS_VALUE


def test_the_question_budget_keeps_optional_curiosity_from_becoming_an_interrogation() -> None:
    first = _question()
    second = _question(target="main", target_kind="artifact_field", purpose="discover_preference",
                       expected_use="refine_artifact", sensitivity="ordinary", asked_about="the spice level",
                       options=["mild", "spicy"])
    proposal = _compile(_revise_context(curiosity=0.5), revise_candidate(inquiries=[first, second]))
    selected = [item for item in proposal.inquiry_opportunities if item.disposition == InquiryDisposition.SELECTED]
    assert len(selected) == 1
    assert {item.suppression_reason for item in proposal.inquiry_opportunities} >= {
        InquirySuppressionReason.QUESTION_BUDGET_EXHAUSTED}


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_the_response_schema_is_strict_and_narrowed_to_the_requests_refs() -> None:
    payload = CollaborationModelPayload(
        operation="create", artifact_type="meal_plan", target_kind="new",
        sources=(_source(CREATE_REQUEST, SourceKind.USER_REQUEST, "create meal_plan"),),
        vocabulary=vocabulary_for(ArtifactOperation.CREATE, ArtifactType.MEAL_PLAN),
    )
    schema = response_json_schema(payload)
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    refs = schema["properties"]["content_items"]["items"]["properties"]["source_refs"]["items"]["enum"]
    assert refs == [CREATE_REQUEST]
    classes = schema["properties"]["content_items"]["items"]["properties"]["content_class"]["enum"]
    assert "verified_fact" not in classes and "tool_result" not in classes
    kinds = schema["properties"]["content_items"]["items"]["properties"]["claim_kind"]["enum"]
    assert "nutrient_amount" not in kinds and "tool_outcome" not in kinds
    criticality = schema["properties"]["inquiries"]["items"]["properties"]["criticality"]["enum"]
    assert criticality == ["quality_improving", "optional_curiosity"]
    assert CollaborationProviderError(F.PROVIDER_TIMEOUT).retryable
    assert not CollaborationProviderError(F.SCHEMA_INVALID).retryable
    assert InquiryPurpose.RESOLVE_SAFETY_RELEVANT_CONTEXT.value in payload.vocabulary.inquiry_purposes
