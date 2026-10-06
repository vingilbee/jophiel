"""Test-only adapter for the conversational-action design fixture.

Production code never imports this module.  Engine inputs come exclusively
from ``canonical_inputs`` in ``data/conversational_action_design_cases_v2.json``;
the schema-v1 fixture is read only to cross-check that the canonical
translations of its acceptable plans preserve its action keys, move order, and
priorities.  Expected plans, rationales, titles, messages, relationship
placeholders, and review notes never reach the engine.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..appraisal.models import (
    AppraisalAggregate, AppraisalDimension, AppraisalFrame, AppraisalResult, AppraisalSubject, DimensionStatus,
)
from ..contracts.actions import (
    ActionDefinitionRef, ActionGroundedObject, ActionPersonalityProfile, ActionRuntimeContext,
    ActionSelectionRequest, ActionSelectionResult, ActionTargetDescriptor, ActionTargetRef,
    ObservedOutcomeRecord, PriorActionRecord, PriorRealizationRecord,
)
from ..contracts.safety import (
    APPLICATION_SAFETY_POLICY_VERSION, ApplicationSafetyDecision, SafetyDecisionProvenance, SafetyDisposition,
)
from ..contracts.goals import (
    GoalAdoptionBasis, GoalDefinitionRef, GoalFocusDecision, GoalFocusRoute, GoalOwner, GoalPriorityBand,
    GoalRecord, GoalRequirement, GoalRequirementAssessment, GoalRequirementStatus, GoalScope, GoalSnapshot,
    GoalStatus, GoalTargetRef, GoalTransition, GoalTransitionKind, project_active_goal,
)
from ..contracts.interpretation import (
    HYPOTHESIS_DIMENSION_BY_KEY, Contradiction, HypothesisDisposition, HypothesisSupport,
    InterpretationEnvelope, InterpretationEvidence, InterpretationEvidenceKind, InterpretationHypothesis,
    InterpretationScenario, InterpretationVersions, SemanticFrame, UnknownInput,
)
from ..dynamic_state.initialization import DynamicStateSnapshotFactory
from ..dynamic_state.models import (
    AttentionItem, DerivedControls, PersonalityProfile, QualitativeBand, ScopeType, StateKey, StateSnapshot,
    StateValue,
)
from ..goal_v0.ontology import definition_by_key
from .engine import ActionSelectionEngine
from .errors import ActionSelectionError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FIXTURE_PATH = DATA_DIR / "conversational_action_design_cases.json"
CANONICAL_PATH = DATA_DIR / "conversational_action_design_cases_v2.json"
RESULTS_PATH = DATA_DIR / "conversational_action_design_results_v0.json"

CANONICAL_SCHEMA_VERSION = 2
PERSONALITY_MAPPER = "action-personality-fixture-v2"
CORE_MEMORY_REF = "core-memory:fixture-v2"
STATE_PERSONALITY = PersonalityProfile(profile_id="fixture-v2-state", mapper_version="personality-runtime-fixture-v2")

# The v1 fixture fields the adapter consumes, and the authoring shorthand it
# deliberately leaves unconsumed.  Every field must appear in exactly one set.
V1_CONSUMED_FOR_COMPARISON = frozenset({"case_id", "acceptable_plans"})
V1_UNCONSUMED_SHORTHAND = frozenset({
    "title", "purpose", "source", "messages", "focus_message_id", "interpretation_input", "appraisal_input",
    "dynamic_state_input", "goal_context", "relationship_context", "core_action_affinities",
    "hard_runtime_context", "disfavored_plans", "hard_invalid_plans", "realization_requirements",
    "outcome_dimensions_to_observe", "learning_expectation", "expansion_expectation", "ontology_questions", "review",
})
V2_CASE_FIELDS = frozenset({"case_id", "canonical_inputs", "canonical_expectations", "notes"})
V2_INPUT_FIELDS = frozenset({
    "messages", "interpretation", "goals", "appraisal", "dynamic_state", "runtime", "personality",
    "grounded_objects", "target_descriptors", "prior_action_records", "prior_realization_records",
    "observed_outcomes",
})

_PROPOSAL = {
    "interaction.discharge_response_obligation": "goal-propose.required-response-obligation.v1",
    "interaction.answer_information_request": "goal-propose.explicit-information-request.v1",
    "interaction.acknowledge_relevant_information": "goal-propose.relevant-information-acknowledgement.v1",
    "interaction.repair_shared_understanding": "goal-propose.explicit-repair-request.v1",
    "interaction.respond_to_social_contact": "goal-propose.explicit-social-contact.v1",
    "epistemic.resolve_required_unknown": "goal-propose.blocking-required-unknown.v1",
    "epistemic.resolve_material_contradiction": "goal-propose.material-contradiction.v1",
    "task.complete_authorized_request": "goal-propose.explicit-authorized-action-request.v1",
    "task.prevent_or_stop_requested_action": "goal-propose.explicit-prevent-or-stop-request.v1",
    "interaction.explain_unavailable_or_failed_operation": "goal-propose.known-operation-failure.v1",
}


def _adoption_rule(key: str, owner: str) -> str:
    if owner == "system":
        return "goal-adopt.required-system-obligation.v1"
    if key.startswith("epistemic."):
        return "goal-adopt.required-subgoal.v1"
    if key in {"interaction.repair_shared_understanding", "interaction.respond_to_social_contact"}:
        return "goal-adopt.explicit-shared-commitment.v1"
    return "goal-adopt.explicit-grounded-user-request.v1"


def load_documents(fixture_path: Path = FIXTURE_PATH, canonical_path: Path = CANONICAL_PATH) -> tuple[dict, dict]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    canonical = json.loads(Path(canonical_path).read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1 or fixture.get("action_ontology_version") != "conversational-actions-v0.1-shadow":
        raise ValueError("unsupported conversational-action design fixture")
    if canonical.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        raise ValueError("unsupported canonical schema-v2 input document")
    fixture_ids = [case["case_id"] for case in fixture["cases"]]
    canonical_ids = [case["case_id"] for case in canonical["cases"]]
    if fixture_ids != canonical_ids or len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("canonical inputs must cover exactly the fixture cases in order")
    for case in fixture["cases"]:
        unknown = set(case) - V1_CONSUMED_FOR_COMPARISON - V1_UNCONSUMED_SHORTHAND
        if unknown:
            raise ValueError(f"unclassified schema-v1 fixture fields: {sorted(unknown)}")
    for case in canonical["cases"]:
        if set(case) - V2_CASE_FIELDS or not {"case_id", "canonical_inputs", "canonical_expectations"} <= set(case):
            raise ValueError("schema-v2 case has unconsumed or missing fields")
        if set(case["canonical_inputs"]) - V2_INPUT_FIELDS:
            raise ValueError("schema-v2 inputs contain unconsumed fields")
    return fixture, canonical


# ---------------------------------------------------------------------------
# Canonical upstream construction (mechanical; all semantics are explicit inputs)
# ---------------------------------------------------------------------------

def _ids(case_id: str) -> dict[str, str]:
    return {
        "turn": f"turn:{case_id}", "conversation": f"conversation:{case_id}", "runtime": f"runtime:{case_id}",
        "interpretation": f"interpretation:{case_id}", "perception": f"perception:{case_id}",
        "capabilities": f"capabilities:{case_id}", "obligation": f"response-obligation:{case_id}",
        "goal_snapshot": f"goal-snapshot:{case_id}", "appraisal": f"appraisal:{case_id}",
    }


def _evidence_id(message_id: str) -> str:
    return f"evidence:{message_id}"


def build_interpretation(case_id: str, inputs: Mapping[str, Any]) -> InterpretationEnvelope:
    ids = _ids(case_id)
    messages = [item["message_id"] for item in inputs["messages"]]
    focus = messages[-1]
    raw = inputs["interpretation"]
    evidence = tuple(InterpretationEvidence(
        evidence_id=_evidence_id(message_id), source_kind=InterpretationEvidenceKind.MESSAGE_SEQUENCE,
        source_ref=message_id, message_ids=(message_id,),
    ) for message_id in messages) + tuple(InterpretationEvidence(
        evidence_id=item["evidence_id"], source_kind=InterpretationEvidenceKind.MEMORY_RECORD,
        source_ref=item["memory_ref"], memory_ref=item["memory_ref"],
    ) for item in raw.get("memory_evidence", ()))
    frames = tuple(SemanticFrame(
        frame_id=item["frame_id"], predicate=item["predicate"], arguments=item.get("arguments", {}),
        polarity=item.get("polarity", "unknown"),
        evidence_refs=tuple(_evidence_id(ref) for ref in item.get("evidence_messages", [focus])),
        unresolved_arguments=tuple(item.get("unresolved_arguments", ())), grounding_rule_ids=("fixture-v2.grounding.v1",),
    ) for item in raw.get("frames", ()))
    frame_ids = tuple(item.frame_id for item in frames)
    hypotheses = tuple(InterpretationHypothesis(
        hypothesis_id=item["hypothesis_id"], hypothesis_key=item["hypothesis_key"], definition_version=1,
        dimension=HYPOTHESIS_DIMENSION_BY_KEY[item["hypothesis_key"]], base_support=HypothesisSupport(item["support"]),
        support=HypothesisSupport(item["support"]), disposition=HypothesisDisposition(item["disposition"]),
        semantic_frame_refs=tuple(item.get("semantic_frame_refs", frame_ids)),
        supporting_evidence_refs=(_evidence_id(focus),), unknown_refs=tuple(item.get("unknown_refs", ())),
        contradiction_refs=tuple(item.get("contradiction_refs", ())), rule_ids=("fixture-v2.hypothesis.v1",),
    ) for item in raw.get("hypotheses", ()))
    unknowns = tuple(UnknownInput(
        unknown_id=item["unknown_id"], key=item["key"], owner=item["owner"], evidence_refs=(_evidence_id(focus),),
    ) for item in raw.get("unknowns", ()))
    contradictions = tuple(Contradiction(
        contradiction_id=item["contradiction_id"], status=item["status"], description=item["description"],
        evidence_refs=tuple(_evidence_id(ref) for ref in item.get("evidence_messages", [focus])),
        resolution_ref=item.get("resolution_ref"),
    ) for item in raw.get("contradictions", ()))
    leading = [item for item in hypotheses if item.disposition == HypothesisDisposition.LEADING]
    alternatives = [item for item in hypotheses if item.disposition == HypothesisDisposition.RETAINED_ALTERNATIVE]

    def group(items: list[InterpretationHypothesis]) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for item in items:
            grouped.setdefault(item.dimension.value, []).append(item.hypothesis_id)
        return {key: tuple(value) for key, value in grouped.items()}

    scenario_unknowns = tuple(raw.get("scenario_unknown_refs", [item.unknown_id for item in unknowns]))
    scenario_contradictions = tuple(item.contradiction_id for item in contradictions if item.status == "active")
    scenarios = [InterpretationScenario(
        scenario_id=f"scenario:{case_id}:leading", support=HypothesisSupport.STRONG, disposition=HypothesisDisposition.LEADING,
        semantic_frame_refs=frame_ids, hypothesis_refs=group(leading), supporting_evidence_refs=(_evidence_id(focus),),
        unknown_refs=scenario_unknowns, contradiction_refs=scenario_contradictions, compatibility_rule_ids=("fixture-v2.scenario.v1",),
    )]
    if alternatives:
        scenarios.append(InterpretationScenario(
            scenario_id=f"scenario:{case_id}:alternative", support=HypothesisSupport.WEAK,
            disposition=HypothesisDisposition.RETAINED_ALTERNATIVE, semantic_frame_refs=frame_ids,
            hypothesis_refs=group(alternatives), supporting_evidence_refs=(_evidence_id(focus),),
            compatibility_rule_ids=("fixture-v2.scenario.v1",),
        ))
    version = "fixture-v2"
    return InterpretationEnvelope(
        interpretation_trace_id=ids["interpretation"], cognitive_turn_id=ids["turn"], conversation_ref=ids["conversation"],
        perception_event_id=ids["perception"], input_message_ids=tuple(messages), focus_message_id=focus,
        runtime_snapshot_id=ids["runtime"], evidence=evidence, semantic_frames=frames, hypotheses=hypotheses,
        scenarios=tuple(scenarios), unknowns=unknowns, contradictions=contradictions,
        versions=InterpretationVersions(
            schema=version, ontology=version, feature_manifest=version, semantic_grounder=version,
            hypothesis_rules=version, scenario_builder=version, bias_policy=version, evidence_validator=version,
            perception_schema=version, perception_cue_manifest=version, perception_detectors=version,
        ),
    )


def _resolve_goal_value(value: str, ids: Mapping[str, str]) -> str:
    return {"$turn": ids["turn"], "$conversation": ids["conversation"]}.get(value, value)


def build_goal_snapshot(case_id: str, inputs: Mapping[str, Any], previous_state: StateSnapshot) -> tuple[GoalSnapshot, tuple]:
    ids = _ids(case_id)
    snapshot_id = ids["goal_snapshot"]
    records, transitions, assessments = [], [], []
    focus: dict[str, list[str]] = {"primary": [], "supporting": [], "non_focal": []}
    for item in inputs["goals"]:
        definition = definition_by_key(item["definition_key"])
        template = definition.requirement_templates[0]
        goal_id = item["goal_id"]
        owner = item["owner"]
        requirement = GoalRequirement(
            requirement_id=f"{goal_id}:requirement", requirement_key=template.requirement_key,
            target_refs=tuple(GoalTargetRef(role=role, ref=_resolve_goal_value(value, ids)) for role, value in item["targets"].items()),
            parameters=item.get("parameters", {}), satisfaction_policy_key=template.satisfaction_policy_key,
        )
        adoption = _adoption_rule(definition.definition_key, owner)
        propose = GoalTransition(
            transition_id=f"{goal_id}:propose", goal_id=goal_id, sequence=len(transitions) + 1, from_status=None,
            to_status=GoalStatus.CANDIDATE, transition_kind=GoalTransitionKind.PROPOSE, rule_id=_PROPOSAL[definition.definition_key],
            cognitive_turn_id=ids["turn"], resulting_snapshot_ref=snapshot_id, source_refs=(ids["interpretation"],),
            reason_code="rule_conditions_met",
        )
        adopt = GoalTransition(
            transition_id=f"{goal_id}:adopt", goal_id=goal_id, sequence=len(transitions) + 2, from_status=GoalStatus.CANDIDATE,
            to_status=GoalStatus.ACTIVE, transition_kind=GoalTransitionKind.ADOPT, rule_id=adoption,
            cognitive_turn_id=ids["turn"], resulting_snapshot_ref=snapshot_id, source_refs=(ids["interpretation"],),
            reason_code="rule_conditions_met",
        )
        transitions.extend((propose, adopt))
        scope = item["scope"]
        scope_ref = {"turn": ids["turn"], "conversation": ids["conversation"]}.get(scope) if scope != "task" else item["scope_ref"]
        records.append(GoalRecord(
            goal_id=goal_id, definition_ref=GoalDefinitionRef(definition_key=definition.definition_key, definition_version=definition.definition_version),
            owner=GoalOwner(owner), domain=definition.domain, mode=definition.mode, scope=GoalScope(scope), scope_ref=scope_ref,
            priority_band=GoalPriorityBand(item.get("priority_band", definition.default_priority_band.value)), status=GoalStatus.ACTIVE,
            requirements=(requirement,), adoption_basis=GoalAdoptionBasis(rule_id=adoption), created_turn_ref=ids["turn"],
            last_transition_ref=adopt.transition_id, source_refs=(ids["interpretation"],),
        ))
        assessments.append(GoalRequirementAssessment(
            assessment_id=f"{goal_id}:assessment", goal_ref=goal_id, requirement_ref=requirement.requirement_id,
            status=GoalRequirementStatus.OPEN, policy_key=requirement.satisfaction_policy_key,
        ))
        focus[item["focus"]].append(goal_id)
    if len(focus["primary"]) != 1 or len(focus["supporting"]) > 2:
        raise ValueError("canonical goals declare exactly one primary and at most two supporting goals")
    decision = GoalFocusDecision(
        focus_decision_id=f"focus:{case_id}", primary_goal_ref=focus["primary"][0],
        supporting_goal_refs=tuple(focus["supporting"]),
        non_focal_active_goal_refs=tuple(item.goal_id for item in records if item.goal_id in focus["non_focal"]),
        rule_ids=("goal-priority.definition-default.v1",), route=GoalFocusRoute.CONTINUE_FAST,
    )
    snapshot = GoalSnapshot(
        goal_snapshot_id=snapshot_id, revision=1, cognitive_turn_id=ids["turn"], conversation_ref=ids["conversation"],
        runtime_snapshot_ref=ids["runtime"], interpretation_trace_ref=ids["interpretation"],
        previous_dynamic_state_ref=previous_state.state_snapshot_id, records=tuple(records),
        requirement_assessments=tuple(assessments), transitions=tuple(transitions), focus_decision=decision,
        active_goal_refs=tuple(item.goal_id for item in records), focal_goal_refs=(decision.primary_goal_ref, *decision.supporting_goal_refs),
    )
    by_id = {item.goal_id: item for item in snapshot.records}
    views = tuple(project_active_goal(by_id[ref]) for ref in snapshot.focal_goal_refs)
    return snapshot, views


_DIMENSION_TYPES = {
    "appraisal.goal_relevance": "unsigned_ordinal", "appraisal.goal_congruence": "signed_ordinal",
    "appraisal.novelty": "unsigned_ordinal", "appraisal.certainty": "unsigned_ordinal",
    "appraisal.controllability": "unsigned_ordinal", "appraisal.norm_compatibility": "signed_ordinal",
    "appraisal.affiliative_value": "signed_ordinal", "appraisal.agency": "agency_category",
}


def build_appraisal(case_id: str, inputs: Mapping[str, Any], interpretation: InterpretationEnvelope, views: tuple) -> AppraisalResult:
    ids = _ids(case_id)
    supplied = inputs.get("appraisal", {})
    if set(supplied) - set(_DIMENSION_TYPES):
        raise ValueError("canonical appraisal names an unknown dimension")
    frames = []
    for scenario in interpretation.scenarios:
        for goal in views:
            dimensions = tuple(
                AppraisalDimension(dimension_key=key, status=DimensionStatus.KNOWN, value_type=value_type, value=supplied[key],
                                   derivation_strength="moderate", rule_id="fixture-v2.appraisal.v1",
                                   evidence_refs=(_evidence_id(interpretation.focus_message_id),))
                if key in supplied else
                AppraisalDimension(dimension_key=key, status=DimensionStatus.UNKNOWN, value_type=value_type,
                                   missing_input_keys=("canonical_input_not_supplied",))
                for key, value_type in _DIMENSION_TYPES.items()
            )
            frames.append(AppraisalFrame(
                frame_id=f"appraisal-frame:{case_id}:{scenario.scenario_id}:{goal.goal_id}", revision=1,
                scenario_id=scenario.scenario_id, scenario_support=scenario.support,
                subject=AppraisalSubject(event_refs=scenario.semantic_frame_refs, focal_goal_ref=goal.goal_id,
                                         goal_owner=goal.owner.value, goal_priority_band=goal.priority_band.value,
                                         value_refs=(), capability_snapshot_ref=ids["capabilities"]),
                dimensions=dimensions, completeness="complete" if len(supplied) == len(_DIMENSION_TYPES) else "partial",
            ))
    return AppraisalResult(
        appraisal_trace_id=ids["appraisal"], cognitive_turn_id=ids["turn"], interpretation_trace_id=ids["interpretation"],
        frames=tuple(frames), aggregate=AppraisalAggregate(dominant_frame_ids=(frames[0].frame_id,) if frames else ()),
        versions={"schema": "appraisal-schema-v1", "rules": "fixture-v2"},
    )


def previous_state_for(case_id: str) -> StateSnapshot:
    ids = _ids(case_id)
    return DynamicStateSnapshotFactory().create_initial(
        conversation_ref=ids["conversation"], cognitive_turn_id=f"{ids['turn']}:before", personality=STATE_PERSONALITY,
    )


def build_current_state(case_id: str, inputs: Mapping[str, Any], previous: StateSnapshot, appraisal: AppraisalResult) -> StateSnapshot:
    ids = _ids(case_id)
    raw = inputs.get("dynamic_state") or {}
    controls = raw.get("derived_controls")
    derived = None
    if controls is not None:
        names = DerivedControls.model_fields
        values = {name: controls if isinstance(controls, str) else controls[name] for name in names}
        derived = DerivedControls(**{name: QualitativeBand(value) for name, value in values.items()})
    extra = tuple(StateValue(
        state_key=StateKey(item["state_key"]), scope_type=ScopeType(item["scope_type"]), scope_ref=item["scope_ref"],
        score=item["score"], band=QualitativeBand(item["band"]), baseline_score=item.get("baseline_score", item["score"]),
        evidence_refs=(appraisal.frames[0].frame_id,) if appraisal.frames else (),
    ) for item in raw.get("values", ()))
    attention = tuple(AttentionItem(
        target_type=ScopeType(item["target_type"]), target_ref=item["target_ref"], weight=item["weight"],
        weight_band=item.get("weight_band", "moderate"),
    ) for item in raw.get("attention", ()))
    return StateSnapshot.model_validate(previous.model_copy(update={
        "state_snapshot_id": f"{ids['turn']}:state:r2", "cognitive_turn_id": ids["turn"], "revision": 2,
        "supersedes_snapshot_id": previous.state_snapshot_id,
        "source_appraisal_revision_ids": tuple(frame.frame_id for frame in appraisal.frames),
        "values": (*previous.values, *extra), "attention": attention, "derived_controls": derived,
    }).model_dump(mode="python"))


SAFETY_DECISION_FIELDS = frozenset({"disposition", "policy_version", "provenance"})


def build_safety_decision(case_id: str, raw: Mapping[str, Any] | None) -> ApplicationSafetyDecision | None:
    """Typed decision from the fixture's compact form; absent stays absent (fail closed)."""

    if raw is None:
        return None
    if not isinstance(raw, Mapping) or not set(raw) <= SAFETY_DECISION_FIELDS or "disposition" not in raw:
        raise ValueError("fixture safety decisions are typed records")
    ids = _ids(case_id)
    disposition = SafetyDisposition(raw["disposition"])
    default_provenance = (SafetyDecisionProvenance.RUNTIME_UNAVAILABLE if disposition == SafetyDisposition.UNAVAILABLE
                          else SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY)
    return ApplicationSafetyDecision(
        decision_id=f"{ids['runtime']}:safety-decision", policy_version=raw.get("policy_version", APPLICATION_SAFETY_POLICY_VERSION),
        disposition=disposition, provenance=SafetyDecisionProvenance(raw.get("provenance", default_provenance)),
        producer_ref=f"{ids['runtime']}:safety-evaluation", runtime_snapshot_ref=ids["runtime"],
    )


