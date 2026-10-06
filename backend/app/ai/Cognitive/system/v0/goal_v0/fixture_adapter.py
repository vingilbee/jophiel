"""Test-only adapter boundary for the reviewed goal design fixture.

Production code never imports this module or reads the fixture JSON.
"""
from __future__ import annotations
import json
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any
from .ontology import all_definitions, definition
from ..contracts.goals import *
from ..contracts.interpretation import (
    InterpretationEnvelope, InterpretationEvidence, InterpretationEvidenceKind,
    InterpretationHypothesis, InterpretationScenario, HypothesisDimension,
    HypothesisDisposition, HypothesisSupport, SemanticFrame, UnknownInput,
    Contradiction, InterpretationVersions,
)
from .models import GoalRuntimeContext, GoalUpdateRequest, GoalUpdateResult
from .engine import GoalEngine
from .errors import GoalExecutionError, GoalFailurePhase
from .lifecycle import next_status
from .rules import (
    ADOPTION_RULE_IDS, CONFLICT_RULE_IDS, LIFECYCLE_RULE_IDS,
    PRIORITY_RULE_IDS, PROPOSAL_RULE_IDS, REJECTION_RULE_IDS,
)
from ..dynamic_state.initialization import DynamicStateSnapshotFactory
from ..dynamic_state.models import PersonalityProfile
from ..contracts.interpretation import FrozenModel
from ..contracts.goals import GOAL_ONTOLOGY_VERSION

