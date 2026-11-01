"""Review verdicts on teacher samples: which ones are left out of training. Offline only.

The follow-through judge that used to flag samples here was dropped on 2026-10-29: 589 of the 615 flags it raised
were rejected in review (96% false alarms), and it never removed anything on its own. What stays is the reviewed
verdicts already on disk: a run's `follow_review.json` ({key: {"verdict": "confirmed" | "rejected" | "test_issue",
"note", "sid", "variant"}}). Only a sample confirmed there is left out of training, and only in the situation and
variant it was given for (`load_review`): the key covers the decision, not its context. A `test_issue` verdict marks a
held-out reference to score again without (`events_qwen.score`).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REVIEW_FILE = 'follow_review.json'


def key(d: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: d.get(k) for k in ('intention', 'work', 'target', 'resolution',
                                                            'what_to_communicate')},
                                     sort_keys=True).encode()).hexdigest()[:16]


def load_review(runs: list[Path]) -> dict[str, Any]:
    """Every run's verdicts, keyed by situation, variant and decision key."""
    return {f'{v["sid"]}|{v["variant"]}|{k}': v for r in runs if (r / REVIEW_FILE).exists()
            for k, v in json.loads((r / REVIEW_FILE).read_text()).items()}


def verdict(review: dict[str, Any], e: Any, d: dict[str, Any]) -> str | None:
    return (review.get(f'{e.sid}|{e.variant}|{key(d)}') or {}).get('verdict')


def left_out(review: dict[str, Any], e: Any, d: dict[str, Any]) -> bool:
    return verdict(review, e, d) == 'confirmed'


__all__ = ['REVIEW_FILE', 'key', 'load_review', 'verdict', 'left_out']
