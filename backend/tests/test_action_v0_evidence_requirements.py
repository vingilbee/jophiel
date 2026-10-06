"""Blocker A: typed evidence requirements are enforced by the hard gate.

Every definition declares enforceable requirements (role, accepted source
kinds, cardinality, authorization, freshness, turn boundary).  The gate checks
them itself rather than trusting the constructor, and an evidence-deficient
candidate can be neither scored nor selected, nor recorded as eligible.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest
from pydantic import ValidationError

from action_v0_support import candidate_by_key, gate_for, index_for, request_for, result_for
from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionEngine, ActionSelectionError
from app.ai.Cognitive.system.v0.action_selection_v0.construction import (
    BINDING_MANIFEST, CandidateConstructor, recipe_evidence_roles, validate_manifest_evidence,
)
from app.ai.Cognitive.system.v0.action_selection_v0.gates import HardGate
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.action_selection_v0.registry import (
    DEFAULT_REGISTRY, DEFINITIONS, EVIDENCE_SPECIFICATIONS, SHADOW_KEYS,
)
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionEvidenceRef, ActionEvidenceRequirement, ActionFailurePhase, ActionGateStatus, ActionSelectionRequest,
    ActionSelectionResult, EvidenceTurnBoundary, ReferenceSourceKind as S,
)

GATE = HardGate(DEFAULT_REGISTRY)
CORRECT = ("evidence-grounded-correction", "provide_information.correct")
ACKNOWLEDGE = ("acknowledge-without-agreement", "give_feedback.acknowledge")
REASSURE = ("reassure-without-promise", "support_socially.reassure")
BASIS_OBJECT = "evidence-confirmation"


def _candidate(case_id: str, action_key: str, step: int = 0):
    candidates = candidate_by_key(result_for(case_id).steps[step], action_key)
    assert candidates, (case_id, action_key)
    return candidates[0]


def _evaluate(case_id: str, candidate, evidence, index: ActionReferenceIndex | None = None):
    forged = candidate.model_copy(update={"supporting_evidence_refs": tuple(sorted(evidence, key=lambda item: item.sort_key))})
    return GATE.evaluate(forged, index or index_for(case_id))


def _schema_codes(result) -> set[str]:
    return {item.code for item in result.violations if item.category.value == "schema_reference_integrity"}


def _request_with_basis(*, validate: bool = True, **updates) -> ActionSelectionRequest:
    """The correction case with its basis evidence object changed.

    ``validate=False`` skips request validation so the gate's own check is
    exercised even where an upstream layer already rejects the input.
    """

    request = request_for(CORRECT[0])
    objects = tuple(
        type(item).model_validate({**item.model_dump(mode="python"), **updates}) if item.object_id == BASIS_OBJECT else item
        for item in request.grounded_objects
    )
    if not validate:
        return request.model_copy(update={"grounded_objects": objects})
    return ActionSelectionRequest.model_validate({**request.model_dump(mode="python"), "grounded_objects": objects})


# -- the typed specification ---------------------------------------------------

def test_every_definition_carries_an_enforceable_typed_specification() -> None:
    assert set(EVIDENCE_SPECIFICATIONS) == SHADOW_KEYS
    for definition in DEFINITIONS:
        specification = definition.evidence_specification
        assert specification and any(item.minimum >= 1 for item in specification)
        assert sorted(name for item in specification for name in item.satisfies) == sorted(definition.evidence_requirements)
        for item in specification:
            assert item.authorization == "authorized_only" and item.freshness == "current_revision_only"
            assert not {S.GOAL, S.APPRAISAL, S.DYNAMIC_STATE, S.PARTIAL_PLAN} & set(item.accepted_source_kinds)


def test_turn_boundaries_are_explicit_and_consistent_with_source_kinds() -> None:
    repair = DEFAULT_REGISTRY.by_key("manage_dialogue.repair").evidence_requirement("grounding")
    assert repair.turn_boundaries == (EvidenceTurnBoundary.PRIOR_RECORD,)
    acknowledge = DEFAULT_REGISTRY.by_key("give_feedback.acknowledge").evidence_requirement("grounding")
    assert acknowledge.turn_boundaries == (EvidenceTurnBoundary.CURRENT_TURN,)
    with pytest.raises(ValidationError):
        ActionEvidenceRequirement(role="grounding", accepted_source_kinds=(S.INTERPRETATION,), minimum=1,
                                  turn_boundaries=(EvidenceTurnBoundary.CURRENT_TURN, EvidenceTurnBoundary.PRIOR_RECORD))
    with pytest.raises(ValidationError):
        ActionEvidenceRequirement(role="grounding", accepted_source_kinds=(S.GOAL,), minimum=1,
                                  turn_boundaries=(EvidenceTurnBoundary.CURRENT_TURN,))
    with pytest.raises(ValidationError):
        ActionEvidenceRequirement(role="grounding", accepted_source_kinds=(S.INTERPRETATION,), minimum=2, maximum=1,
                                  turn_boundaries=(EvidenceTurnBoundary.CURRENT_TURN,))


def test_a_definition_cannot_leave_a_reviewed_requirement_unbound_or_require_nothing() -> None:
    definition = DEFAULT_REGISTRY.by_key("provide_information.correct")
    payload = definition.model_dump(mode="python")
    unbound = copy.deepcopy(payload)
    unbound["evidence_specification"][0]["satisfies"] = ()
    with pytest.raises(ValidationError):
        type(definition).model_validate(unbound)
    optional = copy.deepcopy(payload)
    for item in optional["evidence_specification"]:
        item["minimum"] = 0
    with pytest.raises(ValidationError):
        type(definition).model_validate(optional)


def test_every_recipe_binds_every_required_role_and_only_declared_roles() -> None:
    validate_manifest_evidence(DEFAULT_REGISTRY, BINDING_MANIFEST)
    correct = next(item for item in BINDING_MANIFEST if item.action_key == "provide_information.correct")
    assert recipe_evidence_roles(correct) == {"grounding", "basis", "replacement"}
    stripped = tuple(dataclasses.replace(item, evidence=("grounding",)) if item is correct else item for item in BINDING_MANIFEST)
    with pytest.raises(ValueError):
        CandidateConstructor(DEFAULT_REGISTRY, stripped)
    engine = ActionSelectionEngine(constructor_factory=lambda registry: CandidateConstructor(registry, stripped))
    with pytest.raises(ActionSelectionError) as caught:
        engine.select(request_for(CORRECT[0]))
    assert caught.value.failed_phase == ActionFailurePhase.REGISTRY_RESOLUTION


def test_the_constructor_satisfies_every_specification_in_every_design_case() -> None:
    from action_v0_support import documents

    for case in documents()[1]["cases"]:
        for step in result_for(case["case_id"]).steps:
            for gate in step.gate_results:
                assert not {code for code in _schema_codes(gate) if "evidence" in code}, (case["case_id"], gate.candidate_ref)


# -- the audit reproduction and each rejection code -----------------------------

@pytest.mark.parametrize("case_id,action_key", (CORRECT, ACKNOWLEDGE, REASSURE))
def test_removing_every_supporting_evidence_ref_blocks_the_candidate(case_id: str, action_key: str) -> None:
    candidate = _candidate(case_id, action_key)
    assert gate_for(result_for(case_id).steps[0], candidate.candidate_id).status == ActionGateStatus.SHADOW_ONLY
    forged = _evaluate(case_id, candidate, ())
    assert forged.status == ActionGateStatus.BLOCKED
    missing = [item for item in forged.violations if item.code == "required_evidence_missing"]
    definition = DEFAULT_REGISTRY.get(candidate.definition_ref)
    required = {item.role for item in definition.evidence_specification if item.minimum >= 1}
    assert {item.subject_ref.rpartition(":")[2] for item in missing} == required
    assert all(item.category.value == "schema_reference_integrity" for item in missing)


@pytest.mark.parametrize("role", ("basis", "replacement", "grounding"))
def test_removing_one_required_role_blocks_the_correction(role: str) -> None:
    candidate = _candidate(*CORRECT)
    kept = tuple(item for item in candidate.supporting_evidence_refs if item.role != role)
    assert len(kept) < len(candidate.supporting_evidence_refs)
    result = _evaluate(CORRECT[0], candidate, kept)
    assert result.status == ActionGateStatus.BLOCKED
    assert [item.subject_ref for item in result.violations if item.code == "required_evidence_missing"] == [
        f"{candidate.candidate_id}#evidence:{role}"]


def test_changing_a_role_is_rejected_as_undeclared_and_leaves_the_requirement_unmet() -> None:
    candidate = _candidate(*CORRECT)
    relabelled = tuple(item.model_copy(update={"role": "example"}) if item.role == "basis" else item
                       for item in candidate.supporting_evidence_refs)
    assert {"evidence_role_not_declared", "required_evidence_missing"} <= _schema_codes(_evaluate(CORRECT[0], candidate, relabelled))


def test_a_mislabelled_source_kind_is_rejected() -> None:
    candidate = _candidate(*ACKNOWLEDGE)
    mistyped = tuple(item.model_copy(update={"source_kind": S.RUNTIME}) for item in candidate.supporting_evidence_refs)
    result = _evaluate(ACKNOWLEDGE[0], candidate, mistyped)
    assert {"evidence_source_kind_mismatch", "required_evidence_missing"} <= _schema_codes(result)


def test_a_truthfully_typed_but_unaccepted_source_kind_is_rejected() -> None:
    candidate = _candidate(*ACKNOWLEDGE)
    goal_ref = request_for(ACKNOWLEDGE[0]).goal_snapshot.goal_snapshot_id
    cited = (ActionEvidenceRef(role="grounding", ref=goal_ref, source_kind=S.GOAL),)
    assert {"evidence_source_kind_not_accepted", "required_evidence_missing"} <= _schema_codes(_evaluate(ACKNOWLEDGE[0], candidate, cited))


def test_an_unresolved_citation_is_rejected() -> None:
    candidate = _candidate(*ACKNOWLEDGE)
    cited = (ActionEvidenceRef(role="grounding", ref="evidence:nowhere", source_kind=S.INTERPRETATION),)
    assert {"evidence_does_not_resolve", "required_evidence_missing"} <= _schema_codes(_evaluate(ACKNOWLEDGE[0], candidate, cited))


def test_a_prior_record_is_outside_the_boundary_of_a_current_turn_requirement() -> None:
    case_id = "repair-after-explicit-correction-stale-memory"
    prior = request_for(case_id).prior_action_records[0].record_id
    repair = _candidate(case_id, "manage_dialogue.repair")
    assert any(item.ref == prior and item.source_kind == S.PRIOR_ACTION for item in repair.supporting_evidence_refs)
    cited = (ActionEvidenceRef(role="grounding", ref=prior, source_kind=S.PRIOR_ACTION),)
    result = _evaluate(case_id, _candidate(case_id, "give_feedback.acknowledge"), cited)
    assert {"evidence_source_kind_not_accepted", "required_evidence_missing"} <= _schema_codes(result)


def test_a_prior_record_claiming_the_current_turn_is_outside_the_boundary() -> None:
    case_id = "repair-after-explicit-correction-stale-memory"
    request = request_for(case_id)
    records = tuple(item.model_copy(update={"cognitive_turn_ref": request.cognitive_turn_id}) for item in request.prior_action_records)
    index = ActionReferenceIndex(request.model_copy(update={"prior_action_records": records}))
    repair = _candidate(case_id, "manage_dialogue.repair")
    result = _evaluate(case_id, repair, repair.supporting_evidence_refs, index)
    assert {"evidence_outside_turn_boundary", "required_evidence_missing"} <= _schema_codes(result)


def test_cardinality_maximum_is_enforced() -> None:
    requirement = DEFAULT_REGISTRY.by_key("support_socially.apologize").evidence_requirement("subject")
    assert (requirement.minimum, requirement.maximum) == (1, 1)
    case_id = "repair-after-explicit-correction-stale-memory"
    candidate = _candidate(case_id, "support_socially.apologize")
    subject = next(item for item in candidate.supporting_evidence_refs if item.role == "subject")
    request = request_for(case_id)
    other, kind = next((item.record_id, kind) for kind, records in (
        (S.PRIOR_ACTION, request.prior_action_records), (S.PRIOR_REALIZATION, request.prior_realization_records),
    ) for item in records if item.record_id != subject.ref)
    extra = ActionEvidenceRef(role="subject", ref=other, source_kind=kind)
    assert _evaluate(case_id, candidate, candidate.supporting_evidence_refs).status == ActionGateStatus.SHADOW_ONLY
    result = _evaluate(case_id, candidate, (*candidate.supporting_evidence_refs, extra))
    assert result.status == ActionGateStatus.BLOCKED
    assert "evidence_cardinality_exceeded" in _schema_codes(result)


@pytest.mark.parametrize("updates,code", (
    ({"authorized": False}, "evidence_not_authorized"),
    ({"status": "superseded"}, "evidence_superseded"),
    ({"status": "deleted"}, "evidence_deleted"),
    ({"cognitive_turn_id": "another-turn"}, "evidence_outside_turn_boundary"),
))
def test_unauthorized_stale_or_out_of_turn_basis_blocks_the_correction(updates: dict, code: str) -> None:
    out_of_turn = "cognitive_turn_id" in updates
    if out_of_turn:
        # Request validation is the first layer; the gate still checks on its own.
        with pytest.raises(ValidationError, match="current cognitive turn"):
            _request_with_basis(**updates)
    index = ActionReferenceIndex(_request_with_basis(validate=not out_of_turn, **updates))
    candidate = _candidate(*CORRECT)
    result = _evaluate(CORRECT[0], candidate, candidate.supporting_evidence_refs, index)
    assert result.status == ActionGateStatus.BLOCKED
    assert {code, "required_evidence_missing"} <= _schema_codes(result)
    assert f"{candidate.candidate_id}#evidence:basis" in {item.subject_ref for item in result.violations}


def test_a_rejected_hypothesis_is_resolvable_lineage_but_not_current_evidence() -> None:
    request = request_for(ACKNOWLEDGE[0])
    leading = request.interpretation.hypotheses[0]
    rejected = type(leading).model_validate({
        **leading.model_dump(mode="python"), "hypothesis_id": "hyp-rejected-probe", "disposition": "rejected",
    })
    interpretation = request.interpretation.model_copy(update={"hypotheses": (*request.interpretation.hypotheses, rejected)})
    index = ActionReferenceIndex(request.model_copy(update={"interpretation": interpretation}))
    assert index.resolves("hyp-rejected-probe") and index.is_non_current_ref("hyp-rejected-probe")
    candidate = _candidate(*ACKNOWLEDGE)
    cited = (ActionEvidenceRef(role="grounding", ref="hyp-rejected-probe", source_kind=S.INTERPRETATION),)
    assert {"evidence_not_current", "required_evidence_missing"} <= _schema_codes(_evaluate(ACKNOWLEDGE[0], candidate, cited, index))


# -- an evidence-deficient candidate can be neither scored nor selected ----------

def test_an_unauthorized_basis_removes_the_correction_from_scoring_and_the_plan() -> None:
    baseline = result_for(CORRECT[0])
    assert CORRECT[1] in {move.definition_ref.action_key for move in baseline.plan.moves}
    result = ActionSelectionEngine().select(_request_with_basis(authorized=False))
    for step in result.steps:
        for candidate in candidate_by_key(step, CORRECT[1]):
            assert gate_for(step, candidate.candidate_id).status == ActionGateStatus.BLOCKED
            assert candidate.candidate_id not in {item.candidate_ref for item in step.scored_candidates}
    assert CORRECT[1] not in {move.definition_ref.action_key for move in result.plan.moves}


def test_a_constructor_that_drops_evidence_cannot_get_its_candidates_scored() -> None:
    class Stripping(CandidateConstructor):
        def construct(self, index, plan, context_ref):
            candidates, failures = super().construct(index, plan, context_ref)
            return tuple(item.model_copy(update={"supporting_evidence_refs": ()}) for item in candidates), failures

    for case_id, action_key in (CORRECT, ACKNOWLEDGE, REASSURE):
        try:
            result = ActionSelectionEngine(constructor_factory=Stripping).select(request_for(case_id))
        except ActionSelectionError as error:
            assert (error.failed_phase, error.code) == (ActionFailurePhase.PLAN_VALIDATION, "no_viable_plan")
            continue
        assert all(not step.scored_candidates for step in result.steps)
        assert result.plan.moves == ()


def test_a_trace_cannot_record_an_evidence_deficient_candidate_as_eligible() -> None:
    payload = copy.deepcopy(result_for(CORRECT[0]).model_dump(mode="json"))
    selected = next(item for item in payload["plan"]["moves"] if item["definition_ref"]["action_key"] == CORRECT[1])
    step = payload["steps"][selected["step_index"]]
    candidate = next(item for item in step["constructed_candidates"] if item["candidate_id"] == selected["candidate_ref"])
    candidate["supporting_evidence_refs"] = []
    for move in (selected, payload["moves"][payload["plan"]["moves"].index(selected)]):
        move["supporting_evidence_refs"] = []
    with pytest.raises(ValidationError, match="evidence-deficient"):
        ActionSelectionResult.model_validate(payload)
