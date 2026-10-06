"""Bounded ambiguity-gated personality adjustment."""

from __future__ import annotations

from dataclasses import replace

from ..contracts.interpretation import BiasAdjustment, HypothesisSupport
from .models import InterpretationBiasProfile
from .rules import HypothesisCandidate

BIAS_POLICY_VERSION = "interpretation-bias-policy-v0.1"
_LEVELS = tuple(HypothesisSupport)


class BiasPolicy:
    def apply(
        self,
        candidates: tuple[HypothesisCandidate, ...],
        profile: InterpretationBiasProfile,
    ) -> tuple[HypothesisCandidate, ...]:
        adjusted = []
        for item in candidates:
            if item.ambiguity_gate == 0 or item.support == HypothesisSupport.UNSUPPORTED:
                adjusted.append(item)
                continue
            bias_key, value = self._relevant_bias(item, profile)
            shift = 1 if value >= 0.75 else -1 if value <= 0.25 else 0
            # Ambiguity tolerance preserves alternatives; it never raises certainty.
            if bias_key == "ambiguity_tolerance" and shift > 0:
                shift = 0
            base_index = _LEVELS.index(item.base_support)
            final_index = max(0, min(len(_LEVELS) - 1, base_index + shift))
            final = _LEVELS[final_index]
            adjustment = BiasAdjustment(
                bias_key=bias_key,
                direction={-1: "decrease", 0: "unchanged", 1: "increase"}[shift],
                ordinal_shift=shift,
                ambiguity_gate=item.ambiguity_gate,
                policy_version=BIAS_POLICY_VERSION,
            )
            adjusted.append(replace(
                item, support=final, bias_adjustments=(adjustment,),
            ))
        return tuple(adjusted)

    @staticmethod
    def _relevant_bias(
        candidate: HypothesisCandidate,
        profile: InterpretationBiasProfile,
    ) -> tuple[str, float]:
        if candidate.hypothesis_key == "interpretation.goal.maintain_social_connection":
            return "social_affiliation_prior", profile.social_affiliation_prior
        if candidate.hypothesis_key in {
            "interpretation.pragmatic.literal", "interpretation.act.confirm",
        }:
            return "benign_prior", profile.benign_prior
        if candidate.hypothesis_key in {
            "interpretation.pragmatic.unknown", "interpretation.pragmatic.indirect",
        }:
            return "ambiguity_tolerance", profile.ambiguity_tolerance
        return "trust_prior", profile.trust_prior
