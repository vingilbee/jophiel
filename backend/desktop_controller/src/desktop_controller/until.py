"""Local evaluation of ``until`` conditions (plan §7.2).

The controller measures, for every captured frame and every region a decision
watches, how much of the region differs from (a) the frame at decision start and
(b) the previous frame. Those two ratios are all this module needs, so the same
logic runs against real capture and against the fake controller's scripted
samples. Producing the ratios from pixels belongs to the capture layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import ChangedCondition, StaticCondition, Until

# A region whose frame-to-frame change stays at or below this is "not moving".
STATIC_EPSILON = 0.01


@dataclass(frozen=True)
class RegionSample:
    changed_since_start: float
    changed_since_prev: float


@dataclass(frozen=True)
class UntilHit:
    index: int
    elapsed_ms: int


class UntilTracker:
    def __init__(self, until: Until, started_at_ms: int):
        self._conditions = until.any
        self._started = started_at_ms
        # Decision start counts as the last movement, so "static" needs a full
        # for_ms of stillness observed after the decision began.
        self._last_motion = {c.region: started_at_ms for c in self._conditions}

    @property
    def regions(self) -> frozenset[str]:
        return frozenset(c.region for c in self._conditions)

    def update(self, now_ms: int, samples: dict[str, RegionSample]) -> UntilHit | None:
        for region, sample in samples.items():
            if region in self._last_motion and sample.changed_since_prev > STATIC_EPSILON:
                self._last_motion[region] = now_ms
        for i, cond in enumerate(self._conditions):
            sample = samples.get(cond.region)
            if isinstance(cond, ChangedCondition):
                if sample is not None and sample.changed_since_start > cond.threshold:
                    return UntilHit(index=i, elapsed_ms=now_ms - self._started)
            elif isinstance(cond, StaticCondition):
                if sample is not None and now_ms - self._last_motion[cond.region] >= cond.for_ms:
                    return UntilHit(index=i, elapsed_ms=now_ms - self._started)
        return None
