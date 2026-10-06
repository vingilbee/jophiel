"""Realization V0 bounded engine and terminal outcome assembly (sections 11.2, 12, 14)."""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.contracts.realization import ProviderErrorCode, RealizationTraceV0
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0, RealizationExecutionError
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderError
from realization_v0_support import (
    ScriptedModel, case_inputs, decimal_arg, live_inputs, proposition, realize, reference, schema_invalid,
)

ITEMS = [
    reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
              kind="request", source="current_user_statement", attribution="user", status="unresolved"),
    proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                arguments=(decimal_arg("10.000000", "percent"),), source="authorized_semantic_memory"),
]
SIGNAL = "I'm not certain what you asked in your earlier request."
GOOD = (f"{SIGNAL} The result was 10 percent.", {1: SIGNAL, 2: "The result was 10 percent."})
BAD = (f"{SIGNAL} The result was 12 percent.", {1: SIGNAL, 2: "The result was 12 percent."})
TIMEOUT = RealizationProviderError(ProviderErrorCode.TIMEOUT)


def _inputs():
    return case_inputs("action-selection-prior-failure", ITEMS)


def _roundtrip(trace: RealizationTraceV0) -> None:
    assert RealizationTraceV0.model_validate(trace.model_dump(mode="json")) == trace


def test_accepted_on_the_first_attempt_stops_generation() -> None:
    model = ScriptedModel(GOOD, BAD)
    trace = realize(_inputs(), model)
    assert trace.status == "accepted_shadow" and len(model.payloads) == 1
    assert trace.final_candidate == trace.attempts[0].candidate
    assert trace.validation.status == "accepted_for_shadow_review"
    _roundtrip(trace)


def test_accepted_after_one_retry() -> None:
    trace = realize(_inputs(), ScriptedModel(BAD, GOOD))
    assert trace.status == "accepted_shadow"
    assert [item.outcome for item in trace.attempts] == ["candidate_rejected", "candidate_accepted"]
    assert trace.attempts[1].prior_validation_codes
    assert trace.final_candidate.attempt_number == 2
    _roundtrip(trace)


def test_rejected_keeps_the_last_rejected_candidate_even_after_a_provider_error() -> None:
    trace = realize(_inputs(), ScriptedModel(BAD, TIMEOUT))
    assert trace.status == "rejected"
    assert [item.outcome for item in trace.attempts] == ["candidate_rejected", "provider_error"]
    assert trace.final_candidate == trace.attempts[0].candidate
    assert "numeric_value_changed" in trace.validation.codes
    _roundtrip(trace)


def test_provider_failed_has_no_candidate() -> None:
    trace = realize(_inputs(), ScriptedModel(TIMEOUT, TIMEOUT))
    assert trace.status == "provider_failed" and trace.final_candidate is None and trace.validation is None
    assert trace.request is not None and trace.model_provenance is not None
    _roundtrip(trace)


def test_schema_invalid_output_is_a_rejected_attempt() -> None:
    trace = realize(_inputs(), ScriptedModel(schema_invalid(), GOOD))
    assert trace.status == "accepted_shadow"
    assert trace.attempts[0].validation.codes == ("output_schema_invalid",)
    assert [item.code for item in trace.attempts[1].prior_validation_codes] == ["output_schema_invalid"]


def test_skips_never_call_the_provider() -> None:
    model = ScriptedModel(GOOD)
    trace = realize(case_inputs("action-selection-prior-failure", ITEMS[:1]), model)
    assert trace.status == "skipped_missing_content" and not model.payloads
    assert trace.request is None and trace.skip.code == "required_content_missing"
    _roundtrip(trace)


def test_deterministic_failures_raise_typed_errors_only() -> None:
    with pytest.raises(RealizationExecutionError) as caught:
        realize({**_inputs(), "language_tag": "de"}, ScriptedModel(GOOD))
    assert caught.value.failure().phase == "request_validation"


def test_trace_identity_is_deterministic_and_timings_are_not_semantic() -> None:
    from app.ai.Cognitive.system.v0.contracts.realization import realization_semantic_digest

    first, second = realize(_inputs(), ScriptedModel(GOOD)), realize(_inputs(), ScriptedModel(GOOD))
    assert first.trace_id == second.trace_id
    assert realization_semantic_digest(first) == realization_semantic_digest(second)


def test_temperature_zero_is_not_represented_as_determinism() -> None:
    trace = realize(live_inputs("hi"), ScriptedModel("Hello there."))
    assert trace.model_provenance.temperature == "0.000000"
    assert "deterministic" not in trace.model_provenance.model_dump_json()
    assert RealizationEngineV0(None).max_attempts == 2
