"""Learning's background work, for the worker started with python -m app.workers.

- ``run_once`` (docs/learning-system/01-implementation.md): derive training examples from captured decisions, and
  remove raw captures past their retention (every PRUNE_SECONDS).
- ``assess_once`` (docs/learning-system/02-implementation.md): its own thread, since an assessment is one or more
  model calls: triage new observations, sweep for failures and late captures, then run one due assessment."""
from __future__ import annotations

import logging
import time

from app.db.session import SessionLocal
from app.services import learning_capture, training_examples

logger = logging.getLogger(__name__)
PRUNE_SECONDS = 600.0
_last_prune = 0.0


def run_once() -> bool:
    global _last_prune
    with SessionLocal() as db:
        made = training_examples.derive_pending(db)
    if time.monotonic() - _last_prune >= PRUNE_SECONDS:
        _last_prune = time.monotonic()
        with SessionLocal() as db:
            removed = learning_capture.prune(db)
        if any(removed.values()):
            logger.info("learning capture: pruned %s", removed)
    return made > 0


def assess_once() -> bool:
    from app.services import learning_assessment

    return learning_assessment.run_once()


__all__ = ["run_once", "assess_once"]
