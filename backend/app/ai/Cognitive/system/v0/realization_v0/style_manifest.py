"""Versioned numeric bands and observable delivery controls (``realization-style-manifest-v0.1``).

A model never receives ``warmth=0.8`` and invents its meaning.  Numeric
personality values are retained for provenance and compiled here into named
bands and concrete controls.  The mapping lives in this module, not in prose,
fixtures, or a provider prompt; changing any table below requires a new
``REALIZATION_STYLE_MANIFEST``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

from ..contracts.realization import REALIZATION_STYLE_MANIFEST, DeliveryBand

STYLE_MANIFEST_VERSION = REALIZATION_STYLE_MANIFEST
QUANTUM = Decimal("0.000001")

# The exact V0 partition over canonical six-place decimals.  Lower bounds are
# inclusive; the last band also includes 1.000000.  So 0.800000 is very_high.
BAND_PARTITION: tuple[tuple[Decimal, Decimal, DeliveryBand], ...] = (
    (Decimal("0.000000"), Decimal("0.200000"), DeliveryBand.VERY_LOW),
    (Decimal("0.200000"), Decimal("0.400000"), DeliveryBand.LOW),
    (Decimal("0.400000"), Decimal("0.600000"), DeliveryBand.MODERATE),
    (Decimal("0.600000"), Decimal("0.800000"), DeliveryBand.HIGH),
    (Decimal("0.800000"), Decimal("1.000000"), DeliveryBand.VERY_HIGH),
)


def canonical_unit_decimal(value: float | int | Decimal | str) -> Decimal:
    """A finite value in [0, 1] as its canonical six-place decimal (through decimal text, never binary)."""

    if isinstance(value, bool):
        raise ValueError("personality values are numbers")
    number = value if isinstance(value, Decimal) else Decimal(repr(value) if isinstance(value, float) else str(value))
    if not number.is_finite():
        raise ValueError("personality values must be finite")
    number = number.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    if number < 0 or number > 1:
        raise ValueError("personality values must be within [0, 1]")
    return number


def band_for(value: float | int | Decimal | str) -> DeliveryBand:
    number = canonical_unit_decimal(value)
    for low, high, band in BAND_PARTITION:
        if low <= number < high:
            return band
    return DeliveryBand.VERY_HIGH  # exactly 1.000000


# Which delivery controls each personality trait may change.  A trait never
# changes a control outside its declared set.
TRAIT_CONTROLS: Mapping[str, frozenset[str]] = {
    "warmth": frozenset({"warmth_band", "style_marker_budget"}),
    "directness": frozenset({"directness_band", "transition_style", "correction_style", "content_lead_style",
                             "question_tone"}),
    "playfulness": frozenset({"playfulness_band", "playfulness_intensity"}),
    "curiosity": frozenset({"curiosity_band", "follow_up_style"}),
}

# Dynamic State derived controls and the delivery controls each may change.
STATE_CONTROLS: Mapping[str, frozenset[str]] = {
    "response_energy": frozenset({"compression_preference"}),
    "repair_pressure": frozenset({"repair_explicitness"}),
    "clarification_pressure": frozenset({"question_directness"}),
    "exploration_readiness": frozenset({"question_openness"}),
    # Recorded as intentionally unused: no Realization V0 effect.
    "initiative_readiness": frozenset(),
    "slow_cognition_pressure": frozenset(),
}

_B = DeliveryBand
_LOW = (_B.VERY_LOW, _B.LOW)
_HIGH = (_B.HIGH, _B.VERY_HIGH)


def _by_band(low: str, moderate: str, high: str) -> Mapping[DeliveryBand, str]:
    table = {band: low for band in _LOW} | {_B.MODERATE: moderate, _B.UNKNOWN: moderate}
    return table | {band: high for band in _HIGH}


# Directness: clause shape within a move only.  It never reorders moves and
# never removes required uncertainty.
TRANSITION_STYLE = _by_band("cushioned", "plain", "minimal")
CORRECTION_STYLE = _by_band("gentle_explicit", "explicit", "direct_explicit")
CONTENT_LEAD_STYLE = _by_band("context_first", "balanced", "content_first")
QUESTION_TONE = _by_band("tentative", "neutral", "direct")
# Curiosity: phrasing of an already selected seek-information move only.
FOLLOW_UP_STYLE = _by_band("narrow", "neutral", "inviting")
# Playfulness: effective only with a selected social-play move.
PLAYFULNESS_INTENSITY = _by_band("subtle", "light", "light")
# Warmth: at most one bounded warmth marker, and only where a selected move permits one.
STYLE_MARKER_BUDGET: Mapping[DeliveryBand, int] = {
    _B.VERY_LOW: 0, _B.LOW: 0, _B.MODERATE: 1, _B.HIGH: 1, _B.VERY_HIGH: 1, _B.UNKNOWN: 0,
}
MAXIMUM_STYLE_MARKER_BUDGET = 1

# Dynamic State (qualitative bands from the current snapshot's derived controls).
_STATE_LOW = ("very_low", "low")
_STATE_HIGH = ("high", "very_high")


def state_choice(band: str | None, low: str, moderate: str, high: str) -> str:
    if band in _STATE_LOW:
        return low
    if band in _STATE_HIGH:
        return high
    return moderate


COMPRESSION = ("concise", "balanced", "balanced")          # response_energy: low, moderate, high
QUESTION_DIRECTNESS = ("standard", "standard", "direct")   # clarification_pressure
QUESTION_OPENNESS = ("narrow", "standard", "open")         # exploration_readiness
REPAIR_EXPLICITNESS = ("standard", "standard", "explicit")  # repair_pressure

# Discourse limits by compression preference.  The global configured maximum
# always caps the character budget.
SENTENCES_PER_MOVE: Mapping[str, int] = {"concise": 1, "balanced": 2, "expansive": 2}
CHARACTERS_PER_MOVE: Mapping[str, int] = {"concise": 160, "balanced": 280, "expansive": 400}
MAXIMUM_SENTENCES_CAP = 8

__all__ = [
    "BAND_PARTITION", "CHARACTERS_PER_MOVE", "COMPRESSION", "CONTENT_LEAD_STYLE", "CORRECTION_STYLE",
    "FOLLOW_UP_STYLE", "MAXIMUM_SENTENCES_CAP", "MAXIMUM_STYLE_MARKER_BUDGET", "PLAYFULNESS_INTENSITY",
    "QUESTION_DIRECTNESS", "QUESTION_OPENNESS", "QUESTION_TONE", "REPAIR_EXPLICITNESS", "SENTENCES_PER_MOVE",
    "STATE_CONTROLS", "STYLE_MANIFEST_VERSION", "STYLE_MARKER_BUDGET", "TRAIT_CONTROLS", "TRANSITION_STYLE",
    "band_for", "canonical_unit_decimal", "state_choice",
]
