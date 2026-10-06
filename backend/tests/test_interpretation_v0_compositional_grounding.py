"""Compositional semantic grounding (SH-EVAL-003).

Several compatible acts in one contribution keep separate frames with their
own span evidence; harmless addressees, markers, punctuation, and casing do
not erase an act; quoted acts are never the user's; unknowns stay local to
the unresolved region; and personality cannot manufacture a missing act.
"""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.fixture_validator import _runtime_case
from app.ai.Cognitive.system.v0.interpretation_v0.semantic_grounding import (
    GROUNDING_RULES, SEMANTIC_GROUNDER_VERSION,
)

PRIOR = "Eggs have about six grams of protein each."


def _case(content: str, *, prior: bool = False, biases: dict | None = None) -> dict:
    messages = ([{"message_id": "p", "role": "assistant", "content": PRIOR}] if prior else []) + [
        {"message_id": "m", "role": "user", "content": content},
    ]
    neutral = {key: 0.5 for key in (
        "benign_prior", "trust_prior", "threat_sensitivity", "confirmation_tendency",
        "self_attribution_tendency", "ambiguity_tolerance", "social_affiliation_prior",
    )}
    return {
        "case_id": "compositional", "messages": messages, "focus_message_id": "m",
        "runtime_context": {"attachments": [], "available_capabilities": [], "last_tool_event": None,
                            "task_contract": None},
        "bias_scenarios": [{"profile_id": "neutral", "compiled_biases": {**neutral, **(biases or {})}}],
    }


def _interpret(content: str, **kwargs):
    _, request = _runtime_case(_case(content, **kwargs))
    return InterpretationEngine().interpret(request)


def _frames(envelope) -> dict[str, list]:
    out: dict[str, list] = {}
    for frame in envelope.semantic_frames:
        out.setdefault(frame.predicate, []).append(frame)
    return out


def _span_text(envelope, frame) -> str:
    by_id = {item.evidence_id: item for item in envelope.evidence}
    [ref] = frame.evidence_refs
    return by_id[ref].span.quoted_text


def _leading_acts(envelope) -> set[str]:
    return {
        item.hypothesis_key.rsplit(".", 1)[-1] for item in envelope.hypotheses
        if item.dimension.value == "communicative_act" and item.disposition.value == "leading"
    }


def test_the_rule_collection_is_versioned_and_declares_its_contracts() -> None:
    assert SEMANTIC_GROUNDER_VERSION == "interpretation-semantic-grounder-v0.2"
    ids = [rule.rule_id for rule in GROUNDING_RULES]
    assert len(ids) == len(set(ids))
    for rule in GROUNDING_RULES:
        assert rule.scope in {"prefix", "clause", "message"} and rule.predicates
        # Region rules require speaker-owned text; whole-contribution rules compete exclusively.
        assert rule.owned_only is (rule.scope != "message")
        assert rule.compatible is (rule.scope != "message")


@pytest.mark.parametrize(("alone", "addressed"), [
    ("hi", "hi columbina"),
    ("hello!", "hello there!"),
    ("how much protein is in eggs", "columbina, how much protein is in eggs"),
])
def test_an_act_survives_an_addressee(alone: str, addressed: str) -> None:
    plain, with_addressee = _interpret(alone), _interpret(addressed)
    assert set(_frames(plain)) == set(_frames(with_addressee))
    assert _leading_acts(plain) == _leading_acts(with_addressee)
    assert "unknown" not in _frames(with_addressee)


