"""Black-box behavior for deterministic Interpretation V0."""

from __future__ import annotations

import json

import pytest

from app.ai.Cognitive.system.interpretation_v0 import (
    InterpretationEngine,
    InterpretationExecutionError,
    InterpretationMemory,
)
from app.ai.Cognitive.system.contracts.interpretation import InterpretationEnvelope
from app.ai.Cognitive.system.interpretation_v0.evidence import EvidenceRegistryBuilder
from app.ai.Cognitive.system.interpretation_v0.fixture_validator import (
    DEFAULT_FIXTURE,
    RuntimeEligibility,
    _runtime_case,
    validate_fixture,
)


def _case(case_id: str) -> dict[str, object]:
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    return next(item for item in document["cases"] if item["case_id"] == case_id)


def test_all_structural_and_live_fixture_cases_are_accounted_for() -> None:
    report = validate_fixture()

    assert report.passed
    assert report.cases_validated == 23
    assert report.cases_executed == 22
    assert report.expectations_checked == 128
    assert report.eligibility_counts == {
        RuntimeEligibility.LIVE_V0_ELIGIBLE.value: 22,
        RuntimeEligibility.ONTOLOGY_ONLY_SHADOW_DEPENDENCY.value: 1,
    }
    assert report.deferred_cases == ("positive-negative-mismatch",)
    assert len(report.ontology_questions) == 1


def test_interpretation_is_deterministic_and_preserves_exact_perception_ownership() -> None:
    perception, request = _runtime_case(_case("action-question-missing-recipient"))
    first = InterpretationEngine().interpret(request)
    second = InterpretationEngine().interpret(request)

    assert first == second
    assert first.cognitive_turn_id == perception.cognitive_turn_id
    assert first.perception_event_id == perception.perception_event_id
    assert first.input_message_ids == perception.input_message_ids
    assert first.focus_message_id == perception.focus_message_id
    assert first.runtime_snapshot_id == perception.runtime_snapshot_id
    assert first.unknowns


def test_bias_is_bounded_and_only_applies_behind_an_ambiguity_gate() -> None:
    _, request = _runtime_case(_case("indirect-window-request-with-attachment"))
    profile = request.biases.model_copy(update={"benign_prior": 1.0})
    envelope = InterpretationEngine().interpret(request.model_copy(update={"biases": profile}))

    adjusted = [item for item in envelope.hypotheses if item.bias_adjustments]
    assert adjusted
    assert all(abs(item.bias_adjustments[0].ordinal_shift) <= 1 for item in adjusted)
    assert all(item.bias_adjustments[0].ambiguity_gate > 0 for item in adjusted)
    assert all(
        not item.bias_adjustments
        for item in envelope.hypotheses
        if item.base_support.value == "strong" and item.disposition.value == "leading"
    )


def test_high_consequence_unknown_retains_a_coherent_alternative() -> None:
    _, request = _runtime_case(_case("consequential-delete-first-request"))
    envelope = InterpretationEngine().interpret(request)

    assert len(envelope.scenarios) >= 2
    assert sum(item.disposition.value == "leading" for item in envelope.scenarios) == 1
    assert any(item.disposition.value == "retained_alternative" for item in envelope.scenarios)
    assert any(item.key == "authorization" for item in envelope.unknowns)


class _BrokenGrounder:
    def ground(self, *_: object) -> object:
        raise RuntimeError("sensitive implementation detail")


def test_stage_failure_is_typed_and_does_not_leak_internal_details() -> None:
    _, request = _runtime_case(_case("factual-information-question"))
    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine(grounder=_BrokenGrounder()).interpret(request)  # type: ignore[arg-type]

    assert caught.value.failed_phase == "semantic_grounding"
    assert caught.value.cognitive_turn_id == request.cognitive_turn_id
    assert "sensitive" not in caught.value.safe_message


def test_unauthorized_memory_is_rejected_before_grounding() -> None:
    _, request = _runtime_case(_case("runtime-capability-memory-conflict"))
    forbidden = InterpretationMemory(
        memory_id="forbidden-memory",
        memory_type="episodic",
        content="The export capability is unavailable.",
        provenance="private-store",
        status="stale",
        authorized=False,
    )

    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine().interpret(request.model_copy(update={"memories": (forbidden,)}))

    assert caught.value.failed_phase == "validation"


