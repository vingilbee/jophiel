"""4. Mood: five values that move toward what she felt and drift back to her baseline.

Plain code on purpose: readable, tunable from ``docs/characters/<name>.mood.json``, and shareable
with her game mind later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import (APPRAISALS_SIGNED, FEELINGS, MOOD_KEYS, SIGNED_MOOD, scale, signed)


@dataclass(frozen=True)
class MoodDynamics:
    baseline: dict[str, float]
    react: dict[str, float]
    drift: dict[str, float]

    @classmethod
    def load(cls, path: Path) -> 'MoodDynamics':
        data = json.loads(path.read_text())
        return cls(baseline=data['baseline'], react=data['react'], drift=data['drift'])


def _clip(key: str, value: float) -> float:
    low = -1.0 if key in SIGNED_MOOD else 0.0
    return max(low, min(1.0, value))


def feeling_pull(understand: dict) -> dict[str, float]:
    """Where this turn's feelings pull each mood value (same range as the value)."""
    f = {k: scale(understand['feelings'][k]) for k in FEELINGS}
    good = signed(understand['appraisal'][APPRAISALS_SIGNED[0]])
    warm = signed(understand['appraisal'][APPRAISALS_SIGNED[1]])
    lift = max(f['happy'], f['amused'], f['touched'])
    sting = max(f['hurt'], f['annoyed'])
    return dict(
        mood=max(-1.0, min(1.0, 0.5 * good + 0.5 * (lift - sting))),
        energy=max(0.0, min(1.0, 0.5 + 0.4 * max(f['amused'], f['happy']) - 0.6 * f['tired'])),
        interest=max(0.0, min(1.0, 0.3 + 0.7 * max(f['curious'], f['touched']))),
        tension=max(0.0, min(1.0, 0.8 * sting + 0.4 * f['unsure'])),
        closeness=warm,
    )


def update(mood: dict[str, float], understand: dict, dynamics: MoodDynamics) -> dict[str, float]:
    """One turn: move toward the feeling's pull, then drift toward baseline."""
    pull = feeling_pull(understand)
    out = {}
    for key in MOOD_KEYS:
        value = mood[key] + dynamics.react[key] * (pull[key] - mood[key])
        value += dynamics.drift[key] * (dynamics.baseline[key] - value)
        out[key] = round(_clip(key, value), 4)
    return out


# Bands: the words the teacher reads. Fixed thresholds, so the text is a pure view of the numbers.
_BANDS = {
    'mood': ((-1.0, 'low'), (-0.35, 'a bit down'), (-0.1, 'even'), (0.15, 'content'), (0.5, 'happy')),
    'energy': ((0.0, 'very tired'), (0.25, 'tired'), (0.45, 'steady'), (0.7, 'lively')),
    'interest': ((0.0, 'uninterested'), (0.3, 'mildly interested'), (0.55, 'interested'), (0.8, 'fascinated')),
    'tension': ((0.0, 'relaxed'), (0.25, 'a bit tense'), (0.5, 'tense'), (0.75, 'very tense')),
    'closeness': ((-1.0, 'distant'), (-0.3, 'a little distant'), (-0.05, 'neutral'), (0.2, 'warm'), (0.55, 'close')),
}


def band(key: str, value: float) -> str:
    word = _BANDS[key][0][1]
    for threshold, name in _BANDS[key]:
        if value >= threshold:
            word = name
    return word


# Named starting moods for scenarios (the plan's bands: calm, tense, tired, excited, low).
START_MOODS = {
    'calm': dict(mood=0.3, energy=0.55, interest=0.6, tension=0.1, closeness=0.1),
    'tense': dict(mood=-0.15, energy=0.55, interest=0.45, tension=0.6, closeness=-0.1),
    'tired': dict(mood=0.1, energy=0.15, interest=0.35, tension=0.15, closeness=0.05),
    'excited': dict(mood=0.65, energy=0.85, interest=0.8, tension=0.05, closeness=0.2),
    'low': dict(mood=-0.5, energy=0.35, interest=0.35, tension=0.3, closeness=0.0),
}
# The contrasting mood a replay swaps in.
REPLAY_SWAP = {'calm': 'tense', 'tense': 'calm', 'tired': 'excited', 'excited': 'tired', 'low': 'calm'}
