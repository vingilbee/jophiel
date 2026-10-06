"""The pure, deterministic Personality Runtime Mapper V0.

One immutable source compiles, in one call, into all three canonical profiles
(Interpretation biases, Dynamic State personality, Action personality) wrapped
in the live-shadow ``ShadowPersonalityProjection``. All values are derived
through the versioned manifest with decimal fixed-point arithmetic. There is no
I/O, clock, randomness, UUID4, or model call; the same source and installed
version yield byte-for-byte identical JSON.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import ValidationError

from ..contracts.actions import ActionPersonalityProfile
from ..dynamic_state.models import PersonalityControl, PersonalityProfile, StateKey
from ..interpretation_v0.models import InterpretationBiasProfile
from ..live_shadow.models import ShadowPersonalityProjection
from .manifest import (
    MANIFEST,
    NEUTRAL_INTERACTION_PATIENCE_BAND,
    NEUTRAL_STATE_CONTROL_BANDS,
    PERSONALITY_DECIMAL_QUANTUM,
    PERSONALITY_ROUNDING,
    STATE_BASELINE_SNAPSHOT,
    MappingManifest,
    MappingRule,
    OutputProfile,
    Transform,
)
from .models import (
    PERSONALITY_MAPPER_VERSION,
    PERSONALITY_MAPPING_MANIFEST_VERSION,
    CorePersonalitySourceV0,
    PersonalityMappingError,
    PersonalityMappingErrorCode,
)

REVIEW_REF = "PERSONALITY_RUNTIME_MAPPER_V0_IMPLEMENTATION.md"


def quantize(value: Decimal) -> Decimal:
    return value.quantize(PERSONALITY_DECIMAL_QUANTUM, rounding=PERSONALITY_ROUNDING)


def projection_id_for(core_memory_ref: str) -> str:
    return f"personality-runtime:{core_memory_ref}:{PERSONALITY_MAPPER_VERSION}"


class PersonalityRuntimeMapperV0:
    """Compile an authorized core-identity source into the three profiles."""

    def __init__(self, manifest: MappingManifest = MANIFEST) -> None:
        self.manifest = manifest

    def compile(self, source: CorePersonalitySourceV0) -> ShadowPersonalityProjection:
        if not isinstance(source, CorePersonalitySourceV0):
            raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT)
        try:
            return self._compile(source)
        except PersonalityMappingError:
            raise
        except (ValidationError, ValueError) as exc:
            # Nothing partial escapes: every profile is built and validated
            # before the one aggregate is returned.
            raise PersonalityMappingError(PersonalityMappingErrorCode.PROJECTION_INVALID) from exc
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT) from exc

    def _value(self, rule: MappingRule, source: CorePersonalitySourceV0) -> float:
        if rule.transform == Transform.DIRECT:
            result = getattr(source, rule.source_fields[0])
        elif rule.transform == Transform.MEAN_TWO:
            first, second = (getattr(source, field) for field in rule.source_fields)
            result = (first + second) / Decimal(2)
        elif rule.transform == Transform.POLICY_CONSTANT:
            result = rule.constant
        else:
            raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT)
        result = quantize(result)
        if not (rule.lower_bound <= result <= rule.upper_bound) or not result.is_finite():
            raise PersonalityMappingError(PersonalityMappingErrorCode.PROJECTION_INVALID)
        return float(result)

    def _unit_fields(self, profile: OutputProfile, source: CorePersonalitySourceV0) -> dict[str, float]:
        return {rule.output_field: self._value(rule, source) for rule in self.manifest.rules_for(profile)}

    def _state_fields(self) -> dict[str, object]:
        fields = {rule.output_field for rule in self.manifest.rules_for(OutputProfile.STATE)}
        if fields != {"state_controls", "interaction_patience_band"} or any(
            rule.transform != Transform.DYNAMIC_POLICY_SNAPSHOT
            for rule in self.manifest.rules_for(OutputProfile.STATE)
        ):
            raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT)
        if set(STATE_BASELINE_SNAPSHOT) != set(StateKey):
            # A Dynamic State key this mapper version never reviewed.
            raise PersonalityMappingError(PersonalityMappingErrorCode.INTERNAL_CONTRACT)
        return {
            "interaction_patience_band": NEUTRAL_INTERACTION_PATIENCE_BAND,
            "state_controls": {
                key: PersonalityControl(baseline_score=STATE_BASELINE_SNAPSHOT[key], **NEUTRAL_STATE_CONTROL_BANDS)
                for key in StateKey
            },
        }

    def _compile(self, source: CorePersonalitySourceV0) -> ShadowPersonalityProjection:
        core_ref = source.core_memory_ref
        projection_id = projection_id_for(core_ref)
        interpretation = InterpretationBiasProfile(
            profile_id=f"{projection_id}:interpretation",
            compiler_version=PERSONALITY_MAPPER_VERSION,
            source_core_memory_ref=core_ref,
            **self._unit_fields(OutputProfile.INTERPRETATION, source),
        )
        state = PersonalityProfile(
            profile_id=f"{projection_id}:state",
            mapper_version=PERSONALITY_MAPPER_VERSION,
            **self._state_fields(),
        )
        action = ActionPersonalityProfile(
            profile_id=f"{projection_id}:action",
            mapper_version=PERSONALITY_MAPPER_VERSION,
            source_core_memory_ref=core_ref,
            status="known",
            **self._unit_fields(OutputProfile.ACTION, source),
        )
        projection = ShadowPersonalityProjection(
            projection_kind="runtime_mapper",
            projection_id=projection_id,
            projection_version=PERSONALITY_MAPPER_VERSION,
            scope="core_identity",
            review_ref=REVIEW_REF,
            provenance=PERSONALITY_MAPPING_MANIFEST_VERSION,
            core_memory_ref=core_ref,
            interpretation_biases=interpretation,
            state_personality=state,
            action_personality=action,
        )
        # The exact persisted form must survive a JSON round trip unchanged.
        if ShadowPersonalityProjection.model_validate_json(projection.model_dump_json()) != projection:
            raise PersonalityMappingError(PersonalityMappingErrorCode.PROJECTION_INVALID)
        return projection


__all__ = ["PersonalityRuntimeMapperV0", "REVIEW_REF", "projection_id_for", "quantize"]
