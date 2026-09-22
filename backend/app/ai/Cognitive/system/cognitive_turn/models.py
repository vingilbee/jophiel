"""Immutable contracts for one deterministic shadow cognitive turn."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from ..appraisal.models import (
    ActiveGoal,
    AppraisalResult,
    FrozenModel,
    InterpretationEnvelope,
    MemoryRecord,
    RelationshipSnapshot,
    RuntimeContext,
    ScenarioDisposition,
    SupplementalInput,
    ValueRecord,
)
from ..dynamic_state.models import (
    ControlContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityProfile,
    StateSnapshot,
    UpdateResult,
)
from ..contracts.interpretation import InterpretationVersions

TURN_SCHEMA_VERSION = "cognitive-turn-schema-v1"


class ProcessingPath(StrEnum):
    FAST = "fast"
    SLOW = "slow"
    REAPPRAISAL = "reappraisal"


class CognitiveTurnRequest(FrozenModel):
    schema_version: Literal[1] = 1
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    input_message_ids: tuple[str, ...] = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    processing_path: ProcessingPath = ProcessingPath.FAST

    interpretation: InterpretationEnvelope
    active_goals: tuple[ActiveGoal, ...]
    runtime: RuntimeContext
    memories: tuple[MemoryRecord, ...] = ()
    values: tuple[ValueRecord, ...] = ()
    relationship: RelationshipSnapshot | None = None
    supplemental_inputs: tuple[SupplementalInput, ...] = ()

    previous_state: StateSnapshot
    personality: PersonalityProfile
    grounded_objects: tuple[GroundedObject, ...] = ()
    control_context: ControlContext = Field(default_factory=ControlContext)
    outcomes: tuple[OutcomeObservation, ...] = ()
    elapsed_monotonic_ms: int = Field(default=0, ge=0)

    core_memory_ref: str = Field(min_length=1)
    memory_retrieval_policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_v0_inputs(self) -> CognitiveTurnRequest:
        if any(not item for item in self.input_message_ids):
            raise ValueError("input message ids must be non-empty")
        if len(self.input_message_ids) != len(set(self.input_message_ids)):
            raise ValueError("input message ids must be unique")
        if self.input_message_ids.count(self.focus_message_id) != 1:
            raise ValueError("focus message must occur exactly once in the bounded input")
        if self.focus_message_id != self.input_message_ids[-1]:
            raise ValueError("focus message must be the final bounded input message")
        interpretation = self.interpretation
        if (
            self.cognitive_turn_id != interpretation.cognitive_turn_id
            or self.conversation_ref != interpretation.conversation_ref
            or self.input_message_ids != interpretation.input_message_ids
            or self.focus_message_id != interpretation.focus_message_id
        ):
            raise ValueError("cognitive-turn ownership differs from interpretation")
        if self.previous_state.conversation_ref != self.conversation_ref:
            raise ValueError("previous state belongs to a different conversation")
        if self.previous_state.personality_mapper_version != self.personality.mapper_version:
            raise ValueError("previous state personality mapper differs from the supplied profile")
        if not any(goal.status == "active" for goal in self.active_goals):
            raise ValueError("the V0 cognitive turn requires at least one active goal")
        retained = {
            ScenarioDisposition.LEADING,
            ScenarioDisposition.RETAINED_ALTERNATIVE,
        }
        if not any(item.disposition in retained for item in self.interpretation.scenarios):
            raise ValueError("the V0 cognitive turn requires a retained interpretation scenario")
        for label, identifiers in (
            ("active goal", tuple(goal.goal_id for goal in self.active_goals)),
            ("value", tuple(value.value_id for value in self.values)),
            ("outcome", tuple(outcome.outcome_id for outcome in self.outcomes)),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} ids must be unique")
        return self


class CognitiveTurnVersions(FrozenModel):
    turn_schema: str = Field(min_length=1)
    interpretation_schema: str = Field(min_length=1)
    appraisal_schema: str = Field(min_length=1)
    appraisal_rules: str = Field(min_length=1)
    dynamic_state_policy: str = Field(min_length=1)
    personality_mapper: str = Field(min_length=1)
    memory_retrieval_policy: str = Field(min_length=1)
    interpretation_versions: InterpretationVersions


class CognitiveTurnTimings(FrozenModel):
    appraisal_ms: float
    dynamic_state_ms: float
    total_ms: float

    @model_validator(mode="after")
    def finite_non_negative(self) -> CognitiveTurnTimings:
        values = (self.appraisal_ms, self.dynamic_state_ms, self.total_ms)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("cognitive-turn timings must be finite and non-negative")
        if not math.isclose(
            self.total_ms,
            self.appraisal_ms + self.dynamic_state_ms,
            rel_tol=1e-9,
            abs_tol=1e-3,
        ):
            raise ValueError("total timing must equal the sum of stage timings")
        return self


class CognitiveTurnTrace(FrozenModel):
    schema_version: Literal[1] = 1
    trace_id: str = Field(min_length=1)
    trace_revision: Literal[1] = 1
    supersedes_trace_id: None = None

    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    input_message_ids: tuple[str, ...] = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    processing_path: ProcessingPath
    status: Literal["completed"] = "completed"

    input_snapshot: CognitiveTurnRequest
    appraisal: AppraisalResult
    dynamic_state_update: UpdateResult
    versions: CognitiveTurnVersions
    timings: CognitiveTurnTimings

    @model_validator(mode="after")
    def validate_cross_stage_invariants(self) -> CognitiveTurnTrace:
        request = self.input_snapshot
        snapshot = self.dynamic_state_update.snapshot
        if self.processing_path != ProcessingPath.FAST:
            raise ValueError("only the fast processing path can produce a V0 trace")
        if not (
            self.cognitive_turn_id
            == request.cognitive_turn_id
            == self.appraisal.cognitive_turn_id
            == snapshot.cognitive_turn_id
        ):
            raise ValueError("cognitive-turn identity differs across stages")
        if self.conversation_ref != request.conversation_ref:
            raise ValueError("trace conversation reference differs from its input snapshot")
        if self.input_message_ids != request.input_message_ids:
            raise ValueError("trace input-message references differ from its input snapshot")
        if self.focus_message_id != request.focus_message_id:
            raise ValueError("trace focus-message reference differs from its input snapshot")
        if snapshot.conversation_ref != self.conversation_ref:
            raise ValueError("dynamic-state snapshot belongs to a different conversation")
        if self.processing_path != request.processing_path:
            raise ValueError("trace processing path differs from its input snapshot")
        if self.appraisal.interpretation_trace_id != request.interpretation.interpretation_trace_id:
            raise ValueError("appraisal interpretation identity differs from the request")
        if snapshot.revision != request.previous_state.revision + 1:
            raise ValueError("dynamic-state revision does not follow the previous state")
        if snapshot.supersedes_snapshot_id != request.previous_state.state_snapshot_id:
            raise ValueError("dynamic-state predecessor does not match the previous state")
        if snapshot.personality_mapper_version != request.personality.mapper_version:
            raise ValueError("dynamic state cites the wrong personality mapper")

        active_goal_ids = {goal.goal_id for goal in request.active_goals if goal.status == "active"}
        capability_id = request.runtime.capability_snapshot.snapshot_id
        retained_scenarios = {
            scenario.scenario_id: scenario
            for scenario in request.interpretation.scenarios
            if scenario.disposition in {
                ScenarioDisposition.LEADING,
                ScenarioDisposition.RETAINED_ALTERNATIVE,
            }
        }
        value_ids = {value.value_id for value in request.values}
        relationship_id = (
            request.relationship.relationship_snapshot_id if request.relationship else None
        )
        frame_ids = tuple(frame.frame_id for frame in self.appraisal.frames)
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("appraisal frame ids must be unique")
        for frame in self.appraisal.frames:
            scenario = retained_scenarios.get(frame.scenario_id)
            if scenario is None:
                raise ValueError("appraisal frame references an unknown or non-retained scenario")
            if set(frame.subject.event_refs) != set(scenario.semantic_frame_refs):
                raise ValueError("appraisal frame events do not match its interpretation scenario")
            if frame.subject.focal_goal_ref not in active_goal_ids:
                raise ValueError("appraisal frame references a non-active or unknown goal")
            if frame.subject.capability_snapshot_ref != capability_id:
                raise ValueError("appraisal frame references the wrong capability snapshot")
            if not set(frame.subject.value_refs) <= value_ids:
                raise ValueError("appraisal frame references an unknown value")
            if frame.subject.relationship_snapshot_ref != relationship_id:
                raise ValueError("appraisal frame references the wrong relationship snapshot")

        appraisal_frame_ids = set(frame_ids)
        if not set(self.appraisal.aggregate.dominant_frame_ids) <= appraisal_frame_ids:
            raise ValueError("appraisal aggregate cites an unknown dominant frame")
        dimension_keys = {
            dimension.dimension_key
            for frame in self.appraisal.frames
            for dimension in frame.dimensions
        }
        if not set(self.appraisal.aggregate.dimension_ranges) <= dimension_keys:
            raise ValueError("appraisal aggregate cites an unknown dimension")
        contribution_refs = {
            ref for item in self.dynamic_state_update.contributions
            for ref in item.source_appraisal_frame_refs
        }
        snapshot_refs = set(snapshot.source_appraisal_revision_ids)
        if not contribution_refs <= appraisal_frame_ids or not snapshot_refs <= appraisal_frame_ids:
            raise ValueError("dynamic state cites an unknown appraisal frame")
        outcome_ids = {outcome.outcome_id for outcome in request.outcomes}
        cited_outcomes = {
            ref
            for item in self.dynamic_state_update.contributions
            for ref in item.source_outcome_refs
        } | set(snapshot.source_outcome_ids)
        if not cited_outcomes <= outcome_ids:
            raise ValueError("dynamic state cites an unknown outcome")
        cited_dimensions = {
            ref
            for item in self.dynamic_state_update.contributions
            for ref in item.source_dimension_keys
        }
        if not cited_dimensions <= dimension_keys:
            raise ValueError("dynamic state cites an unknown appraisal dimension")
        contribution_branch_refs = {
            ref
            for item in self.dynamic_state_update.contributions
            for ref in item.branch_refs
        }
        if not contribution_branch_refs <= (appraisal_frame_ids | set(retained_scenarios)):
            raise ValueError("dynamic-state contribution cites an unknown branch")
        snapshot_branch_refs = {
            ref
            for value in snapshot.values
            for ref in value.branch_refs
        }
        inherited_branch_refs = {
            ref
            for value in request.previous_state.values
            for ref in value.branch_refs
        }
        if not snapshot_branch_refs <= (
            appraisal_frame_ids | set(retained_scenarios) | inherited_branch_refs
        ):
            raise ValueError("dynamic-state snapshot cites an unknown branch")

        emitted = self.appraisal.versions
        expected_versions = {
            "interpretation_schema": request.interpretation.versions.schema_id,
            "appraisal_schema": emitted.get("schema"),
            "appraisal_rules": emitted.get("rules"),
            "dynamic_state_policy": snapshot.policy_version,
            "personality_mapper": request.personality.mapper_version,
            "memory_retrieval_policy": request.memory_retrieval_policy_version,
        }
        if self.versions.turn_schema != TURN_SCHEMA_VERSION:
            raise ValueError("trace version mismatch for turn_schema")
        if self.versions.interpretation_versions != request.interpretation.versions:
            raise ValueError("trace interpretation versions differ from the envelope")
        for field, expected in expected_versions.items():
            if expected is None or getattr(self.versions, field) != expected:
                raise ValueError(f"trace version mismatch for {field}")
        return self
