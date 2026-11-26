"""Her hands on the phone (PLAN_PHONE_HANDS.md): claim a running job nobody holds and run it on a thread of its own
(app/ai/hands.py), so a long job never sits in front of another user's; end jobs left paused too long."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

from app.services import hands

log = logging.getLogger(__name__)

_running: dict[uuid.UUID, threading.Thread] = {}
_lock = threading.Lock()
_last_sweep = 0.0
SWEEP_SECONDS = 60.0


def _run(job_id: uuid.UUID, token: str) -> None:
    from app.ai.hands import run_job

    try:
        asyncio.run(run_job(job_id, token))
    except Exception:  # noqa: BLE001 - run_job ends its job itself; this is the last resort
        log.exception("hands: run of job %s broke", job_id)
        hands.release(job_id, token)
    finally:
        with _lock:
            _running.pop(job_id, None)


def run_once() -> bool:
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep > SWEEP_SECONDS:
        _last_sweep = now
        hands.expire_paused()
    token = uuid.uuid4().hex
    with _lock:
        alive = [job_id for job_id, thread in _running.items() if thread.is_alive()]
    job_id = hands.claim_next(token, exclude=alive)
    if job_id is None:
        return False
    thread = threading.Thread(target=_run, args=(job_id, token), name=f"hands-{str(job_id)[:8]}", daemon=True)
    with _lock:
        _running[job_id] = thread
    thread.start()
    return True
