"""Source, error, and manifest contracts for Personality Runtime Mapper V0."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.dynamic_state.models import PersonalityProfile
from app.ai.Cognitive.system.v0.contracts.actions import ActionPersonalityProfile
from app.ai.Cognitive.system.v0.interpretation_v0.models import InterpretationBiasProfile
from app.ai.Cognitive.system.v0.live_shadow.models import PersonalityMissingReason
from app.ai.Cognitive.system.v0.personality_runtime_v0 import (
    MANIFEST, PERSONALITY_MAPPER_VERSION, PERSONALITY_MAPPING_MANIFEST_VERSION, PERSONALITY_SOURCE_SCHEMA_VERSION,
    CorePersonalitySourceV0, OutputProfile, PersonalityMappingError, PersonalityMappingErrorCode, Transform,
    build_source,
)
from app.ai.Cognitive.system.v0.personality_runtime_v0.models import SOURCE_PATHS
from cognitive_shadow_support import COLUMBINA_CORE_GROUPS

E = PersonalityMappingErrorCode


def _groups() -> dict:
    return copy.deepcopy(COLUMBINA_CORE_GROUPS)


def _build(groups=None, **identity):
    return build_source(
        name=identity.get("name", "columbina"), version=identity.get("version", 1),
        schema_version=identity.get("schema_version", 1), groups=groups if groups is not None else _groups(),
    )


def _code(**kwargs) -> PersonalityMappingErrorCode:
    with pytest.raises(PersonalityMappingError) as caught:
        _build(**kwargs)
    return caught.value.code


def test_versions_are_the_brief_versions() -> None:
    assert (PERSONALITY_SOURCE_SCHEMA_VERSION, PERSONALITY_MAPPING_MANIFEST_VERSION, PERSONALITY_MAPPER_VERSION) == (
        "core-personality-source-v0.2", "personality-runtime-mapping-v0.2", "personality-runtime-mapper-v0.2",
    )


def test_source_reads_exactly_the_fourteen_declared_paths() -> None:
    assert {f"{group}.{key}" for group, key in SOURCE_PATHS.values()} == {
        "interpretation_biases.optimism_prior", "interpretation_biases.charitable_interpretation",
        "interpretation_biases.trust_prior", "interpretation_biases.ambiguity_tolerance",
        "perception_priors.threat_sensitivity", "drives.affiliation", "drives.curiosity",
        "action_style.warmth", "action_style.playfulness", "action_style.directness",
        "interpretation_biases.hostile_attribution_bias", "perception_priors.social_cue_sensitivity",
        "perception_priors.contradiction_sensitivity", "traits.emotional_stability",
    }
    source = _build()
    assert source.core_memory_ref == "core_memory:columbina:v1"
    assert source.optimism_prior == Decimal("0.72") and source.directness == Decimal("0.62")


@pytest.mark.parametrize("field", sorted(SOURCE_PATHS))
def test_every_consumed_field_is_required(field: str) -> None:
    group, key = SOURCE_PATHS[field]
    groups = _groups()
    del groups[group][key]
    assert _code(groups=groups) == E.SOURCE_FIELD_MISSING
    groups = _groups()
    del groups[group]
    assert _code(groups=groups) == E.SOURCE_FIELD_MISSING


@pytest.mark.parametrize("value, code", (
    (None, E.SOURCE_FIELD_MISSING),
    (True, E.SOURCE_TYPE_INVALID),
    (False, E.SOURCE_TYPE_INVALID),
    ("0.5", E.SOURCE_TYPE_INVALID),
    ([0.5], E.SOURCE_TYPE_INVALID),
    ({"value": 0.5}, E.SOURCE_TYPE_INVALID),
    (float("nan"), E.SOURCE_NONFINITE),
    (float("inf"), E.SOURCE_NONFINITE),
    (float("-inf"), E.SOURCE_NONFINITE),
    (-0.000001, E.SOURCE_OUT_OF_RANGE),
    (1.000001, E.SOURCE_OUT_OF_RANGE),
    (2, E.SOURCE_OUT_OF_RANGE),
))
def test_invalid_values_fail_with_typed_codes(value, code) -> None:
    for group, key in SOURCE_PATHS.values():
        groups = _groups()
        groups[group][key] = value
        with pytest.raises(PersonalityMappingError) as caught:
            _build(groups=groups)
        assert caught.value.code == code
        assert caught.value.field == f"{group}.{key}"
        assert str(value) not in caught.value.safe_message


def test_integer_bounds_are_valid_numbers() -> None:
    groups = _groups()
    groups["action_style"]["warmth"] = 0
    groups["action_style"]["directness"] = 1
    source = _build(groups=groups)
    assert (source.warmth, source.directness) == (Decimal(0), Decimal(1))


@pytest.mark.parametrize("identity, code", (
    ({"schema_version": 2}, E.CORE_MEMORY_SCHEMA_UNSUPPORTED),
    ({"schema_version": 0}, E.CORE_MEMORY_SCHEMA_UNSUPPORTED),
    ({"schema_version": True}, E.CORE_MEMORY_SCHEMA_UNSUPPORTED),
    ({"version": 0}, E.CORE_MEMORY_LINEAGE_INVALID),
    ({"version": True}, E.CORE_MEMORY_LINEAGE_INVALID),
    ({"version": "1"}, E.CORE_MEMORY_LINEAGE_INVALID),
    ({"name": ""}, E.CORE_MEMORY_LINEAGE_INVALID),
    ({"name": "col:umbina"}, E.CORE_MEMORY_LINEAGE_INVALID),
))
def test_lineage_format_and_version_are_validated(identity, code) -> None:
    assert _code(**identity) == code


def test_source_lineage_must_match_its_identity() -> None:
    payload = _build().model_dump(mode="json")
    with pytest.raises(ValidationError, match="lineage"):
        CorePersonalitySourceV0.model_validate({**payload, "core_memory_ref": "core_memory:columbina:v2"})


def test_unconsumed_fields_cannot_affect_the_source() -> None:
    groups = _groups()
    groups["values"]["privacy"] = 0.0
    groups["traits"]["openness"] = "prose is ignored here"
    groups["drives"]["competence"] = None
    groups["perception_priors"]["novelty_sensitivity"] = float("nan")
    groups["extra_group"] = {"anything": True}
    assert _build(groups=groups) == _build()


def test_source_is_immutable_extra_forbid_and_json_round_trips() -> None:
    source = _build()
    with pytest.raises(ValidationError):
        source.warmth = Decimal("0.1")  # type: ignore[misc]
    payload = source.model_dump(mode="json")
    assert payload["warmth"] == 0.8 and isinstance(payload["warmth"], float)
    assert CorePersonalitySourceV0.model_validate(payload) == source
    assert CorePersonalitySourceV0.model_validate_json(source.model_dump_json()) == source
    with pytest.raises(ValidationError):
        CorePersonalitySourceV0.model_validate({**payload, "openness": 0.85})
    with pytest.raises(ValidationError):
        CorePersonalitySourceV0.model_validate({**payload, "warmth": "0.8"})


def test_manifest_declares_every_output_field_exactly_once() -> None:
    identity = {"profile_id", "compiler_version", "mapper_version", "source_core_memory_ref", "status"}
    expected = {
        OutputProfile.INTERPRETATION: set(InterpretationBiasProfile.model_fields) - identity,
        OutputProfile.STATE: set(PersonalityProfile.model_fields) - identity,
        OutputProfile.ACTION: set(ActionPersonalityProfile.model_fields) - identity,
    }
    for profile, fields in expected.items():
        assert {rule.output_field for rule in MANIFEST.rules_for(profile)} == fields, profile
    assert MANIFEST.manifest_version == PERSONALITY_MAPPING_MANIFEST_VERSION
    assert {rule.rule_id for rule in MANIFEST.rules} == {
        "personality.interpretation.benign-prior.mean-v1", "personality.interpretation.trust-prior.direct-v1",
        "personality.interpretation.threat-sensitivity.direct-v1",
        "personality.interpretation.confirmation.one-minus-contradiction-v1",
        "personality.interpretation.self-attribution-neutral.v1",
        "personality.interpretation.ambiguity-tolerance.direct-v1",
        "personality.interpretation.social-affiliation.direct-v1",
        "personality.interpretation.hostile-attribution.direct-v1",
        "personality.interpretation.social-cue-sensitivity.direct-v1",
        "personality.state.stability-bands.v1", "personality.state.defaults-snapshot.v1",
        "personality.action.curiosity.direct-v1", "personality.action.warmth.direct-v1",
        "personality.action.playfulness.direct-v1", "personality.action.directness.direct-v1",
    }
    for rule in MANIFEST.rules:
        assert rule.influence_boundary
        if rule.transform != Transform.DYNAMIC_POLICY_SNAPSHOT:
            assert (rule.lower_bound, rule.upper_bound) == (Decimal(0), Decimal(1))
        if rule.transform not in {Transform.DYNAMIC_POLICY_SNAPSHOT, Transform.STABILITY_BANDS}:
            assert rule.rounding == "decimal-quantize-0.000001-round-half-even"


def test_manifest_transforms_are_the_declared_mappings() -> None:
    table = {(rule.output_profile.value, rule.output_field): (rule.transform.value, rule.source_paths)
             for rule in MANIFEST.rules}
    assert table[("interpretation", "benign_prior")] == (
        "mean_two", ("interpretation_biases.optimism_prior", "interpretation_biases.charitable_interpretation"),
    )
    assert table[("interpretation", "confirmation_tendency")] == (
        "one_minus", ("perception_priors.contradiction_sensitivity",))
    assert table[("interpretation", "hostile_attribution")] == (
        "direct", ("interpretation_biases.hostile_attribution_bias",))
    assert table[("interpretation", "social_cue_sensitivity")] == (
        "direct", ("perception_priors.social_cue_sensitivity",))
    assert table[("interpretation", "self_attribution_tendency")] == ("policy_constant", ("policy_constant",))
    assert table[("interpretation", "social_affiliation_prior")] == ("direct", ("drives.affiliation",))
    assert table[("interpretation", "threat_sensitivity")] == ("direct", ("perception_priors.threat_sensitivity",))
    assert table[("action", "curiosity")] == ("direct", ("drives.curiosity",))
    for field in ("warmth", "playfulness", "directness"):
        assert table[("action", field)] == ("direct", (f"action_style.{field}",))
    assert table[("state", "state_controls")] == ("stability_bands", ("traits.emotional_stability",))
    assert table[("state", "interaction_patience_band")][0] == "dynamic_policy_snapshot"
    # The remaining neutral constant is explicit policy, not a CoreMemory fact.
    constants = [rule for rule in MANIFEST.rules if rule.transform == Transform.POLICY_CONSTANT]
    assert {rule.constant for rule in constants} == {Decimal("0.500000")}
    assert all(rule.source_fields == () for rule in constants)


def test_every_error_code_has_a_typed_missing_reason() -> None:
    from app.services.cognitive_shadow import _MAPPER_FAILURE_REASONS

    assert set(_MAPPER_FAILURE_REASONS) == set(PersonalityMappingErrorCode)
    assert set(_MAPPER_FAILURE_REASONS.values()) <= set(PersonalityMissingReason)
    assert {item.value for item in PersonalityMappingErrorCode} >= {
        "core_memory_unavailable", "core_memory_inactive", "core_memory_schema_unsupported",
        "core_memory_lineage_invalid", "personality_source_field_missing", "personality_source_type_invalid",
        "personality_source_nonfinite", "personality_source_out_of_range", "personality_projection_invalid",
        "personality_mapper_internal_contract",
    }


def test_pure_package_imports_no_database_agent_or_fixture_code() -> None:
    import subprocess
    import sys

    probe = "import sys, app.ai.Cognitive.system.v0.personality_runtime_v0; print('\\n'.join(sys.modules))"
    loaded = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout.split()
    forbidden = ("sqlalchemy", "app.db", "app.services", "app.ai.chat", "app.ai.agent", "app.ai.prompts",
                 "app.ai.tools", "langchain", "langgraph", "redis")
    offenders = [name for name in loaded if name.startswith(forbidden) or "fixture" in name]
    assert offenders == []
