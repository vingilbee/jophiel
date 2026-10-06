"""Realization V0 model-protocol isolation tests (section 19.5)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.Cognitive.system.v0.realization_provider import MAX_OUTPUT_TOKENS, OpenAIRealizationModel
from app.ai.Cognitive.system.v0.contracts.realization import ProviderErrorCode
from app.ai.Cognitive.system.v0.live_shadow import ReplayStatus, realize_record, replay_realization
from app.ai.Cognitive.system.v0.realization_v0 import (
    RESPONSE_JSON_SCHEMA, RealizationEngineV0, RealizationModelPayload, canonical_payload_json, project_payload,
)
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderError
from realization_v0_support import (
    ScriptedModel, case_inputs, compile_request, decimal_arg, live_inputs, live_record, proposition, realize,
    reference, schema_invalid,
)

BACKEND = Path(__file__).resolve().parents[1]
ITEMS = [
    reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
              kind="request", source="current_user_statement", attribution="user", status="unresolved"),
    proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                arguments=(decimal_arg("10.000000", "percent"),), source="authorized_semantic_memory"),
    # A memory the trusted producer did not bind to any selected move.
    proposition("zz-unprojected-memory", ("unrelated-memory",), "the user's bank password",
                source="authorized_semantic_memory"),
]
TEXT = "I'm not certain what you asked in your earlier request. The result was 10 percent."
MOVES = {1: "I'm not certain what you asked in your earlier request.", 2: "The result was 10 percent."}
PAYLOAD_KEYS = {"protocol_version", "language", "moves", "content_items", "delivery_controls",
                "linguistic_context", "discourse_controls", "constraints", "prior_validation_codes"}


def _walk_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _walk_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _walk_keys(item)}
    return set()


def test_payload_is_a_strict_projection_excluding_history_memory_tools_and_traces() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS)
    model = ScriptedModel((TEXT, MOVES))
    assert realize(inputs, model).status == "accepted_shadow"
    assert len(model.payloads) == 1
    payload = model.payloads[0]
    assert isinstance(payload, RealizationModelPayload)
    data = json.loads(canonical_payload_json(payload))
    assert set(data) == PAYLOAD_KEYS
    serialized = canonical_payload_json(payload)
    keys = _walk_keys(data)
    for forbidden in ("messages", "history", "tools", "tool_choice", "functions", "system", "persona",
                      "appraisal", "attention", "values", "scored_candidates", "gate_results", "steps", "features",
                      "score", "session_id", "conversation_id", "previous_response_id", "thread_id",
                      "source_refs", "represents_refs", "cognitive_turn_id", "authorized_for_realization",
                      "reasoning", "trait_sources"):
        assert forbidden not in keys, forbidden
    assert "bank password" not in serialized and "zz-unprojected-memory" not in serialized

    def message_shaped(value) -> bool:  # a chat turn: {"role": ..., "content": ...}
        if isinstance(value, dict):
            return {"role", "content"} <= set(value) or any(message_shaped(item) for item in value.values())
        return isinstance(value, list) and any(message_shaped(item) for item in value)

    assert not message_shaped(data)
    action = inputs["action_result"]
    for step in action.steps:
        for candidate in step.constructed_candidates:
            if candidate.candidate_id not in {move.candidate_ref for move in action.plan.moves}:
                assert candidate.candidate_id not in serialized
    assert action.conversation_ref not in serialized and action.result_id not in serialized
    for phrase in ("You are Columbina", "Columbina stickers", "personal companion"):
        assert phrase not in serialized
    assert data["prior_validation_codes"] == []


def test_only_the_current_user_utterance_reaches_the_model_as_non_authoritative_context() -> None:
    text = "My neighbour Priya says the museum closes at 5 on Tuesdays, thanks!"
    record = live_record(text)
    inputs = live_inputs(text)
    model = ScriptedModel("Got your message.")
    realize(inputs, model)
    assert model.payloads, "the live plan should be realizable with generic structure"
    serialized = canonical_payload_json(model.payloads[0])
    context = model.payloads[0].linguistic_context
    assert context.text == text
    assert context.authority == "linguistic_context_only"
    assert context.permitted_use == "wording_and_reference_resolution_only"
    assert not any((context.fact_authority, context.action_authority,
                    context.authorization_authority, context.safety_authority))
    for message in record.compiled_request.messages:
        assert message.message_id not in serialized


def test_live_record_and_replay_use_the_same_stored_focus_utterance() -> None:
    record = live_record("hi")
    model = ScriptedModel("Hello there.")
    realized = realize_record(record, engine=RealizationEngineV0(model), configured_maximum_characters=1200)
    assert model.payloads[0].linguistic_context.text == "hi"
    report = replay_realization(
        realization_status=realized.realization_status,
        realization_trace_payload=realized.realization_trace.model_dump(mode="json"),
        realization_trace_digest_value=realized.realization_trace_digest,
        realization_semantic_digest_value=realized.realization_semantic_digest,
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode="json"),
        compiled_request_payload=record.compiled_request.model_dump(mode="json"),
        compiled_request_digest_value=record.compiled_request_digest,
        language_tag="en",
        fallback_maximum_characters=1200,
        expected_provenance=model.provenance,
    )
    assert (report.status, report.code) == (ReplayStatus.MATCH, "realization_semantic_digest_match")


def test_one_call_per_attempt_at_most_two_attempts_and_codes_only_on_retry() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS[:2])
    bad = ("I'm not certain what you asked in your earlier request. The result was 12 percent.",
           {1: MOVES[1], 2: "The result was 12 percent."})
    model = ScriptedModel(bad, bad, (TEXT, MOVES))
    trace = realize(inputs, model)
    assert len(model.payloads) == 2 and trace.status == "rejected"
    retry = model.payloads[1]
    assert retry.prior_validation_codes and all(set(item.model_dump()) == {"code", "subject_ref"}
                                                for item in retry.prior_validation_codes)
    assert "12 percent" not in canonical_payload_json(retry)
    assert model.payloads[0].prior_validation_codes == ()
    with pytest.raises(ValueError):
        RealizationEngineV0(model, max_attempts=3)
    single = ScriptedModel(bad)
    assert len(realize(inputs, single, max_attempts=1).attempts) == 1 and len(single.payloads) == 1


def test_no_model_means_provider_unavailable_never_a_fallback() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS[:2])
    trace = RealizationEngineV0(None).realize(**inputs, configured_maximum_characters=1200)
    assert trace.status == "provider_unavailable" and not trace.attempts and trace.model_provenance is None


def test_provider_errors_are_typed_and_bounded() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS[:2])
    timeout = RealizationProviderError(ProviderErrorCode.TIMEOUT)
    model = ScriptedModel(timeout, timeout, (TEXT, MOVES))
    trace = realize(inputs, model)
    assert trace.status == "provider_failed" and len(model.payloads) == 2
    assert [item.provider_error_code for item in trace.attempts] == ["provider_timeout", "provider_timeout"]
    refused = ScriptedModel(RealizationProviderError(ProviderErrorCode.REFUSED), (TEXT, MOVES))
    assert len(realize(inputs, refused).attempts) == 1  # refusal is not retried
    crash = ScriptedModel(RuntimeError("provider bug with secret body"), (TEXT, MOVES))
    trace = realize(inputs, crash)
    assert trace.status == "accepted_shadow"
    assert trace.attempts[0].provider_error_code == "provider_internal_error"
    assert "secret body" not in trace.model_dump_json()
    malformed = ScriptedModel(schema_invalid(), schema_invalid())
    trace = realize(inputs, malformed)
    assert trace.status == "rejected" and trace.final_candidate is None
    assert trace.validation.codes == ("output_schema_invalid",)


# ---------------------------------------------------------------------------
# The OpenAI adapter against a recording fake client
# ---------------------------------------------------------------------------

class _Completions:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, BaseException):
            raise response
        return response


def _client(*responses) -> SimpleNamespace:
    completions = _Completions(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions), completions=completions)


def _response(content: str | None, finish_reason: str = "stop", **message) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content, refusal=None, tool_calls=None,
                                                             function_call=None, **message),
    )])


def _json(payload, text: str, moves: dict[int, str]) -> str:
    return json.dumps({
        "text": text,
        "move_alignments": [{"move_ref": move.move_ref, "excerpt": moves[move.order]} for move in payload.moves],
        "content_alignments": [{"content_ref": binding.content_ref, "excerpt": moves[move.order]}
                               for move in payload.moves for binding in move.content_refs],
        "omitted_move_refs": [],
    })


def test_openai_adapter_is_a_single_structured_tool_free_call() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS[:2])
    request = compile_request(inputs)
    payload = project_payload(request)
    client = _client(_response(_json(payload, TEXT, MOVES)))
    model = OpenAIRealizationModel(model_id="gpt-4.1-mini", api_key=None, timeout_seconds=7, client=client)
    trace = RealizationEngineV0(model).realize(**inputs, configured_maximum_characters=1200)
    assert trace.status == "accepted_shadow", trace.validation
    [call] = client.completions.calls
    assert call["model"] == "gpt-4.1-mini"
    assert call["stream"] is False and call["store"] is False and call["n"] == 1 and call["temperature"] == 0
    assert call["timeout"] == 7.0 and call["max_completion_tokens"] == MAX_OUTPUT_TOKENS
    for key in ("tools", "tool_choice", "functions", "function_call", "parallel_tool_calls", "stream_options",
                "previous_response_id", "user", "metadata"):
        assert key not in call
    assert call["messages"] == [{"role": "user", "content": canonical_payload_json(payload)}]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["schema"] == json.loads(json.dumps(RESPONSE_JSON_SCHEMA))
    assert trace.model_provenance.model_id == "gpt-4.1-mini"
    assert call == model.request_parameters(payload)
    assert (trace.model_provenance.tools, trace.model_provenance.streaming,
            trace.model_provenance.provider_session_memory) == ("none", False, "disabled")


def test_openai_adapter_maps_failures_to_typed_codes() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS[:2])
    payload = project_payload(compile_request(inputs))
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    cases = (
        (openai.APITimeoutError(request=request), ProviderErrorCode.TIMEOUT),
        (openai.APIConnectionError(request=request), ProviderErrorCode.TRANSPORT),
        (_response(None, "length"), ProviderErrorCode.OUTPUT_TRUNCATED),
        (_response("{}", "stop", ), None),
        (_response(None, "stop", ), ProviderErrorCode.OUTPUT_INVALID),
        (_response("x", "content_filter"), ProviderErrorCode.REFUSED),
        (openai.BadRequestError("unsupported parameter", response=httpx.Response(400, request=request), body=None),
         ProviderErrorCode.UNSUPPORTED),
    )
    for response, code in cases:
        model = OpenAIRealizationModel(model_id="gpt-4.1", api_key=None, timeout_seconds=5, client=_client(response))
        with pytest.raises(RealizationProviderError) as caught:
            model.generate(payload)
        assert caught.value.code == code
        assert caught.value.schema_invalid is (code is None)
    refusal = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
        content=None, refusal="no", tool_calls=None, function_call=None))])
    tool_call = SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(
        content=None, refusal=None, tool_calls=[{"id": "x"}], function_call=None))])
    for response, code in ((refusal, ProviderErrorCode.REFUSED), (tool_call, ProviderErrorCode.OUTPUT_INVALID)):
        model = OpenAIRealizationModel(model_id="gpt-4.1", api_key=None, timeout_seconds=5, client=_client(response))
        with pytest.raises(RealizationProviderError) as caught:
            model.generate(payload)
        assert caught.value.code == code
    extra = json.loads(_json(payload, TEXT, MOVES)) | {"reasoning": "hidden chain of thought"}
    model = OpenAIRealizationModel(model_id="gpt-4.1", api_key=None, timeout_seconds=5,
                                   client=_client(_response(json.dumps(extra))))
    with pytest.raises(RealizationProviderError) as caught:
        model.generate(payload)
    assert caught.value.schema_invalid


def test_openai_adapter_requires_an_explicit_model_and_key() -> None:
    for model_id in ("", "   ", None):
        with pytest.raises(ValueError, match="explicit"):
            OpenAIRealizationModel(model_id=model_id, api_key="sk-test", timeout_seconds=5)
    with pytest.raises(ValueError, match="API key"):
        OpenAIRealizationModel(model_id="gpt-4.1", api_key=None, timeout_seconds=5)
    with pytest.raises(ValueError, match="timeout"):
        OpenAIRealizationModel(model_id="gpt-4.1", api_key="sk-test", timeout_seconds=0)


def test_configured_engine_has_no_model_fallback() -> None:
    from app.core.config import get_settings
    from app.services.cognitive_shadow import realization_engine

    base = get_settings().model_copy(update={
        "chat_model": "gpt-4.1", "cognitive_model": "gpt-cognitive", "subagent_model": "gpt-4.1-mini",
        "voice_chat_model": "gpt-voice", "openai_api_key": "sk-test",
        "cognitive_shadow_realization_model": None,
    })
    assert realization_engine(base).model is None
    assert realization_engine(base.model_copy(update={"cognitive_shadow_realization_model": "  "})).model is None
    assert realization_engine(base.model_copy(update={"cognitive_shadow_realization_model": "gpt-r",
                                                      "openai_api_key": None})).model is None
    engine = realization_engine(base.model_copy(update={"cognitive_shadow_realization_model": "gpt-4.1-nano",
                                                        "cognitive_shadow_realization_timeout_seconds": 9,
                                                        "cognitive_shadow_realization_max_attempts": 1}))
    assert engine.model.provenance.model_id == "gpt-4.1-nano"
    # An incompatible model is not swapped for another one: no model at all.
    incompatible = realization_engine(base.model_copy(update={"cognitive_shadow_realization_model": "gpt-5-mini"}))
    assert incompatible.model is None
    assert incompatible.unavailable_reason == "provider_configuration_incompatible"
    assert engine.model.provenance.timeout_seconds == 9 and engine.max_attempts == 1


def test_realization_defaults_are_conservative() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["cognitive_shadow_realization_enabled"].default is False
    assert fields["cognitive_shadow_realization_model"].default == "gpt-6-luna"
    assert fields["cognitive_shadow_realization_timeout_seconds"].default == 20
    assert fields["cognitive_shadow_realization_max_attempts"].default == 2
    assert fields["cognitive_shadow_realization_max_characters"].default == 1200
    with pytest.raises(ValueError):
        Settings(cognitive_shadow_realization_max_attempts=3)
    assert not any("deliver" in name or "live" in name for name in fields if name.startswith("cognitive_shadow_realization"))


def test_provider_sdk_is_isolated_to_the_adapter() -> None:
    root = BACKEND / "app/ai/Cognitive/system/v0"
    for path in [*(root / "realization_v0").glob("*.py"), root / "contracts/realization.py"]:
        text = path.read_text()
        assert "import openai" not in text and "from openai" not in text and "anthropic" not in text, path.name
