"""Typed Requirement Resolution V0 contracts: closed vocabularies, ordering, digests, lineage.

Resolutions carry only references and codes.  Every reference must belong to
the exact turn, conversation, goal snapshot, and runtime it describes; raw
text, foreign goals, and other turns' objects are rejected.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineTrace
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionGroundedObject, ActionPlanningCondition, ActionTargetType, PlanningConditionKind,
)
from app.ai.Cognitive.system.v0.contracts.requirement_resolution import (
    ALLOWED_OUTCOMES, ContentProducerStatus, GoalReadiness, RequirementActionBinding, RequirementActionHandoff,
    RequirementKind, RequirementReason, RequirementResolution, RequirementResolutionInputs,
    RequirementResolutionSnapshot, RequirementResolutionStatus, validate_requirement_snapshot,
)
from app.ai.Cognitive.system.v0.producers_v0 import ProducerStatus
from requirement_resolution_support import AMBIGUOUS, IDENTITY, REMINDER, run_turn

K, S, R = RequirementKind, RequirementResolutionStatus, RequirementReason


def _resolution(**overrides) -> RequirementResolution:
    values = dict(
        resolution_id="res-1", cognitive_turn_id="turn-1", conversation_ref="conv-1", goal_ref="goal-1",
        frame_ref="frame-1", kind=K.REQUIRED_ARGUMENT, key="argument.subject_ref", argument_name="subject_ref",
        status=S.MISSING_USER_INPUT, blocking=True, tier=1, reason_code=R.ARGUMENT_UNRESOLVED,
        subject_refs=("frame-1", "frame-1#subject_ref"), resolver_id="resolver", resolver_version="v1",
    )
    values.update(overrides)
    return RequirementResolution(**values)


def _snapshot(*resolutions: RequirementResolution, **overrides) -> RequirementResolutionSnapshot:
    goals = tuple(sorted({item.goal_ref for item in resolutions})) or ("goal-1",)
    readiness = []
    for goal in goals:
        own = [item for item in resolutions if item.goal_ref == goal and item.blocking]
        readiness.append(GoalReadiness(goal_ref=goal, readiness="blocked", blocking_tier=min(item.tier for item in own),
                                       blocking_resolution_refs=tuple(sorted(item.resolution_id for item in own)))
                         if own else GoalReadiness(goal_ref=goal, readiness="ready"))
    values = dict(
        snapshot_id="snap-1", engine_version="engine", manifest_version="manifest", producer_manifest_version="pm",
        cognitive_turn_id="turn-1", conversation_ref="conv-1", interpretation_trace_ref="interp-1",
        goal_snapshot_ref="goals-1", runtime_snapshot_ref="runtime-1", goal_refs=goals,
        resolutions=tuple(resolutions), goal_readiness=tuple(readiness),
    )
    values.update(overrides)
    return RequirementResolutionSnapshot.sealed(**values)


# ---------------------------------------------------------------------------
# Closed vocabularies and single records
# ---------------------------------------------------------------------------

def test_every_kind_declares_its_closed_outcomes_and_producer_status_parity() -> None:
    assert set(ALLOWED_OUTCOMES) == set(RequirementKind)
    assert {item.value for item in ContentProducerStatus} == {item.value for item in ProducerStatus}
    for kind, outcomes in ALLOWED_OUTCOMES.items():
        assert outcomes and all(isinstance(status, S) and isinstance(reason, R) for status, reason in outcomes)
    # Missing feasibility is never user input, and user input is never prohibited.
    assert (S.MISSING_USER_INPUT, R.CAPABILITY_UNREGISTERED) not in ALLOWED_OUTCOMES[K.CAPABILITY]
    assert not any(status == S.PROHIBITED for status, _ in ALLOWED_OUTCOMES[K.REQUIRED_ARGUMENT])
    with pytest.raises(ValidationError):
        _resolution(status="maybe")


@pytest.mark.parametrize("overrides, message", [
    ({"status": S.PROHIBITED}, "not allowed"),
    ({"reason_code": R.CAPABILITY_UNREGISTERED}, "not allowed"),
    ({"status": S.SATISFIED, "reason_code": R.ARGUMENT_PRESENT, "blocking": True}, "never blocks"),
    ({"argument_name": None}, "name their argument"),
    ({"key": "argument.other"}, "keyed by its argument"),
    ({"key": "Not A Key"}, "dotted key"),
    ({"subject_refs": ("z", "a")}, "canonical order"),
    ({"subject_refs": ("a", "a")}, "canonical order"),
    ({"kind": K.SAFETY, "key": "safety.request", "argument_name": None, "status": S.PROHIBITED,
      "reason_code": R.SAFETY_BLOCKS_COMMITMENTS, "tier": 0}, "cites the trusted safety decision"),
    ({"kind": K.CAPABILITY, "key": "capability.requested_effect", "argument_name": None, "status": S.UNAVAILABLE,
      "reason_code": R.CAPABILITY_UNREGISTERED, "tier": 2}, "cites the capability"),
])
def test_a_resolution_rejects_incoherent_records(overrides: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _resolution(**overrides)


def test_inputs_require_a_zoned_turn_time_and_a_real_zone() -> None:
    assert RequirementResolutionInputs(turn_time="2026-09-30T12:00:00+00:00", time_zone="America/Vancouver")
    with pytest.raises(ValidationError, match="timezone"):
        RequirementResolutionInputs(turn_time="2026-09-30T12:00:00")
    with pytest.raises(Exception):
        RequirementResolutionInputs(time_zone="Mars/Olympus_Mons")


# ---------------------------------------------------------------------------
# Snapshots, readiness, and digests
# ---------------------------------------------------------------------------

def test_snapshot_ordering_uniqueness_readiness_and_digest() -> None:
    missing = _resolution()
    capability = _resolution(
        resolution_id="res-2", kind=K.CAPABILITY, key="capability.requested_effect", argument_name=None,
        status=S.UNAVAILABLE, reason_code=R.CAPABILITY_UNREGISTERED, tier=2, blocking=False,
        subject_refs=("frame-1",), capability_refs=("capability-unregistered:action_request",),
    )
    snapshot = _snapshot(missing, capability)
    assert snapshot.goal_readiness[0].readiness == "blocked" and snapshot.goal_readiness[0].blocking_tier == 1
    assert RequirementResolutionSnapshot.model_validate(snapshot.model_dump(mode="json")) == snapshot
    with pytest.raises(ValidationError, match="canonical order"):
        _snapshot(capability, missing)
    with pytest.raises(ValidationError, match="unique"):
        _snapshot(missing, missing.model_copy(update={"key": "argument.subject_ref"}))
    tampered = snapshot.model_dump(mode="json")
    tampered["resolutions"][1]["tier"] = 1
    with pytest.raises(ValidationError):
        RequirementResolutionSnapshot.model_validate(tampered)  # digest and readiness no longer hold
    wrong_blocking = snapshot.model_dump(mode="json")
    wrong_blocking["resolutions"][1]["blocking"] = True
    with pytest.raises(ValidationError):
        RequirementResolutionSnapshot.model_validate(wrong_blocking)
    with pytest.raises(ValidationError, match="different turn"):
        _snapshot(missing.model_copy(update={"cognitive_turn_id": "turn-2"}))


def test_bindings_are_one_per_resolution_and_only_blockers_name_targets() -> None:
    snapshot = _snapshot(_resolution())
    binding = RequirementActionBinding(resolution_ref="res-1", goal_ref="goal-1", blocking=True,
                                       target_refs=("frame-1#subject_ref",))
    assert RequirementActionHandoff(snapshot=snapshot, bindings=(binding,))
    with pytest.raises(ValidationError, match="exactly one action binding"):
        RequirementActionHandoff(snapshot=snapshot, bindings=())
    with pytest.raises(ValidationError, match="misstates"):
        RequirementActionHandoff(snapshot=snapshot, bindings=(
            RequirementActionBinding(resolution_ref="res-1", goal_ref="goal-1", blocking=False),
        ))
    with pytest.raises(ValidationError, match="only a blocking"):
        RequirementActionBinding(resolution_ref="r", goal_ref="g", blocking=False, target_refs=("x",))


# ---------------------------------------------------------------------------
# Lineage and reference integrity against a real turn
# ---------------------------------------------------------------------------

def _validate(trace: CognitivePipelineTrace, snapshot) -> None:
    runtime = trace.cognitive_turn.input_snapshot.action_runtime
    validate_requirement_snapshot(
        snapshot, interpretation=trace.interpretation, goal_snapshot=trace.goal_snapshot,
        runtime_refs=(runtime.runtime_snapshot_ref, runtime.capability_snapshot_ref, runtime.safety_basis.decision_ref,
                      runtime.safety_decision.producer_ref),
    )


def _reseal(snapshot, **updates):
    fields = {name: getattr(snapshot, name) for name in type(snapshot).model_fields if name != "digest"}
    fields.update(updates)
    return RequirementResolutionSnapshot.sealed(**fields)


def test_real_snapshots_validate_and_reject_foreign_or_raw_references() -> None:
    trace = run_turn("Can you check it for me?", AMBIGUOUS)
    snapshot = trace.requirement_resolution.handoff.snapshot
    _validate(trace, snapshot)
    [argument] = [item for item in snapshot.resolutions if item.kind == K.DIALOGUE_REFERENT]
    # Raw user text can never stand in for a reference.
    raw = argument.model_copy(update={"value_refs": ("the thing I mentioned",)})
    with pytest.raises(ValueError, match="permitted lineage"):
        _validate(trace, _reseal(snapshot, resolutions=tuple(raw if item is argument else item
                                                             for item in snapshot.resolutions)))
    # Another turn's snapshot is rejected.
    other = run_turn("Can you check it for me?", AMBIGUOUS)
    with pytest.raises(ValueError, match="different turn"):
        _validate(trace, other.requirement_resolution.handoff.snapshot)
    # A goal that is not an active current-turn request goal is rejected.
    obligation = next(item.goal_id for item in trace.goal_snapshot.records
                      if item.definition_ref.definition_key == "interaction.discharge_response_obligation")
    moved = argument.model_copy(update={"goal_ref": obligation})
    with pytest.raises(ValueError):
        _validate(trace, _reseal(snapshot, goal_refs=(obligation,), resolutions=(moved,),
                                 goal_readiness=(GoalReadiness(goal_ref=obligation, readiness="blocked", blocking_tier=1,
                                                               blocking_resolution_refs=(moved.resolution_id,)),)))


def test_the_recorded_projection_cannot_be_edited_independently_of_its_resolutions() -> None:
    trace = run_turn("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER)
    assert CognitivePipelineTrace.model_validate(trace.model_dump(mode="json")) == trace

    def request_objects(value):
        recorded = value["requirement_resolution"]["grounded_objects"]
        handed = value["cognitive_turn"]["input_snapshot"]["action_grounded_objects"]
        return [item for item in (*recorded, *handed) if item["object_type"] == "request"]

    def blocking_bindings(value):
        return [item for handoff in (value["requirement_resolution"]["handoff"],
                                     value["cognitive_turn"]["input_snapshot"]["requirement_handoff"])
                for item in handoff["bindings"] if item["blocking"]]

    for mutate in (
        # A coordinated forgery (recorded and handed alike) of an executable request.
        lambda value: [item.update(acceptance_status="acceptable", decline_reason_refs=[])
                       for item in request_objects(value)],
        # Dropping the grounded decline reason everywhere.
        lambda value: [item.update(decline_reason_refs=[]) for item in request_objects(value)],
        # Erasing the blocker a move must address, everywhere.
        lambda value: [item.update(target_refs=[]) for item in blocking_bindings(value)],
        # Editing only the recorded copy.
        lambda value: value["requirement_resolution"]["grounded_objects"].pop(),
    ):
        broken = trace.model_dump(mode="json")
        mutate(broken)
        with pytest.raises(ValidationError):
            CognitivePipelineTrace.model_validate(broken)


def test_known_content_provenance_is_value_free_and_must_match_its_producer_record() -> None:
    trace = run_turn("What is your name?", IDENTITY)
    resolution = trace.requirement_resolution
    [provenance] = resolution.handoff.snapshot.content_queries
    assert provenance.producer_status == ContentProducerStatus.KNOWN and provenance.fact_refs
    # The neutral snapshot never carries produced values; only the producer record does.
    assert "Columbina" not in resolution.handoff.snapshot.model_dump_json()
    assert "Columbina" in resolution.content_records[0].model_dump_json()
    broken = trace.model_dump(mode="json")
    broken["requirement_resolution"]["content_records"][0]["result"]["facts"][0]["value"] = "Someone Else"
    with pytest.raises(ValidationError):
        CognitivePipelineTrace.model_validate(broken)


# ---------------------------------------------------------------------------
# Additive Action contract fields
# ---------------------------------------------------------------------------

def test_capability_status_is_typed_additive_and_only_on_capability_objects() -> None:
    plain = ActionGroundedObject(object_id="o", object_type=ActionTargetType.PROPOSITION, cognitive_turn_id="t",
                                 source_refs=("s",))
    assert "capability_status" not in plain.model_dump(mode="json")
    capability = ActionGroundedObject(object_id="c", object_type=ActionTargetType.CAPABILITY, cognitive_turn_id="t",
                                      source_refs=("s",), capability_status="unregistered")
    assert capability.model_dump(mode="json")["capability_status"] == "unregistered"
    with pytest.raises(ValidationError, match="only a capability object"):
        ActionGroundedObject(object_id="o", object_type=ActionTargetType.REQUEST, cognitive_turn_id="t",
                             source_refs=("s",), capability_status="unavailable")
    with pytest.raises(ValidationError):
        ActionGroundedObject(object_id="c", object_type=ActionTargetType.CAPABILITY, cognitive_turn_id="t",
                             source_refs=("s",), capability_status="maybe")


def test_a_planning_condition_has_exactly_one_typed_source() -> None:
    common = dict(condition_id="c", kind=PlanningConditionKind.MISSING_CAPABILITY, blocked_goal_ref="g",
                  blocked_goal_definition="interaction.handle_action_request")
    rejection = ActionPlanningCondition(**common, rejection_rule_id="goal-reject.hard-gate-denial.v1")
    assert "resolution_refs" not in rejection.model_dump(mode="json")
    resolution = ActionPlanningCondition(**common, resolution_refs=("res-1",))
    assert resolution.from_resolution and "rejection_rule_id" not in resolution.model_dump(mode="json")
    with pytest.raises(ValidationError, match="exactly one"):
        ActionPlanningCondition(**common)
    with pytest.raises(ValidationError, match="exactly one"):
        ActionPlanningCondition(**common, rejection_rule_id="r", resolution_refs=("res-1",))
