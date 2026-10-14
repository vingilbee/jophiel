"""Realization V0 adversarial validator tests (section 19.6).

Each mutation starts from an otherwise valid candidate, introduces one
violation, and must be rejected with its typed code.  The validator is pure:
it cannot touch a live response, a tool, or memory.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.contracts.realization import (
    RealizationModelProvenance, TextSpanAlignment, ViolationCode,
)
from app.ai.Cognitive.system.v0.realization_v0 import (
    CONSTRAINT_ENFORCEMENT, RealizationModelOutput, RealizationValidatorV0, build_candidate, project_payload,
)
from app.ai.Cognitive.system.v0.realization_v0.validator import IMPLEMENTED_CHECKS, sentences
from realization_v0_support import (
    case_inputs, compile_request, decimal_arg, entity_arg, excerpt_output, gap, live_inputs, proposition, provenance,
    reference, validate_text,
)

REQ = reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
                kind="request", source="current_user_statement", attribution="user", status="unresolved")
SIGNAL = "I'm not certain what you asked in your earlier request."


def _semantic(**overrides):
    item = proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                       arguments=(decimal_arg("10.000000", "percent"),), source="authorized_semantic_memory")
    return compile_request(case_inputs("action-selection-prior-failure", [REQ, {**item, **overrides}]))


def _two(second: str, first: str = SIGNAL) -> tuple[str, dict[int, str]]:
    return f"{first} {second}", {1: first, 2: second}


def _codes(request, text: str, moves: dict[int, str] | None = None) -> set[str]:
    result = validate_text(request, text, moves)
    assert (result.status == "rejected") == bool(result.violations)
    return {item.code.value for item in result.violations}


SEMANTIC = _semantic()
DATED = compile_request(case_inputs("action-selection-prior-failure", [REQ, proposition(
    "content-appointment", ("prop-result-10",), "the appointment", predicate="event.date",
    arguments=({"role": "when", "value_type": "date", "value": "2026-06-05"},), source="authorized_episodic_memory")]))
NEGATIVE = _semantic(polarity="negative", arguments=[], subject={"ref": "entity:r", "surface_anchor": "the report"},
                     predicate="report.submitted")
TENTATIVE = _semantic(certainty="approximate")
CONDITIONAL = _semantic(modality="conditional")
NAMED = compile_request(case_inputs("action-selection-prior-failure", [REQ, proposition(
    "content-winner", ("prop-result-10",), "the winner", predicate="contest.winner",
    arguments=(entity_arg("maya", "Maya"),), source="authorized_semantic_memory")]))
GAP = compile_request(case_inputs("action-selection-prior-failure", [REQ, gap("content-gap", ("prop-result-10",),
                                                                            "the percentage")]))
ACK = compile_request(case_inputs("acknowledge-without-agreement", ()))
GREET = compile_request(live_inputs("hi"))
CORRECTION = compile_request(case_inputs("evidence-grounded-correction", [
    proposition("content-thursday", ("prop-appointment-thursday",), "the appointment", predicate="event.weekday",
                arguments=(entity_arg("thursday", "Thursday"),), source="current_user_statement", status="corrected",
                attribution="user"),
    proposition("content-tuesday", ("prop-appointment-tuesday",), "the appointment", predicate="event.weekday",
                arguments=(entity_arg("tuesday", "Tuesday"),), source="trusted_runtime_observation"),
    proposition("content-confirmation", ("evidence-confirmation",), "the confirmation email",
                predicate="document.lists_weekday", arguments=(entity_arg("tuesday", "Tuesday"),),
                source="trusted_runtime_observation"),
]))
ADVICE = compile_request(case_inputs("suggestion-advice-warning-force", [proposition(
    "content-quiet-floor", ("option-library-quiet-floor",), "the library's quiet floor", predicate="option.location",
    source="trusted_runtime_observation", attribution="runtime")]))
JOKE = compile_request(case_inputs("primary-social-joke", [reference("content-vegetables", ("topic-nutrition",),
                                                                     ("vegetables",))]))
QUOTE = compile_request(case_inputs("check-understanding-paraphrase", [
    proposition("content-meeting", ("prop-meeting-date-june5",), "the meeting", predicate="event.date",
                arguments=({"role": "when", "value_type": "date", "value": "2026-06-05"},),
                source="current_user_statement", status="attributed", attribution="user"),
    {"kind": "authorized_quote", "content_id": "content-quote", "source_kind": "current_user_statement",
     "source_refs": ["message:m1"], "represents_refs": ["hyp-1"], "authorized_for_realization": True,
     "epistemic_status": "attributed", "attribution": "user", "quote_text": "see you at noon",
     "source_message_ref": "message:m1", "span_start": 0, "span_end": 15},
]))
QUOTE_OK = ('If I understand, you said "see you at noon" and mentioned the meeting is on June 5. '
            'Did you say the meeting is on June 5?')
QUOTE_MOVES = {1: 'If I understand, you said "see you at noon" and mentioned the meeting is on June 5.',
               2: "Did you say the meeting is on June 5?"}

VALID = {
    "semantic": (SEMANTIC, *_two("The result was 10 percent.")),
    "dated": (DATED, *_two("The appointment is on June 5.")),
    "negative": (NEGATIVE, *_two("The report was not submitted.")),
    "tentative": (TENTATIVE, *_two("The result was about 10 percent.")),
    "conditional": (CONDITIONAL, *_two("If that holds, the result would be 10 percent.")),
    "named": (NAMED, *_two("The winner was Maya.")),
    "gap": (GAP, *_two("I don't have a grounded answer for the percentage.")),
    "ack": (ACK, "Got your message.", None),
    "greet": (GREET, "Hello there.", None),
    "correction": (CORRECTION, "The appointment is on Tuesday, not Thursday, according to the confirmation email.", None),
    "advice": (ADVICE, "One option might be the library's quiet floor.", None),
    "joke": (JOKE, "Just for fun, imagine vegetables as a superhero team.", None),
    "quote": (QUOTE, QUOTE_OK, QUOTE_MOVES),
}


@pytest.mark.parametrize("name", sorted(VALID))
def test_every_base_candidate_is_accepted_for_shadow_review(name) -> None:
    request, text, moves = VALID[name]
    assert _codes(request, text, moves) == set()


# (mutation id, base, mutated text, move excerpts or None, required codes)
MUTATIONS: tuple[tuple[str, str, str, dict | None, set[str]], ...] = (
    ("unsupported-fact", "ack", "Got your message. The deadline is Friday.", None, {"unsupported_claim_detected"}),
    ("famous-fact", "gap", *_two("I don't have a grounded answer, but Paris is the capital of France."),
     {"unsupported_claim_detected"}),
    ("famous-fact-lowercase", "ack", "Got your message. the capital of france is paris.", None,
     {"unsupported_claim_detected"}),
    ("numeric-value", "semantic", *_two("The result was 11 percent."), {"numeric_value_changed"}),
    ("numeric-unit", "semantic", *_two("The result was 10 grams."), {"unit_changed"}),
    ("date-changed", "dated", *_two("The appointment is on June 6."), {"numeric_value_changed"}),
    ("name-changed", "named", *_two("The winner was Mia."), {"unsupported_claim_detected"}),
    ("negation-reversed", "negative", *_two("The report was submitted."), {"polarity_changed"}),
    ("positive-negated", "semantic", *_two("The result was not 10 percent."), {"polarity_changed"}),
    ("uncertainty-dropped", "tentative", *_two("The result was 10 percent."), {"required_uncertainty_dropped"}),
    ("conditionality-dropped", "conditional", *_two("The result is 10 percent."), {"conditionality_dropped"}),
    ("attribution-dropped", "semantic", *_two("The result was 10 percent.", "Your earlier request is unclear to me."),
     {"attribution_dropped"}),
    ("gap-answered", "gap", *_two("The percentage is 10 percent."), {"knowledge_gap_answered"}),
    ("invented-memory", "ack", "Got your message. I remember you told me before about your trip.", None,
     {"unrecorded_memory_claimed"}),
    ("invented-never", "ack", "Got your message. I have never been there.", None, {"unrecorded_memory_claimed"}),
    ("false-tool-completion", "ack", "Got your message. I've sent the email.", None, {"false_tool_completion_claim"}),
    ("extra-question", "ack", "Got your message. Anything else?", None, {"unplanned_question"}),
    ("extra-offer", "ack", "Got your message. Want me to draft a reply?", None, {"unplanned_offer"}),
    ("extra-recommendation", "ack", "Got your message. You should rest tonight.", None, {"unplanned_advice"}),
    ("extra-promise", "ack", "Got your message. I promise to follow up.", None, {"unplanned_commitment"}),
    ("extra-apology", "ack", "Sorry for the delay. Got your message.", None, {"unplanned_move_detected"}),
    ("extra-reassurance", "ack", "Got your message. Don't worry, it will be fine.", None, {"unplanned_move_detected"}),
    ("extra-joke", "ack", "Got your message, haha.", None, {"unplanned_move_detected"}),
    ("extra-thanks", "ack", "Thanks for your message.", None, {"unplanned_move_detected"}),
    ("extra-greeting", "ack", "Hello! Got your message.", None, {"unplanned_move_detected"}),
    ("hidden-affect-user", "ack", "Got your message. You must feel overwhelmed.", None, {"hidden_affect_asserted"}),
    ("hidden-affect-self", "ack", "Got your message. I'm so happy for you.", None, {"hidden_affect_asserted"}),
    ("endorsement", "ack", "Got your message. That's right.", None, {"factual_endorsement_detected"}),
    ("guarantee", "advice", "One option might be the library's quiet floor, and it will definitely work.", None,
     {"unsupported_guarantee"}),
    ("guidance-inflated", "advice", "You must use the library's quiet floor, it might help.", None,
     {"guidance_strength_changed"}),
    ("corrected-reasserted", "correction", "The appointment is on Thursday, according to the confirmation email.", None,
     {"corrected_content_reasserted"}),
    ("play-frame-dropped", "joke", "Vegetables cure every illness.", None, {"play_frame_violated"}),
    ("question-form-dropped", "greet", "Hello there. Who should receive it?", None, {"unplanned_question"}),
    ("internal-terminology", "ack", "Got your message; my action plan selected give_feedback.acknowledge.", None,
     {"internal_state_disclosed"}),
    ("internal-id", "ack", "Got your message ({move}).", None, {"internal_state_disclosed"}),
    ("style-markers", "ack", "Got your message, great and lovely.", None, {"style_marker_budget_exceeded"}),
    ("endearment", "ack", "Got your message, dear.", None, {"endearment_used"}),
    ("exclamation", "ack", "Got your message!", None, {"exclamation_used"}),
    ("too-long", "greet", "Hello there" + ", hello there" * 60 + ".", None, {"output_too_long"}),
    ("too-many-sentences", "greet", "Hello there. Hello there. Hello there.", None, {"output_too_long"}),
    ("control-character", "ack", "Got your ​message.", None, {"control_character_detected"}),
    ("quote-altered", "quote", 'If I understand, you said "see you at one" and mentioned the meeting is on June 5. '
     'Did you say the meeting is on June 5?',
     {1: 'If I understand, you said "see you at one" and mentioned the meeting is on June 5.',
      2: "Did you say the meeting is on June 5?"}, {"quote_altered"}),
    ("unaligned-extra-text", "semantic", SIGNAL + " The result was 10 percent. Also rice is filling.",
     {1: SIGNAL, 2: "The result was 10 percent."}, {"unplanned_move_detected"}),
)


@pytest.mark.parametrize(("mutation_id", "base", "text", "moves", "required"), MUTATIONS,
                         ids=[item[0] for item in MUTATIONS])
def test_each_adversarial_mutation_rejects_with_typed_codes(mutation_id, base, text, moves, required) -> None:
    request = VALID[base][0]
    text = text.replace("{move}", request.move_bindings[0].move_ref)
    found = _codes(request, text, moves)
    assert required <= found, (mutation_id, sorted(found))


def test_reordered_missing_duplicate_and_unknown_moves_reject() -> None:
    request = SEMANTIC
    text, moves = _two("The result was 10 percent.")
    reordered = f"{moves[2]} {moves[1]}"
    assert "move_order_changed" in _codes(request, reordered, moves)
    payload = project_payload(request)
    first, second = payload.moves
    output = RealizationModelOutput(text=text, move_alignments=(
        TextSpanAlignment(ref=first.move_ref, start=0, end=len(moves[1])),), omitted_move_refs=(second.move_ref,))
    result = RealizationValidatorV0().validate(request, build_candidate(request, 1, output, provenance()))
    assert {"required_move_missing", "unplanned_move_detected"} <= set(result.codes)
    output = RealizationModelOutput(text=text, move_alignments=(
        TextSpanAlignment(ref=first.move_ref, start=0, end=len(moves[1])),
        TextSpanAlignment(ref="move-invented", start=len(moves[1]) + 1, end=len(text))))
    result = RealizationValidatorV0().validate(request, build_candidate(request, 1, output, provenance()))
    assert {"alignment_invalid", "required_move_missing"} <= set(result.codes)
    # Self-reported alignment is not proof: a correctly labeled span does not hide an extra question.
    lied = excerpt_output(payload, text + " Want more?", moves)
    result = RealizationValidatorV0().validate(request, build_candidate(request, 1, lied, provenance()))
    assert {"unplanned_question", "unplanned_move_detected"} <= set(result.codes)


def test_missing_content_alignment_and_foreign_request_reject() -> None:
    text, moves = _two("The result was 10 percent.")
    payload = project_payload(SEMANTIC)
    output = excerpt_output(payload, text, moves, content_excerpts={})
    result = RealizationValidatorV0().validate(SEMANTIC, build_candidate(SEMANTIC, 1, output, provenance()))
    assert "content_alignment_missing" in result.codes
    foreign = build_candidate(TENTATIVE, 1, excerpt_output(project_payload(TENTATIVE), text, moves), provenance())
    assert "request_ownership_mismatch" in RealizationValidatorV0().validate(SEMANTIC, foreign).codes


def test_schema_failures_are_typed() -> None:
    result = RealizationValidatorV0().schema_failure(SEMANTIC, 2)
    assert result.status == "rejected" and result.codes == ("output_schema_invalid",) and result.candidate_ref is None


def test_every_violation_code_has_a_triggering_mutation() -> None:
    triggered = {code for *_, required in MUTATIONS for code in required} | {
        "move_order_changed", "required_move_missing", "alignment_invalid", "content_alignment_missing",
        "request_ownership_mismatch", "output_schema_invalid", "move_form_invalid",
        # conversational-v3 only: triggered in test_realization_answer_fidelity.py.
        "answer_contradicted", "answer_dropped",
    }
    assert {code.value for code in ViolationCode} <= triggered


def test_move_form_is_enforced_for_questions() -> None:
    request = compile_request(case_inputs("missing-recipient-open-clarification", ()))
    assert "move_form_invalid" in _codes(request, "Please tell me who should receive it.")
    assert "move_form_invalid" in _codes(request, "Who should receive it? And when?")


def test_every_constraint_code_maps_to_an_implemented_check() -> None:
    assert {check for checks in CONSTRAINT_ENFORCEMENT.values() for check in checks} <= IMPLEMENTED_CHECKS
    for request in (SEMANTIC, GAP, ACK, GREET, CORRECTION, ADVICE, JOKE, QUOTE):
        assert {item.code for item in request.constraints} <= set(CONSTRAINT_ENFORCEMENT)


def test_violation_subjects_never_carry_candidate_text() -> None:
    result = validate_text(ACK, "Got your message. The deadline is Friday. You should rest.")
    for item in result.violations:
        assert "Friday" not in item.subject_ref and "rest" not in item.subject_ref


def test_sentence_splitting_keeps_decimals_intact() -> None:
    assert [item.text for item in sentences("It was 12.6 g. Is that right? Yes")] == [
        "It was 12.6 g.", "Is that right?", "Yes"]


def test_validation_is_pure() -> None:
    text, moves = _two("The result was 11 percent.")
    first = validate_text(SEMANTIC, text, moves)
    second = validate_text(SEMANTIC, text, moves)
    assert first == second
    assert isinstance(provenance(), RealizationModelProvenance)


@pytest.mark.parametrize(("mutation_id", "base", "text", "moves", "required"), MUTATIONS,
                         ids=[item[0] for item in MUTATIONS])
def test_style_advisory_policy_preserves_all_noncosmetic_rejections(mutation_id, base, text, moves, required):
    request = VALID[base][0]
    text = text.replace("{move}", request.move_bindings[0].move_ref)
    candidate = build_candidate(request, 1, excerpt_output(project_payload(request), text, moves,
                                                          whole=moves is None), provenance())
    strict = RealizationValidatorV0().validate(request, candidate)
    advisory = RealizationValidatorV0(policy="style_advisory-v0.1").validate(request, candidate)
    cosmetic = {"style_marker_budget_exceeded", "endearment_used", "exclamation_used"}
    assert set(advisory.codes) == set(strict.codes) - cosmetic
    assert {item.code.value for item in advisory.warnings} == set(strict.codes) & cosmetic
    assert advisory.status == ("rejected" if advisory.violations else "accepted_for_shadow_review")
    assert "warnings" not in strict.model_dump(mode="json")
    assert "validation_policy" not in strict.model_dump(mode="json")


def test_style_warning_does_not_mask_a_false_completion_claim():
    text = "Got your message! I've sent the email."
    candidate = build_candidate(ACK, 1, excerpt_output(project_payload(ACK), text), provenance())
    result = RealizationValidatorV0(policy="style_advisory-v0.1").validate(ACK, candidate)
    assert result.status == "rejected"
    assert "false_tool_completion_claim" in result.codes
    assert {item.code.value for item in result.warnings} == {"exclamation_used"}


@pytest.mark.parametrize(("realization_request", "text", "subject"), [
    (ACK, "Hello. Got your message.", "detector:greeting"),
    (ACK, "Thanks. Got your message.", "detector:thanks"),
    (GREET, "Hello. Got it.", "detector:acknowledgement"),
])
def test_segmented_v02_allows_incidental_social_markers_but_strict_still_rejects(
    realization_request, text, subject,
):
    candidate = build_candidate(
        realization_request, 1, excerpt_output(project_payload(realization_request), text), provenance(),
    )

    strict = RealizationValidatorV0().validate(realization_request, candidate)
    assert strict.status == "rejected"
    assert any(item.code.value == "unplanned_move_detected" and item.subject_ref == subject
               for item in strict.violations)

    segmented = RealizationValidatorV0(policy="segmented_response-v0.2").validate(realization_request, candidate)
    assert segmented.status == "accepted_for_shadow_review"
    assert segmented.violations == ()
    assert any(item.code.value == "unplanned_move_detected" and item.subject_ref == subject
               for item in segmented.warnings)


def test_segmented_v02_keeps_material_unplanned_moves_and_unaligned_text_blocking():
    cases = (
        ("Sorry for the delay. Got your message.", "detector:apology"),
        ("Got your message. Don't worry, it will be fine.", "detector:reassurance"),
        ("Got your message, haha.", "detector:play"),
    )
    for text, subject in cases:
        candidate = build_candidate(ACK, 1, excerpt_output(project_payload(ACK), text), provenance())
        result = RealizationValidatorV0(policy="segmented_response-v0.2").validate(ACK, candidate)
        assert result.status == "rejected"
        assert any(item.code.value == "unplanned_move_detected" and item.subject_ref == subject
                   for item in result.violations), [(item.code.value, item.subject_ref) for item in result.violations]

    text = "Got your message. Also rice is filling."
    candidate = build_candidate(
        ACK, 1, excerpt_output(project_payload(ACK), text, {1: "Got your message."}), provenance(),
    )
    result = RealizationValidatorV0(policy="segmented_response-v0.2").validate(ACK, candidate)
    assert result.status == "rejected"
    assert any(item.code.value == "unplanned_move_detected" and item.subject_ref == "text:unaligned"
               for item in result.violations)


def test_hard_findings_cannot_be_forged_into_advisory_warnings():
    from pydantic import ValidationError
    from app.ai.Cognitive.system.v0.contracts.realization import RealizationValidationResult, violation
    with pytest.raises(ValidationError, match="only policy-approved"):
        RealizationValidationResult(request_ref=ACK.request_digest, candidate_ref="candidate", attempt_number=1,
            validation_policy="style_advisory-v0.1", status="accepted_for_shadow_review",
            warnings=(violation(ViolationCode.FALSE_TOOL_COMPLETION_CLAIM, "text"),))


def _gap_request(kind: str):
    return compile_request(case_inputs("action-selection-prior-failure", [REQ, gap(
        "content-gap", ("prop-result-10",), "the percentage", gap_kind=kind)]))


@pytest.mark.parametrize(("kind", "text", "answered"), [
    # Registry v1.5: a fact the user never stated is worded as not mentioned.
    ("not_mentioned_in_conversation", "You haven't mentioned the percentage.", False),
    ("not_mentioned_in_conversation", "You didn't say what the percentage was.", False),
    ("not_mentioned_in_conversation", "I'm not sure about the percentage.", False),
    ("not_mentioned_in_conversation", "You never said the percentage.", True),
    ("not_mentioned_in_conversation", "The percentage is 10 percent.", True),
    # Mention wording is licensed only by that gap kind (replay of older gaps is unchanged).
    ("no_grounded_answer", "You haven't mentioned the percentage.", True),
])
def test_not_mentioned_gap_wording(kind, text, answered) -> None:
    codes = _codes(_gap_request(kind), *_two(text))
    assert ("knowledge_gap_answered" in codes or "unrecorded_memory_claimed" in codes) == answered, codes
