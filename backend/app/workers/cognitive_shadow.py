"""Durable live-shadow cognition worker (observational only).

Claims one captured turn at a time, observes it with the canonical cognitive
pipeline, and persists exactly one immutable terminal record. It runs on its
own worker thread, so shadow CPU work never runs inside the chat worker or the
WebSocket. It imports no chat agent, prompt, tool, memory writer, outcome
worker, or fixture adapter, and publishes no chat event.

When ``cognitive_shadow_realization_enabled`` is set, a completed observation
is extended with a Realization V0 outcome (one bounded, tool-free model call
per attempt over the authorized plan) before the single atomic terminal write.
The candidate is shadow-only: it is never logged, published, or delivered.
"""
from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import (
    CognitiveShadowCapture,
    CognitiveShadowTerminalRecord,
    SegmentResetReason,
    SkipReason,
)
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.live_shadow.service import (
    ObservationContext,
    load_capture,
    observe,
    shadow_failure,
    unresolved_record,
    utc_now_text,
)
from app.core.config import get_settings
from app.db.models import CognitiveShadowJob
from app.db.session import SessionLocal
from app.services import cognitive_shadow as shadow_service
from app.services.cognitive_shadow import metrics

logger = logging.getLogger(__name__)

_PURGE_INTERVAL_SECONDS = 3600.0
_STATS_INTERVAL_SECONDS = 300.0
# None = not yet run in this process, so the first pass after startup is always
# eligible (time.monotonic() counts from boot, so 0.0 would not guarantee it).
_last_purge: float | None = None
_last_stats: float | None = None


def _unresolved(job: CognitiveShadowJob, started_at: str, **outcome) -> CognitiveShadowTerminalRecord:
    return unresolved_record(
        job_id=str(job.id), turn_id=str(job.turn_id), user_ref=str(job.user_id),
        conversation_ref=str(job.conversation_id), focus_message_id=str(job.user_message_id),
        capture_schema_version=job.capture_schema_version, capture_digest=job.capture_digest,
        started_at=started_at, **outcome,
    )


def _capture_matches_job(capture: CognitiveShadowCapture, job: CognitiveShadowJob) -> bool:
    return (
        capture.capture_id == str(job.id)
        and capture.turn_id == str(job.turn_id)
        and capture.user_ref == str(job.user_id)
        and capture.conversation_ref == str(job.conversation_id)
        and capture.focus_message_id == str(job.user_message_id)
        and capture.schema_version == job.capture_schema_version
        and capture.source_channel.value == job.source_channel
        and capture.generation_path.value == job.generation_path
        and capture.sampling_policy_version == job.sampling_policy_version
        and (job.chat_job_id is None or capture.chat_job_ref == str(job.chat_job_id))
    )


def build_terminal(job: CognitiveShadowJob, *, max_attempts: int) -> CognitiveShadowTerminalRecord:
    """Produce the job's terminal record. Raises only for transient failures."""

    started_at = utc_now_text()
    if job.attempts > max_attempts:
        return _unresolved(job, started_at, failure=shadow_failure(
            "worker", "attempts_exhausted",
            "The shadow job exhausted its bounded retry attempts.",
            attempt=job.attempts, retryable=False,
        ))
    payload = job.capture_payload
    if payload_digest(payload) != job.capture_digest:
        return _unresolved(job, started_at, failure=shadow_failure(
            "capture", "capture_digest_mismatch",
            "The stored capture does not match its recorded digest.", attempt=job.attempts,
        ))
    try:
        loaded = load_capture(payload)
    except ValidationError:
        return _unresolved(job, started_at, failure=shadow_failure(
            "capture", "capture_invalid", "The stored capture failed contract validation.",
            attempt=job.attempts,
        ))
    if isinstance(loaded, SkipReason):
        return _unresolved(job, started_at, skip_reason=loaded)
    if not _capture_matches_job(loaded, job):
        return _unresolved(job, started_at, failure=shadow_failure(
            "capture", "capture_ownership_mismatch",
            "The stored capture does not belong to its job.", attempt=job.attempts,
        ))

    with SessionLocal() as db:
        predecessor = shadow_service.load_predecessor(db, job)
    record = observe(
        loaded, predecessor,
        ObservationContext(
            job_id=str(job.id), attempt=job.attempts, capture_digest=job.capture_digest,
            started_at=started_at,
        ),
        pipeline_factory=lambda turn_id: shadow_service.cognitive_pipeline(turn_id, get_settings()),
    )
    settings = get_settings()
    if settings.cognitive_shadow_realization_enabled or (
        record.pipeline_trace is not None and record.pipeline_trace.orchestration is not None
    ):
        # Downstream of the finished deterministic trace; never changes it.
        record = realize_record(
            record, engine=shadow_service.realization_engine(settings),
            configured_maximum_characters=settings.cognitive_shadow_realization_max_characters,
            # v0.3 captures carry the dialogue turn context; content comes only from it.
            dialogue_context=loaded.dialogue.turn_context if loaded.dialogue is not None else None,
        )
    return record


