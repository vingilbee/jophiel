"""The versioned, code-owned mapping manifest for Personality Runtime Mapper V0.

Every output field of all three profiles is declared here with its exact
source, transform, bounds, rounding, influence boundary, and rule id. The
mapper derives its values from these rules; nothing behavioral lives in an
undocumented conditional.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ..dynamic_state.models import StateKey
from .models import PERSONALITY_MAPPING_MANIFEST_VERSION, SOURCE_PATHS, source_path

PERSONALITY_DECIMAL_QUANTUM = Decimal("0.000001")
PERSONALITY_ROUNDING = ROUND_HALF_EVEN
ROUNDING_POLICY = "decimal-quantize-0.000001-round-half-even"
POLICY_CONSTANT = "policy_constant"
DYNAMIC_POLICY_SNAPSHOT = "dynamic_state.policy.DEFAULT_BASELINE_SCORES@personality-runtime-mapping-v0.1"

# An explicit no-effect control: the current CoreMemory schema has no reviewed
# source for these dimensions. It is a versioned policy constant, not a claim
# extracted from CoreMemory.
NEUTRAL_POLICY_CONSTANT = Decimal("0.500000")

# Pinned copy of Dynamic State's default baselines as they were when this
# mapper version was reviewed. A later change to Dynamic State defaults cannot
# silently change what this mapper version meant; a test pins the equality.
STATE_BASELINE_SNAPSHOT = MappingProxyType({
    StateKey.VALENCE: 0,
    StateKey.ACTIVATION: 25,
    StateKey.EPISTEMIC_CONFIDENCE: 50,
    StateKey.CURIOSITY: 25,
    StateKey.ENGAGEMENT: 50,
    StateKey.GOAL_PRESSURE: 25,
    StateKey.SOCIAL_ALIGNMENT: 0,
    StateKey.INTERACTION_STRAIN: 25,
})
NEUTRAL_STATE_CONTROL_BANDS = MappingProxyType({
    "persistence_band": "moderate",
    "recovery_band": "moderate",
    "sensitivity": "neutral",
    "maximum_step_band": "moderate",
})
NEUTRAL_INTERACTION_PATIENCE_BAND = "moderate"


class OutputProfile(StrEnum):
    INTERPRETATION = "interpretation"
    STATE = "state"
    ACTION = "action"


class Transform(StrEnum):
    DIRECT = "direct"
    MEAN_TWO = "mean_two"
    POLICY_CONSTANT = "policy_constant"
    DYNAMIC_POLICY_SNAPSHOT = "dynamic_policy_snapshot"


INTERPRETATION_BOUNDARY = "ambiguity-gated support adjustment only; at most one ordinal step"
STATE_BOUNDARY = "explicit neutral/default controls only; never evidence or contribution direction"
ACTION_BOUNDARY = "small post-gate personality-affinity score only; never eligibility, targets, facts, or safety"


class MappingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    output_profile: OutputProfile
    output_field: str
    source_fields: tuple[str, ...]
    source_paths: tuple[str, ...]
    transform: Transform
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    rounding: Literal["decimal-quantize-0.000001-round-half-even", "not_applicable"]
    influence_boundary: str
    constant: Decimal | None = None

    @model_validator(mode="after")
    def valid(self) -> MappingRule:
        if self.transform == Transform.DIRECT and len(self.source_fields) != 1:
            raise ValueError("a direct rule has exactly one source")
        if self.transform == Transform.MEAN_TWO and len(self.source_fields) != 2:
            raise ValueError("a mean_two rule has exactly two sources")
        if self.transform in {Transform.DIRECT, Transform.MEAN_TWO}:
            if self.source_paths != tuple(source_path(field) for field in self.source_fields):
                raise ValueError("source paths must be the declared CoreMemory paths")
            if self.constant is not None:
                raise ValueError("a sourced rule has no constant")
        if self.transform == Transform.POLICY_CONSTANT:
            if self.source_fields or self.source_paths != (POLICY_CONSTANT,) or self.constant is None:
                raise ValueError("a policy constant declares only its constant")
        if self.transform == Transform.DYNAMIC_POLICY_SNAPSHOT:
            if self.source_fields or self.source_paths != (DYNAMIC_POLICY_SNAPSHOT,):
                raise ValueError("a dynamic policy snapshot has no CoreMemory source")
        if not set(self.source_fields) <= set(SOURCE_PATHS):
            raise ValueError("unknown source field")
        return self


def _unit(rule_id, profile, field, transform, sources=(), boundary="", constant=None) -> MappingRule:
    return MappingRule(
        rule_id=rule_id, output_profile=profile, output_field=field, source_fields=tuple(sources),
        source_paths=(POLICY_CONSTANT,) if transform == Transform.POLICY_CONSTANT
        else tuple(source_path(item) for item in sources),
        transform=transform, lower_bound=Decimal(0), upper_bound=Decimal(1), rounding=ROUNDING_POLICY,
        influence_boundary=boundary, constant=constant,
    )


_I, _S, _A = OutputProfile.INTERPRETATION, OutputProfile.STATE, OutputProfile.ACTION

MAPPING_RULES: tuple[MappingRule, ...] = (
    _unit("personality.interpretation.benign-prior.mean-v1", _I, "benign_prior", Transform.MEAN_TWO,
          ("optimism_prior", "charitable_interpretation"), INTERPRETATION_BOUNDARY),
    _unit("personality.interpretation.trust-prior.direct-v1", _I, "trust_prior", Transform.DIRECT,
          ("trust_prior",), INTERPRETATION_BOUNDARY),
    _unit("personality.interpretation.threat-sensitivity.direct-v1", _I, "threat_sensitivity", Transform.DIRECT,
          ("threat_sensitivity",), INTERPRETATION_BOUNDARY),
    _unit("personality.interpretation.confirmation-neutral.v1", _I, "confirmation_tendency",
          Transform.POLICY_CONSTANT, (), INTERPRETATION_BOUNDARY, NEUTRAL_POLICY_CONSTANT),
    _unit("personality.interpretation.self-attribution-neutral.v1", _I, "self_attribution_tendency",
          Transform.POLICY_CONSTANT, (), INTERPRETATION_BOUNDARY, NEUTRAL_POLICY_CONSTANT),
    _unit("personality.interpretation.ambiguity-tolerance.direct-v1", _I, "ambiguity_tolerance", Transform.DIRECT,
          ("ambiguity_tolerance",), INTERPRETATION_BOUNDARY),
    _unit("personality.interpretation.social-affiliation.direct-v1", _I, "social_affiliation_prior",
          Transform.DIRECT, ("affiliation",), INTERPRETATION_BOUNDARY),
    # One reviewed rule covers the whole neutral Dynamic State profile.
    MappingRule(
        rule_id="personality.state.defaults-snapshot.v1", output_profile=_S, output_field="state_controls",
        source_fields=(), source_paths=(DYNAMIC_POLICY_SNAPSHOT,), transform=Transform.DYNAMIC_POLICY_SNAPSHOT,
        lower_bound=None, upper_bound=None, rounding="not_applicable", influence_boundary=STATE_BOUNDARY,
    ),
    MappingRule(
        rule_id="personality.state.defaults-snapshot.v1", output_profile=_S,
        output_field="interaction_patience_band", source_fields=(), source_paths=(DYNAMIC_POLICY_SNAPSHOT,),
        transform=Transform.DYNAMIC_POLICY_SNAPSHOT, lower_bound=None, upper_bound=None,
        rounding="not_applicable", influence_boundary=STATE_BOUNDARY,
    ),
    _unit("personality.action.curiosity.direct-v1", _A, "curiosity", Transform.DIRECT, ("curiosity",),
          ACTION_BOUNDARY),
    _unit("personality.action.warmth.direct-v1", _A, "warmth", Transform.DIRECT, ("warmth",), ACTION_BOUNDARY),
    _unit("personality.action.playfulness.direct-v1", _A, "playfulness", Transform.DIRECT, ("playfulness",),
          ACTION_BOUNDARY),
    _unit("personality.action.directness.direct-v1", _A, "directness", Transform.DIRECT, ("directness",),
          ACTION_BOUNDARY),
)


class MappingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: Literal["personality-runtime-mapping-v0.1"] = PERSONALITY_MAPPING_MANIFEST_VERSION
    rules: tuple[MappingRule, ...]

    @model_validator(mode="after")
    def unique_outputs(self) -> MappingManifest:
        outputs = [(rule.output_profile, rule.output_field) for rule in self.rules]
        if len(outputs) != len(set(outputs)):
            raise ValueError("every output field is mapped by exactly one rule")
        return self

    def rules_for(self, profile: OutputProfile) -> tuple[MappingRule, ...]:
        return tuple(rule for rule in self.rules if rule.output_profile == profile)


MANIFEST = MappingManifest(rules=MAPPING_RULES)

__all__ = [
    "DYNAMIC_POLICY_SNAPSHOT", "MANIFEST", "MAPPING_RULES", "MappingManifest", "MappingRule",
    "NEUTRAL_INTERACTION_PATIENCE_BAND", "NEUTRAL_POLICY_CONSTANT", "NEUTRAL_STATE_CONTROL_BANDS", "OutputProfile",
    "PERSONALITY_DECIMAL_QUANTUM", "PERSONALITY_ROUNDING", "POLICY_CONSTANT", "ROUNDING_POLICY",
    "STATE_BASELINE_SNAPSHOT", "Transform",
]
