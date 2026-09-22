"""Immutable contracts for the deterministic appraisal engine."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts.interpretation import (
    Contradiction,
    InterpretationEnvelope,
    InterpretationScenario,
    ScenarioDisposition,
    ScenarioSupport,
    SemanticFrame,
    UnknownInput,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_post_init(self, __context: Any) -> None:
        """Freeze containers too; Pydantic's ``frozen`` only protects fields."""
        for name in type(self).model_fields:
            object.__setattr__(self, name, _deep_freeze(getattr(self, name)))


class FrozenDict(dict):
    """A JSON-serializable dictionary that rejects all mutation."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("frozen mapping cannot be modified")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        frozen = FrozenDict()
        dict.update(frozen, {key: _deep_freeze(item) for key, item in value.items()})
        return frozen
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


class DimensionStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    DISPUTED = "disputed"
    NOT_APPLICABLE = "not_applicable"


class ActiveGoal(FrozenModel):
    goal_id: str = Field(min_length=1)
    owner: str
    goal_type: str
    description: str
    desired_state: Mapping[str, Any]
    priority_band: str
    status: str = "active"
    source_refs: tuple[str, ...] = ()


class CapabilitySnapshot(FrozenModel):
    snapshot_id: str = Field(min_length=1)
    available_actions: tuple[str, ...] = ()


class RuntimeContext(FrozenModel):
    capability_snapshot: CapabilitySnapshot
    available_actions: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    provided_parameters: Mapping[str, Any] = Field(default_factory=dict)
    authorization: str | None = None
    consent: str | None = None
    safety_decision: str | None = None
    external_export_control: bool | None = None
    policy_records: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = Field(default_factory=dict)
    last_tool_event: Mapping[str, Any] = Field(default_factory=dict)


class MemoryRecord(FrozenModel):
    memory_id: str = Field(min_length=1)
    memory_type: str
    content: str
    provenance: str
    status: str


class ValueRecord(FrozenModel):
    value_id: str = Field(min_length=1)
    key: str
    status: str


class RelationshipSnapshot(FrozenModel):
    relationship_snapshot_id: str = Field(min_length=1)
    status: str
    evidence: str | None = None
    records: tuple[str, ...] = ()


class SupplementalInput(FrozenModel):
    input_id: str = Field(min_length=1)
    input_type: str
    value: Any = None
    content: str | None = None
    provenance: str | None = None
    status: str | None = None
    outcome: str | None = None
    authorization: str | None = None
    resolved_arguments: Mapping[str, Any] = Field(default_factory=dict)
    memory_id: str | None = None
    message_id: str | None = None
    scenario_refs: tuple[str, ...] = ()
    goal_refs: tuple[str, ...] = ()
    event_refs: tuple[str, ...] = ()
    proposition_refs: tuple[str, ...] = ()


class AppraisalDimension(FrozenModel):
    dimension_key: str
    definition_version: int = 1
    status: DimensionStatus
    value_type: str
    value: str | None = None
    derivation_strength: str | None = None
    evidence_refs: tuple[str, ...] = ()
    rule_id: str | None = None
    required_input_refs: tuple[str, ...] = ()
    missing_input_keys: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_nullability(self) -> AppraisalDimension:
        dimension_types = {
            "appraisal.goal_relevance": "unsigned_ordinal",
            "appraisal.goal_congruence": "signed_ordinal",
            "appraisal.novelty": "unsigned_ordinal",
            "appraisal.certainty": "unsigned_ordinal",
            "appraisal.controllability": "unsigned_ordinal",
            "appraisal.norm_compatibility": "signed_ordinal",
            "appraisal.affiliative_value": "signed_ordinal",
            "appraisal.agency": "agency_category",
        }
        allowed_values = {
            "unsigned_ordinal": {"low", "moderate", "high"},
            "signed_ordinal": {"negative", "neutral", "positive"},
            "agency_category": {"user", "columbina", "third_party", "environment", "unknown"},
        }
        expected_type = dimension_types.get(self.dimension_key)
        if expected_type is None:
            raise ValueError(f"unsupported appraisal dimension: {self.dimension_key}")
        if self.value_type != expected_type:
            raise ValueError(f"{self.dimension_key} requires value_type={expected_type}")
        if self.status == DimensionStatus.KNOWN and self.value is None:
            raise ValueError("known appraisal dimensions require a value")
        if self.status != DimensionStatus.KNOWN and self.value is not None:
            raise ValueError("non-known appraisal dimensions cannot carry a resolved value")
        if self.status == DimensionStatus.UNKNOWN and not self.missing_input_keys:
            raise ValueError("unknown appraisal dimensions require missing input provenance")
        if self.status == DimensionStatus.DISPUTED and len(self.conflict_refs) < 2:
            raise ValueError("disputed appraisal dimensions require at least two conflict refs")
        if self.value is not None and self.value not in allowed_values[self.value_type]:
            raise ValueError(f"invalid {self.value_type} value: {self.value}")
        if self.derivation_strength not in {None, "weak", "moderate", "strong"}:
            raise ValueError(f"invalid derivation strength: {self.derivation_strength}")
        return self


class AppraisalSubject(FrozenModel):
    event_refs: tuple[str, ...]
    focal_goal_ref: str
    goal_owner: str
    goal_priority_band: str
    value_refs: tuple[str, ...]
    capability_snapshot_ref: str
    relationship_snapshot_ref: str | None = None


class AppraisalFrame(FrozenModel):
    frame_id: str
    revision: int = Field(ge=1)
    supersedes_frame_id: str | None = None
    scenario_id: str
    scenario_support: ScenarioSupport
    subject: AppraisalSubject
    dimensions: tuple[AppraisalDimension, ...]
    missing_inputs: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    completeness: str

    @model_validator(mode="after")
    def all_dimensions_are_unique(self) -> AppraisalFrame:
        keys = [item.dimension_key for item in self.dimensions]
        approved = {
            "appraisal.goal_relevance", "appraisal.goal_congruence", "appraisal.novelty",
            "appraisal.certainty", "appraisal.controllability", "appraisal.norm_compatibility",
            "appraisal.affiliative_value", "appraisal.agency",
        }
        if set(keys) != approved or len(keys) != len(approved):
            raise ValueError("an appraisal frame requires each of the eight V0 dimensions")
        return self

    def dimension(self, key: str) -> AppraisalDimension:
        return next(item for item in self.dimensions if item.dimension_key == key)


class AppraisalAggregate(FrozenModel):
    dominant_frame_ids: tuple[str, ...] = ()
    dimension_ranges: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    scenario_disagreement: str = "none"
    goal_conflict: bool = False
    high_consequence_uncertainty: bool = False
    missing_required_inputs: tuple[str, ...] = ()


class AppraisalRequest(FrozenModel):
    cognitive_turn_id: str
    interpretation: InterpretationEnvelope
    active_goals: tuple[ActiveGoal, ...]
    runtime: RuntimeContext
    memories: tuple[MemoryRecord, ...] = ()
    values: tuple[ValueRecord, ...] = ()
    relationship: RelationshipSnapshot | None = None
    supplemental_inputs: tuple[SupplementalInput, ...] = ()
    previous_frames: tuple[AppraisalFrame, ...] = ()
    frame_id_overrides: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def supplemental_scope_is_unambiguous(self) -> AppraisalRequest:
        if self.cognitive_turn_id != self.interpretation.cognitive_turn_id:
            raise ValueError("appraisal and interpretation cognitive-turn ids differ")
        retained = [
            item for item in self.interpretation.scenarios
            if item.disposition in {ScenarioDisposition.LEADING, ScenarioDisposition.RETAINED_ALTERNATIVE}
        ]
        goals = [item for item in self.active_goals if item.status == "active"]
        scenario_ids = {item.scenario_id for item in self.interpretation.scenarios}
        goal_ids = {item.goal_id for item in self.active_goals}
        if len(goal_ids) != len(self.active_goals):
            raise ValueError("appraisal active-goal ids must be unique")
        value_ids = [item.value_id for item in self.values]
        if len(value_ids) != len(set(value_ids)):
            raise ValueError("appraisal value ids must be unique")
        event_refs = {
            ref for item in self.interpretation.semantic_frames
            for ref in (item.frame_id, item.predicate)
        }
        proposition_refs = {
            ref for frame in self.interpretation.semantic_frames
            for key in ("proposition_ref", "proposition_refs")
            for ref in _string_refs(frame.arguments.get(key))
        }
        for item in self.supplemental_inputs:
            if not (item.provenance or item.message_id or item.memory_id):
                raise ValueError(
                    f"supplemental input {item.input_id} requires explicit provenance"
                )
            scoped = item.scenario_refs or item.goal_refs or item.event_refs or item.proposition_refs
            if not scoped and (len(retained) != 1 or len(goals) != 1):
                raise ValueError(
                    f"supplemental input {item.input_id} requires scope for ambiguous scenarios/goals"
                )
            for label, supplied, available in (
                ("scenario", set(item.scenario_refs), scenario_ids),
                ("goal", set(item.goal_refs), goal_ids),
                ("event", set(item.event_refs), event_refs),
                ("proposition", set(item.proposition_refs), proposition_refs),
            ):
                if not supplied <= available:
                    raise ValueError(
                        f"supplemental input {item.input_id} has unknown {label} scope refs: "
                        f"{sorted(supplied - available)}"
                    )
        frame_ids = [item.frame_id for item in self.previous_frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("historical appraisal frame ids must be unique")
        all_by_id = {item.frame_id: item for item in self.previous_frames}
        grouped: dict[tuple[str, str], list[AppraisalFrame]] = {}
        for frame in self.previous_frames:
            key = (frame.scenario_id, frame.subject.focal_goal_ref)
            grouped.setdefault(key, []).append(frame)
        for key, history in grouped.items():
            revisions = [item.revision for item in history]
            if len(revisions) != len(set(revisions)):
                raise ValueError(f"duplicate historical appraisal revisions for {key}")
            for frame in history:
                if frame.revision == 1:
                    if frame.supersedes_frame_id is not None:
                        raise ValueError("revision 1 appraisal frames cannot supersede another frame")
                    continue
                if frame.supersedes_frame_id is None:
                    raise ValueError("later appraisal revisions require a predecessor reference")
                predecessor = all_by_id.get(frame.supersedes_frame_id)
                if predecessor is not None and (
                    predecessor.revision != frame.revision - 1
                    or predecessor.scenario_id != frame.scenario_id
                    or predecessor.subject.focal_goal_ref != frame.subject.focal_goal_ref
                ):
                    raise ValueError("historical appraisal supersession chain is invalid")
        return self


def _string_refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


class AppraisalResult(FrozenModel):
    schema_version: int = 1
    appraisal_trace_id: str
    cognitive_turn_id: str
    interpretation_trace_id: str
    frames: tuple[AppraisalFrame, ...]
    aggregate: AppraisalAggregate
    versions: Mapping[str, str]

    @model_validator(mode="after")
    def internal_references_resolve(self) -> AppraisalResult:
        frame_ids = tuple(frame.frame_id for frame in self.frames)
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("appraisal frame ids must be unique")
        available = set(frame_ids)
        if not set(self.aggregate.dominant_frame_ids) <= available:
            raise ValueError("appraisal aggregate cites an unknown dominant frame")
        dimension_keys = {
            dimension.dimension_key
            for frame in self.frames
            for dimension in frame.dimensions
        }
        if not set(self.aggregate.dimension_ranges) <= dimension_keys:
            raise ValueError("appraisal aggregate cites an unknown dimension")
        return self