def _record_metrics(
    record: CognitiveShadowTerminalRecord, wall_ms: float, *, omitted_inputs: tuple[str, ...] = ()
) -> None:
    """Content-free aggregates. Plan length, STOP, and action keys describe the
    shadow decision only; none of them is an outcome or a success signal."""

    code = (
        record.failure.code if record.failure is not None
        else record.skip_reason.value if record.skip_reason is not None
        else "ok"
    )
    metrics.increment("terminal_records", status=record.status.value, code=code)
    metrics.observe("worker_wall_ms", wall_ms)
    if record.realization_status.value != "not_requested":
        # Status and typed codes only: candidate text is never logged or exported.
        metrics.increment("realization_outcomes", status=record.realization_status.value)
        if record.realization_failure is not None:
            metrics.increment("realization_failures", code=record.realization_failure.code)
        if record.realization_timings is not None:
            metrics.observe("realization_total_ms", record.realization_timings.total_ms)
    if record.segment_reset_reason is not None:
        metrics.increment("segment_resets", reason=record.segment_reset_reason.value)
    for item in omitted_inputs:
        metrics.increment("missing_input_producers", input=item)
    if record.pipeline_trace is None:
        return
    trace = record.pipeline_trace
    for stage, value in trace.timings.model_dump().items():
        metrics.observe("pipeline_stage_ms", float(value), stage=stage)
    if record.worker_timings is not None:
        metrics.observe("pipeline_cpu_ms", record.worker_timings.pipeline_cpu_ms)
    metrics.observe("trace_payload_bytes", float(len(trace.model_dump_json())))
    plan = trace.action_selection.plan
    metrics.observe("action_plan_length", float(len(plan.moves)))
    metrics.increment("plan_terminations", termination=plan.termination.value)
    for move in plan.moves:
        metrics.increment("selected_action_keys", action_key=move.definition_ref.action_key)


def _maintenance(settings) -> None:
    """Retention purge and queue-depth logging, each at a bounded cadence."""

    global _last_purge, _last_stats
    now = time.monotonic()
    retention = settings.cognitive_live_shadow_retention_days
    if retention is not None and (_last_purge is None or now - _last_purge >= _PURGE_INTERVAL_SECONDS):
        _last_purge = now
        with SessionLocal() as db:
            purged = shadow_service.purge_expired(db, retention_days=retention)
        if purged:
            metrics.increment("retention_purged_jobs", purged)
            logger.info("Cognitive shadow retention purged %d captures", purged)
    if _last_stats is None or now - _last_stats >= _STATS_INTERVAL_SECONDS:
        _last_stats = now
        with SessionLocal() as db:
            stats = shadow_service.queue_stats(db)
        logger.info(
            "Cognitive shadow queue: pending=%d oldest_pending_age_seconds=%.1f",
            int(stats["pending"]), stats["oldest_pending_age_seconds"],
        )