def load_design_fixture(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("goal_ontology_version") != GOAL_ONTOLOGY_VERSION:
        raise ValueError("unsupported goal design fixture")
    if len(document.get("goal_definitions", ())) != len(all_definitions()):
        raise ValueError("fixture definition count differs from installed registry")
    return document

def fixture_definitions_equal(document: dict[str, Any]) -> bool:
    expected = tuple(document["goal_definitions"])
    actual = tuple(item.model_dump(mode="json") for item in all_definitions())
    return expected == actual

def build_interpretation(case: dict[str, Any], *, cognitive_turn_id: str | None = None) -> InterpretationEnvelope:
    raw = case["interpretation_input"]
    raw = json.loads(json.dumps(raw))
    evidence = []
    for item in raw.get("evidence", ()):
        data = dict(item)
        data.setdefault("source_kind", "message_span")
        data["source_kind"] = InterpretationEvidenceKind(data["source_kind"])
        evidence.append(InterpretationEvidence(**data))
    frames = tuple(SemanticFrame(**item) for item in raw.get("semantic_frames", ()))
    hypotheses = tuple(InterpretationHypothesis(
        hypothesis_id=item["hypothesis_id"], hypothesis_key=item["hypothesis_key"],
        definition_version=1, dimension=HypothesisDimension(item["dimension"]),
        base_support=HypothesisSupport(item["support"]), support=HypothesisSupport(item["support"]),
        disposition=HypothesisDisposition(item["disposition"]), semantic_frame_refs=tuple(item.get("semantic_frame_refs", ())),
        supporting_evidence_refs=tuple(item.get("supporting_evidence_refs", ())),
        contradicting_evidence_refs=tuple(item.get("contradicting_evidence_refs", ())),
        unknown_refs=tuple(item.get("unknown_refs", ())), contradiction_refs=tuple(item.get("contradiction_refs", ())),
        rule_ids=("fixture.adapter.v1",),
    ) for item in raw.get("hypotheses", ()))
    hypothesis_by_id = {item.hypothesis_id: item for item in hypotheses}
    scenarios = tuple(InterpretationScenario.model_validate({
        **item, "support": HypothesisSupport(item["support"]), "disposition": HypothesisDisposition(item["disposition"]),
        "semantic_frame_refs": tuple(item.get("semantic_frame_refs", ())),
        "hypothesis_refs": {dimension: tuple(ref for ref in refs if ref in hypothesis_by_id) for dimension, refs in item.get("hypothesis_refs", {}).items()}, "supporting_evidence_refs": tuple(item.get("supporting_evidence_refs", ())),
        "contradiction_refs": tuple(item.get("contradiction_refs", ())), "unknown_refs": tuple(item.get("unknown_refs", ())),
        "compatibility_rule_ids": tuple(item.get("compatibility_rule_ids", ())),
    }) for item in raw.get("scenarios", ()))
    if not scenarios:
        raise ValueError("fixture interpretation must declare a canonical scenario")
    unknowns = tuple(UnknownInput(**item) for item in raw.get("unknowns", ()))
    contradictions = tuple(Contradiction(**item) for item in raw.get("contradictions", ()))
    # The canonical envelope requires every scenario reference to resolve.  The
    # fixture is deliberately allowed to exercise unresolved alternatives, but
    # those alternatives must still be represented as typed records rather than
    # being silently discarded by the adapter.
    versions = InterpretationVersions(schema="fixture-adapter-v1", ontology="fixture-adapter-v1", feature_manifest="fixture-adapter-v1", semantic_grounder="fixture-adapter-v1", hypothesis_rules="fixture-adapter-v1", scenario_builder="fixture-adapter-v1", bias_policy="fixture-adapter-v1", evidence_validator="fixture-adapter-v1", perception_schema="fixture-adapter-v1", perception_cue_manifest="fixture-adapter-v1", perception_detectors="fixture-adapter-v1")
    return InterpretationEnvelope(
        schema_version=1, interpretation_trace_id=raw["interpretation_trace_ref"], cognitive_turn_id=cognitive_turn_id or case["turn_context"]["cognitive_turn_id"],
        conversation_ref=case.get("conversation", {}).get("conversation_ref", f"fixture:{case['case_id']}"), perception_event_id=f"fixture-perception:{case['case_id']}",
        input_message_ids=tuple(item.get("message_id", f"message:{case['case_id']}") for item in case.get("conversation", {}).get("messages", ())) or (f"message:{case['case_id']}",),
        focus_message_id=case.get("conversation", {}).get("focus_message_id") or f"message:{case['case_id']}", runtime_snapshot_id=case["turn_context"]["runtime_snapshot_ref"], evidence=tuple(evidence), semantic_frames=frames, hypotheses=hypotheses, scenarios=scenarios, unknowns=unknowns, contradictions=contradictions, out_of_scope_records=(), context_truncated=False, versions=versions,
    )

def _evidence(items: Any) -> tuple[GoalEvidence, ...]:
    return tuple(GoalEvidence(**item) for item in (items or ()))

def _fixture_identity(case: dict[str, Any], kind: str, parts: tuple[str, ...]) -> str:
    expected_updates = [case.get("expected_goal_update") or {}] + [step.get("expected_goal_update") or {} for step in case.get("update_steps", ())]
    proposals = [item for expected in expected_updates for item in expected.get("proposals", ())]
    records = [record for expected in expected_updates for record in (expected.get("snapshot") or {}).get("records", ())]
    records_by_goal = {record["goal_id"]: record for record in records}
    if kind in {"goal", "requirement"} and len(parts) > 1:
        matching = [item for item in proposals if item["definition_ref"]["definition_key"] == parts[1]]
        selected = None
        for item in matching:
            record = records_by_goal.get(item["goal_id"], {})
            requirements = record.get("requirements", ())
            parameter_values = [str(value) for requirement in requirements for value in requirement.get("parameters", {}).values()]
            if parameter_values and all(value in parts for value in parameter_values):
                selected = item
                break
        selected = selected or (matching[0] if matching else None)
        if selected and kind == "goal":
            return selected["goal_id"]
        if selected and kind == "requirement":
            record = records_by_goal.get(selected["goal_id"], {})
            if record.get("requirements"):
                return record["requirements"][0]["requirement_id"]
            views = [view for expected in expected_updates for view in expected.get("active_goal_views", ())]
            view = next((view for view in views if view.get("goal_id") == selected["goal_id"] and view.get("requirements")), None)
            if view:
                return view["requirements"][0]["requirement_id"]
    if kind == "transition" and parts:
        goal_id = parts[0]
        hint = parts[-1]
        for expected in expected_updates:
            for item in expected.get("transitions", ()):
                if item.get("goal_id") == goal_id and item.get("transition_kind") == hint:
                    return item["transition_id"]
    if kind == "proposal" and parts:
        proposal = next((item for item in proposals if item.get("goal_id") == parts[0]), None)
        if proposal:
            return proposal["proposal_id"]
    if kind == "assessment" and len(parts) >= 2:
        candidates = []
        for expected in expected_updates:
            for item in expected.get("requirement_assessments", ()):
                if item.get("goal_ref") == parts[0] and item.get("requirement_ref") == parts[1]:
                    candidates.append(item)
            for item in (expected.get("snapshot") or {}).get("requirement_assessments", ()):
                if item.get("goal_ref") == parts[0] and item.get("requirement_ref") == parts[1]:
                    candidates.append(item)
        revision = None
        if len(parts) > 2 and str(parts[2]).startswith("revision:"):
            revision = int(str(parts[2]).split(":", 1)[1])
        evidence_ref = parts[2] if len(parts) > 2 and revision is None else None
        if revision is not None:
            for expected in expected_updates:
                snapshot = expected.get("snapshot") or {}
                if snapshot.get("revision") != revision:
                    continue
                selected = next((item for item in expected.get("requirement_assessments", ()) if item.get("goal_ref") == parts[0] and item.get("requirement_ref") == parts[1]), None)
                if selected:
                    return selected["assessment_id"]
        selected = next((item for item in candidates if evidence_ref in item.get("evidence_refs", ())), None)
        selected = selected or next((item for item in candidates if not item.get("evidence_refs")), None)
        if selected:
            return selected["assessment_id"]
    if kind == "priority" and parts:
        for expected in expected_updates:
            item = next((item for item in expected.get("priority_changes", ()) if item.get("goal_ref") == parts[0]), None)
            if item:
                return item["priority_change_id"]
    if kind == "conflict" and len(parts) >= 2:
        for expected in expected_updates:
            item = next((item for item in expected.get("conflicts", ()) if {item.get("left_goal_ref"), item.get("right_goal_ref")} == {parts[0], parts[1]}), None)
            if item:
                return item["conflict_id"]
    if kind == "goal-snapshot":
        revision = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 1
        for expected in expected_updates:
            snapshot = expected.get("snapshot") or {}
            if snapshot.get("revision") == revision:
                return snapshot["goal_snapshot_id"]
    if kind == "evidence-conflict":
        for expected in expected_updates:
            for item in tuple(expected.get("evidence_conflicts", ())) + tuple((expected.get("snapshot") or {}).get("evidence_conflicts", ())):
                if item.get("left_evidence_ref") in parts or item.get("right_evidence_ref") in parts:
                    return item["conflict_id"]
    if kind == "focus":
        requested_revision = int(parts[-1]) if parts and str(parts[-1]).isdigit() else None
        for expected in expected_updates:
            focus = expected.get("focus_decision") or {}
            snapshot = expected.get("snapshot") or {}
            primary_ref = parts[-2] if requested_revision is not None and len(parts) > 1 else parts[-1]
            if focus.get("primary_goal_ref") == primary_ref and (
                requested_revision is None or snapshot.get("revision") == requested_revision
            ):
                return focus.get("focus_decision_id")
    return f"{kind}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]}"

def build_request(case: dict[str, Any], *, step: dict[str, Any] | None = None, previous_snapshot: GoalSnapshot | None = None, turn_rollover: GoalTurnRollover | None = None) -> GoalUpdateRequest:
    turn = dict(case["turn_context"])
    interpretation = build_interpretation(case, cognitive_turn_id=turn["cognitive_turn_id"])
    previous_state = DynamicStateSnapshotFactory().create_initial(conversation_ref=interpretation.conversation_ref, cognitive_turn_id=f"{turn['cognitive_turn_id']}:before", personality=PersonalityProfile(profile_id="fixture", mapper_version="fixture-v1"))
    state_ref = case.get("previous_dynamic_state", {}).get("snapshot_ref")
    if state_ref:
        previous_state = previous_state.model_copy(update={"state_snapshot_id": state_ref})
    runtime = GoalRuntimeContext(cognitive_turn_id=turn["cognitive_turn_id"], runtime_snapshot_ref=turn["runtime_snapshot_ref"], response_obligation=turn["response_obligation"], response_obligation_ref=turn.get("response_obligation_ref"), conversation_status=turn.get("conversation_status", "open"), task_ref=turn.get("task_ref"), available_capabilities=tuple(turn.get("available_capabilities", ())), authorization=turn.get("authorization"), consent=turn.get("consent"), safety_decision=turn.get("safety_decision"), deadline_band=turn.get("deadline_band"), last_tool_event=turn.get("last_tool_event"))
    trigger = GoalUpdateTrigger((step or {}).get("trigger", "response_turn"))
    if previous_snapshot is None and case.get("previous_goal_snapshot"):
        previous_snapshot = GoalSnapshot.model_validate(case["previous_goal_snapshot"])
    raw_evidence = list((step or {}).get("new_goal_evidence", case.get("new_goal_evidence", ())))
    for item in raw_evidence:
        # Evidence semantics are fixture inputs.  The adapter performs no
        # inference from diagnostic text, identifiers, or expected outputs.
        item["provenance"] = dict(item.get("provenance", {}))
    return GoalUpdateRequest(trigger=trigger, cognitive_turn_id=turn["cognitive_turn_id"], conversation_ref=interpretation.conversation_ref, interpretation=interpretation, runtime=runtime, previous_goal_snapshot=previous_snapshot, previous_dynamic_state=previous_state, new_goal_evidence=_evidence(raw_evidence), turn_rollover=turn_rollover)

def execute_case(case: dict[str, Any], *, engine: GoalEngine | None = None) -> GoalUpdateResult:
    return (engine or GoalEngine()).update(build_request(case))

def _compare_update(result: GoalUpdateResult, expected: dict[str, Any]) -> tuple[str, ...]:
    """Compare the fixture's semantic contract, retaining IDs only as joins.

    Generated IDs are implementation identities; definition, lifecycle, policy,
    evidence, conflict, focus, and projection fields are the conformance
    surface.  Expected goal IDs are therefore mapped to actual goals by their
    definition and owner before comparing references.
    """
    errors: list[str] = []
    exp_props = expected.get("proposals", ())
    act_props = result.proposals
    if [(x["definition_ref"]["definition_key"], x["rule_id"], tuple(x.get("source_refs", ()))) for x in exp_props] != [(x.definition_ref.definition_key, x.rule_id, x.source_refs) for x in act_props]:
        errors.append("proposals differ")
    expected_to_actual = {e["goal_id"]: a.goal_id for e, a in zip(exp_props, act_props)}
    def gid(value: str | None) -> str | None:
        return expected_to_actual.get(value, value)
    exp_trans = expected.get("transitions", ())
    act_trans = result.transitions
    exp_t = [(gid(x["goal_id"]), x["sequence"], x.get("from_status"), x["to_status"], x["transition_kind"], x["rule_id"], tuple(x.get("source_refs", ())), tuple(x.get("evidence_refs", ()))) for x in exp_trans]
    act_t = [(x.goal_id, x.sequence, x.from_status.value if x.from_status else None, x.to_status.value, x.transition_kind.value, x.rule_id, x.source_refs, x.evidence_refs) for x in act_trans]
    if exp_t != act_t: errors.append("transitions differ")
    exp_a = expected.get("requirement_assessments", ())
    actual_req_by_goal = {record.goal_id: record.requirements[0].requirement_id for record in result.snapshot.records if record.requirements}
    def assessment_key(x): return (gid(x["goal_ref"]), actual_req_by_goal.get(gid(x["goal_ref"])), x["status"], x["policy_key"], tuple(x.get("evidence_refs", ())), tuple(x.get("conflict_refs", ())))
    act_a = [(x.goal_ref, x.requirement_ref, x.status.value, x.policy_key, x.evidence_refs, x.conflict_refs) for x in result.requirement_assessments]
    if sorted(assessment_key(x) for x in exp_a) != sorted(act_a): errors.append("requirement assessments differ")
    exp_c = expected.get("conflicts", ())
    act_c = [(x.left_goal_ref, x.right_goal_ref, x.classification.value, x.rule_id) for x in result.conflicts]
    if [(gid(x["left_goal_ref"]), gid(x["right_goal_ref"]), x["classification"], x["rule_id"]) for x in exp_c] != act_c: errors.append("goal conflicts differ")
    exp_pc = expected.get("priority_changes", ())
    act_pc = [(x.goal_ref, x.old_priority_band.value, x.new_priority_band.value, x.rule_id) for x in result.priority_changes]
    if [(gid(x["goal_ref"]), x["old_priority_band"], x["new_priority_band"], x["rule_id"]) for x in exp_pc] != act_pc: errors.append("priority changes differ")
    exp_focus = expected.get("focus_decision")
    if exp_focus:
        actual_focus = result.focus_decision
        if (gid(exp_focus["primary_goal_ref"]), tuple(gid(x) for x in exp_focus.get("supporting_goal_refs", ())), tuple(gid(x) for x in exp_focus.get("non_focal_active_goal_refs", ())), exp_focus["route"]) != (actual_focus.primary_goal_ref, actual_focus.supporting_goal_refs, actual_focus.non_focal_active_goal_refs, actual_focus.route.value):
            errors.append("focus differs")
    exp_views = expected.get("active_goal_views", ())
    if len(exp_views) != len(result.active_goal_views): errors.append("active projections differ")
    else:
        actual_by_definition = {view.definition_ref.definition_key: view for view in result.active_goal_views}
        for expected_view in exp_views:
            actual_view = actual_by_definition.get(expected_view.get("definition_ref", {}).get("definition_key"))
            if actual_view is None:
                errors.append("active projection content differs")
                break
            expected_requirements = expected_view.get("requirements", ())
            actual_requirements = [item.model_dump(mode="json") for item in actual_view.requirements]
            if expected_view.get("definition_ref") != actual_view.definition_ref.model_dump(mode="json") or expected_view.get("owner") != actual_view.owner.value or expected_view.get("domain") != actual_view.domain.value or expected_view.get("mode") != actual_view.mode.value or expected_view.get("scope") != actual_view.scope.value or expected_view.get("scope_ref") != actual_view.scope_ref or expected_view.get("priority_band") != actual_view.priority_band.value or expected_view.get("source_refs", []) != list(actual_view.source_refs) or expected_requirements != actual_requirements:
                errors.append("active projection content differs")
                break
    expected_snapshot = expected.get("snapshot")
    if expected_snapshot:
        actual_snapshot = result.snapshot
        expected_versions = GoalVersions.model_validate(expected_snapshot.get("versions", {})).model_dump(mode="json")
        if expected_snapshot.get("revision") != actual_snapshot.revision or expected_snapshot.get("conversation_ref") != actual_snapshot.conversation_ref or expected_snapshot.get("interpretation_trace_ref") != actual_snapshot.interpretation_trace_ref or expected_snapshot.get("active_goal_refs") != list(actual_snapshot.active_goal_refs) or expected_snapshot.get("focal_goal_refs") != list(actual_snapshot.focal_goal_refs) or expected_versions != actual_snapshot.versions.model_dump(mode="json"):
            errors.append("snapshot metadata differs")
        if len(expected_snapshot.get("records", ())) != len(actual_snapshot.records) or len(expected_snapshot.get("transitions", ())) != len(actual_snapshot.transitions) or len(expected_snapshot.get("requirement_assessments", ())) != len(actual_snapshot.requirement_assessments) or len(expected_snapshot.get("evidence_conflicts", ())) != len(actual_snapshot.evidence_conflicts):
            errors.append("snapshot complete history differs")

    # The checks above produce concise diagnostics.  This second pass is the
    # completeness guard: every canonical semantic field is compared for every
    # emitted collection and for the complete immutable snapshot.  Generated
    # identities are injected by ``_fixture_identity``; rationales are
    # explanatory text rather than policy output, and the fixture contains two
    # historical spellings for equivalent transition reasons, so only those
    # two surfaces are normalized.
    def semantic(value: Any) -> Any:
        if isinstance(value, dict):
            normalized = {}
            for key, item in value.items():
                if key == "rationale":
                    continue
                if key == "reason_code" and isinstance(item, str):
                    transition_kind = value.get("transition_kind")
                    if transition_kind in {"propose", "adopt"} and item in {transition_kind, "rule_conditions_met"}:
                        item = "rule_conditions_met"
                    elif transition_kind == "reject" and item in {"reject", "candidate_not_adopted"}:
                        item = "candidate_not_adopted"
                normalized_item = semantic(item)
                if key == "requirement_assessments" and isinstance(normalized_item, list):
                    normalized_item = sorted(
                        normalized_item,
                        key=lambda assessment: (
                            assessment.get("goal_ref", ""),
                            assessment.get("requirement_ref", ""),
                        ),
                    )
                normalized[key] = normalized_item
            return normalized
        if isinstance(value, (list, tuple)):
            return [semantic(item) for item in value]
        return value

    collections = (
        ("proposals", GoalProposal),
        ("transitions", GoalTransition),
        ("requirement_assessments", GoalRequirementAssessment),
        ("evidence_conflicts", GoalEvidenceConflict),
        ("priority_changes", GoalPriorityChange),
        ("conflicts", GoalConflict),
    )
    for field, model in collections:
        expected_values = [model.model_validate(item).model_dump(mode="json") for item in expected.get(field, ())]
        actual_values = [item.model_dump(mode="json") for item in getattr(result, field)]
        if field == "requirement_assessments":
            expected_values.sort(key=lambda item: (item["goal_ref"], item["requirement_ref"]))
            actual_values.sort(key=lambda item: (item["goal_ref"], item["requirement_ref"]))
        if semantic(expected_values) != semantic(actual_values):
            errors.append(f"complete {field} differ")
    if exp_focus:
        expected_focus = GoalFocusDecision.model_validate(exp_focus).model_dump(mode="json")
        if semantic(expected_focus) != semantic(result.focus_decision.model_dump(mode="json")):
            errors.append("complete focus decision differs")
    expected_views = sorted(
        (ActiveGoalView.model_validate(item).model_dump(mode="json") for item in exp_views),
        key=lambda item: item["goal_id"],
    )
    actual_views = sorted(
        (item.model_dump(mode="json") for item in result.active_goal_views),
        key=lambda item: item["goal_id"],
    )
    if semantic(expected_views) != semantic(actual_views):
        errors.append("complete active projections differ")
    if expected_snapshot:
        canonical_expected_snapshot = GoalSnapshot.model_validate(expected_snapshot).model_dump(mode="json")
        if semantic(canonical_expected_snapshot) != semantic(result.snapshot.model_dump(mode="json")):
            errors.append("complete snapshot differs")
    return tuple(errors)


def _apply_json_pointer(document: Any, mutation: dict[str, Any]) -> None:
    """Apply the fixture's bounded add/replace mutation without extensions."""
    pointer = mutation["json_pointer"]
    if not pointer.startswith("/"):
        raise ValueError("failure mutation requires an absolute JSON Pointer")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    parent = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    operation = mutation["operation"]
    value = deepcopy(mutation.get("value"))
    if operation == "add" and isinstance(parent, list) and leaf == "-":
        parent.append(value)
    elif operation in {"add", "replace"}:
        if isinstance(parent, list):
            parent[int(leaf)] = value
        else:
            if operation == "replace" and leaf not in parent:
                raise ValueError("replace mutation targets a missing field")
            parent[leaf] = value
    else:
        raise ValueError("unsupported failure mutation operation")


def _validate_expected_result(
    expected: dict[str, Any],
    evidence_ids: set[str],
    previous_snapshot: dict[str, Any] | None = None,
) -> None:
    """Run corrupted output through the same ordered Goal phase boundaries."""
    turn_id = expected.get("snapshot", {}).get("cognitive_turn_id", "fixture-turn")

    def phase(phase_name: GoalFailurePhase, category: str, operation):
        try:
            return operation()
        except GoalExecutionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise GoalExecutionError(
                turn_id, phase_name, category,
                "Goal processing could not complete safely.",
            ) from exc

    raw_snapshot = expected["snapshot"]

    def validate_input_contract():
        expected_revision = 1 if previous_snapshot is None else previous_snapshot["revision"] + 1
        if raw_snapshot["revision"] != expected_revision:
            raise ValueError("result snapshot revision is not consecutive")
        for raw_record in raw_snapshot.get("records", ()):
            record = GoalRecord.model_validate(raw_record)
            registered = definition(record.definition_ref)
            if record.domain != registered.domain or record.mode != registered.mode:
                raise ValueError("goal record definition compatibility differs")
            if record.owner not in registered.allowed_owners or record.scope not in registered.allowed_scopes:
                raise ValueError("goal record owner or scope is incompatible")

    phase(GoalFailurePhase.VALIDATION, "invalid_goal_input", validate_input_contract)

    def validate_candidates():
        values = tuple(GoalProposal.model_validate(item) for item in expected.get("proposals", ()))
        for proposal in values:
            definition(proposal.definition_ref)
            if proposal.rule_id not in PROPOSAL_RULE_IDS:
                raise ValueError("proposal rule is not registered")
        return values

    proposals = phase(
        GoalFailurePhase.CANDIDATE_FORMATION,
        "unknown_goal_definition",
        validate_candidates,
    )
    transitions = phase(
        GoalFailurePhase.LIFECYCLE_UPDATE,
        "invalid_goal_transition",
        lambda: tuple(GoalTransition.model_validate(item) for item in expected.get("transitions", ())),
    )

    def validate_transition_edges():
        for transition in transitions:
            if next_status(transition.from_status, transition.transition_kind) != transition.to_status:
                raise ValueError("transition edge is invalid")

    phase(GoalFailurePhase.LIFECYCLE_UPDATE, "invalid_goal_transition", validate_transition_edges)

    def validate_adoption_rules():
        raw_goal_ids = [item["goal_id"] for item in raw_snapshot.get("records", ())]
        duplicate_goal_ids = len(raw_goal_ids) != len(set(raw_goal_ids))
        adoption_basis_by_goal = {
            item["goal_id"]: item.get("adoption_basis", {}).get("rule_id")
            for item in raw_snapshot.get("records", ())
        }
        for transition in transitions:
            if transition.transition_kind == GoalTransitionKind.ADOPT and transition.rule_id not in ADOPTION_RULE_IDS:
                raise ValueError("adoption transition uses the wrong rule")
            if not duplicate_goal_ids and transition.transition_kind == GoalTransitionKind.ADOPT and transition.rule_id != adoption_basis_by_goal.get(transition.goal_id):
                raise ValueError("adoption transition differs from the record adoption basis")
            if transition.transition_kind == GoalTransitionKind.REJECT and transition.rule_id not in REJECTION_RULE_IDS:
                raise ValueError("rejection transition uses the wrong rule")
            if transition.transition_kind == GoalTransitionKind.PROPOSE and transition.rule_id not in PROPOSAL_RULE_IDS:
                raise ValueError("proposal transition uses the wrong rule")
            if transition.transition_kind not in {GoalTransitionKind.ADOPT, GoalTransitionKind.REJECT, GoalTransitionKind.PROPOSE} and transition.rule_id not in LIFECYCLE_RULE_IDS:
                raise ValueError("lifecycle transition uses the wrong rule")

    phase(GoalFailurePhase.ADOPTION, "invalid_goal_adoption", validate_adoption_rules)

    def validate_evidence_output():
        values = tuple(GoalRequirementAssessment.model_validate(item) for item in expected.get("requirement_assessments", ()))
        for assessment in values:
            if not set(assessment.evidence_refs) <= evidence_ids:
                raise ValueError("assessment contains an unresolved evidence reference")
        return values

    assessments = phase(
        GoalFailurePhase.EVIDENCE_RECONCILIATION,
        "invalid_goal_evidence",
        validate_evidence_output,
    )
    evidence_conflicts = phase(
        GoalFailurePhase.EVIDENCE_RECONCILIATION,
        "invalid_goal_evidence",
        lambda: tuple(GoalEvidenceConflict.model_validate(item) for item in expected.get("evidence_conflicts", ())),
    )

    def validate_priorities():
        values = tuple(GoalPriorityChange.model_validate(item) for item in expected.get("priority_changes", ()))
        if any(item.rule_id not in PRIORITY_RULE_IDS for item in values):
            raise ValueError("priority output uses an unregistered rule")
        return values

    priority_changes = phase(
        GoalFailurePhase.PRIORITY_ASSIGNMENT,
        "invalid_goal_priority",
        validate_priorities,
    )

    def validate_conflicts():
        values = tuple(GoalConflict.model_validate(item) for item in expected.get("conflicts", ()))
        if any(item.rule_id not in CONFLICT_RULE_IDS for item in values):
            raise ValueError("conflict output uses an unregistered rule")
        return values

    conflicts = phase(
        GoalFailurePhase.CONFLICT_RESOLUTION,
        "invalid_goal_conflict",
        validate_conflicts,
    )
    focus = phase(
        GoalFailurePhase.FOCUS_SELECTION,
        "invalid_goal_focus",
        lambda: GoalFocusDecision.model_validate(expected["focus_decision"]),
    )

    def validate_focus_views():
        values = tuple(ActiveGoalView.model_validate(item) for item in expected.get("active_goal_views", ()))
        required_refs = (focus.primary_goal_ref, *focus.supporting_goal_refs)
        by_id = {item.goal_id: item for item in values}
        if len(by_id) != len(values) or set(by_id) != set(required_refs):
            raise ValueError("active views differ from the focus decision")
        return tuple(by_id[ref] for ref in required_refs)

    views = phase(
        GoalFailurePhase.FOCUS_SELECTION,
        "invalid_goal_focus",
        validate_focus_views,
    )
    def validate_snapshot():
        value = GoalSnapshot.model_validate(raw_snapshot)
        expected_predecessor = None if previous_snapshot is None else previous_snapshot["goal_snapshot_id"]
        if value.supersedes_goal_snapshot_id != expected_predecessor:
            raise ValueError("result snapshot predecessor differs from the request")
        return value

    snapshot = phase(
        GoalFailurePhase.SNAPSHOT_VALIDATION,
        "invalid_goal_snapshot",
        validate_snapshot,
    )
    phase(
        GoalFailurePhase.SNAPSHOT_VALIDATION,
        "invalid_goal_snapshot",
        lambda: GoalUpdateResult(
            proposals=proposals,
            transitions=transitions,
            requirement_assessments=assessments,
            evidence_conflicts=evidence_conflicts,
            priority_changes=priority_changes,
            conflicts=conflicts,
            focus_decision=focus,
            snapshot=snapshot,
            active_goal_views=views,
        ),
    )


def _execute_failure_variants(case: dict[str, Any]) -> tuple[int, tuple[str, ...]]:
    errors: list[str] = []
    baselines = case.get("failure_baseline") or {}
    variants = case.get("failure_variants") or ()
    for variant in variants:
        variant_id = variant["variant_id"]
        baseline_key = variant["baseline_key"]
        if baseline_key not in baselines:
            errors.append(f"{variant_id}: unknown failure baseline")
            continue
        mutated = deepcopy(baselines[baseline_key])
        mutation = variant["input_mutation"]
        detected_phase: str | None = None
        detected_category: str | None = None
        appraisal_calls = 0
        dynamic_state_calls = 0
        try:
            _apply_json_pointer(mutated, mutation)
            pointer = mutation["json_pointer"]
            if pointer.startswith(("/turn_context", "/previous_goal_snapshot", "/new_goal_evidence")):
                engine = GoalEngine(
                    identity_factory=lambda kind, parts, c=mutated: _fixture_identity(c, kind, parts)
                )
                declared_steps = mutated.get("update_steps") or ()
                try:
                    if declared_steps:
                        predecessor = GoalSnapshot.model_validate(mutated["previous_goal_snapshot"])
                        for step in declared_steps:
                            result = engine.update(build_request(mutated, step=step, previous_snapshot=predecessor))
                            predecessor = result.snapshot
                    else:
                        engine.update(build_request(mutated))
                except GoalExecutionError:
                    raise
                except ValueError as exc:
                    failed_phase = (
                        GoalFailurePhase.EVIDENCE_RECONCILIATION
                        if pointer.startswith("/new_goal_evidence")
                        else GoalFailurePhase.VALIDATION
                    )
                    category = (
                        "invalid_goal_evidence"
                        if failed_phase == GoalFailurePhase.EVIDENCE_RECONCILIATION
                        else "invalid_goal_input"
                    )
                    raise GoalExecutionError(
                        mutated.get("turn_context", {}).get("cognitive_turn_id", "fixture-turn"),
                        failed_phase,
                        category,
                        "Goal processing could not complete safely.",
                    ) from exc
                appraisal_calls += 1
                dynamic_state_calls += 1
                errors.append(f"{variant_id}: malformed input was accepted")
            else:
                expected = mutated.get("expected_goal_update")
                if expected is None and mutated.get("update_steps"):
                    expected = mutated["update_steps"][0]["expected_goal_update"]
                evidence_ids = {
                    item["evidence_id"] for item in mutated.get("new_goal_evidence", ())
                    if item.get("evidence_id")
                }
                for step in mutated.get("update_steps", ()):
                    evidence_ids.update(
                        item["evidence_id"] for item in step.get("new_goal_evidence", ())
                        if item.get("evidence_id")
                    )
                _validate_expected_result(
                    expected,
                    evidence_ids,
                    mutated.get("previous_goal_snapshot"),
                )
                appraisal_calls += 1
                dynamic_state_calls += 1
                errors.append(f"{variant_id}: malformed output was accepted")
        except GoalExecutionError as exc:
            # Rejection is the required fail-closed outcome.  No partial result
            # is retained or passed to downstream stages by this adapter.
            detected_phase = exc.failed_phase.value
            detected_category = exc.safe_message_category
            if exc.__cause__ is None:
                errors.append(f"{variant_id}: original exception was not chained")
            if appraisal_calls or dynamic_state_calls:
                errors.append(f"{variant_id}: downstream stage ran after goal failure")
        if detected_phase is not None:
            if detected_phase != variant["failure_phase"]:
                errors.append(f"{variant_id}: failure phase differs")
            if detected_category != variant["safe_message_category"]:
                errors.append(f"{variant_id}: safe category differs")
            if variant["pipeline_failed_stage"] != "goal_update":
                errors.append(f"{variant_id}: unexpected outer failure stage")
            required_flags = {
                "previous_snapshot_remains_authoritative": True,
                "partial_snapshot_returned": False,
                "downstream_appraisal_runs": False,
                "downstream_dynamic_state_runs": False,
                "caller_fallback_goals_allowed": False,
                "safe_output_contains_raw_user_content": False,
            }
            if any(variant.get(key) != value for key, value in required_flags.items()):
                errors.append(f"{variant_id}: fail-closed invariant metadata differs")
    return len(variants), tuple(errors)

def run_fixture(path: str | Path) -> dict[str, Any]:
    document = load_design_fixture(path)
    results = []
    errors = []
    update_steps_run = 0
    failure_variants_run = 0
    invariance_variants_run = 0
    allowed_case_fields = {
        "case_id", "title", "objective", "coverage_requirements", "conversation",
        "turn_context", "interpretation_input", "previous_goal_snapshot",
        "previous_dynamic_state", "new_goal_evidence", "expected_goal_update",
        "update_steps", "expected_pipeline_effect", "forbidden_inferences",
        "goal_questions", "review", "design_expectations", "excluded_history",
        "invariance_variants", "invariance_assertion", "failure_baseline",
        "failure_variants",
    }
    allowed_step_fields = {
        "step", "trigger", "new_input_refs", "new_goal_evidence",
        "non_satisfaction_observations", "previous_snapshot_ref",
        "expected_goal_update",
    }
    expected_output_fields = {
        "proposals", "transitions", "requirement_assessments",
        "evidence_conflicts", "priority_changes", "conflicts",
        "focus_decision", "snapshot", "active_goal_views",
    }
    required_output_fields = expected_output_fields - {"evidence_conflicts"}
    for case in document["cases"]:
        try:
            unknown_case_fields = set(case) - allowed_case_fields
            if unknown_case_fields:
                raise ValueError(f"unconsumed case fields: {sorted(unknown_case_fields)}")
            expected_identity_source = case
            engine = GoalEngine(identity_factory=lambda kind, parts, c=expected_identity_source: _fixture_identity(c, kind, parts))
            declared_steps = case.get("update_steps") or ()
            for step in declared_steps:
                unknown_step_fields = set(step) - allowed_step_fields
                if unknown_step_fields:
                    raise ValueError(f"unconsumed update-step fields: {sorted(unknown_step_fields)}")
            update_steps_run += len(declared_steps)
            steps = declared_steps or (({"trigger": "response_turn", "new_goal_evidence": case.get("new_goal_evidence", ()), "expected_goal_update": case.get("expected_goal_update")} ,) if case.get("expected_goal_update") is not None else ())
            predecessor = None
            for step in steps:
                expected = step.get("expected_goal_update", {})
                if not required_output_fields <= set(expected) <= expected_output_fields:
                    raise ValueError("expected goal update has unconsumed or missing fields")
                declared_predecessor = step.get("previous_snapshot_ref")
                if declared_predecessor is not None:
                    effective_predecessor_ref = (
                        predecessor.goal_snapshot_id if predecessor is not None
                        else (case.get("previous_goal_snapshot") or {}).get("goal_snapshot_id")
                    )
                    if declared_predecessor != effective_predecessor_ref:
                        raise ValueError("declared update predecessor differs from executed predecessor")
                declared_inputs = set(step.get("new_input_refs", ()))
                if declared_inputs:
                    evidence_ids = {
                        item["evidence_id"] for item in step.get("new_goal_evidence", ())
                    }
                    observation_ids = {
                        item["observation_ref"]
                        for item in step.get("non_satisfaction_observations", ())
                    }
                    response_inputs = set()
                    if step.get("trigger") == "response_turn":
                        response_inputs.add(case["interpretation_input"]["interpretation_trace_ref"])
                        if case["turn_context"].get("response_obligation_ref"):
                            response_inputs.add(case["turn_context"]["response_obligation_ref"])
                    if declared_inputs != evidence_ids | observation_ids | response_inputs:
                        raise ValueError("declared new input references were not fully consumed")
                if any(
                    item.get("admissible_goal_evidence") is not False
                    for item in step.get("non_satisfaction_observations", ())
                ):
                    raise ValueError("non-satisfaction observation was marked admissible")
                result = engine.update(build_request(case, step=step, previous_snapshot=predecessor))
                predecessor = result.snapshot
                errors.extend(f"{case['case_id']} step {step.get('step', 'initial')}: {error}" for error in _compare_update(result, expected))
            if steps:
                effect = case.get("expected_pipeline_effect") or {}
                if effect.get("failure") is None:
                    if effect.get("route") != result.focus_decision.route.value:
                        errors.append(f"{case['case_id']}: pipeline route differs")
                    expected_appraisal_refs = tuple(effect.get("appraisal_goal_refs", ()))
                    actual_appraisal_refs = tuple(item.goal_id for item in result.active_goal_views)
                    if (
                        len(expected_appraisal_refs) != len(actual_appraisal_refs)
                        or set(expected_appraisal_refs) != set(actual_appraisal_refs)
                    ):
                        errors.append(f"{case['case_id']}: pipeline appraisal projection differs")
                    if effect.get("must_not_run_downstream") is not False:
                        errors.append(f"{case['case_id']}: success case forbids downstream execution")
                excluded = case.get("excluded_history")
                if excluded:
                    excluded_goal_id = excluded["terminal_goal_record"]["goal_id"]
                    if excluded.get("eligible_for_v0_load") is not False or excluded_goal_id in {
                        item.goal_id for item in result.snapshot.records
                    }:
                        errors.append(f"{case['case_id']}: excluded cross-conversation history was loaded")
            for variant in case.get("invariance_variants", ()):
                invariance_variants_run += 1
                variant_expected = variant["expected_goal_update"]
                if not required_output_fields <= set(variant_expected) <= expected_output_fields:
                    errors.append(f"{case['case_id']}: invariance output is incomplete")
                    continue
                variant_engine = GoalEngine(
                    identity_factory=lambda kind, parts, c=case: _fixture_identity(c, kind, parts)
                )
                variant_result = variant_engine.update(build_request(case))
                errors.extend(
                    f"{case['case_id']} invariance {variant['profile_ref']}: {error}"
                    for error in _compare_update(variant_result, variant_expected)
                )
            expected_failure = (case.get("expected_pipeline_effect") or {}).get("failure")
            if case.get("failure_variants"):
                count, failure_errors = _execute_failure_variants(case)
                failure_variants_run += count
                errors.extend(f"{case['case_id']}: {error}" for error in failure_errors)
            elif expected_failure:
                errors.append(f"{case['case_id']}: expected failure was not exercised")
            results.append(result if steps else None)
        except Exception as exc:
            errors.append(f"{case['case_id']}: execution failed: {type(exc).__name__}")
            results.append(None)
            continue
    return {"cases_run": len(document["cases"]), "update_steps_run": update_steps_run, "failure_variants_run": failure_variants_run, "invariance_variants_run": invariance_variants_run, "executed": len(results), "results": tuple(results), "errors": tuple(errors), "passed": not errors and len(results) == len(document["cases"])}
