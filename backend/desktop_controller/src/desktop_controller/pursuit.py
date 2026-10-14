"""Chasing a moving target inside the VM (plan D3, ``pursue``).

The driver sees a picture ~2 s old, so a sheep it aims at has moved by the time
the move runs. With ``pursue`` it names the target once; from then on this
module finds it in the sampler's pictures (~15 a second) by normalised
cross-correlation against the target's picture, and the scheduler turns toward
it and taps attack while it is centred. Plain numpy, no model.

Coordinates: the template and the samples are at the sample scale (half the
observation frame); positions returned are in observation-frame pixels, like
every other point.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .profile import Profile

from .reflexes import SAMPLE_SCALE  # noqa: E402  (templates are cut at the samples' scale)
# A match at least this good counts as seeing the target.
FOUND_SCORE = 0.55
# Near its last place it is searched first; a match elsewhere must be this good.
REACQUIRE_SCORE = 0.7
SEARCH_RADIUS = 48  # sample pixels
# When the match is this good, the template drifts toward what it looks like now
# (it grows as she closes in, turns, walks).
UPDATE_SCORE = 0.75
UPDATE_WEIGHT = 0.2
# A patch flatter than this (grey-level standard deviation) can't be told apart
# from anything else of its shade: never a match.
MIN_STD = 4.0


def template_size(size: tuple[int, int]) -> tuple[int, int]:
    return (max(4, round(size[0] * SAMPLE_SCALE)), max(4, round(size[1] * SAMPLE_SCALE)))


def template_pixels(event, profile: Profile) -> np.ndarray | None:
    """The event's template as a (h, w) float array, or None if it isn't one."""
    if not event.template:
        return None
    w, h = template_size(event.size)
    try:
        raw = base64.b64decode(event.template, validate=True)
    except ValueError:
        return None
    if len(raw) != w * h:
        return None
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float64)


def cut_template(image: Image.Image, point: tuple[int, int], size: tuple[int, int]) -> str:
    """The target's picture for a pursue: cut ``size`` around ``point`` from an
    observation frame, grey, at the sample scale, base64. Used by the backend."""
    w, h = size
    x, y = point
    box = (x - w // 2, y - h // 2, x - w // 2 + w, y - h // 2 + h)
    patch = image.convert("L").crop(box).resize(template_size(size), Image.Resampling.BILINEAR)
    return base64.b64encode(patch.tobytes()).decode()


def _ncc(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of ``template`` at every position where it
    fits inside ``image`` (top-left anchored), via FFT and running sums."""
    ih, iw = image.shape
    th, tw = template.shape
    t = template - template.mean()
    t_norm = float(np.sqrt((t * t).sum()))
    if t_norm < MIN_STD * np.sqrt(t.size):
        t_norm = 0.0  # a featureless template matches nothing
    out_h, out_w = ih - th + 1, iw - tw + 1
    if t_norm < 1e-6 or out_h <= 0 or out_w <= 0:
        return np.zeros((max(out_h, 1), max(out_w, 1)))
    shape = (ih + th - 1, iw + tw - 1)
    corr = np.fft.irfft2(np.fft.rfft2(image, shape) * np.fft.rfft2(t[::-1, ::-1], shape), shape)
    numerator = corr[th - 1:th - 1 + out_h, tw - 1:tw - 1 + out_w]

    def window_sums(a: np.ndarray) -> np.ndarray:
        c = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return c[th:, tw:] - c[:-th, tw:] - c[th:, :-tw] + c[:-th, :-tw]

    n = th * tw
    s1 = window_sums(image)
    s2 = window_sums(image * image)
    variance = np.maximum(s2 - s1 * s1 / n, 0.0)  # float64: float32 sums lose flat patches to rounding
    denominator = np.sqrt(variance) * t_norm
    flat = variance < (MIN_STD ** 2) * n
    return np.where(flat, 0.0, numerator / np.maximum(denominator, 1e-6))


@dataclass
class Sighting:
    x: float  # observation-frame pixels (target centre)
    y: float
    score: float


class Tracker:
    def __init__(self, template: np.ndarray, point: tuple[int, int]):
        self.template = template
        self.last = (point[0] * SAMPLE_SCALE, point[1] * SAMPLE_SCALE)  # sample pixels, centre
        self.first = True

    def locate(self, image: Image.Image) -> Sighting | None:
        grey = np.asarray(image.convert("L"), dtype=np.float64)
        th, tw = self.template.shape
        scores = _ncc(grey, self.template)
        if scores.size == 0:
            return None
        # Near where it was first: a sheep moves a little between samples.
        cx, cy = self.last[0] - tw / 2, self.last[1] - th / 2
        ys, xs = np.ogrid[:scores.shape[0], :scores.shape[1]]
        near = (xs - cx) ** 2 + (ys - cy) ** 2 <= SEARCH_RADIUS ** 2
        best_near = np.where(near, scores, -1.0)
        y, x = np.unravel_index(int(np.argmax(best_near)), scores.shape)
        score = float(best_near[y, x])
        if score < FOUND_SCORE:
            # Not near: anywhere, but only a clearly better match (the first
            # look also searches everywhere: the driver's picture is ~2 s old).
            y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
            score = float(scores[y, x])
            if score < (FOUND_SCORE if self.first else REACQUIRE_SCORE):
                return None
        self.first = False
        self.last = (x + tw / 2, y + th / 2)
        if score >= UPDATE_SCORE:
            patch = grey[y:y + th, x:x + tw]
            self.template = (1 - UPDATE_WEIGHT) * self.template + UPDATE_WEIGHT * patch
        return Sighting(self.last[0] / SAMPLE_SCALE, self.last[1] / SAMPLE_SCALE, score)
