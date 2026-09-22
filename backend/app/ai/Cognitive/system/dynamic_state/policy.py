"""Reviewable fixed-point policy tables for dynamic-state v0.1."""

from __future__ import annotations

from dataclasses import dataclass

from .models import QualitativeBand, SIGNED_KEYS, StateKey, StepBand

POLICY_VERSION = "dynamic-state-rules-v0.1"

# Integers are control values, not psychological measurements.
STEP_SCORES = {StepBand.NONE: 0, StepBand.WEAK: 15, StepBand.MODERATE: 35, StepBand.STRONG: 55}
SENSITIVITY_RATIOS = {
    "strongly_dampened": (1, 4),
    "dampened": (1, 2),
    "neutral": (1, 1),
    "amplified": (2, 1),
    "strongly_amplified": (5, 2),
}
MAXIMUM_STEPS = {"weak": 20, "moderate": 70, "strong": 100}
RULE_MAGNITUDE_RATIOS = {
    # The design fixture distinguishes these bounded regulatory effects from
    # otherwise moderate state movements.
    "activation.high-relevance.v1": (3, 5),
    "confidence.explicit-correction.v1": (4, 3),
    "strain.norm-boundary.v1": (3, 5),
}

UNSIGNED_BAND_SCORES = {
    "very_low": 5,
    "low": 25,
    "moderate": 50,
    "high": 70,
    "very_high": 95,
}
SIGNED_BAND_SCORES = {
    "strong_negative": -70,
    "negative": -20,
    "neutral": 0,
    "positive": 20,
    "strong_positive": 70,
}

DEFAULT_BASELINE_SCORES = {
    StateKey.VALENCE: 0,
    StateKey.ACTIVATION: 25,
    StateKey.EPISTEMIC_CONFIDENCE: 50,
    StateKey.CURIOSITY: 25,
    StateKey.ENGAGEMENT: 50,
    StateKey.GOAL_PRESSURE: 25,
    StateKey.SOCIAL_ALIGNMENT: 0,
    StateKey.INTERACTION_STRAIN: 25,
}


def score_for_band(state_key: StateKey, band: str) -> int:
    table = SIGNED_BAND_SCORES if state_key in SIGNED_KEYS else UNSIGNED_BAND_SCORES
    if band not in table:
        raise ValueError(f"band {band!r} is invalid for {state_key}")
    return table[band]


def band_for_score(state_key: StateKey, score: int) -> QualitativeBand:
    if state_key in SIGNED_KEYS:
        if score <= -50:
            return QualitativeBand.STRONG_NEGATIVE
        if score <= -10:
            return QualitativeBand.NEGATIVE
        if score < 10:
            return QualitativeBand.NEUTRAL
        if score < 50:
            return QualitativeBand.POSITIVE
        return QualitativeBand.STRONG_POSITIVE
    if score < 15:
        return QualitativeBand.VERY_LOW
    if score < 30:
        return QualitativeBand.LOW
    if score < 60:
        return QualitativeBand.MODERATE
    if score < 90:
        return QualitativeBand.HIGH
    return QualitativeBand.VERY_HIGH


def clamp(state_key: StateKey, score: int) -> int:
    return max(-100 if state_key in SIGNED_KEYS else 0, min(100, score))


def bounded_step(base: StepBand, sensitivity: str, maximum_step_band: str) -> int:
    numerator, denominator = SENSITIVITY_RATIOS[sensitivity]
    # Half-up integer rounding is explicit and stable for positive magnitudes.
    adjusted = (STEP_SCORES[base] * numerator + denominator // 2) // denominator
    return min(adjusted, MAXIMUM_STEPS[maximum_step_band])


@dataclass(frozen=True)
class DecayRule:
    eligible: bool
    time_scale_ms: int


DECAY_RULES = {
    StateKey.ACTIVATION: DecayRule(True, 60_000),
    StateKey.VALENCE: DecayRule(True, 600_000),
    StateKey.ENGAGEMENT: DecayRule(True, 900_000),
    StateKey.CURIOSITY: DecayRule(True, 900_000),
    StateKey.SOCIAL_ALIGNMENT: DecayRule(True, 3_600_000),
    StateKey.EPISTEMIC_CONFIDENCE: DecayRule(False, 0),
    StateKey.GOAL_PRESSURE: DecayRule(False, 0),
    StateKey.INTERACTION_STRAIN: DecayRule(False, 0),
}

PERSISTENCE_RATIOS = {"low": (1, 2), "moderate": (1, 1), "high": (2, 1)}
RECOVERY_RATIOS = {"low": (1, 2), "moderate": (1, 1), "high": (3, 2)}
RECOVERY_RULE_IDS = frozenset(
    {
        "alignment.verified-repair.v1",
        "engagement.verified-close.v1",
        "goal-pressure.verified-completion.v1",
        "strain.verified-repair.v1",
    }
)


def decayed_score(
    state_key: StateKey,
    score: int,
    baseline: int,
    elapsed_ms: int,
    persistence_band: str,
    *,
    active: bool,
    learnable: bool,
) -> int:
    rule = DECAY_RULES[state_key]
    if not rule.eligible or elapsed_ms == 0:
        return score
    if state_key == StateKey.CURIOSITY and active and learnable:
        return score
    if state_key == StateKey.ENGAGEMENT and active:
        return score
    persistence_num, persistence_den = PERSISTENCE_RATIOS[persistence_band]
    scale = rule.time_scale_ms * persistence_num // persistence_den
    # Piecewise rational retention avoids floating point and model-call timing.
    if elapsed_ms >= scale * 5:
        retained_num, retained_den = 1, 4
    elif elapsed_ms >= scale:
        retained_num, retained_den = 1, 3
    else:
        retained_num, retained_den = 2, 3
    delta = score - baseline
    retained_delta = (abs(delta) * retained_num + retained_den // 2) // retained_den
    return clamp(state_key, baseline + (-retained_delta if delta < 0 else retained_delta))


def unsigned_control_band(score: int) -> QualitativeBand:
    score = max(0, min(100, score))
    if score < 15:
        return QualitativeBand.VERY_LOW
    if score < 40:
        return QualitativeBand.LOW
    if score < 60:
        return QualitativeBand.MODERATE
    if score < 90:
        return QualitativeBand.HIGH
    return QualitativeBand.VERY_HIGH


def attention_band(weight: int) -> str:
    if weight >= 85:
        return "critical"
    if weight >= 60:
        return "high"
    if weight >= 30:
        return "moderate"
    return "low"
