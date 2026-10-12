"""Golden, determinism, perturbation, and pipeline-boundary tests for the mapper."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import (
    CognitivePipelineRequest, CognitivePipelineRuntimeSnapshot, CognitivePipelineV0,
)
from app.ai.Cognitive.system.v0.cognitive_turn import CognitiveTurnRunner
from app.ai.Cognitive.system.v0.contracts.interpretation import HypothesisSupport
from app.ai.Cognitive.system.v0.contracts.safety import SafetyDisposition
from app.ai.Cognitive.system.v0.dynamic_state import DynamicStateSnapshotFactory
from app.ai.Cognitive.system.v0.dynamic_state.models import StateKey
from app.ai.Cognitive.system.v0.dynamic_state.policy import DEFAULT_BASELINE_SCORES
from app.ai.Cognitive.system.v0.personality_runtime_v0 import (
    PERSONALITY_MAPPER_VERSION, PERSONALITY_MAPPING_MANIFEST_VERSION, PersonalityMappingError,
    PersonalityMappingErrorCode, PersonalityRuntimeMapperV0, build_source,
)
from app.ai.Cognitive.system.v0.personality_runtime_v0 import mapper as mapper_module
from app.ai.Cognitive.system.v0.personality_runtime_v0.manifest import STATE_BASELINE_SNAPSHOT
from cognitive_action_support import safety_decision
from cognitive_shadow_support import COLUMBINA_CORE_GROUPS

MAPPER = PersonalityRuntimeMapperV0()
INTERPRETATION_FIELDS = (
    "benign_prior", "trust_prior", "threat_sensitivity", "confirmation_tendency",
    "self_attribution_tendency", "ambiguity_tolerance", "social_affiliation_prior",
)
ACTION_FIELDS = ("curiosity", "warmth", "playfulness", "directness")


def _compile(groups=None, **identity):
    return MAPPER.compile(build_source(
        name=identity.get("name", "columbina"), version=identity.get("version", 1), schema_version=1,
        groups=groups if groups is not None else copy.deepcopy(COLUMBINA_CORE_GROUPS),
    ))


def _values(projection) -> dict[str, float]:
    return {
        **{f"interpretation.{f}": getattr(projection.interpretation_biases, f) for f in INTERPRETATION_FIELDS},
        **{f"action.{f}": getattr(projection.action_personality, f) for f in ACTION_FIELDS},
        "state": projection.state_personality.model_dump(mode="json")["state_controls"],
        "patience": projection.state_personality.interaction_patience_band,
    }


# ---------------------------------------------------------------------------
# Golden vector and identity
# ---------------------------------------------------------------------------

def test_seeded_columbina_golden_vector() -> None:
    projection = _compile()
    interpretation = projection.interpretation_biases
    assert tuple(getattr(interpretation, f) for f in INTERPRETATION_FIELDS) == (0.77, 0.64, 0.25, 0.5, 0.5, 0.55, 0.75)
    action = projection.action_personality
    assert action.status.value == "known"
    assert tuple(getattr(action, f) for f in ACTION_FIELDS) == (0.88, 0.80, 0.58, 0.62)
    state = projection.state_personality
    assert state.interaction_patience_band == "moderate"
    assert set(state.state_controls) == set(StateKey)
    for key, control in state.state_controls.items():
        assert control.model_dump() == {
            "baseline_score": DEFAULT_BASELINE_SCORES[key], "persistence_band": "moderate",
            "recovery_band": "moderate", "sensitivity": "neutral", "maximum_step_band": "moderate",
        }


def test_state_snapshot_is_pinned_to_the_reviewed_dynamic_state_defaults() -> None:
    assert dict(STATE_BASELINE_SNAPSHOT) == DEFAULT_BASELINE_SCORES
    assert set(STATE_BASELINE_SNAPSHOT) == set(StateKey)


def test_identity_version_and_ownership_are_deterministic() -> None:
    projection = _compile()
    projection_id = f"personality-runtime:core_memory:columbina:v1:{PERSONALITY_MAPPER_VERSION}"
    assert (projection.projection_kind, projection.scope, projection.projection_id) == (
        "runtime_mapper", "core_identity", projection_id,
    )
    assert projection.projection_version == PERSONALITY_MAPPER_VERSION
    assert projection.review_ref == "PERSONALITY_RUNTIME_MAPPER_V0_IMPLEMENTATION.md"
    assert projection.provenance == PERSONALITY_MAPPING_MANIFEST_VERSION
    assert projection.interpretation_biases.profile_id == f"{projection_id}:interpretation"
    assert projection.state_personality.profile_id == f"{projection_id}:state"
    assert projection.action_personality.profile_id == f"{projection_id}:action"
    assert {
        projection.interpretation_biases.compiler_version, projection.state_personality.mapper_version,
        projection.action_personality.mapper_version,
    } == {PERSONALITY_MAPPER_VERSION}
    assert projection.core_memory_ref == projection.interpretation_biases.source_core_memory_ref == \
        projection.action_personality.source_core_memory_ref == "core_memory:columbina:v1"
    assert _compile(version=7).projection_id.startswith("personality-runtime:core_memory:columbina:v7:")


def test_output_is_byte_identical_across_runs_and_processes() -> None:
    first = _compile().model_dump_json()
    assert all(_compile().model_dump_json() == first for _ in range(5))
    probe = (
        "import copy, sys; sys.path.insert(0, 'tests');"
        "from cognitive_shadow_support import COLUMBINA_CORE_GROUPS;"
        "from app.ai.Cognitive.system.v0.personality_runtime_v0 import PersonalityRuntimeMapperV0, build_source;"
        "print(PersonalityRuntimeMapperV0().compile(build_source(name='columbina', version=1, schema_version=1,"
        " groups=copy.deepcopy(COLUMBINA_CORE_GROUPS))).model_dump_json())"
    )
    backend = Path(__file__).resolve().parents[1]
    for seed in ("0", "12345"):
        output = subprocess.run(
            [sys.executable, "-c", probe], cwd=backend, capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONPATH": str(backend), "PYTHONHASHSEED": seed},
        ).stdout.strip()
        assert output == first


def test_rounding_is_decimal_half_even_to_six_places() -> None:
    groups = copy.deepcopy(COLUMBINA_CORE_GROUPS)
    groups["interpretation_biases"].update(optimism_prior=0.1234565, charitable_interpretation=0.1234565)
    groups["action_style"]["warmth"] = 0.0000005
    projection = _compile(groups)
    assert projection.interpretation_biases.benign_prior == 0.123456  # half-even: ...565 -> ...56
    assert projection.action_personality.warmth == 0.0
    groups["interpretation_biases"].update(optimism_prior=0.1, charitable_interpretation=0.2)
    assert _compile(groups).interpretation_biases.benign_prior == 0.15  # no binary-float drift


# ---------------------------------------------------------------------------
# Perturbation: each source changes only its declared outputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("group, key, changed", (
    ("action_style", "warmth", {"action.warmth"}),
    ("drives", "curiosity", {"action.curiosity"}),
    ("action_style", "playfulness", {"action.playfulness"}),
    ("action_style", "directness", {"action.directness"}),
    ("interpretation_biases", "trust_prior", {"interpretation.trust_prior"}),
    ("drives", "affiliation", {"interpretation.social_affiliation_prior"}),
    ("interpretation_biases", "optimism_prior", {"interpretation.benign_prior"}),
    ("interpretation_biases", "charitable_interpretation", {"interpretation.benign_prior"}),
    ("interpretation_biases", "ambiguity_tolerance", {"interpretation.ambiguity_tolerance"}),
    ("perception_priors", "threat_sensitivity", {"interpretation.threat_sensitivity"}),
    ("values", "privacy", set()),
    ("traits", "openness", set()),
    ("traits", "emotional_stability", set()),
    ("drives", "competence", set()),
    ("action_style", "initiative", set()),
    ("interpretation_biases", "hostile_attribution_bias", set()),
))
def test_one_source_changes_only_its_declared_output(group: str, key: str, changed: set[str]) -> None:
    base = _values(_compile())
    groups = copy.deepcopy(COLUMBINA_CORE_GROUPS)
    groups[group][key] = 0.0 if groups[group][key] != 0.0 else 1.0
    after = _values(_compile(groups))
    assert {name for name in base if base[name] != after[name]} == changed


# ---------------------------------------------------------------------------
# Atomic failure
# ---------------------------------------------------------------------------

def test_invalid_input_never_yields_a_partial_or_unknown_profile(monkeypatch) -> None:
    groups = copy.deepcopy(COLUMBINA_CORE_GROUPS)
    groups["action_style"]["warmth"] = None
    with pytest.raises(PersonalityMappingError) as caught:
        _compile(groups)
    assert caught.value.code == PersonalityMappingErrorCode.SOURCE_FIELD_MISSING

    def broken_state(self):
        raise ValueError("state profile could not be built")

    monkeypatch.setattr(PersonalityRuntimeMapperV0, "_state_fields", broken_state)
    with pytest.raises(PersonalityMappingError) as caught:
        _compile()
    assert caught.value.code == PersonalityMappingErrorCode.PROJECTION_INVALID
    with pytest.raises(PersonalityMappingError) as caught:
        MAPPER.compile({"not": "a source"})  # type: ignore[arg-type]
    assert caught.value.code == PersonalityMappingErrorCode.INTERNAL_CONTRACT


def test_an_unreviewed_state_key_fails_closed(monkeypatch) -> None:
    snapshot = {key: value for key, value in STATE_BASELINE_SNAPSHOT.items() if key != StateKey.CURIOSITY}
    monkeypatch.setattr(mapper_module, "STATE_BASELINE_SNAPSHOT", snapshot)
    with pytest.raises(PersonalityMappingError) as caught:
        _compile()
    assert caught.value.code == PersonalityMappingErrorCode.INTERNAL_CONTRACT


# ---------------------------------------------------------------------------
# The real seeded CoreMemory (migration d8f1a2b3c4e5)
# ---------------------------------------------------------------------------

def test_the_migration_seed_maps_to_the_golden_vector() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to map the real migrated CoreMemory seed")
    name = "personality_seed_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    backend = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, check=True, capture_output=True,
            env={**os.environ, "DATABASE_URL": target, "PYTHONPATH": str(backend)},
        )
        engine = create_engine(target)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM core_memory WHERE name = 'columbina'")).mappings().one()
        engine.dispose()
        groups = {key: row[key] for key in COLUMBINA_CORE_GROUPS}
        # Core v2 adds her character in words; every numeric group is unchanged.
        character = groups["identity"].pop("character")
        assert groups == COLUMBINA_CORE_GROUPS and character["version"] == "character-v1"
        assert (row["is_active"], row["version"], row["schema_version"]) == (True, 2, 1)
        projection = MAPPER.compile(build_source(
            name=row["name"], version=1, schema_version=row["schema_version"], groups=groups,
        ))
        assert projection == _compile()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


# ---------------------------------------------------------------------------
# Pipeline boundaries: personality is a bounded control, never evidence
# ---------------------------------------------------------------------------

def _opposite_extreme(low: bool):
    groups = copy.deepcopy(COLUMBINA_CORE_GROUPS)
    value = 0.0 if low else 1.0
    for group, key in (
        ("interpretation_biases", "optimism_prior"), ("interpretation_biases", "charitable_interpretation"),
        ("interpretation_biases", "trust_prior"), ("interpretation_biases", "ambiguity_tolerance"),
        ("perception_priors", "threat_sensitivity"), ("drives", "affiliation"), ("drives", "curiosity"),
        ("action_style", "warmth"), ("action_style", "playfulness"), ("action_style", "directness"),
    ):
        groups[group][key] = value
    return _compile(groups)


PROFILES = {"columbina": _compile(), "all_low": _opposite_extreme(True), "all_high": _opposite_extreme(False)}
BOUNDARY_CASES = (
    "factual-information-question", "action-question-missing-recipient", "explicit-correction-tuesday",
    "decline-no-thanks", "short-acknowledgement", "informal-greeting", "indirect-window-request-with-attachment",
    "runtime-capability-memory-conflict", "unknown-out-of-scope-command", "consequential-delete-first-request",
    "mixed-literal-evaluation",
)
_ORDINAL = {support: index for index, support in enumerate(HypothesisSupport)}


def _run(case_id: str, projection):
    from app.ai.Cognitive.system.v0.interpretation_v0.fixture_validator import DEFAULT_FIXTURE, _runtime_case

    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = next(item for item in document["cases"] if item["case_id"] == case_id)
    _, interpretation_request = _runtime_case(case)
    runtime = interpretation_request.runtime
    previous = DynamicStateSnapshotFactory().create_initial(
        conversation_ref=interpretation_request.conversation_ref, cognitive_turn_id="personality-boundary-before",
        personality=projection.state_personality,
    )
    request = CognitivePipelineRequest(
        cognitive_turn_id=interpretation_request.cognitive_turn_id,
        conversation_ref=interpretation_request.conversation_ref,
        messages=interpretation_request.messages,
        focus_message_id=interpretation_request.perception.focus_message_id,
        runtime=CognitivePipelineRuntimeSnapshot(
            snapshot_id=runtime.snapshot_id, predicate_registry_version=runtime.predicate_registry_version,
            known_predicates=runtime.known_predicates, known_entities=runtime.known_entities,
            attachments=runtime.attachments, available_capabilities=runtime.available_capabilities,
            capability_snapshot_id=f"capability:{case_id}", task_contract=runtime.task_contract,
            authorization=runtime.authorization,
            safety_decision=safety_decision(runtime.snapshot_id, SafetyDisposition.UNAVAILABLE),
            last_tool_event=runtime.last_tool_event, response_obligation="required",
            response_obligation_ref=f"response-obligation:{case_id}",
        ),
        memories=interpretation_request.memories,
        interpretation_biases=projection.interpretation_biases,
        previous_state=previous, state_personality=projection.state_personality,
        core_memory_ref=projection.core_memory_ref, memory_retrieval_policy_version="memory-retrieval-v1",
        action_personality=projection.action_personality,
    )
    return CognitivePipelineV0(
        cognitive_turn_runner=CognitiveTurnRunner(id_factory=lambda: "turn-trace"),
        trace_id_factory=lambda: "pipeline-trace",
    ).run(request)


# Downstream identifiers are content hashes whose inputs include the profile
# identity (legitimate provenance), so structure is compared with them masked.
_HASH_ID = re.compile(r"[0-9a-f]{20,64}")


def _shape(model) -> object:
    return json.loads(_HASH_ID.sub("#", json.dumps(model.model_dump(mode="json"), sort_keys=True)))


def _signs(trace) -> dict[tuple[str, str], str]:
    return {
        (item.rule_id, item.state_key.value): item.direction.value
        for item in trace.cognitive_turn.dynamic_state_update.contributions
    }


@pytest.mark.parametrize("case_id", BOUNDARY_CASES)
def test_personality_never_alters_facts_evidence_gates_state_signs_or_safety(case_id: str) -> None:
    traces = {name: _run(case_id, projection) for name, projection in PROFILES.items()}
    reference = traces["columbina"]
    for name, trace in traces.items():
        # Perception is personality-free.
        assert trace.perception == reference.perception, name
        # Grounded meaning, evidence, and base support are personality-free.
        interpretation = trace.interpretation
        assert interpretation.semantic_frames == reference.interpretation.semantic_frames, name
        assert _shape(interpretation)["evidence"] == _shape(reference.interpretation)["evidence"], name
        base = {item.hypothesis_key: item.base_support for item in reference.interpretation.hypotheses}
        assert {item.hypothesis_key: item.base_support for item in interpretation.hypotheses} == base, name
        for hypothesis in interpretation.hypotheses:
            # Ambiguous meaning moves by at most one ordinal level ...
            assert abs(_ORDINAL[hypothesis.support] - _ORDINAL[hypothesis.base_support]) <= 1, name
            # ... and explicit or runtime-grounded meaning does not move at all.
            if all(item.ambiguity_gate == 0 for item in hypothesis.bias_adjustments):
                assert hypothesis.support == hypothesis.base_support, (name, hypothesis.hypothesis_key)
        interpretation_moved = [
            (h.hypothesis_key, h.support) for h in interpretation.hypotheses
        ] != [(h.hypothesis_key, h.support) for h in reference.interpretation.hypotheses]
        if not interpretation_moved:
            # With the same interpretation, the whole downstream turn is identical.
            assert _shape(trace.cognitive_turn.appraisal) == _shape(reference.cognitive_turn.appraisal), name
            assert _signs(trace) == _signs(reference), name
        # A permitted interpretation shift may add or drop contributions, but no
        # evidence-owned contribution ever reverses direction.
        shared = _signs(trace).keys() & _signs(reference).keys()
        assert all(_signs(trace)[key] == _signs(reference)[key] for key in shared), name
        # Action construction and every hard gate are personality-free; only
        # post-gate features and ordering may differ.
        first, reference_first = trace.action_selection.steps[0], reference.action_selection.steps[0]
        assert [_shape(c) for c in first.constructed_candidates] == [
            _shape(c) for c in reference_first.constructed_candidates
        ], name
        assert [_shape(g) for g in first.gate_results] == [_shape(g) for g in reference_first.gate_results], name
        # Safety-unavailable stays fail-closed for commitments.
        assert trace.action_selection.safety.restriction.value == "commitments", name
        assert trace.action_selection.safety == reference.action_selection.safety, name


def test_the_boundary_cases_exercise_real_personality_influence() -> None:
    """Guard against a vacuous invariance suite: extremes do move ambiguous meaning."""

    moved = {
        case_id for case_id in BOUNDARY_CASES
        if any(h.support != h.base_support for h in _run(case_id, PROFILES["all_high"]).interpretation.hypotheses)
    }
    assert {"short-acknowledgement", "indirect-window-request-with-attachment"} <= moved
