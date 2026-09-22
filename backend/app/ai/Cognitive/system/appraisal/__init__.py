"""Deterministic, evidence-bound V0 appraisal."""

from .engine import AppraisalEngine
from .fixture_validator import FixtureValidationReport, validate_fixture
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
