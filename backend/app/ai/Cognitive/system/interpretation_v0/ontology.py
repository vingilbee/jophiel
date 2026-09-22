"""Static Interpretation V0 hypothesis, predicate, and feature manifests."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from ..contracts.interpretation import (
    HYPOTHESIS_CONFLICTS,
    HYPOTHESIS_COMPATIBILITIES,
    FrozenModel,
    HYPOTHESIS_KEYS_BY_DIMENSION,
    HypothesisDimension,
    SemanticFrame,
)

ONTOLOGY_VERSION = "interpretation-ontology-v1"
FEATURE_MANIFEST_VERSION = "interpretation-features-v1"
PREDICATE_REGISTRY_VERSION = "interpretation-predicates-v0.1"


class HypothesisDefinition(FrozenModel):
    hypothesis_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    dimension: HypothesisDimension
    description: str = Field(min_length=1)
    compatible_with: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    rule_references: tuple[str, ...] = Field(min_length=1)
    lifecycle_status: str = "v0_active"


def _hypothesis(key: str, dimension: HypothesisDimension) -> HypothesisDefinition:
    label = key.rsplit(".", 1)[-1].replace("_", " ")
    return HypothesisDefinition(
        hypothesis_key=key,
        definition_version=1,
        dimension=dimension,
        description=f"Deterministic V0 hypothesis for {label}.",
        compatible_with=HYPOTHESIS_COMPATIBILITIES.get(key, ()),
        conflicts_with=HYPOTHESIS_CONFLICTS.get(key, ()),
        rule_references=(f"{key}.rules-v0.1",),
    )


HYPOTHESIS_MANIFEST = tuple(
    _hypothesis(key, dimension)
    for dimension in HypothesisDimension
    for key in HYPOTHESIS_KEYS_BY_DIMENSION[dimension]
)


class PredicateDefinition(FrozenModel):
    predicate: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    lexical_or_structural_patterns: tuple[str, ...] = Field(min_length=1)
    allowed_arguments: tuple[str, ...]
    required_arguments: tuple[str, ...] = ()
    consequence_band: str
    reversibility: str
    capability_key: str | None = None


def _predicate(
    name: str,
    patterns: tuple[str, ...],
    arguments: tuple[str, ...],
    required: tuple[str, ...] = (),
    *,
    consequence: str = "low",
    reversibility: str = "reversible",
    capability: str | None = None,
) -> PredicateDefinition:
    return PredicateDefinition(
        predicate=name, definition_version=1,
        lexical_or_structural_patterns=patterns,
        allowed_arguments=arguments, required_arguments=required,
        consequence_band=consequence, reversibility=reversibility,
        capability_key=capability,
    )


PREDICATE_MANIFEST = (
    _predicate("museum_closing_time", ("museum close",), ("museum",), ("museum",)),
    _predicate("send_report", ("send the report", "sent the report"), ("agent", "object", "recipient"), ("object",), capability="send_report"),
    _predicate("appointment_date", ("appointment", "Tuesday", "Thursday"), ("accepted", "rejected"), ("accepted", "rejected")),
    _predicate("accept_offer", ("yes", "no thanks"), ("offer",), ("offer",)),
    _predicate("submit_form", ("submitted",), ("agent", "object"), ("agent", "object")),
    _predicate("acknowledge_message", ("okay", "ok"), ("message_id",), ("message_id",)),
    _predicate("evaluate_meal", ("soup", "service"), ("soup", "service"), ("soup", "service")),
    _predicate("weather_condition", ("raining",), ("condition", "location"), ("condition", "location")),
    _predicate("social_opening", ("hello", "hey", "heyy"), ("addressee",), ("addressee",)),
    _predicate("social_closing", ("goodbye", "bye"), ("future_contact",)),
    _predicate("train_departure_time", ("train leaves",), ("train", "time"), ("train", "time")),
    _predicate("bridge_closed", ("bridge is closed",), ("speaker", "bridge"), ("bridge",)),
    _predicate("contingency_action", ("what should",), ("condition", "action"), ("condition", "action")),
    _predicate("stop_timer", ("stop", "timer"), ("timer",), ("timer",), capability="stop_timer"),
    _predicate("room_temperature_change", ("getting cold",), ("direction", "location"), ("direction",)),
    _predicate("close_window", ("window", "cold"), ("window",), ("window",), capability="close_window"),
    _predicate("evaluate_export_event", ("broken export",), ("event", "positive_phrase"), ("event",)),
    _predicate("shared_progress", ("we did", "progress"), ("participants", "time"), ("participants",)),
    _predicate("capability_available", ("cannot export", "can't export"), ("capability", "claimed_value"), ("capability", "claimed_value")),
    _predicate("delete_database", ("delete", "database"), ("database", "authorization"), ("database", "authorization"), consequence="high", reversibility="irreversible", capability="delete_database"),
    _predicate("summarize_attachment", ("summarize", "invoice"), ("attachment_id", "object"), ("attachment_id", "object"), capability="summarize_attachment"),
    _predicate("unknown", ("unregistered predicate",), ("surface_action", "object_reference", "predicate"), ("predicate",)),
)
PREDICATES = tuple(item.predicate for item in PREDICATE_MANIFEST)


ACTIVE_PERCEPTION_FEATURES = (
    "conversation.question_form", "conversation.explicit_action_request",
    "conversation.explicit_correction", "conversation.repetition",
    "linguistic.negation", "linguistic.uncertainty_marker",
    "linguistic.self_reference", "runtime.attachment_present", "runtime.tool_failure",
)


def validate_hypothesis_manifest(
    definitions: Iterable[HypothesisDefinition] = HYPOTHESIS_MANIFEST,
) -> tuple[HypothesisDefinition, ...]:
    items = tuple(definitions)
    keys = tuple(item.hypothesis_key for item in items)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate interpretation hypothesis key")
    known = set(keys)
    for item in items:
        if item.lifecycle_status != "v0_active":
            raise ValueError("Interpretation V0 accepts only active hypothesis definitions")
        if item.hypothesis_key not in HYPOTHESIS_KEYS_BY_DIMENSION[item.dimension]:
            raise ValueError("hypothesis definition is assigned to the wrong dimension")
        if item.hypothesis_key in item.conflicts_with:
            raise ValueError("hypothesis cannot conflict with itself")
        if set(item.compatible_with) & set(item.conflicts_with):
            raise ValueError("hypothesis cannot be both compatible and conflicting")
        if not set((*item.compatible_with, *item.conflicts_with)) <= known:
            raise ValueError("hypothesis compatibility reference is unknown")
    return items


def validate_predicate_manifest(
    definitions: Iterable[PredicateDefinition] = PREDICATE_MANIFEST,
) -> tuple[PredicateDefinition, ...]:
    items = tuple(definitions)
    names = tuple(item.predicate for item in items)
    if len(names) != len(set(names)):
        raise ValueError("duplicate interpretation predicate")
    for item in items:
        if not set(item.required_arguments) <= set(item.allowed_arguments):
            raise ValueError(f"predicate {item.predicate} requires a disallowed argument")
    return items


def validate_semantic_frames(frames: Iterable[SemanticFrame]) -> tuple[SemanticFrame, ...]:
    items = tuple(frames)
    definitions = {item.predicate: item for item in PREDICATE_MANIFEST}
    for frame in items:
        definition = definitions.get(frame.predicate)
        if definition is None:
            raise ValueError("semantic grounder emitted an unregistered predicate")
        argument_keys = set(frame.arguments)
        if not argument_keys <= set(definition.allowed_arguments):
            raise ValueError(f"semantic frame {frame.predicate} emitted a disallowed argument")
        if not set(definition.required_arguments) <= argument_keys:
            raise ValueError(f"semantic frame {frame.predicate} omitted a required argument")
    return items
