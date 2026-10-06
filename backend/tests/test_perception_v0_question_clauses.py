"""Clause-level, quote-aware question detection (SH-EVAL-004)."""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.perception_v0 import (
    CueState,
    PerceptionEvent,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    perceive,
)
from app.ai.Cognitive.system.v0.perception_v0.clauses import scan_clauses
from app.ai.Cognitive.system.v0.perception_v0.detectors import DETECTOR_BUNDLE_VERSION
from app.ai.Cognitive.system.v0.perception_v0.engine import PerceptionEngine
from app.ai.Cognitive.system.v0.perception_v0.manifest import LEGACY_MANIFEST_VERSION, MANIFEST_VERSION


def _event(content: str, *, manifest: str = MANIFEST_VERSION):
    request = PerceptionRequest(
        cognitive_turn_id="turn-question", runtime=PerceptionRuntimeContext(snapshot_id="runtime-question"),
        messages=(PerceptionMessage(message_id="focus", role="user", content=content),), focus_message_id="focus",
        cue_manifest_version=manifest,
    )
    return PerceptionEngine(manifest).perceive(request)


def _question(content: str, **kwargs):
    event = _event(content, **kwargs)
    return next(item for item in event.cue_activations if item.cue_key == "conversation.question_form")


def _spans(activation) -> list[str]:
    return [item.quoted_text for item in activation.evidence]


@pytest.mark.parametrize(("content", "clauses"), [
    # leading, embedded (after prefixes), and trailing question clauses
    ("How much protein is in eggs", ["How much protein is in eggs"]),
    ("hi columbina how much protein is in eggs", ["how much protein is in eggs"]),
    ("Hey Columbina, what should I eat tonight?", ["what should I eat tonight?"]),
    ("ok so can you tell me the calories in rice", ["can you tell me the calories in rice"]),
    ("I ate lunch. Is that enough protein", ["Is that enough protein"]),
    ("thanks! what about tofu", ["what about tofu"]),
    # punctuation present and absent
    ("Is tofu high in protein?", ["Is tofu high in protein?"]),
    ("Is tofu high in protein", ["Is tofu high in protein"]),
    ("you had eggs?", ["you had eggs?"]),
    # multi-question contributions
    ("What is in hummus? And how many calories are in tofu?", ["What is in hummus?", "how many calories are in tofu?"]),
    # combined with other acts
    ("Thanks, that helps. Could you also check my fat intake", ["Could you also check my fat intake"]),
    ('what does "net carbs" mean', ['what does "net carbs" mean']),
    ('"Protein" is what?', ['"Protein" is what?']),
])
def test_owned_question_clauses_are_present_with_clause_evidence(content: str, clauses: list[str]) -> None:
    activation = _question(content)
    assert (activation.state, activation.value) == (CueState.PRESENT, True)
    assert _spans(activation) == clauses


@pytest.mark.parametrize("content", [
    # question words used non-interrogatively
    "When I ate eggs I felt sick",
    "the place where I live is quiet",
    "What a great meal!",
    "How many times do I have to say it!",
    "what this means is simple",
    "Which is why I asked.",
    "I wonder what time it is",
    "Tell me how much protein is in eggs",
    # imperatives and statements sharing a first word with an auxiliary
    "Do this now", "Have a look at this", "May 5 works for me", "no problem", "hi", "Please send the report.",
    # quoted questions are not the speaker's
    'She asked "what time is it?"',
    'The tool said "Could you retry?"',
])
def test_non_questions_and_quoted_questions_are_absent(content: str) -> None:
    activation = _question(content)
    assert (activation.state, activation.value) == (CueState.ABSENT, False), content


@pytest.mark.parametrize("content", ['"What time is it?"', 'He wrote "is it done?'])
def test_questions_only_inside_ambiguous_quotation_stay_unknown(content: str) -> None:
    activation = _question(content)
    assert activation.state == CueState.UNKNOWN
    assert activation.missing_information


def test_clause_scan_reports_prefix_kinds_and_bases() -> None:
    [clause] = scan_clauses("good morning columbina what should I eat")
    assert clause.prefix_kinds == ("greeting", "addressee")
    assert clause.interrogative_basis == "wh_inversion"
    first, second = scan_clauses("It has 2.5 grams. How much fat is there")
    assert not first.interrogative and second.interrogative_basis == "wh_quantity"


def test_versions_and_historical_events_keep_their_meaning() -> None:
    current = _event("hi columbina how much protein is in eggs")
    assert current.versions.cue_manifest == MANIFEST_VERSION
    assert current.versions.deterministic_detectors == DETECTOR_BUNDLE_VERSION == "perception-deterministic-detectors-v0.2"
    question = next(item for item in current.cue_activations if item.cue_key == "conversation.question_form")
    assert question.detector_reference == "question-form-rules-v0.2"

    # A cue-manifest-v1 event is produced (and still validates) under the v0.1
    # rule it was recorded with; its meaning is not reinterpreted.
    legacy = _event("hi columbina how much protein is in eggs", manifest=LEGACY_MANIFEST_VERSION)
    assert legacy.versions.deterministic_detectors == "perception-deterministic-detectors-v0.1"
    legacy_question = next(item for item in legacy.cue_activations if item.cue_key == "conversation.question_form")
    assert legacy_question.detector_reference == "question-form-rules-v0.1"
    assert legacy_question.state == CueState.ABSENT  # the v0.1 rule missed it; history is preserved
    assert PerceptionEvent.model_validate(legacy.model_dump(mode="json")) == legacy
    punctuated = _event("What time does the museum close?", manifest=LEGACY_MANIFEST_VERSION)
    assert next(item for item in punctuated.cue_activations if item.cue_key == "conversation.question_form").state == CueState.PRESENT


def test_default_perceive_uses_the_current_manifest() -> None:
    event = perceive(PerceptionRequest(
        cognitive_turn_id="turn-default", runtime=PerceptionRuntimeContext(snapshot_id="runtime-default"),
        messages=(PerceptionMessage(message_id="m", role="user", content="so is it ready"),), focus_message_id="m",
    ))
    assert event.versions.cue_manifest == MANIFEST_VERSION
    assert next(item for item in event.cue_activations if item.cue_key == "conversation.question_form").value is True
