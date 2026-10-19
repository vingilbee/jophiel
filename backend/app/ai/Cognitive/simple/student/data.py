"""Teacher records -> training examples and the students' input features.

One accepted full-view sample = one example (main turns and mood replays). Bounded-teacher samples
are diagnostic only. Everything a student reads comes from the bundle (plus, for Student 2,
Student 1's outputs and the code stages computed from them).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import (
    ACTS, APPRAISALS, APPRAISALS_SIGNED, DIALS, FEELINGS, FLAGS, GROUPS, MAX_MEMORIES, MAX_NOTES, MOOD_KEYS,
    NO_SECOND, NOTE_KINDS, NOTE_UPDATES, REQUEST_KINDS, SCALE, SIGNED, TONES, WINDOW, Bundle,
    decide_targets, understand_targets,
)
from ..notice import NOTICE_WIDTH, numeric as notice_numeric
from .encode import DIM, Encoder

SLOTS = 1 + WINDOW + MAX_NOTES + MAX_MEMORIES
SLOT_EXTRA = 3 + len(NOTE_KINDS) + 1     # present, from user, position, note kind, note age
TIMES = ('morning', 'afternoon', 'evening', 'late_night')
PREVIOUS_WIDTH = 1 + len(ACTS) + len(TONES) + len(REQUEST_KINDS) + 2 + 4 + len(FEELINGS) + len(GROUPS) + len(GROUPS) + 1 + len(DIALS)
NUMERIC_WIDTH = NOTICE_WIDTH + len(MOOD_KEYS) + 1 + len(TIMES) + PREVIOUS_WIDTH
S1_OUT_WIDTH = len(ACTS) + len(TONES) + len(REQUEST_KINDS) + 2 + 4 + len(FEELINGS)
S2_EXTRA_WIDTH = S1_OUT_WIDTH + len(MOOD_KEYS) + len(FLAGS) + len(GROUPS)
RARE_MOVES = ('apologise_fix', 'say_no_limit', 'push_back', 'comfort')


@dataclass
class Example:
    episode: str
    family: str
    turn: int                 # turn index in the episode
    kind: str                 # 'main' or 'replay'
    sample: int
    weight: float
    bundle: Bundle
    understand: dict[str, Any]
    decide: dict[str, Any]
    mood_after: dict[str, float]
    flags: dict[str, bool]
    allowed: list[str]
    tags: set[str] = field(default_factory=set)


def load_episodes(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((run_dir / 'episodes').glob('*.json'))]


def _accepted(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    last = attempts[-1]
    return last if last.get('accepted') else None


def examples_from(episodes: list[dict[str, Any]], votes: dict[str, dict] | None = None) -> list[Example]:
    """Your spot-check votes (keyed episode:turn:sample) drop thumbs-down samples and up-weight thumbs-up."""
    votes = votes or {}
    out = []
    for ep in episodes:
        sc = ep['scenario']
        for t in ep['turns']:
            for kind, block in (('main', t), ('replay', t.get('replay'))):
                if not block:
                    continue
                bundle = Bundle.from_dict(block['bundle'])
                for attempts in block['samples']:
                    s = _accepted(attempts)
                    if not s:
                        continue
                    tags = set()
                    used = s['decide'].get('used_older_messages') or []
                    if used:
                        tags.add('older')
                        covered = t.get('coverage', {}).get(str(s['index']), [])
                        if kind == 'main' and not all(covered):
                            tags.add('uncovered')
                    if sc['situation'] == 'memory' and t['situation_here']:
                        tags.add('memory_turn')
                    if bundle.memories:
                        tags.add('has_memory')
                    if t.get('noisy'):
                        tags.add('noisy')
                    weight = 1.0 * (0.3 if 'uncovered' in tags else 1.0) * (0.5 if 'noisy' in tags else 1.0)
                    vote = votes.get(f'{sc["id"]}:{t["index"]}:{s["index"]}', {}).get('vote') if kind == 'main' else None
                    if vote == 'down':
                        continue
                    if vote == 'up':
                        tags.add('thumbs_up')
                        weight *= 1.5
                    out.append(Example(episode=sc['id'], family=sc['family'], turn=t['index'], kind=kind,
                                       sample=s['index'], weight=weight, bundle=bundle, understand=s['understand'],
                                       decide=s['decide'], mood_after=s['mood_after'], flags=s['flags'],
                                       allowed=s['allowed'], tags=tags))
    return out


# --- features ---------------------------------------------------------------------------------

def slot_texts(bundle: Bundle) -> list[str]:
    texts = [bundle.message]
    window = bundle.window[-WINDOW:]
    texts += [f'{"them" if m.speaker == "user" else "columbina"}: {m.text}' for m in window] + [''] * (WINDOW - len(window))
    notes = bundle.notes[:MAX_NOTES]
    texts += [n.text for n in notes] + [''] * (MAX_NOTES - len(notes))
    memories = bundle.memories[:MAX_MEMORIES]
    texts += list(memories) + [''] * (MAX_MEMORIES - len(memories))
    return texts


def slot_extras(bundle: Bundle) -> np.ndarray:
    extra = np.zeros((SLOTS, SLOT_EXTRA), np.float32)
    extra[0, 0] = 1.0
    extra[0, 1] = 1.0
    window = bundle.window[-WINDOW:]
    for i, m in enumerate(window):
        row = 1 + i
        extra[row, 0] = 1.0
        extra[row, 1] = float(m.speaker == 'user')
        extra[row, 2] = (len(window) - i) / WINDOW
    for i, n in enumerate(bundle.notes[:MAX_NOTES]):
        row = 1 + WINDOW + i
        extra[row, 0] = 1.0
        extra[row, 3 + NOTE_KINDS.index(n.kind)] = 1.0
        extra[row, -1] = min(1.0, (bundle.turn - n.opened_turn) / 20.0)
    for i, _ in enumerate(bundle.memories[:MAX_MEMORIES]):
        row = 1 + WINDOW + MAX_NOTES + i
        extra[row, 0] = 1.0
        extra[row, 2] = (i + 1) / MAX_MEMORIES
    return extra


def _onehot(index: int, size: int) -> list[float]:
    v = [0.0] * size
    v[index] = 1.0
    return v


def s1_vector(understand: dict[str, Any]) -> list[float]:
    t = understand_targets(understand)
    return (t['acts'] + _onehot(t['tone'], len(TONES)) + _onehot(t['request'], len(REQUEST_KINDS))
            + [t['about_her'], t['sure']] + t['appraisal'] + t['feelings'])


def previous_vector(previous: dict[str, Any] | None) -> list[float]:
    if not previous:
        return [0.0] * PREVIOUS_WIDTH
    d = decide_targets(previous['decide'])
    return [1.0] + s1_vector(previous['understand']) + _onehot(d['main'], len(GROUPS)) + _onehot(d['second'], len(GROUPS) + 1) + d['dials']


def numeric_vector(bundle: Bundle) -> list[float]:
    return (notice_numeric(bundle.notice) + [bundle.mood[k] for k in MOOD_KEYS] + [bundle.affinity]
            + _onehot(TIMES.index(bundle.time_of_day), len(TIMES)) + previous_vector(bundle.previous))


def s2_extra(s1: list[float], mood_after: dict[str, float], turn_flags: dict[str, bool], allowed: list[str]) -> list[float]:
    return (list(s1) + [mood_after[k] for k in MOOD_KEYS] + [float(turn_flags[k]) for k in FLAGS]
            + [float(g in allowed) for g in GROUPS])


@dataclass
class Batch:
    slots: np.ndarray        # (N, SLOTS, DIM + SLOT_EXTRA)
    numeric: np.ndarray      # (N, NUMERIC_WIDTH)


def context_batch(bundles: list[Bundle], encoder: Encoder) -> Batch:
    texts = [slot_texts(b) for b in bundles]
    flat = [t for row in texts for t in row if t]
    vectors = encoder.encode(flat)
    lookup = {t: vectors[i] for i, t in enumerate(flat)}
    slots = np.zeros((len(bundles), SLOTS, DIM + SLOT_EXTRA), np.float32)
    for i, (b, row) in enumerate(zip(bundles, texts, strict=True)):
        for j, t in enumerate(row):
            if t:
                slots[i, j, :DIM] = lookup[t]
        slots[i, :, DIM:] = slot_extras(b)
    numeric = np.array([numeric_vector(b) for b in bundles], np.float32)
    return Batch(slots=slots, numeric=numeric)


def s1_targets(examples: list[Example]) -> dict[str, np.ndarray]:
    rows = [understand_targets(e.understand) for e in examples]
    notes = np.full((len(examples), MAX_NOTES), -100, np.int64)
    for i, e in enumerate(examples):
        updates = {u['note']: u['update'] for u in e.understand.get('note_updates', [])}
        for j, n in enumerate(e.bundle.notes[:MAX_NOTES]):
            notes[i, j] = NOTE_UPDATES.index(updates.get(n.id, 'none'))
    return dict(
        acts=np.array([r['acts'] for r in rows], np.float32),
        tone=np.array([r['tone'] for r in rows], np.int64),
        request=np.array([r['request'] for r in rows], np.int64),
        scalars=np.array([[r['about_her'], r['sure']] + r['appraisal'] + r['feelings'] for r in rows], np.float32),
        notes=notes,
    )


def s2_targets(examples: list[Example]) -> dict[str, np.ndarray]:
    rows = [decide_targets(e.decide) for e in examples]
    return dict(main=np.array([r['main'] for r in rows], np.int64),
                second=np.array([r['second'] for r in rows], np.int64),
                dials=np.array([r['dials'] for r in rows], np.float32))


# --- predictions back to words (so the code stages run unchanged on student output) -----------

_SCALE_ITEMS = sorted(SCALE.items(), key=lambda kv: kv[1])
_SIGNED_ITEMS = sorted(SIGNED.items(), key=lambda kv: kv[1])


def nearest(value: float, items) -> str:
    return min(items, key=lambda kv: abs(kv[1] - value))[0]


def scalars_to_words(scalars: np.ndarray) -> dict[str, Any]:
    about, sure = scalars[0], scalars[1]
    appraisal = scalars[2:6]
    feelings = scalars[6:14]
    return dict(about_her=nearest(about, _SCALE_ITEMS), sure=nearest(sure, _SCALE_ITEMS),
                appraisal={APPRAISALS_SIGNED[0]: nearest(appraisal[0], _SIGNED_ITEMS),
                           APPRAISALS_SIGNED[1]: nearest(appraisal[1], _SIGNED_ITEMS),
                           APPRAISALS[0]: nearest(appraisal[2], _SCALE_ITEMS),
                           APPRAISALS[1]: nearest(appraisal[3], _SCALE_ITEMS)},
                feelings={f: nearest(feelings[i], _SCALE_ITEMS) for i, f in enumerate(FEELINGS)})


def prediction_to_understand(pred: dict[str, np.ndarray], i: int, bundle: Bundle) -> dict[str, Any]:
    words = scalars_to_words(pred['scalars'][i])
    note_updates = [{'note': n.id, 'update': NOTE_UPDATES[int(pred['notes'][i, j].argmax())]}
                    for j, n in enumerate(bundle.notes[:MAX_NOTES])]
    return dict(acts={a: bool(pred['acts'][i, k] >= 0.5) for k, a in enumerate(ACTS)},
                tone=TONES[int(pred['tone'][i].argmax())], request=REQUEST_KINDS[int(pred['request'][i].argmax())],
                note_updates=note_updates, **words)


def prediction_s1_vector(pred: dict[str, np.ndarray], i: int) -> list[float]:
    """Student 1's soft outputs as Student 2 reads them (probabilities, not rounded words)."""
    return (list(pred['acts'][i]) + list(pred['tone'][i]) + list(pred['request'][i]) + list(pred['scalars'][i]))


def prediction_to_decide(pred: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    second = int(pred['second'][i].argmax())
    return dict(main=GROUPS[int(pred['main'][i].argmax())], second=NO_SECOND if second == len(GROUPS) else GROUPS[second],
                dials={d: nearest(pred['dials'][i, k], _SCALE_ITEMS) for k, d in enumerate(DIALS)})
