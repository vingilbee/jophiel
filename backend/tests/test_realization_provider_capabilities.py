"""Provider request compatibility is modeled and validated before any call (SH-EVAL-007)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0 import realization_provider as provider
from app.ai.Cognitive.system.v0.realization_provider import (
    PROVIDER_CAPABILITY_MANIFEST_VERSION, OpenAIRealizationModel, capabilities_for, resolve_provenance,
)
from app.ai.Cognitive.system.v0.contracts.realization import (
    ProviderUnavailableReason, RealizationModelProvenance, RealizationStatus, RealizationTraceV0,
)
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0, project_payload
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderConfigurationError
from realization_v0_support import compile_request, case_inputs, proposition, reference

ITEMS = [
    reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
              kind="request", source="current_user_statement", attribution="user", status="unresolved"),
    proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                arguments=({"role": "value", "value_type": "decimal", "value": "10.000000", "unit": "percent",
                            "surface_anchor": "10 percent"},), source="authorized_semantic_memory"),
]

SUPPORTED = {
    "gpt-4o": "gpt-4o", "gpt-4o-mini": "gpt-4o", "gpt-4o-2024-08-06": "gpt-4o",
    "gpt-4o-mini-2024-07-18": "gpt-4o", "gpt-4.1": "gpt-4.1", "gpt-4.1-mini": "gpt-4.1",
    "gpt-4.1-nano-2025-04-14": "gpt-4.1",
}
INCOMPATIBLE = {
    # Reasoning models accept only their default temperature.
    "gpt-5": ("custom_temperature",), "gpt-5-mini": ("custom_temperature",), "o3": ("custom_temperature",),
    "o4-mini": ("custom_temperature",),
    "gpt-4o-2024-05-13": ("strict_json_schema",),
    "o1-mini": ("strict_json_schema", "custom_temperature"),
}
UNKNOWN = ("gpt-test", "gpt-4o-audio-preview", "gpt-4.1-turbo", "claude-sonnet-5", "GPT-4.1", " gpt-4.1")


class RecordingClient:
    """Fails the test if any request reaches the provider."""

    def __init__(self):
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):  # pragma: no cover - reaching it is the failure
        self.calls.append(kwargs)
        raise AssertionError("an incompatible configuration must never call the provider")


@pytest.mark.parametrize(("model_id", "family"), sorted(SUPPORTED.items()))
def test_supported_models_resolve_with_exact_provenance(model_id: str, family: str) -> None:
    provenance = resolve_provenance(model_id=model_id, timeout_seconds=11)
    assert provenance.model_dump(mode="json") == {
        "provider": "openai", "model_id": model_id, "protocol_version": "realization-model-protocol-v0.3",
        "temperature": "0.000000", "max_output_tokens": provider.MAX_OUTPUT_TOKENS, "timeout_seconds": 11,
        "structured_output": "json_schema_strict", "tools": "none", "streaming": False,
        "provider_session_memory": "disabled", "endpoint": "chat.completions",
        "capability_manifest_version": PROVIDER_CAPABILITY_MANIFEST_VERSION, "model_family": family,
        "token_limit_parameter": "max_completion_tokens", "sampling_controls": "temperature",
        "reasoning_controls": "not_sent", "candidate_count": 1, "sdk_retries": 0,
    }
    assert RealizationModelProvenance.model_validate(provenance.model_dump(mode="json")) == provenance


@pytest.mark.parametrize(("model_id", "missing"), sorted(INCOMPATIBLE.items()))
def test_capability_rejected_models_fail_closed_before_any_call(model_id: str, missing: tuple[str, ...]) -> None:
    client = RecordingClient()
    with pytest.raises(RealizationProviderConfigurationError) as caught:
        OpenAIRealizationModel(model_id=model_id, api_key="sk-test", timeout_seconds=5, client=client)
    assert caught.value.code == "provider_model_capability_incompatible"
    assert caught.value.missing_features == missing
    assert client.calls == []


@pytest.mark.parametrize("model_id", UNKNOWN)
def test_models_outside_the_manifest_are_never_guessed(model_id: str, monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **_: pytest.fail("no client may be built"))
    with pytest.raises(RealizationProviderConfigurationError) as caught:
        OpenAIRealizationModel(model_id=model_id, api_key="sk-test", timeout_seconds=5)
    assert caught.value.code == "provider_model_not_in_capability_manifest"


def test_request_parameters_come_from_the_validated_capability_set() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS)
    payload = project_payload(compile_request(inputs))
    model = OpenAIRealizationModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=6, client=RecordingClient())
    parameters = model.request_parameters(payload)
    provenance = model.provenance
    assert set(parameters) == {
        "model", "messages", "response_format", "temperature", provenance.token_limit_parameter, "n", "stream",
        "store", "timeout",
    }
    assert parameters[provenance.token_limit_parameter] == provenance.max_output_tokens
    assert parameters["temperature"] == float(provenance.temperature)
    assert (parameters["n"], parameters["stream"], parameters["store"]) == (1, False, False)
    assert "reasoning_effort" not in parameters and provenance.reasoning_controls == "not_sent"


def test_every_manifest_entry_declares_the_protocol_features() -> None:
    from app.ai.Cognitive.system.v0.realization_v0.errors import PROVIDER_CONFIGURATION_CODES

    families = [capabilities.family for _, capabilities in provider.CAPABILITY_MANIFEST]
    assert len(families) == len(set(families))
    for _, capabilities in provider.CAPABILITY_MANIFEST:
        try:
            provider.validate_protocol_capabilities(capabilities)
        except RealizationProviderConfigurationError as exc:
            assert exc.code in PROVIDER_CONFIGURATION_CODES
            assert set(exc.missing_features) <= set(provider.PROTOCOL_REQUIRED_FEATURES)
            assert exc.missing_features
    assert capabilities_for("gpt-4o-2024-05-13").strict_json_schema is False
    assert capabilities_for("gpt-4o-2024-11-20").strict_json_schema is True


def test_an_incompatible_configured_model_yields_a_typed_unavailable_trace() -> None:
    from app.core.config import get_settings
    from app.services.cognitive_shadow import realization_configuration_code, realization_engine

    settings = get_settings().model_copy(update={
        "openai_api_key": "sk-test", "cognitive_shadow_realization_enabled": True,
        "cognitive_shadow_realization_model": "gpt-5",
    })
    assert realization_configuration_code(settings) == "provider_model_capability_incompatible"
    engine = realization_engine(settings)
    assert engine.model is None
    trace = engine.realize(**case_inputs("action-selection-prior-failure", ITEMS), configured_maximum_characters=1200)
    assert trace.status == RealizationStatus.PROVIDER_UNAVAILABLE
    assert trace.unavailable_reason == ProviderUnavailableReason.CONFIGURATION_INCOMPATIBLE
    assert trace.attempts == () and trace.model_provenance is None
    assert RealizationTraceV0.model_validate(trace.model_dump(mode="json")) == trace
    unconfigured = settings.model_copy(update={"cognitive_shadow_realization_model": None})
    assert realization_configuration_code(unconfigured) == "model_not_configured"
    assert realization_engine(unconfigured).unavailable_reason == ProviderUnavailableReason.MODEL_NOT_CONFIGURED
    compatible = settings.model_copy(update={"cognitive_shadow_realization_model": "gpt-4.1-mini"})
    assert realization_configuration_code(compatible) is None
    assert realization_configuration_code(settings.model_copy(update={
        "cognitive_shadow_realization_enabled": False})) is None


def test_worker_startup_reports_the_configuration_code(caplog) -> None:
    from app.core.config import get_settings
    from app.workers.cognitive_shadow import check_realization_configuration

    settings = get_settings().model_copy(update={
        "openai_api_key": "sk-test", "cognitive_shadow_realization_enabled": True,
        "cognitive_shadow_realization_model": "o3",
    })
    with caplog.at_level("WARNING"):
        assert check_realization_configuration(settings) == "provider_model_capability_incompatible"
    assert "provider_model_capability_incompatible" in caplog.text
    assert "sk-test" not in caplog.text


def test_legacy_provenance_keeps_its_serialization_and_options_are_all_or_nothing() -> None:
    legacy = RealizationModelProvenance(provider="openai", model_id="gpt-4.1", temperature="0.000000",
                                        max_output_tokens=1200, timeout_seconds=20)
    dumped = legacy.model_dump(mode="json")
    assert "endpoint" not in dumped and "capability_manifest_version" not in dumped
    assert legacy.resolved is False
    with pytest.raises(ValidationError, match="recorded together"):
        RealizationModelProvenance(**dumped, endpoint="chat.completions")


def test_unavailable_reason_is_only_for_unavailable_traces() -> None:
    inputs = case_inputs("action-selection-prior-failure", ITEMS)
    trace = RealizationEngineV0(None).realize(**inputs, configured_maximum_characters=1200)
    assert "unavailable_reason" not in trace.model_dump(mode="json")
    with pytest.raises(ValueError, match="no unavailable reason"):
        RealizationEngineV0(object(), unavailable_reason=ProviderUnavailableReason.MODEL_NOT_CONFIGURED)
    payload = trace.model_dump(mode="json") | {"unavailable_reason": "provider_configuration_incompatible"}
    assert RealizationTraceV0.model_validate(payload).unavailable_reason == "provider_configuration_incompatible"
