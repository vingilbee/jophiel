"""Deterministic v0 dynamic-state updater."""

from .initialization import DynamicStateSnapshotFactory, INITIAL_CONVERSATION_KEYS
from .models import (
    AppraisalAggregate,
    AppraisalDimension,
    AppraisalFrame,
    AttentionItem,
    AttentionSignal,
    Contribution,
    ControlContext,
    DerivedControls,
    Direction,
    DynamicStateContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityControl,
    PersonalityProfile,
    QualitativeBand,
    ScopeType,
    SemanticFrame,
    SourceKind,
    StateKey,
    StateSnapshot,
    StateValue,
    StepBand,
    TransitionEvidence,
    UpdateRequest,
    UpdateResult,
    ValueStatus,
)
from .rules import DEFAULT_RULE_REGISTRY, TransitionRule, TransitionRuleRegistry
from .updater import DynamicStateUpdater

# The persistence boundary imports SQLAlchemy and the ORM. It resolves lazily
# so pure consumers of these contracts (the personality mapper, live-shadow
# compiler) never load the database layer.
_PERSISTENCE_EXPORTS = frozenset({"DynamicStateSnapshotPersistence", "InitialSnapshotAlreadyExists"})


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in _PERSISTENCE_EXPORTS:
        from . import persistence

        value = getattr(persistence, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def validate_fixture(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Load the optional fixture adapter lazily so its CLI has clean imports."""
    from .fixture_validator import validate_fixture as _validate_fixture

    return _validate_fixture(*args, **kwargs)

__all__ = [
    "AttentionItem",
    "AttentionSignal",
    "AppraisalAggregate",
    "AppraisalDimension",
    "AppraisalFrame",
    "Contribution",
    "ControlContext",
    "DEFAULT_RULE_REGISTRY",
    "DerivedControls",
    "Direction",
    "DynamicStateSnapshotFactory",
    "DynamicStateSnapshotPersistence",
    "DynamicStateUpdater",
    "DynamicStateContext",
    "GroundedObject",
    "INITIAL_CONVERSATION_KEYS",
    "InitialSnapshotAlreadyExists",
    "OutcomeObservation",
    "PersonalityControl",
    "PersonalityProfile",
    "QualitativeBand",
    "ScopeType",
    "SemanticFrame",
    "SourceKind",
    "StateKey",
    "StateSnapshot",
    "StateValue",
    "StepBand",
    "TransitionEvidence",
    "TransitionRule",
    "TransitionRuleRegistry",
    "UpdateRequest",
    "UpdateResult",
    "ValueStatus",
    "validate_fixture",
]
