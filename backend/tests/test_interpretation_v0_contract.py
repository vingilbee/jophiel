"""Canonical Interpretation V0 trust-boundary contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.appraisal.models import (
    InterpretationEnvelope as AppraisalInterpretationEnvelope,
    SemanticFrame as AppraisalSemanticFrame,
)
from app.ai.Cognitive.system.v0.contracts.interpretation import (
    InterpretationEnvelope,
    SemanticFrame,
)
from app.ai.Cognitive.system.v0.interpretation_v0.fixture_validator import (
    DEFAULT_FIXTURE,
    _runtime_case,
)
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine


def _case(case_id: str) -> dict[str, object]:
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    return next(item for item in document["cases"] if item["case_id"] == case_id)


def _envelope() -> InterpretationEnvelope:
    _, request = _runtime_case(_case("factual-information-question"))
    return InterpretationEngine().interpret(request)


def test_shared_contracts_are_exact_class_aliases() -> None:
    assert AppraisalInterpretationEnvelope is InterpretationEnvelope
    assert AppraisalSemanticFrame is SemanticFrame


def test_envelope_is_deeply_frozen_extra_forbidden_and_round_trips() -> None:
    envelope = _envelope()
    restored = InterpretationEnvelope.model_validate_json(envelope.model_dump_json())

    assert restored == envelope
    with pytest.raises(ValidationError):
        InterpretationEnvelope.model_validate(envelope.model_dump() | {"invented": True})
    with pytest.raises(TypeError):
        envelope.semantic_frames[0].arguments["museum"] = "forged"  # type: ignore[index]


def test_duplicate_and_unresolved_references_fail_closed() -> None:
    envelope = _envelope()
    duplicate = envelope.model_dump(mode="python")
    duplicate["semantic_frames"] = (
        duplicate["semantic_frames"][0], duplicate["semantic_frames"][0]
    )
    with pytest.raises(ValidationError, match="duplicate frame"):
        InterpretationEnvelope.model_validate(duplicate)

    unresolved = envelope.model_dump(mode="python")
    unresolved["semantic_frames"][0]["evidence_refs"] = ("missing-evidence",)
    with pytest.raises(ValidationError, match="does not resolve"):
        InterpretationEnvelope.model_validate(unresolved)


def test_semantic_frame_rejects_noncanonical_polarity_and_bad_unknown_shape() -> None:
    frame = _envelope().semantic_frames[0]
    data = frame.model_dump(mode="python")
    with pytest.raises(ValidationError):
        SemanticFrame.model_validate(data | {"polarity": "mixed"})
    with pytest.raises(ValidationError, match="null"):
        SemanticFrame.model_validate(
            data | {"arguments": {"museum": "resolved"}, "unresolved_arguments": ("museum",)}
        )


def test_scenario_rejects_conflicting_pragmatic_meanings() -> None:
    _, request = _runtime_case(_case("indirect-window-request-with-attachment"))
    envelope = InterpretationEngine().interpret(request)
    payload = envelope.model_dump(mode="python")
    literal = next(
        item for item in payload["hypotheses"]
        if item["hypothesis_key"] == "interpretation.pragmatic.literal"
    )
    indirect = next(
        item for item in payload["hypotheses"]
        if item["hypothesis_key"] == "interpretation.pragmatic.indirect"
    )
    payload["scenarios"][0]["hypothesis_refs"]["pragmatic_meaning"] = (
        literal["hypothesis_id"], indirect["hypothesis_id"],
    )

    with pytest.raises(ValidationError, match="conflicting hypotheses"):
        InterpretationEnvelope.model_validate(payload)


def test_hypothesis_keys_are_unique_after_rule_consolidation() -> None:
    _, request = _runtime_case(_case("ambiguous-acknowledgement-bonding"))
    envelope = InterpretationEngine().interpret(request)
    keys = tuple(item.hypothesis_key for item in envelope.hypotheses)

    assert len(keys) == len(set(keys))