def test_stale_authorized_memory_remains_explicit_conflicting_evidence() -> None:
    _, request = _runtime_case(_case("runtime-capability-memory-conflict"))
    envelope = InterpretationEngine().interpret(request)
    memory_ids = {
        item.evidence_id for item in envelope.evidence if item.memory_ref is not None
    }

    assert memory_ids
    assert any(
        memory_ids & set(contradiction.evidence_refs)
        for contradiction in envelope.contradictions
    )


def test_injected_factory_cannot_replace_validated_evidence() -> None:
    _, request = _runtime_case(_case("factual-information-question"))

    def forged_factory(**values: object) -> InterpretationEnvelope:
        evidence = list(values["evidence"])  # type: ignore[arg-type]
        evidence[0] = evidence[0].model_copy(update={"cue_key": "conversation.forged"})
        return InterpretationEnvelope.model_construct(**(values | {"evidence": tuple(evidence)}))

    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine(envelope_factory=forged_factory).interpret(request)

    assert caught.value.failed_phase == "envelope_validation"


def test_injected_evidence_stage_cannot_forge_ids_or_source_ownership() -> None:
    _, request = _runtime_case(_case("factual-information-question"))

    class _ForgedEvidenceBuilder:
        @staticmethod
        def build(supplied_request: object):
            records = list(EvidenceRegistryBuilder().build(supplied_request))  # type: ignore[arg-type]
            records[0] = records[0].model_copy(update={
                "evidence_id": "forged-evidence-id",
                "source_ref": "foreign-perception:conversation.question_form",
            })
            return tuple(records)

    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine(evidence_builder=_ForgedEvidenceBuilder()).interpret(request)  # type: ignore[arg-type]

    assert caught.value.failed_phase == "evidence"


def test_indirect_request_alternative_is_one_coherent_hypothesis_group() -> None:
    _, request = _runtime_case(_case("indirect-window-request-with-attachment"))
    envelope = InterpretationEngine().interpret(request)
    by_id = {item.hypothesis_id: item.hypothesis_key for item in envelope.hypotheses}
    alternatives = [
        scenario for scenario in envelope.scenarios
        if scenario.disposition.value == "retained_alternative"
    ]

    assert len(alternatives) == 1
    assert {
        by_id[ref]
        for refs in alternatives[0].hypothesis_refs.values()
        for ref in refs
    } == {
        "interpretation.act.request_action",
        "interpretation.goal.cause_action",
        "interpretation.modality.asserted",
        "interpretation.pragmatic.indirect",
    }


def test_unsupported_language_is_rejected_explicitly() -> None:
    _, request = _runtime_case(_case("factual-information-question"))

    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine().interpret(request.model_copy(update={"language_tag": "fr"}))

    assert caught.value.failed_phase == "validation"


class _BrokenStage:
    def __getattr__(self, _: str):
        return self.fail

    @staticmethod
    def fail(*_: object, **__: object) -> object:
        raise RuntimeError("sensitive implementation detail")


@pytest.mark.parametrize(
    ("phase", "engine_kwargs"),
    (
        ("validation", {"request_validator": _BrokenStage().fail}),
        ("evidence", {"evidence_builder": _BrokenStage()}),
        ("semantic_grounding", {"grounder": _BrokenStage()}),
        ("hypothesis_rules", {"rule_engine": _BrokenStage()}),
        ("bias_adjustment", {"bias_policy": _BrokenStage()}),
        ("scenario_building", {"scenario_builder": _BrokenStage()}),
        ("envelope_validation", {"envelope_factory": _BrokenStage().fail}),
    ),
)
def test_every_execution_phase_has_a_typed_failure(
    phase: str, engine_kwargs: dict[str, object],
) -> None:
    _, request = _runtime_case(_case("factual-information-question"))

    with pytest.raises(InterpretationExecutionError) as caught:
        InterpretationEngine(**engine_kwargs).interpret(request)  # type: ignore[arg-type]

    assert caught.value.failed_phase == phase
    assert "sensitive" not in caught.value.safe_message
