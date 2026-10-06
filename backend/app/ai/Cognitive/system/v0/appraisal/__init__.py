"""Deterministic, evidence-bound V0 appraisal."""

from .engine import AppraisalEngine
from .models import (
    ActiveGoal,
    AppraisalAggregate,
    AppraisalDimension,
    AppraisalFrame,
    AppraisalRequest,
    AppraisalResult,
    AppraisalSubject,
    CapabilitySnapshot,
    InterpretationEnvelope,
    InterpretationScenario,
    MemoryRecord,
    RelationshipSnapshot,
    RuntimeContext,
    SemanticFrame,
    SupplementalInput,
    UnknownInput,
    ValueRecord,
)

__all__ = [
    "ActiveGoal", "AppraisalAggregate", "AppraisalDimension", "AppraisalEngine",
    "AppraisalFrame", "AppraisalRequest", "AppraisalResult", "AppraisalSubject",
    "CapabilitySnapshot", "FixtureValidationReport", "InterpretationEnvelope",
    "InterpretationScenario", "MemoryRecord", "RelationshipSnapshot", "RuntimeContext",
    "SemanticFrame", "SupplementalInput", "UnknownInput", "ValueRecord", "validate_fixture",
]

# The fixture validator is review tooling that imports Goal and Dynamic State.
# It resolves lazily so production imports stay free of fixture code and the
# appraisal -> goal -> dynamic_state -> appraisal import cycle cannot trigger.
_FIXTURE_EXPORTS = frozenset({"FixtureValidationReport", "validate_fixture"})


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _FIXTURE_EXPORTS:
        from . import fixture_validator

        value = getattr(fixture_validator, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
