from __future__ import annotations
from typing import Any, Literal, Mapping
from pydantic import Field, model_validator
from ..contracts.interpretation import FrozenModel, InterpretationEnvelope
from ..contracts.goals import *
from ..dynamic_state.models import StateSnapshot

class GoalRuntimeContext(FrozenModel):
    cognitive_turn_id: str = Field(min_length=1)
    runtime_snapshot_ref: str = Field(min_length=1)
    response_obligation: Literal["required", "not_required"]
    response_obligation_ref: str | None = None
    conversation_status: Literal["open", "closed"] = "open"
    task_ref: str | None = None
    available_capabilities: tuple[str, ...] = ()
    authorization: str | None = None
    consent: str | None = None
    safety_decision: str | None = None
    deadline_band: str | None = None
    last_tool_event: Mapping[str, Any] | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.response_obligation == "required" and not self.response_obligation_ref: raise ValueError("required response obligation needs a reference")
        if self.response_obligation == "not_required" and self.response_obligation_ref is not None: raise ValueError("not-required response cannot have a reference")
        return self

class GoalUpdateRequest(FrozenModel):
    schema_version: int = 1
    trigger: GoalUpdateTrigger
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    interpretation: InterpretationEnvelope
    runtime: GoalRuntimeContext
    previous_goal_snapshot: GoalSnapshot | None = None
    previous_dynamic_state: StateSnapshot
    new_goal_evidence: tuple[GoalEvidence, ...] = ()
    # Trusted proof that the prior response episode ended.  Required exactly
    # when a response turn inherits non-terminal turn-scoped goals of another
    # turn; those goals are expired (never satisfied) before new goals form.
    turn_rollover: GoalTurnRollover | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.runtime.cognitive_turn_id != self.cognitive_turn_id or self.interpretation.cognitive_turn_id != self.cognitive_turn_id: raise ValueError("goal request turn ownership differs")
        if self.interpretation.conversation_ref != self.conversation_ref or self.previous_dynamic_state.conversation_ref != self.conversation_ref: raise ValueError("goal request conversation ownership differs")
        if self.previous_goal_snapshot is not None:
            previous = self.previous_goal_snapshot
            # Design-case update sequences may issue multiple immutable goal
            # revisions within one cognitive turn.  Revision and snapshot
            # lineage, rather than a synthetic turn suffix, establish order.
            if previous.runtime_snapshot_ref == "":
                raise ValueError("goal predecessor runtime reference is blank")
        if self.trigger == GoalUpdateTrigger.RESPONSE_TURN and self.runtime.response_obligation != "required": raise ValueError("response turns require a trusted obligation")
        ids = tuple(x.evidence_id for x in self.new_goal_evidence)
        if len(ids) != len(set(ids)): raise ValueError("goal evidence ids must be unique")
        if self.previous_goal_snapshot is not None:
            prior_evidence = {ref for item in self.previous_goal_snapshot.requirement_assessments for ref in item.evidence_refs}
            if set(ids) & prior_evidence:
                raise ValueError("goal evidence cannot be replayed across revisions")
        self._validate_turn_rollover()
        return self

    def _validate_turn_rollover(self) -> None:
        rollover = self.turn_rollover
        previous = self.previous_goal_snapshot
        if rollover is not None:
            if self.trigger != GoalUpdateTrigger.RESPONSE_TURN:
                raise ValueError("a turn rollover belongs only to a response turn")
            if previous is None:
                raise ValueError("a turn rollover requires the predecessor goal snapshot")
            if (
                rollover.conversation_ref != self.conversation_ref
                or rollover.closed_goal_snapshot_ref != previous.goal_snapshot_id
                or rollover.closed_turn_ref != previous.cognitive_turn_id
                or rollover.closing_turn_ref != self.cognitive_turn_id
                or rollover.closing_response_obligation_ref != self.runtime.response_obligation_ref
            ):
                raise ValueError("the turn rollover proof does not match this update's lineage")
        if (
            self.trigger == GoalUpdateTrigger.RESPONSE_TURN
            and previous is not None
            and stale_turn_scoped_goals(previous, self.cognitive_turn_id)
            and rollover is None
        ):
            # Fail closed: a new response episode may not silently inherit a
            # completed turn's goals as active.
            raise ValueError("stale turn-scoped goals require a trusted turn rollover")

class GoalUpdateResult(FrozenModel):
    proposals: tuple[GoalProposal, ...] = ()
    transitions: tuple[GoalTransition, ...] = ()
    requirement_assessments: tuple[GoalRequirementAssessment, ...] = ()
    evidence_conflicts: tuple[GoalEvidenceConflict, ...] = ()
    priority_changes: tuple[GoalPriorityChange, ...] = ()
    conflicts: tuple[GoalConflict, ...] = ()
    focus_decision: GoalFocusDecision
    snapshot: GoalSnapshot
    active_goal_views: tuple[ActiveGoalView, ...]

    @model_validator(mode="after")
    def valid(self):
        if tuple(x.goal_id for x in self.active_goal_views) != self.snapshot.focal_goal_refs: raise ValueError("active views do not match snapshot focus")
        if any(view.goal_id not in self.snapshot.active_goal_refs for view in self.active_goal_views): raise ValueError("active view is not active")
        current_transitions = tuple(
            transition for transition in self.snapshot.transitions
            if transition.resulting_snapshot_ref == self.snapshot.goal_snapshot_id
        )
        if self.transitions != current_transitions:
            raise ValueError("result transitions differ from current snapshot transitions")
        return self
