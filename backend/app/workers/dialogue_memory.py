"""Durable Dialogue Working Memory update worker (shadow-only).

Claims one update job at a time with a lease and advances that conversation's
head to exactly the job's captured target, finishing the job in the same
transaction.  A stale worker (lost lease) writes nothing.  It imports no chat
agent, prompt, tool, or memory writer, and never touches the live response.
"""
from __future__ import annotations

import logging

from app.ai.Cognitive.system.v0.dialogue_memory_v0.errors import DialogueMemoryError
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services import dialogue_memory as service
from app.services.dialogue_memory import metrics

logger = logging.getLogger(__name__)


def run_once() -> bool:
    """Process at most one update job.  Returns True when a job was claimed."""

    settings = get_settings()
    if not settings.cognitive_dialogue_memory_enabled:
        return False  # kill switch: pending jobs wait unmodified
    with SessionLocal() as db:
        job = service.claim_next_job(db, lease_seconds=settings.cognitive_dialogue_memory_worker_lease_seconds)
    if job is None:
        return False
    claim_token = job.claim_token
    try:
        with SessionLocal() as db:
            outcome = service.process_job(db, job, claim_token=claim_token)
        metrics.increment("jobs_processed", outcome=outcome)
        if outcome == "stale":
            logger.warning("Dialogue memory job %s was reclaimed; nothing was written", job.id)
        return True
    except DialogueMemoryError as exc:
        # Content-free: the job id and the stable code only.
        metrics.increment("job_failures", code=exc.code.value)
        logger.warning("Dialogue memory job %s failed (%s)", job.id, exc.code.value)
        with SessionLocal() as db:
            if exc.failure.retryable and job.attempts < settings.cognitive_dialogue_memory_max_attempts:
                service.release_for_retry(db, job, claim_token, code=exc.code.value)
            else:
                service.fail_job(db, job, claim_token, code=exc.code.value, safe=exc.failure.safe_message)
        return True
    except Exception as exc:  # noqa: BLE001 - transient (database, worker infrastructure)
        metrics.increment("job_failures", code="transient_worker_error")
        logger.warning("Dialogue memory job %s attempt %d failed (%s)", job.id, job.attempts, type(exc).__name__)
        with SessionLocal() as db:
            if job.attempts < settings.cognitive_dialogue_memory_max_attempts:
                service.release_for_retry(db, job, claim_token, code="transient_worker_error")
            else:
                service.fail_job(db, job, claim_token, code="attempts_exhausted",
                                 safe="The dialogue memory update exhausted its bounded retry attempts.")
        return True


if __name__ == "__main__":
    from app.workers.runner import run
    run()