def run_once() -> bool:
    """Process at most one shadow job. Returns True when a job was claimed."""

    settings = get_settings()
    if not settings.cognitive_live_shadow_enabled:
        # Kill switch: claim nothing. Captured jobs are retained unmodified.
        return False
    _maintenance(settings)
    with SessionLocal() as db:
        job = shadow_service.claim_next_job(
            db, lease_seconds=settings.cognitive_live_shadow_worker_lease_seconds,
        )
    if job is None:
        return False
    claim_token = job.claim_token
    started = time.perf_counter()
    max_attempts = settings.cognitive_live_shadow_max_attempts
    try:
        record = build_terminal(job, max_attempts=max_attempts)
        if record.failure is not None and record.failure.retryable:
            # A typed transient failure (semantic-parser timeout/transport):
            # retry the immutable capture with its stable code, and on the last
            # attempt persist that code as the terminal failure.
            if job.attempts < max_attempts:
                metrics.increment("transient_failures", code=record.failure.code)
                with SessionLocal() as db:
                    shadow_service.release_for_retry(
                        db, job_id=job.id, claim_token=claim_token, code=record.failure.code,
                        safe_message=record.failure.safe_message,
                    )
                return True
            record = type(record).model_validate(record.model_copy(update={
                "failure": record.failure.model_copy(update={"retryable": False}),
            }).model_dump(mode="python"))
        with SessionLocal() as db:
            persisted = shadow_service.persist_terminal(db, job=job, claim_token=claim_token, record=record)
    except Exception as exc:
        # Transient (database, predecessor still pending, worker infrastructure):
        # release the immutable capture for a bounded retry. Never recapture.
        code = "predecessor_pending" if isinstance(exc, shadow_service.PredecessorPending) else "transient_worker_error"
        logger.warning("Cognitive shadow job %s attempt %d failed (%s)", job.id, job.attempts, type(exc).__name__)
        metrics.increment("transient_failures", code=code)
        if job.attempts >= max_attempts:
            try:
                exhausted = _unresolved(job, utc_now_text(), failure=shadow_failure(
                    "persistence" if code == "transient_worker_error" else "worker", "attempts_exhausted",
                    "The shadow job exhausted its bounded retry attempts.",
                    attempt=job.attempts, retryable=False,
                ))
                with SessionLocal() as db:
                    if shadow_service.persist_terminal(db, job=job, claim_token=claim_token, record=exhausted):
                        _record_metrics(exhausted, (time.perf_counter() - started) * 1000)
                return True
            except Exception:
                logger.exception("Cognitive shadow job %s could not record exhaustion", job.id)
        with SessionLocal() as db:
            shadow_service.release_for_retry(
                db, job_id=job.id, claim_token=claim_token, code=code,
                safe_message="A transient shadow worker failure occurred; the capture will be retried.",
            )
        return True
    if not persisted:
        metrics.increment("stale_completions")
        logger.warning("Cognitive shadow job %s was reclaimed; stale result discarded", job.id)
        return True
    omitted = ()
    if record.segment_reset_reason != SegmentResetReason.LINEAGE_UNRESOLVED:
        omitted = tuple(job.capture_payload.get("omitted_inputs", ()))
    _record_metrics(record, (time.perf_counter() - started) * 1000, omitted_inputs=omitted)
    return True


def check_realization_configuration(settings=None) -> str | None:
    """Report an unusable realization configuration once, at worker startup.

    Content-free: only the stable configuration code is logged.  It never
    blocks the deterministic observation, which does not depend on a model.
    """

    settings = settings or get_settings()
    if not getattr(settings, "cognitive_shadow_realization_enabled", False):
        return None
    try:
        code = shadow_service.realization_configuration_code(settings)
    except Exception:  # noqa: BLE001 - a startup diagnostic must never stop the worker
        logger.warning("cognitive shadow realization configuration check failed")
        return None
    if code is not None:
        logger.warning("cognitive shadow realization unavailable: %s", code)
        metrics.increment("realization_configuration_unavailable", code=code)
    return code


def check_semantic_parser_configuration(settings=None) -> str | None:
    """Report an unusable semantic-parser configuration once, at worker startup.

    Content-free.  Observation still runs: each turn then records the same
    typed, non-retryable configuration failure instead of retrying.
    """

    settings = settings or get_settings()
    try:
        code = shadow_service.semantic_parser_configuration_code(settings)
    except Exception:  # noqa: BLE001 - a startup diagnostic must never stop the worker
        logger.warning("cognitive shadow semantic parser configuration check failed")
        return None
    if code is not None:
        logger.warning("cognitive shadow semantic parser unavailable: %s", code)
        metrics.increment("semantic_parser_configuration_unavailable", code=code)
    return code


def run_isolated() -> None:
    """Run only the shadow loop, in its own process, until SIGINT/SIGTERM."""

    import signal
    import threading

    from app.workers.runner import _work_loop

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    check_semantic_parser_configuration(settings)
    check_realization_configuration(settings)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: stop.set())
    _work_loop(
        "cognitive-shadow", run_once, settings.cognitive_live_shadow_queue_name, stop,
        settings.cognitive_live_shadow_worker_poll_seconds,
    )


if __name__ == "__main__":
    run_isolated()
