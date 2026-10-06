"""OpenAI adapter for the Realization V0 model protocol.

This is the only module that touches a provider SDK; the pure contracts,
compiler, style mapper, and validator never import it.  One call per attempt:
the canonical JSON payload is sent mechanically as the single input message
(no system/developer prompt, persona, history, or per-case prose), the fixed
strict JSON schema is enforced, and tools, streaming, provider-side storage,
and SDK retries are all disabled.  The model id is explicit and there is no
fallback to any other configured model.

Request compatibility is owned here (SH-EVAL-007).  The configured model is
resolved against a versioned capability manifest when the adapter is built,
before any client exists: a model that cannot carry every request feature of
``realization-model-protocol-v0.2`` (for example, a reasoning model that only
accepts its default sampling temperature) fails closed with a stable
configuration code and never reaches the API.  Request parameters are then
constructed from the validated capability set, and every behavior-affecting
option is recorded in ``RealizationModelProvenance``.

Errors are mapped to typed provider codes.  Provider error bodies, request ids,
and generated text are never logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.ai.Cognitive.system.v0.contracts.realization import ProviderErrorCode, RealizationModelProvenance
from app.ai.Cognitive.system.v0.realization_v0.errors import (
    RealizationProviderConfigurationError, RealizationProviderError,
)
from app.ai.Cognitive.system.v0.realization_v0.model_protocol import (
    RESPONSE_JSON_SCHEMA, RESPONSE_SCHEMA_NAME, RealizationModelOutput, RealizationModelPayload,
    canonical_payload_json, output_from_excerpts, response_json_schema,
)

PROVIDER = "openai"
ENDPOINT = "chat.completions"
TEMPERATURE = "0.000000"  # reduces variation; never represented as determinism
MAX_OUTPUT_TOKENS = 1200
SDK_RETRIES = 0
PROVIDER_CAPABILITY_MANIFEST_VERSION = "realization-openai-capabilities-v0.2"


@dataclass(frozen=True)
class ModelCapabilities:
    """What one model family accepts on the endpoint this adapter uses."""

    family: str
    endpoints: frozenset[str]
    strict_json_schema: bool
    custom_temperature: bool
    reasoning_controls: bool
    token_limit_parameter: str | None
    storage_control: bool
    # Explicit none keeps Luna sampling controls valid and prevents default medium reasoning.
    reasoning_effort: Literal["none"] | None = None


# Request features ``realization-model-protocol-v0.2`` sends on every call.
PROTOCOL_REQUIRED_FEATURES = (
    "endpoint", "strict_json_schema", "custom_temperature", "token_limit_parameter", "storage_control",
)

_DATED = r"(?:-\d{4}-\d{2}-\d{2})?"
_CHAT = frozenset({ENDPOINT})
# Ordered, fully anchored patterns; the first match wins, so exceptions precede
# their family.  A model absent from this manifest is never guessed at.
CAPABILITY_MANIFEST: tuple[tuple[re.Pattern[str], ModelCapabilities], ...] = tuple(
    (re.compile(pattern), capabilities) for pattern, capabilities in (
        # The first gpt-4o snapshot predates strict structured outputs.
        (r"gpt-4o-2024-05-13", ModelCapabilities(
            "gpt-4o-2024-05-13", _CHAT, strict_json_schema=False, custom_temperature=True,
            reasoning_controls=False, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
        (rf"gpt-4o(?:-mini)?{_DATED}", ModelCapabilities(
            "gpt-4o", _CHAT, strict_json_schema=True, custom_temperature=True,
            reasoning_controls=False, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
        (rf"gpt-4\.1(?:-mini|-nano)?{_DATED}", ModelCapabilities(
            "gpt-4.1", _CHAT, strict_json_schema=True, custom_temperature=True,
            reasoning_controls=False, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
        # Early o1 variants lack strict structured outputs.
        (rf"o1-(?:mini|preview){_DATED}", ModelCapabilities(
            "o1-early", _CHAT, strict_json_schema=False, custom_temperature=False,
            reasoning_controls=False, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
        (rf"gpt-6-luna{_DATED}", ModelCapabilities(
            "gpt-6-luna", _CHAT, strict_json_schema=True, custom_temperature=True,
            reasoning_controls=True, token_limit_parameter="max_completion_tokens", storage_control=True,
            reasoning_effort="none",
        )),
        # Other registered reasoning families do not accept our custom temperature.
        (rf"(?:o1|o3|o3-mini|o4-mini){_DATED}", ModelCapabilities(
            "o-series", _CHAT, strict_json_schema=True, custom_temperature=False,
            reasoning_controls=True, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
        (rf"gpt-5(?:-mini|-nano)?{_DATED}", ModelCapabilities(
            "gpt-5", _CHAT, strict_json_schema=True, custom_temperature=False,
            reasoning_controls=True, token_limit_parameter="max_completion_tokens", storage_control=True,
        )),
    )
)


def capabilities_for(model_id: str) -> ModelCapabilities:
    """The manifest entry for ``model_id``, or a typed configuration failure."""

    for pattern, capabilities in CAPABILITY_MANIFEST:
        if pattern.fullmatch(model_id):
            return capabilities
    raise RealizationProviderConfigurationError("provider_model_not_in_capability_manifest")


def validate_protocol_capabilities(capabilities: ModelCapabilities) -> None:
    """Every request feature protocol v0.1 uses must be supported; no silent downgrade."""

    if ENDPOINT not in capabilities.endpoints:
        raise RealizationProviderConfigurationError("provider_endpoint_unsupported", missing_features=("endpoint",))
    missing = tuple(
        feature for feature, supported in (
            ("strict_json_schema", capabilities.strict_json_schema),
            ("custom_temperature", capabilities.custom_temperature),
            ("token_limit_parameter", capabilities.token_limit_parameter is not None),
            ("storage_control", capabilities.storage_control),
        ) if not supported
    )
    if missing:
        raise RealizationProviderConfigurationError("provider_model_capability_incompatible", missing_features=missing)


def resolve_provenance(
    *, model_id: str, timeout_seconds: int, max_output_tokens: int = MAX_OUTPUT_TOKENS,
    instruction_variant: str | None = None,
) -> RealizationModelProvenance:
    """Validate a configuration and return the exact provenance its calls record.

    Pure: no client, credential, or network access.  Used by the adapter and
    by replay to compare a stored configuration with the installed one.
    """

    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("an explicit realization model id is required")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("the realization timeout must be a positive number of seconds")
    capabilities = capabilities_for(model_id)
    validate_protocol_capabilities(capabilities)
    return RealizationModelProvenance(
        provider=PROVIDER, model_id=model_id, temperature=TEMPERATURE,
        max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds,
        endpoint=ENDPOINT, capability_manifest_version=PROVIDER_CAPABILITY_MANIFEST_VERSION,
        model_family=capabilities.family, token_limit_parameter=capabilities.token_limit_parameter,
        sampling_controls="temperature", reasoning_controls=capabilities.reasoning_effort or "not_sent",
        reasoning_effort=capabilities.reasoning_effort, candidate_count=1,
        sdk_retries=SDK_RETRIES, instruction_variant=instruction_variant,
    )


def _schema() -> dict[str, Any]:
    return json.loads(json.dumps(RESPONSE_JSON_SCHEMA))


class OpenAIRealizationModel:
    """A single-call structured transducer over the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str | None,
        timeout_seconds: int,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        client: Any | None = None,
        instruction_variant: str | None = None,
    ) -> None:
        # Validate the configuration before any client exists: an incompatible
        # model raises ``RealizationProviderConfigurationError`` and is never called.
        self._provenance = resolve_provenance(
            model_id=model_id, timeout_seconds=timeout_seconds, max_output_tokens=max_output_tokens,
            instruction_variant=instruction_variant,
        )
        if client is None:
            if not api_key:
                raise ValueError("an API key is required for the OpenAI realization provider")
            from openai import OpenAI  # imported lazily: the pure package never loads an SDK

            client = OpenAI(api_key=api_key, timeout=float(timeout_seconds), max_retries=SDK_RETRIES)
        self._client = client

    @property
    def provenance(self) -> RealizationModelProvenance:
        return self._provenance

    def with_instruction_variant(self, variant: str | None) -> "OpenAIRealizationModel":
        """The same configured provider and client with another instruction variant."""
        provenance = self._provenance
        return type(self)(model_id=provenance.model_id, api_key=None, timeout_seconds=provenance.timeout_seconds,
                          max_output_tokens=provenance.max_output_tokens, client=self._client,
                          instruction_variant=variant)

    def request_parameters(self, payload: RealizationModelPayload) -> dict[str, Any]:
        """The exact request, built from the validated capability set and recorded provenance."""

        provenance = self._provenance
        parameters = {
            "model": provenance.model_id,
            "messages": [{"role": "user", "content": canonical_payload_json(payload)}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": RESPONSE_SCHEMA_NAME, "strict": True,
                                "schema": response_json_schema(provenance.instruction_variant)},
            },
            "temperature": float(provenance.temperature),
            provenance.token_limit_parameter: provenance.max_output_tokens,
            "n": provenance.candidate_count,
            "stream": provenance.streaming,
            "store": False,
            "timeout": float(provenance.timeout_seconds),
        }

        if provenance.reasoning_effort is not None:
            parameters["reasoning_effort"] = provenance.reasoning_effort
        return parameters

    def generate(self, payload: RealizationModelPayload) -> RealizationModelOutput:
        import openai

        try:
            response = self._client.chat.completions.create(**self.request_parameters(payload))
        except openai.APITimeoutError as exc:
            raise RealizationProviderError(ProviderErrorCode.TIMEOUT) from exc
        except (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError) as exc:
            raise RealizationProviderError(ProviderErrorCode.UNSUPPORTED) from exc
        except (openai.APIConnectionError, openai.APIStatusError, openai.APIError) as exc:
            raise RealizationProviderError(ProviderErrorCode.TRANSPORT) from exc
        choices = getattr(response, "choices", None) or ()
        if len(choices) != 1:
            raise RealizationProviderError(ProviderErrorCode.OUTPUT_INVALID)
        choice = choices[0]
        message = choice.message
        if getattr(message, "tool_calls", None) or getattr(message, "function_call", None):
            raise RealizationProviderError(ProviderErrorCode.OUTPUT_INVALID)
        if getattr(message, "refusal", None):
            raise RealizationProviderError(ProviderErrorCode.REFUSED)
        if choice.finish_reason == "length":
            raise RealizationProviderError(ProviderErrorCode.OUTPUT_TRUNCATED)
        if choice.finish_reason == "content_filter":
            raise RealizationProviderError(ProviderErrorCode.REFUSED)
        if choice.finish_reason != "stop" or not isinstance(message.content, str):
            raise RealizationProviderError(ProviderErrorCode.OUTPUT_INVALID)
        try:
            return output_from_excerpts(json.loads(message.content), payload,
                                        tolerant=self._provenance.instruction_variant == "conversational-v1")
        except (ValueError, ValidationError, TypeError) as exc:
            raise RealizationProviderError(None, schema_invalid=True) from exc


__all__ = [
    "CAPABILITY_MANIFEST", "ENDPOINT", "MAX_OUTPUT_TOKENS", "ModelCapabilities", "OpenAIRealizationModel",
    "PROTOCOL_REQUIRED_FEATURES", "PROVIDER", "PROVIDER_CAPABILITY_MANIFEST_VERSION", "SDK_RETRIES", "TEMPERATURE",
    "capabilities_for", "resolve_provenance", "validate_protocol_capabilities",
]
