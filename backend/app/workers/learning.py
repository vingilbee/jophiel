"""Learning's background work, for the worker started with python -m app.workers.

- ``run_once`` (docs/learning-system/01-implementation.md): remove raw captures past their retention (every
  PRUNE_SECONDS).
- ``trajectory_once`` (docs/learning-system/POST-AUDIT-01-implementation.md): its own thread, in desktop mode: her
  trajectory's new chunks pulled from the game pack and handed to the capture writer
  (services/trajectory_capture.run_once). No memory, no model call.
- ``practice_once`` (tool learning, 2026-11-26): her practice notes from a settled stretch of her hands
  (services/practice.run_once)."""
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


def trajectory_once() -> bool:
    from app.services import trajectory_capture

    return trajectory_capture.run_once()


def practice_once() -> bool:
    from app.services import practice

    return practice.run_once()


__all__ = ["run_once", "trajectory_once", "practice_once"]