@pytest.mark.parametrize("content", [
    "hi, how much protein is in eggs?",
    "hi how much protein is in eggs",
    "HEYY Columbina what is in hummus",
    "hey columbina, what is in hummus?",
    "Good morning! What should I eat before a run?",
])
def test_greeting_and_question_keep_separate_frames_with_their_own_spans(content: str) -> None:
    envelope = _interpret(content)
    frames = _frames(envelope)
    assert set(frames) == {"social_opening", "information_request"}
    [greeting], [question] = frames["social_opening"], frames["information_request"]
    greeting_text, question_text = _span_text(envelope, greeting), _span_text(envelope, question)
    assert greeting_text.casefold().startswith(("hi", "hey", "good morning"))
    assert question_text.casefold().startswith(("how", "what"))
    assert not set(greeting_text) - set(content) and greeting_text in content and question_text in content
    assert content.index(greeting_text) < content.index(question_text)
    assert question.unresolved_arguments == ("topic_ref",)
    assert {"greet", "request_information"} <= _leading_acts(envelope)
    # Each act cites only its own frame.
    greet = next(item for item in envelope.hypotheses if item.hypothesis_key == "interpretation.act.greet")
    ask = next(item for item in envelope.hypotheses if item.hypothesis_key == "interpretation.act.request_information")
    assert greet.semantic_frame_refs == (greeting.frame_id,)
    assert ask.semantic_frame_refs == (question.frame_id,)
    leading = next(item for item in envelope.scenarios if item.disposition.value == "leading")
    assert "scenario.compositional-acts.v1" in leading.compatibility_rule_ids


@pytest.mark.parametrize("content", ["okay. how much protein is in eggs?", "how much protein is in eggs? okay."])
def test_two_compatible_acts_compose_in_either_order(content: str) -> None:
    envelope = _interpret(content, prior=True)
    frames = _frames(envelope)
    assert set(frames) == {"acknowledge_message", "information_request"}
    assert dict(frames["acknowledge_message"][0].arguments) == {"message_id": "p"}
    assert {"acknowledge", "request_information"} <= _leading_acts(envelope)


def test_two_questions_are_two_frames() -> None:
    envelope = _interpret("What is in hummus? And how many calories are in tofu?")
    questions = _frames(envelope)["information_request"]
    assert len(questions) == 2 and questions[0].frame_id != questions[1].frame_id
    assert [_span_text(envelope, item) for item in questions] == [
        "What is in hummus?", "how many calories are in tofu?",
    ]


@pytest.mark.parametrize("content", ['She said "hi" to me.', 'He asked "how much protein is in eggs?"'])
def test_quoted_acts_are_not_attributed_to_the_user(content: str) -> None:
    envelope = _interpret(content)
    frames = _frames(envelope)
    assert "social_opening" not in frames and "information_request" not in frames
    assert not {"greet", "request_information"} & _leading_acts(envelope)


@pytest.mark.parametrize("content", ["hmm", "blorp zib", '"what is it?"'])
def test_genuinely_unresolved_forms_stay_unknown(content: str) -> None:
    envelope = _interpret(content)
    assert set(_frames(envelope)) == {"unknown"}
    assert any(item.key == "meaning" for item in envelope.unknowns)


def test_unknowns_are_local_to_the_unresolved_region() -> None:
    envelope = _interpret("thanks! what about tofu")
    frames = _frames(envelope)
    assert set(frames) == {"unknown", "information_request"}
    assert _span_text(envelope, frames["unknown"][0]) == "thanks!"
    assert _span_text(envelope, frames["information_request"][0]) == "what about tofu"
    # A recognized act with an unresolved argument is not an unknown act.
    assert "request_information" in _leading_acts(envelope)


def test_legacy_domain_rules_still_ground_behind_a_prefix() -> None:
    envelope = _interpret("hey, no thanks", prior=False)
    assert set(_frames(envelope)) == {"social_opening", "accept_offer"}
    assert "reject_or_decline" in _leading_acts(envelope)
    single = _interpret("What time does the museum close?")
    assert set(_frames(single)) == {"museum_closing_time"}


@pytest.mark.parametrize("biases", [
    {"social_affiliation_prior": 1.0, "benign_prior": 1.0, "trust_prior": 1.0},
    {"social_affiliation_prior": 0.0, "threat_sensitivity": 1.0, "ambiguity_tolerance": 0.0},
])
def test_personality_cannot_manufacture_or_erase_an_act(biases: dict) -> None:
    neutral_question = _interpret("how much protein is in eggs")
    biased_question = _interpret("how much protein is in eggs", biases=biases)
    assert set(_frames(biased_question)) == set(_frames(neutral_question)) == {"information_request"}
    assert "greet" not in _leading_acts(biased_question)
    compound = _interpret("hi columbina how much protein is in eggs", biases=biases)
    assert {"greet", "request_information"} <= _leading_acts(compound)
