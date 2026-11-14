"""Learning's background work, for the worker started with python -m app.workers.

- ``run_once`` (docs/learning-system/01-implementation.md): remove raw captures past their retention (every
  PRUNE_SECONDS).
- ``assess_once`` (docs/learning-system/02-implementation.md): its own thread, since an assessment is one or more
  model calls: triage new observations, sweep for failures and late captures, then run one due assessment.
- ``lessons_once`` (docs/learning-system/03-implementation.md): its own thread: read one due assessment revision
  for lessons and hand the checked changes to memory.
- ``trials_once`` (docs/learning-system/04-implementation.md): its own thread, in desktop mode: bounded game
  trials, from investigation to assessment (services/learning_trials.run_once).
- ``trajectory_once`` (docs/learning-system/POST-AUDIT-01-implementation.md): its own thread, in desktop mode: her
  trajectory's new chunks pulled from the game pack and handed to the capture writer
  (services/trajectory_capture.run_once). No memory, no model call."""
from __future__ import annotations

import logging
import time

from app.db.session import SessionLocal
from app.services import learning_capture

logger = logging.getLogger(__name__)
PRUNE_SECONDS = 600.0
_last_prune = 0.0


def run_once() -> bool:
    global _last_prune
    if time.monotonic() - _last_prune >= PRUNE_SECONDS:
        _last_prune = time.monotonic()
        with SessionLocal() as db:
            removed = learning_capture.prune(db)
        if any(removed.values()):
            logger.info("learning capture: pruned %s", removed)
    return False


def assess_once() -> bool:
    from app.services import learning_assessment

    return learning_assessment.run_once()


def lessons_once() -> bool:
    from app.services import learning_lessons

    return learning_lessons.run_once()


def trials_once() -> bool:
    from app.services import learning_trials

    return learning_trials.run_once()


def trajectory_once() -> bool:
    from app.services import trajectory_capture

    return trajectory_capture.run_once()


__all__ = ["run_once", "assess_once", "lessons_once", "trials_once", "trajectory_once"]
