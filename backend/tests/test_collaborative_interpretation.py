"""Gate 1 of Collaborative Content and Inquiry V0: the requested-outcome distinction.

The parser decides the pragmatic outcome from the bounded conversation and the
code-owned schema; the deterministic compiler rejects unknown operations,
foreign evidence or inquiries, and malformed answers.  Nothing here is a phrase
rule: the same surface text compiles to whatever validated frame the parser
returned.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineRuntimeSnapshot
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import ModelSemanticGrounder
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import (
    SemanticModelOutput, SemanticParserError, SemanticParserErrorCode as E,
)
from app.ai.Cognitive.system.v0.interpretation_v0.models import InterpretationOpenInquiry
from app.ai.Cognitive.system.v0.interpretation_v0.ontology import (
    ARTIFACT_TYPES, COLLABORATIVE_OPERATIONS, HISTORICAL_PREDICATE_REGISTRIES, MODEL_PREDICATE_MANIFEST,
    PREDICATE_REGISTRY_VERSION, PREDICATES, ActFamily, RequestedOutcome, act_family, requested_outcome,
)
from app.ai.Cognitive.system.v0.interpretation_v0.regression import (
    FrozenCandidateModel, RegressionTurn, iter_turns, load_corpus, request_for, run_turn,
)

CC = "collaborative_content_request"
CORPUS = load_corpus()
TURNS = {turn.case_id: turn for turn in iter_turns(CORPUS)}
CONTRASTS = sorted(key.removeprefix("contrast-artifact-") for key in TURNS if key.startswith("contrast-artifact-"))
INQUIRY = InterpretationOpenInquiry(
    inquiry_ref="workspace-inquiry:dairy-free-reason", purpose="resolve_safety_relevant_context",
    asked_about="whether dairy-free is a preference or an allergy", answer_options=("preference", "allergy"),
)


def _turn(text: str, frames: list[dict], *, inquiries: tuple = ()) -> RegressionTurn:
    return RegressionTurn("ad-hoc", "ad-hoc", ({"role": "user", "content": text},), {"frames": frames}, {},
                          tuple(item.model_dump(mode="json") for item in inquiries))


def _interpret(turn: RegressionTurn, model=None):
    model = model or FrozenCandidateModel(turn.frozen_candidate)
    return InterpretationEngine(grounder=ModelSemanticGrounder(model)).interpret(request_for(turn))


def _cc(evidence: str, **arguments) -> dict:
    return {"predicate": CC, "polarity": "positive", "evidence_text": evidence,
            "arguments": {"operation": "create", "artifact_type": "meal_plan", **arguments}}


# ---------------------------------------------------------------------------
# The closed requested-outcome classification
# ---------------------------------------------------------------------------

def test_requested_outcome_is_a_closed_property_of_each_request_predicate() -> None:
    assert set(RequestedOutcome) == {
        RequestedOutcome.INFORMATION_ANSWER, RequestedOutcome.CONVERSATIONAL_ARTIFACT,
        RequestedOutcome.EXTERNAL_EFFECT, RequestedOutcome.SOCIAL_EXCHANGE,
    }
    assert requested_outcome(CC) == RequestedOutcome.CONVERSATIONAL_ARTIFACT
    assert requested_outcome("action_request") == RequestedOutcome.EXTERNAL_EFFECT
    assert requested_outcome("unsafe_request") == RequestedOutcome.EXTERNAL_EFFECT
    assert requested_outcome("information_request") == RequestedOutcome.INFORMATION_ANSWER
    assert requested_outcome("social_opening") == RequestedOutcome.SOCIAL_EXCHANGE
    # Statements, constraints, corrections, and answers request nothing by themselves.
    for name in ("statement", "constraint_statement", "correction", "inquiry_answer", "unknown"):
        assert requested_outcome(name) is None
    # Every model-exposed request act declares its outcome.
    request_families = {ActFamily.REQUEST_ACTION, ActFamily.REQUEST_CONTENT, ActFamily.REQUEST_INFORMATION}
    for item in MODEL_PREDICATE_MANIFEST:
        if act_family(item.predicate) in request_families:
            assert item.requested_outcome is not None, item.predicate


def test_the_collaborative_frame_vocabularies_are_closed() -> None:
    [definition] = [item for item in MODEL_PREDICATE_MANIFEST if item.predicate == CC]
    assert definition.argument_values["operation"] == COLLABORATIVE_OPERATIONS == (
        "create", "revise", "explain", "evaluate", "describe", "compare", "summarize",
    )
    assert definition.argument_values["artifact_type"] == ARTIFACT_TYPES
    assert set(definition.required_arguments) == {"operation", "artifact_type"}
    assert definition.capability_key is None  # content in the chat needs no external capability
    assert definition.referent_arguments == ("target_ref",)


# ---------------------------------------------------------------------------
# The reviewed contrast corpus (Section 5.3), scored through the real compiler
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", CONTRASTS)
def test_each_contrast_pair_separates_conversational_content_from_external_effects(key: str) -> None:
    assert len(CONTRASTS) >= 6
    artifact = run_turn(TURNS[f"contrast-artifact-{key}"], FrozenCandidateModel(
        TURNS[f"contrast-artifact-{key}"].frozen_candidate))
    external = run_turn(TURNS[f"contrast-external-{key}"], FrozenCandidateModel(
        TURNS[f"contrast-external-{key}"].frozen_candidate))
    assert artifact.passed and external.passed, (artifact.mismatches, external.mismatches)
    assert artifact.observed["requested_outcomes"] == [f"{CC}:conversational_artifact"]
    assert external.observed["requested_outcomes"] == ["action_request:external_effect"]


@pytest.mark.parametrize("case_id", sorted(key for key in TURNS if key.startswith("form-")))
def test_indirect_polite_interrogative_and_embedded_forms_score_as_reviewed(case_id: str) -> None:
    turn = TURNS[case_id]
    score = run_turn(turn, FrozenCandidateModel(turn.frozen_candidate))
    assert score.passed, score.mismatches


def test_the_dinner_vertical_slice_parses_every_turn_as_reviewed() -> None:
    turns = [turn for key, turn in TURNS.items() if key.startswith("trace-collaborative-dinner#")]
    assert len(turns) == 8
    for turn in turns:
        score = run_turn(turn, FrozenCandidateModel(turn.frozen_candidate))
        assert score.passed, (turn.case_id, score.mismatches)


def test_an_interrogative_content_request_is_a_directive_not_an_information_question() -> None:
    envelope = _interpret(TURNS["form-interrogative-artifact"])
    keys = {item.hypothesis_key: item for item in envelope.hypotheses}
    assert keys["interpretation.act.request_action"].disposition.value == "leading"
    assert keys["interpretation.act.request_action"].rule_ids == ("act.frame-content-request.v1",)
    assert keys["interpretation.goal.cause_action"].rule_ids == ("goal.content-outcome.v1",)
    assert keys["interpretation.act.request_information"].disposition.value == "rejected"


def test_quoted_and_hypothetical_content_requests_are_never_the_speakers_own() -> None:
    for case_id in ("form-quoted-artifact", "form-hypothetical-artifact"):
        envelope = _interpret(TURNS[case_id])
        act = next(item for item in envelope.hypotheses if item.hypothesis_key == "interpretation.act.request_action")
        assert act.disposition.value == "rejected", case_id


def test_context_from_a_prior_greeting_never_leaks_into_the_focus_contribution() -> None:
    envelope = _interpret(TURNS["form-context-after-greeting"])
    assert [frame.predicate for frame in envelope.semantic_frames] == [CC]
    acts = {item.hypothesis_key for item in envelope.hypotheses if item.disposition.value == "leading"
            and item.dimension.value == "communicative_act"}
    assert acts == {"interpretation.act.request_action"}


def test_the_same_words_compile_to_whatever_validated_frame_the_parser_returned() -> None:
    """No phrase rule: identical text, different parses, different outcomes."""

    text = "Handle the invitations."
    as_content = _interpret(_turn(text, [_cc("Handle the invitations", artifact_type="draft_message")]))
    as_effect = _interpret(_turn(text, [{
        "predicate": "action_request", "polarity": "positive", "evidence_text": "Handle the invitations",
        "arguments": {"requested_effect": "send invitations", "subject_ref": "the invitations"},
    }]))
    assert [requested_outcome(item.predicate) for item in as_content.semantic_frames] == [
        RequestedOutcome.CONVERSATIONAL_ARTIFACT]
    assert [requested_outcome(item.predicate) for item in as_effect.semantic_frames] == [
        RequestedOutcome.EXTERNAL_EFFECT]


def test_a_genuinely_ambiguous_outcome_is_retained_not_guessed() -> None:
    envelope = _interpret(TURNS["form-ambiguous-outcome"])
    [frame] = envelope.semantic_frames
    assert frame.predicate == CC and frame.arguments["outcome_certainty"] == "ambiguous_external_effect"


# ---------------------------------------------------------------------------
# Compiler trust boundary
# ---------------------------------------------------------------------------

def _raw(frames: list[dict]):
    class _Model:
        version = "raw-double"
        provenance = None

        def __init__(self) -> None:
            self.payload = None

        def generate(self, payload):
            self.payload = payload
            region = payload.evidence_regions[0].evidence_ref
            return SemanticModelOutput.model_validate({"frames": [
                {"polarity": "positive", "evidence_refs": [region], **frame} for frame in frames
            ]})
    return _Model()


def _arguments(**values) -> list[dict]:
    return [{"name": key, "value": value} for key, value in values.items()]


@pytest.mark.parametrize("arguments, code", [
    (_arguments(operation="rewrite_everything", artifact_type="meal_plan"), E.ARGUMENT_VALUE_OUTSIDE_VOCABULARY),
    (_arguments(operation="create", artifact_type="spaceship"), E.ARGUMENT_VALUE_OUTSIDE_VOCABULARY),
    (_arguments(operation="create"), E.MISSING_REQUIRED_ARGUMENT),
    (_arguments(operation="create", artifact_type="meal_plan", tool="email"), E.DISALLOWED_ARGUMENT),
])
def test_the_compiler_rejects_unknown_operations_types_and_arguments(arguments, code) -> None:
    model = _raw([{"predicate": CC, "arguments": arguments}])
    with pytest.raises(Exception) as caught:
        _interpret(_turn("Plan dinner.", []), model)
    error = caught.value
    while error is not None and not isinstance(error, SemanticParserError):
        error = error.__cause__
    assert error is not None and error.code == code


def test_a_bare_deictic_target_is_an_explicit_unknown() -> None:
    model = _raw([{"predicate": CC, "arguments": _arguments(operation="revise", artifact_type="meal_plan",
                                                            target_ref="it")}])
    envelope = _interpret(_turn("Revise it.", []), model)
    [frame] = envelope.semantic_frames
    assert frame.arguments["target_ref"] is None and "target_ref" in frame.unresolved_arguments
    assert any(item.key == "target_ref" for item in envelope.unknowns)


def test_inquiry_answers_are_offered_only_with_supplied_open_inquiries() -> None:
    plain = _raw([{"predicate": CC, "arguments": _arguments(operation="create", artifact_type="meal_plan")}])
    _interpret(_turn("Plan dinner.", []), plain)
    assert "inquiry_answer" not in {item.predicate for item in plain.payload.predicates}
    assert "open_inquiries" not in plain.payload.model_dump(mode="json")

    answering = _raw([{"predicate": "inquiry_answer", "arguments": _arguments(
        inquiry_ref=INQUIRY.inquiry_ref, answer_kind="option", answer_option="allergy")}])
    envelope = _interpret(_turn("It's an allergy.", [], inquiries=(INQUIRY,)), answering)
    assert "inquiry_answer" in {item.predicate for item in answering.payload.predicates}
    assert [item.inquiry_ref for item in answering.payload.open_inquiries] == [INQUIRY.inquiry_ref]
    [frame] = envelope.semantic_frames
    assert frame.arguments["answer_option"] == "allergy"
    acts = {item.hypothesis_key: item for item in envelope.hypotheses}
    assert acts["interpretation.act.inform"].rule_ids == ("act.inquiry-answer.v1",)


@pytest.mark.parametrize("arguments, code, inquiries", [
    (_arguments(inquiry_ref="workspace-inquiry:never-asked", answer_kind="option", answer_option="allergy"),
     E.FOREIGN_INQUIRY, (INQUIRY,)),
    (_arguments(inquiry_ref=INQUIRY.inquiry_ref, answer_kind="option", answer_option="intolerance"),
     E.INQUIRY_ANSWER_INVALID, (INQUIRY,)),
    (_arguments(inquiry_ref=INQUIRY.inquiry_ref, answer_kind="free_text", answer_text=None),
     E.INQUIRY_ANSWER_INVALID, (INQUIRY,)),
    (_arguments(inquiry_ref=INQUIRY.inquiry_ref, answer_kind="option", answer_option="allergy"),
     E.UNREGISTERED_PREDICATE, ()),
])
def test_an_answer_cites_only_a_supplied_inquiry_in_a_shape_it_admits(arguments, code, inquiries) -> None:
    model = _raw([{"predicate": "inquiry_answer", "arguments": arguments}])
    with pytest.raises(Exception) as caught:
        _interpret(_turn("It's an allergy.", [], inquiries=inquiries), model)
    error = caught.value
    while error is not None and not isinstance(error, SemanticParserError):
        error = error.__cause__
    assert error is not None and error.code == code


# ---------------------------------------------------------------------------
# Historical registries
# ---------------------------------------------------------------------------

def _runtime(version: str, predicates: tuple[str, ...]) -> dict:
    return {"snapshot_id": "runtime-1", "predicate_registry_version": version, "known_predicates": predicates,
            "capability_snapshot_id": "capability-1", "response_obligation": "required",
            "response_obligation_ref": "obligation-1"}


def test_a_stored_request_of_the_previous_registry_stays_parseable_but_is_never_interpreted() -> None:
    previous = HISTORICAL_PREDICATE_REGISTRIES["interpretation-predicates-v0.3"]
    assert CC not in previous and "inquiry_answer" not in previous
    assert PREDICATE_REGISTRY_VERSION == "interpretation-predicates-v0.4"
    parsed = CognitivePipelineRuntimeSnapshot.model_validate(_runtime("interpretation-predicates-v0.3", previous))
    assert parsed.known_predicates == previous
    with pytest.raises(ValueError):
        CognitivePipelineRuntimeSnapshot.model_validate(_runtime("interpretation-predicates-v0.3", PREDICATES))
    request = request_for(_turn("Plan dinner.", []))
    stale = request.model_copy(update={"runtime": request.runtime.model_copy(update={
        "predicate_registry_version": "interpretation-predicates-v0.3", "known_predicates": previous,
    })})
    with pytest.raises(Exception):
        InterpretationEngine(grounder=ModelSemanticGrounder(_raw([]))).interpret(stale)
