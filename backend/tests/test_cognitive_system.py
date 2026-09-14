import json
import time

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system import (
    CognitiveInputPipeline,
    Intent,
    IntentInterpreter,
    PerceptionEngine,
    PerceptionSignal,
)
from app.ai.Cognitive.system.classifier import IntentClassifier
from app.ai.Cognitive.system.training.evaluate_intent import evaluate


def test_perception_scores_are_bounded_and_signals_are_typed():
    perception = PerceptionEngine().perceive(
        "Can you analyze this image?",
        ["Hello", "Can you inspect the photo?"],
        context_signals={
            "has_image": PerceptionSignal(
                value=True,
                confidence=1.0,
                source="runtime_context",
            )
        },
    )

    assert perception.is_question is True
    assert perception.is_request is True
    assert 0.0 <= perception.novelty <= 1.0
    assert 0.0 <= perception.ambiguity <= 1.0
    assert perception.signals["has_image"].value is True


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("", Intent.UNKNOWN),
        ("Hello!", Intent.GREETING),
        ("What does this mean?", Intent.QUESTION),
        ("Please create a report", Intent.REQUEST),
        ("To clarify, I meant the second image", Intent.CLARIFY),
        ("The image was uploaded yesterday", Intent.INFORM),
    ],
)
def test_rules_fallback_handles_core_intents(message, expected):
    perception = PerceptionEngine().perceive(message)
    interpretation = IntentInterpreter().interpret(message, perception)

    assert interpretation.intent is expected
    assert interpretation.source in {"rules_fallback", "empty_input_guard"}
    assert 0.0 <= interpretation.confidence <= 1.0
    if expected is Intent.UNKNOWN:
        assert interpretation.needs_clarification is True


def test_repeated_message_has_zero_novelty():
    perception = PerceptionEngine().perceive("Same message", ["same message"])
    assert perception.novelty == 0.0


def test_common_indirect_request_is_detected():
    perception = PerceptionEngine().perceive("I want you to analyze this image")
    assert perception.is_request is True


def test_context_cannot_replace_reserved_rule_signals():
    with pytest.raises(ValueError, match="reserved signals"):
        PerceptionEngine().perceive(
            "hello",
            context_signals={
                "word_count": PerceptionSignal(value=999, confidence=1.0, source="test")
            },
        )


def test_signal_validation_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        PerceptionSignal(value="urgent", confidence=1.01, source="test")


def test_classifier_round_trip_and_probabilities(tmp_path):
    model = IntentClassifier().fit(
        [
            ("hello there", Intent.GREETING),
            ("hi friend", Intent.GREETING),
            ("please build this", Intent.REQUEST),
            ("create the report", Intent.REQUEST),
        ]
    )
    artifact = tmp_path / "intent.json"
    model.save(artifact)
    prediction = IntentClassifier.load(artifact).predict("hello friend")

    assert prediction.label is Intent.GREETING
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0)


def test_missing_or_invalid_artifact_uses_rules_fallback(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")

    for artifact in (tmp_path / "missing.json", invalid):
        result = CognitiveInputPipeline(
            interpretation=IntentInterpreter(artifact)
        ).process("Can you explain this?")
        assert result.interpretation.intent is Intent.REQUEST
        assert result.interpretation.source == "rules_fallback"


def test_empty_input_remains_unknown_with_trained_classifier(tmp_path):
    artifact = tmp_path / "intent.json"
    IntentClassifier().fit(
        [
            ("some information", Intent.INFORM),
            ("more information", Intent.INFORM),
            ("hello", Intent.GREETING),
        ]
    ).save(artifact)

    result = CognitiveInputPipeline(
        interpretation=IntentInterpreter(artifact)
    ).process("")

    assert result.interpretation.intent is Intent.UNKNOWN
    assert result.interpretation.needs_clarification is True


def test_artifact_with_unknown_intent_is_rejected_safely(tmp_path):
    artifact = tmp_path / "intent.json"
    artifact.write_text(
        json.dumps(
            {
                "artifact_version": 1,
                "alpha": 1.0,
                "class_counts": {"not-a-real-intent": 1},
                "token_counts": {"not-a-real-intent": {"hello": 1}},
            }
        ),
        encoding="utf-8",
    )

    result = CognitiveInputPipeline(
        interpretation=IntentInterpreter(artifact)
    ).process("Hello")

    assert result.interpretation.intent is Intent.GREETING
    assert result.interpretation.source == "rules_fallback"


def test_local_pipeline_has_no_material_per_call_latency():
    pipeline = CognitiveInputPipeline()
    started = time.perf_counter()
    for _ in range(500):
        pipeline.process("Can you analyze this image?", ["Please inspect this image"])
    elapsed = time.perf_counter() - started

    # This is intentionally generous: it catches accidental model/network work,
    # not minor machine-to-machine timing differences.
    assert elapsed < 2.0


def test_evaluation_is_deterministic_and_reports_required_metrics(tmp_path):
    dataset = tmp_path / "examples.jsonl"
    rows = [
        {"text": "hello", "intent": "greeting"},
        {"text": "hi there", "intent": "greeting"},
        {"text": "good morning", "intent": "greeting"},
        {"text": "please create it", "intent": "request"},
        {"text": "build the report", "intent": "request"},
        {"text": "send this", "intent": "request"},
    ]
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    first = evaluate(dataset, seed=7)
    second = evaluate(dataset, seed=7)

    assert first == second
    assert first["class_counts"] == {"greeting": 3, "request": 3}
    assert first["train_size"] + first["test_size"] == len(rows)
    assert 0.0 <= first["accuracy"] <= 1.0
    assert set(first["per_class"]) == {"greeting", "request"}
    assert set(first["confusion"]) == {"greeting", "request"}