def build_runtime(case_id: str, inputs: Mapping[str, Any]) -> ActionRuntimeContext:
    ids = _ids(case_id)
    raw = inputs.get("runtime", {})
    return ActionRuntimeContext(
        runtime_snapshot_ref=ids["runtime"], response_obligation=raw.get("response_obligation", "required"),
        response_obligation_ref=ids["obligation"] if raw.get("response_obligation", "required") == "required" else None,
        conversation_status=raw.get("conversation_status", "open"), capability_snapshot_ref=ids["capabilities"],
        available_capabilities=tuple(raw.get("available_capabilities", ())), authorization=raw.get("authorization"),
        consent=raw.get("consent"), safety_decision=build_safety_decision(case_id, raw.get("safety_decision")),
        external_export_control=raw.get("external_export_control"), policy_records=tuple(raw.get("policy_records", ())),
        task_ref=raw.get("task_ref"), required_task_parameters=tuple(raw.get("required_task_parameters", ())),
        provided_task_parameters=raw.get("provided_task_parameters", {}), deadline_band=raw.get("deadline_band"),
    )


def build_personality(case_id: str, inputs: Mapping[str, Any]) -> ActionPersonalityProfile:
    raw = inputs["personality"]
    return ActionPersonalityProfile(
        profile_id=f"action-personality:{case_id}", mapper_version=PERSONALITY_MAPPER,
        source_core_memory_ref=CORE_MEMORY_REF, **raw,
    )


