"""Typed, content-safe Realization V0 failures.

``RealizationExecutionError`` is only for deterministic implementation or
contract failures.  Provider transport problems are typed provider outcomes
(``ProviderErrorCode``), and missing authorized content is a typed skip, never
an error the model is invited to repair.  Safe messages contain no user text,
model output, memory content, or provider error body.
"""

from __future__ import annotations

from ..contracts.realization import ProviderErrorCode, RealizationFailure, RealizationFailurePhase

SAFE_MESSAGES: dict[RealizationFailurePhase, str] = {
    RealizationFailurePhase.REQUEST_VALIDATION: "The realization inputs failed validation.",
    RealizationFailurePhase.ACTION_MANIFEST_RESOLUTION: "A selected action could not be resolved through the realization manifest.",
    RealizationFailurePhase.CONTENT_RESOLUTION: "Bound realization content failed authorization or integrity checks.",
    RealizationFailurePhase.STYLE_MAPPING: "The delivery profile could not be compiled.",
    RealizationFailurePhase.REQUEST_COMPILATION: "The realization request could not be compiled safely.",
    RealizationFailurePhase.MODEL_PROTOCOL: "The model payload could not be projected under the fixed protocol.",
    RealizationFailurePhase.PROVIDER_CALL: "The realization provider call failed.",
    RealizationFailurePhase.CANDIDATE_VALIDATION: "The realization candidate could not be validated.",
    RealizationFailurePhase.TRACE_VALIDATION: "The realization trace failed validation.",
}


class RealizationExecutionError(RuntimeError):
    """Fail-closed deterministic realization failure; no partial request or trace is attached."""

    def __init__(self, phase: RealizationFailurePhase, code: str, safe_message: str | None = None) -> None:
        message = safe_message or SAFE_MESSAGES[phase]
        super().__init__(message)
        self.phase = phase
        self.code = code
        self.safe_message = message

    def failure(self) -> RealizationFailure:
        return RealizationFailure(phase=self.phase, code=self.code, safe_message=self.safe_message)


class RealizationProviderError(RuntimeError):
    """A provider outcome: transport, timeout, refusal, truncation, or unsupported behavior.

    ``output_schema_invalid`` is raised with ``schema_invalid=True`` when the
    provider returned output that does not parse into the fixed protocol; the
    engine records that as a rejected attempt, not as a transport failure.
    """

    def __init__(self, code: ProviderErrorCode | None, *, schema_invalid: bool = False) -> None:
        if (code is None) != schema_invalid:
            raise ValueError("a provider error is either a typed provider code or a schema failure")
        super().__init__(code.value if code is not None else "output_schema_invalid")
        self.code = code
        self.schema_invalid = schema_invalid


# Stable, content-free configuration codes for incompatibilities detected
# locally, before any provider call is made.
PROVIDER_CONFIGURATION_CODES = (
    "provider_model_not_in_capability_manifest",
    "provider_model_capability_incompatible",
    "provider_endpoint_unsupported",
)


class RealizationProviderConfigurationError(ValueError):
    """The configured provider/model cannot carry the protocol's request features.

    Raised at adapter construction, so an incompatible configuration makes no
    API call.  ``missing_features`` names protocol features, never user data.
    """

    def __init__(self, code: str, *, missing_features: tuple[str, ...] = ()) -> None:
        if code not in PROVIDER_CONFIGURATION_CODES:
            raise ValueError("unregistered provider configuration code")
        super().__init__(code)
        self.code = code
        self.missing_features = missing_features


__all__ = [
    "PROVIDER_CONFIGURATION_CODES", "RealizationExecutionError", "RealizationProviderConfigurationError",
    "RealizationProviderError", "SAFE_MESSAGES",
]