def build_request(case: Mapping[str, Any]) -> ActionSelectionRequest:
    case_id = case["case_id"]
    inputs = case["canonical_inputs"]
    ids = _ids(case_id)
    interpretation = build_interpretation(case_id, inputs)
    previous = previous_state_for(case_id)
    goals, views = build_goal_snapshot(case_id, inputs, previous)
    appraisal = build_appraisal(case_id, inputs, interpretation, views)
    state = build_current_state(case_id, inputs, previous, appraisal)
    before = f"{ids['turn']}:before"
    return ActionSelectionRequest(
        cognitive_turn_id=ids["turn"], conversation_ref=ids["conversation"], core_memory_ref=CORE_MEMORY_REF,
        interpretation=interpretation, goal_snapshot=goals, active_goals=views, appraisal=appraisal, dynamic_state=state,
        runtime=build_runtime(case_id, inputs), personality=build_personality(case_id, inputs),
        grounded_objects=tuple(ActionGroundedObject(cognitive_turn_id=ids["turn"], **item) for item in inputs.get("grounded_objects", ())),
        target_descriptors=tuple(ActionTargetDescriptor(**item) for item in inputs.get("target_descriptors", ())),
        prior_action_records=tuple(PriorActionRecord(
            cognitive_turn_ref=before, definition_ref=ActionDefinitionRef(action_key=item["action_key"], definition_version=1),
            target_refs=tuple(ActionTargetRef(**target) for target in item["target_refs"]),
            **{key: value for key, value in item.items() if key not in {"action_key", "target_refs"}},
        ) for item in inputs.get("prior_action_records", ())),
        prior_realization_records=tuple(PriorRealizationRecord(cognitive_turn_ref=before, **item) for item in inputs.get("prior_realization_records", ())),
        observed_outcomes=tuple(ObservedOutcomeRecord(**item) for item in inputs.get("observed_outcomes", ())),
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def plan_signature(result: ActionSelectionResult) -> list[dict[str, Any]]:
    primary = result.plan.primary_move_ref
    return [{
        "action_key": move.definition_ref.action_key,
        "priority": "primary" if move.move_id == primary else "supporting",
        "targets": sorted([item.role, item.target_type.value, item.ref] for item in move.target_refs),
        "parameters": json.loads(json.dumps(dict(move.parameters), sort_keys=True)),
    } for move in result.plan.moves]


def _expected_signature(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{
        "action_key": move["action_key"], "priority": move["priority"],
        "targets": sorted([item["role"], item["target_type"], item["ref"]] for item in move["targets"]),
        "parameters": json.loads(json.dumps(move.get("parameters", {}), sort_keys=True)),
    } for move in plan["moves"]]


def check_translation(fixture_case: Mapping[str, Any], canonical_case: Mapping[str, Any]) -> tuple[str, ...]:
    """Canonical translations must keep each fixture plan's id, keys, order, and priorities."""

    errors = []
    translated = {item["plan_id"]: item for item in canonical_case["canonical_expectations"]["acceptable_plans"]}
    source = {item["plan_id"]: item for item in fixture_case["acceptable_plans"]}
    if set(translated) != set(source):
        errors.append("canonical acceptable plans differ from the fixture plan ids")
        return tuple(errors)
    for plan_id, plan in source.items():
        expected = [(move["action_key"], move["priority"]) for move in plan["moves"]]
        actual = [(move["action_key"], move["priority"]) for move in translated[plan_id]["moves"]]
        if expected != actual or plan.get("termination") != "STOP":
            errors.append(f"{plan_id}: canonical translation changes keys, order, or priorities")
        if [move["move_id"] for move in plan["moves"]] != [move["move_id"] for move in translated[plan_id]["moves"]]:
            errors.append(f"{plan_id}: canonical translation changes move ids")
    return tuple(errors)


def matching_plan(result: ActionSelectionResult, canonical_case: Mapping[str, Any]) -> str | None:
    actual = plan_signature(result)
    for plan in canonical_case["canonical_expectations"]["acceptable_plans"]:
        if _expected_signature(plan) == actual:
            return plan["plan_id"]
    return None


def normalized_result(result: ActionSelectionResult) -> dict[str, Any]:
    """Complete decision trace with generated identities replaced by stable labels."""

    labels: dict[str, str] = {}

    def label(value: str, kind: str) -> str:
        if value not in labels:
            labels[value] = f"{kind}#{sum(1 for item in labels.values() if item.startswith(kind + '#')) + 1}"
        return labels[value]

    for step in result.steps:
        for candidate in step.constructed_candidates:
            label(candidate.candidate_id, "candidate")
        for attempt in step.uninstantiable:
            label(attempt.attempt_id, "attempt")
    for move in result.moves:
        label(move.move_id, "move")
    for effect in result.planned_effects:
        label(effect.effect_id, "effect")
    for step in result.steps:
        label(step.step_id, "step")
        for state in (step.partial_state_before, step.partial_state_after):
            label(state.state_signature, "state")
    label(result.context_ref, "context"); label(result.plan.plan_id, "plan"); label(result.result_id, "result")
    label(result.request_digest, "digest")

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            if value in labels:
                return labels[value]
            if value.startswith("{"):
                return value
        return value

    return replace(result.model_dump(mode="json"))


def result_digest(result: ActionSelectionResult) -> str:
    """Digest of the complete normalized decision trace (every field, every step)."""

    import hashlib
    payload = json.dumps(normalized_result(result), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def semantic_projection(result: ActionSelectionResult) -> dict[str, Any]:
    """Compact reviewable projection of the decision: identities by semantics, not generated ids."""

    def identity(candidate) -> str:
        targets = ",".join(f"{item.role}:{item.target_type.value}:{item.ref}" for item in candidate.target_refs)
        parameters = json.dumps(json.loads(json.dumps(dict(candidate.parameters))), sort_keys=True, separators=(",", ":"))
        return f"{candidate.definition_ref.action_key}({targets}){parameters}"

    steps = []
    for step in result.steps:
        by_id = {item.candidate_id: identity(item) for item in step.constructed_candidates}
        gates = {item.candidate_ref: item for item in step.gate_results}
        steps.append({
            "uninstantiable": sorted(f"{item.definition_ref.action_key}:{'|'.join(item.missing_codes)}" for item in step.uninstantiable),
            "blocked": sorted(
                f"{by_id[ref]}:{'|'.join(v.code for v in gate.violations)}" for ref, gate in gates.items() if gate.status.value == "blocked"
            ),
            "scored": [
                {"candidate": by_id[item.candidate_ref], "score": item.score.score,
                 "features": {feature.feature_key.value: [feature.value, feature.status.value] for feature in item.features},
                 "constraints": [c.code for c in gates[item.candidate_ref].realization_constraints]}
                for item in sorted(step.scored_candidates, key=lambda item: by_id[item.candidate_ref])
            ],
            "selected": by_id.get(step.selected_candidate_ref) if step.selected_candidate_ref else None,
            "tie": None if step.tie_resolution is None else {
                "tied": sorted(by_id[ref] for ref in step.tie_resolution.tied_candidate_refs),
                "resolved_by": step.tie_resolution.resolved_by.value,
                "preference_evidence": step.tie_resolution.preference_evidence,
            },
            "effects": [f"{effect.effect_key}->{effect.planned_state}@{effect.ledger_key}" for effect in step.applied_effects],
            "goal_coverage": {} if step.partial_state_delta is None else {
                item.key: item.value for item in step.partial_state_delta.goal_coverage_updates
            },
            "termination": step.termination.value if step.termination else None,
        })
    primary = result.plan.primary_move_ref
    return {
        "plan": [f"{'primary' if move.move_id == primary else 'supporting'}:{identity(move)}" for move in result.plan.moves],
        "termination": result.plan.termination.value,
        "versions": result.versions.model_dump(mode="json"),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_case(case: Mapping[str, Any], *, engine: ActionSelectionEngine | None = None) -> ActionSelectionResult:
    return (engine or ActionSelectionEngine()).select(build_request(case))


def _fault_engine(phase: str) -> ActionSelectionEngine:
    from .construction import CandidateConstructor
    from .features import FeatureCalculator
    from .gates import HardGate
    from .registry import ActionRegistry, DEFINITIONS

    class Broken(RuntimeError):
        pass

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise Broken("injected fault")

    if phase == "registry_resolution":
        return ActionSelectionEngine(registry=ActionRegistry(tuple(item for item in DEFINITIONS if item.definition_ref.action_key != "seek_information.clarify")))
    if phase == "candidate_construction":
        class BrokenConstructor(CandidateConstructor):
            construct = boom
        return ActionSelectionEngine(constructor_factory=BrokenConstructor)
    if phase == "hard_gate":
        class BrokenGate(HardGate):
            evaluate = boom
        return ActionSelectionEngine(gate_factory=BrokenGate)
    if phase == "feature_extraction":
        class BrokenFeatures(FeatureCalculator):
            compute = boom
        return ActionSelectionEngine(feature_factory=BrokenFeatures)
    if phase == "plan_transition":
        return ActionSelectionEngine(max_steps=1)
    raise KeyError("unknown fault phase")


def run_failure_variant(canonical: Mapping[str, Any], variant: Mapping[str, Any]) -> ActionSelectionError | None:
    case = copy.deepcopy(next(item for item in canonical["cases"] if item["case_id"] == variant["baseline_case"]))
    for mutation in variant.get("input_mutations", ()):
        target = case["canonical_inputs"]
        path = mutation["path"]
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = mutation["value"]
    engine = _fault_engine(variant["engine_fault"]) if variant.get("engine_fault") else ActionSelectionEngine()
    try:
        request = build_request(case)
    except Exception:
        if variant["failure_phase"] != "request_validation":
            raise
        try:
            engine.select(_invalid_request_probe(case))
        except ActionSelectionError as exc:
            return exc
        return None
    try:
        engine.select(request)
    except ActionSelectionError as exc:
        return exc
    return None


def _invalid_request_probe(case: Mapping[str, Any]) -> Any:
    """A request whose canonical construction failed is still sent through the engine boundary."""

    class Unvalidated:
        cognitive_turn_id = _ids(case["case_id"])["turn"]

        def model_dump(self, **_kwargs: Any) -> dict:
            raise ValueError("unconstructible canonical request")

    return Unvalidated()


def run_fixture(fixture_path: Path = FIXTURE_PATH, canonical_path: Path = CANONICAL_PATH) -> dict[str, Any]:
    fixture, canonical = load_documents(fixture_path, canonical_path)
    errors: list[str] = []
    outcomes = []
    steps = 0
    invariance = 0
    for fixture_case, canonical_case in zip(fixture["cases"], canonical["cases"]):
        case_id = fixture_case["case_id"]
        errors.extend(f"{case_id}: {item}" for item in check_translation(fixture_case, canonical_case))
        try:
            result = run_case(canonical_case)
        except ActionSelectionError as exc:
            errors.append(f"{case_id}: action selection failed in {exc.failed_phase.value}")
            continue
        steps += len(result.steps)
        # Expected-output independence: no expectation or prose may reach the engine.
        stripped = copy.deepcopy(canonical_case)
        stripped["canonical_expectations"] = {"acceptable_plans": []}
        stripped["notes"] = ["replaced diagnostic note"]
        invariance += 1
        if run_case(stripped) != result:
            errors.append(f"{case_id}: removing expectations or notes changed the engine output")
        # Diagnostic-prose invariance: descriptive text inside canonical inputs never drives policy.
        reworded = copy.deepcopy(canonical_case)
        for contradiction in reworded["canonical_inputs"]["interpretation"].get("contradictions", ()):
            contradiction["description"] = "reworded diagnostic description"
        invariance += 1
        if semantic_projection(run_case(reworded)) != semantic_projection(result):
            errors.append(f"{case_id}: diagnostic prose changed the policy output")
        matched = matching_plan(result, canonical_case)
        if matched is None:
            errors.append(f"{case_id}: plan is not a declared acceptable plan: {[item['action_key'] for item in plan_signature(result)]}")
        outcomes.append({"case_id": case_id, "matched_plan": matched, "steps": len(result.steps), "moves": len(result.moves)})
    failures = 0
    for variant in canonical.get("failure_variants", ()):
        failures += 1
        exc = run_failure_variant(canonical, variant)
        if exc is None:
            errors.append(f"{variant['variant_id']}: failure was not raised")
        elif exc.failed_phase.value != variant["failure_phase"] or exc.code != variant["code"]:
            errors.append(f"{variant['variant_id']}: failure phase or code differs ({exc.failed_phase.value}/{exc.code})")
        elif exc.__cause__ is None:
            errors.append(f"{variant['variant_id']}: original exception was not chained")
    return {"cases_run": len(fixture["cases"]), "planning_steps": steps, "failure_variants_run": failures,
            "invariance_checks_run": invariance, "outcomes": outcomes, "errors": tuple(errors), "passed": not errors}


def build_expected_results(canonical_path: Path = CANONICAL_PATH, fixture_path: Path = FIXTURE_PATH) -> dict[str, Any]:
    """Regenerate the reviewed expected-result document (used only after review)."""

    _fixture, canonical = load_documents(fixture_path, canonical_path)
    cases = []
    for case in canonical["cases"]:
        result = run_case(case)
        cases.append({"case_id": case["case_id"], "matched_plan": matching_plan(result, case),
                      "result_digest": result_digest(result), "projection": semantic_projection(result)})
    return {"schema_version": 1, "action_versions": ActionSelectionResult.model_fields["versions"].default_factory().model_dump(mode="json"),
            "status": "reviewed_shadow_regression_expectations_pending_behavioral_calibration", "cases": cases}


def compare_expected(result: ActionSelectionResult, expected: Mapping[str, Any]) -> tuple[str, ...]:
    errors = []
    projection = semantic_projection(result)
    if projection != expected["projection"]:
        for key in ("plan", "termination", "versions"):
            if projection[key] != expected["projection"][key]:
                errors.append(f"{key} differs")
        for index, (actual, wanted) in enumerate(zip(projection["steps"], expected["projection"]["steps"])):
            for key in actual:
                if actual[key] != wanted.get(key):
                    errors.append(f"step {index} {key} differs")
        if len(projection["steps"]) != len(expected["projection"]["steps"]):
            errors.append("planning step count differs")
    if result_digest(result) != expected["result_digest"]:
        errors.append("complete normalized decision trace differs")
    return tuple(errors) or ()


__all__ = ["CANONICAL_PATH", "FIXTURE_PATH", "RESULTS_PATH", "build_expected_results", "build_request", "check_translation",
           "compare_expected", "load_documents", "matching_plan", "normalized_result", "plan_signature", "result_digest",
           "run_case", "run_failure_variant", "run_fixture", "semantic_projection"]
