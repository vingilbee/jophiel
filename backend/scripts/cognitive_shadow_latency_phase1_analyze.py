"""Analyze one Phase 1 live shadow latency run through trace_summary.

The script emits a private local JSON artifact. It uses the content-minimized
``summarize_trace`` projection for stage decisions and telemetry, supplementing
it only with persisted timing primitives that the current summary does not yet
project: job capture time and per-attempt Realization latency.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.Cognitive.system.v0.trace_summary import reveal_final_candidate, summarize_trace
from app.db.models import CognitiveShadowJob, CognitiveShadowTrace
from app.db.session import SessionLocal


LIVE_INPUT = Path("/private/tmp/cognitive-phase1-latency-live-turns.json")
OUTPUT = Path("/private/tmp/cognitive-phase1-latency-analysis.json")


def milliseconds(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds() * 1000


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def distribution(values) -> dict:
    clean = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return {
        "n": len(clean),
        "median_ms": statistics.median(clean) if clean else None,
        "p95_ms": nearest_rank(clean, 0.95),
        "max_ms": max(clean) if clean else None,
    }


def mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def list_value(value) -> list:
    return value if isinstance(value, list) else []


def main() -> None:
    live = json.loads(LIVE_INPUT.read_text())
    live_rows = {row["turn_id"]: row for row in live["turns"]}
    turn_ids = list(live_rows)

    with SessionLocal() as session:
        records = session.execute(
            select(CognitiveShadowTrace, CognitiveShadowJob)
            .join(CognitiveShadowJob, CognitiveShadowJob.id == CognitiveShadowTrace.job_id)
            .where(CognitiveShadowTrace.turn_id.in_(turn_ids))
        ).all()

    by_turn = {str(trace.turn_id): (trace, job) for trace, job in records}
    turns = []
    for turn_id in turn_ids:
        live_row = live_rows[turn_id]
        if turn_id not in by_turn:
            turns.append({**live_row, "shadow_terminal": "missing"})
            continue
        trace, job = by_turn[turn_id]
        summary = summarize_trace(trace)
        realization_payload = mapping(trace.realization_trace_payload)
        realization_attempts = list_value(realization_payload.get("attempts"))
        realization_calls = [
            {
                "attempt": attempt.get("attempt_number"),
                "duration_ms": attempt.get("latency_ms"),
                "outcome": attempt.get("outcome"),
                "provider_error_code": attempt.get("provider_error_code"),
            }
            for attempt in realization_attempts
        ]
        appraisal_calls = [
            {
                "attempt": span.get("attempt_number"),
                "duration_ms": span.get("elapsed_ms"),
                "status": span.get("status"),
                "input_tokens": span.get("input_tokens"),
                "output_tokens": span.get("output_tokens"),
                "cached_input_tokens": span.get("cached_input_tokens"),
            }
            for span in summary["latency"]["stages"]
            if span.get("component") == "provider_attempt" and span.get("status") != "skipped"
        ]

        # Live Interpretation always has exactly one configured provider call
        # when it either completes or returns a semantic-parser typed failure.
        interpretation_ran = (
            summary["interpretation"]["status"] in {"ran", "not_persisted", "failed"}
            or mapping(summary.get("failure")).get("stage") == "pipeline.interpretation"
        )
        interpretation_calls = 1 if interpretation_ran else 0
        total_calls = interpretation_calls + len(appraisal_calls) + len(realization_calls)
        shadow_processing_ms = milliseconds(trace.completed_at, trace.started_at)
        capture_to_terminal_ms = milliseconds(trace.completed_at, job.captured_at)
        accepted = trace.realization_status == "accepted_shadow"

        turns.append({
            "label": live_row["label"],
            "turn_id": turn_id,
            "conversation_id": live_row["conversation_id"],
            "live_terminal": live_row["live_terminal"],
            "live_first_token_ms": live_row["live_first_token_ms"],
            "live_complete_ms": live_row["live_complete_ms"],
            "shadow_terminal": trace.terminal_status,
            "shadow_failure": summary["failure"],
            "shadow_queue_wait_ms": milliseconds(trace.started_at, job.captured_at),
            "shadow_processing_ms": shadow_processing_ms,
            "shadow_capture_to_terminal_ms": capture_to_terminal_ms,
            "validated_shadow_ready_ms": capture_to_terminal_ms if accepted else None,
            "interpretation_stage_ms": summary["timings_ms"]["core"].get("interpretation_ms"),
            "interpretation_call_count": interpretation_calls,
            "appraisal_implementation": summary["appraisal"].get("implementation"),
            "appraisal_calls": appraisal_calls,
            "realization_status": trace.realization_status,
            "realization_calls": realization_calls,
            "realization_total_ms": summary["timings_ms"]["realization"].get("total_ms"),
            "model_call_count": total_calls,
            "application_retry_count": max(0, len(realization_calls) - 1),
            "worker_attempt_count": job.attempts,
            "worker_retry_count": max(0, job.attempts - 1),
            "accepted_candidate_text": reveal_final_candidate(trace),
            "selected_actions": [move.get("action") for move in summary["action"].get("selected_moves", [])],
            "summary_schema": summary["summary_schema"],
            "latency_coverage": summary["latency"]["coverage"],
        })

    completed = [turn for turn in turns if turn.get("shadow_terminal") == "completed"]
    accepted = [turn for turn in completed if turn.get("realization_status") == "accepted_shadow"]
    appraisal_call_durations = [
        call["duration_ms"] for turn in turns for call in turn.get("appraisal_calls", [])
    ]
    realization_call_durations = [
        call["duration_ms"] for turn in turns for call in turn.get("realization_calls", [])
    ]

    output = {
        "run_id": live["run_id"],
        "user_id": live["user_id"],
        "turn_count": len(turns),
        "trace_count": len(by_turn),
        "terminal_counts": {
            status: sum(turn.get("shadow_terminal") == status for turn in turns)
            for status in sorted({turn.get("shadow_terminal") for turn in turns})
        },
        "realization_counts": {
            status: sum(turn.get("realization_status") == status for turn in turns)
            for status in sorted({turn.get("realization_status") for turn in turns if turn.get("realization_status")})
        },
        "accepted_candidate_count": len(accepted),
        "terminal_trace_visible_model_calls": sum(turn.get("model_call_count", 0) for turn in turns),
        "total_application_retries": sum(turn.get("application_retry_count", 0) for turn in turns),
        "total_worker_retries": sum(turn.get("worker_retry_count", 0) for turn in turns),
        "provider_error_call_count": sum(
            call.get("provider_error_code") is not None
            for turn in turns for call in turn.get("realization_calls", [])
        ) + sum(
            call.get("status") not in {None, "completed"}
            for turn in turns for call in turn.get("appraisal_calls", [])
        ),
        "distributions": {
            "live_first_token": distribution(turn.get("live_first_token_ms") for turn in turns),
            "live_complete": distribution(turn.get("live_complete_ms") for turn in turns),
            "shadow_queue_wait": distribution(turn.get("shadow_queue_wait_ms") for turn in turns),
            "shadow_processing": distribution(turn.get("shadow_processing_ms") for turn in turns),
            "shadow_capture_to_terminal": distribution(turn.get("shadow_capture_to_terminal_ms") for turn in turns),
            "validated_shadow_ready": distribution(turn.get("validated_shadow_ready_ms") for turn in accepted),
            "interpretation_stage": distribution(turn.get("interpretation_stage_ms") for turn in completed),
            "appraisal_provider_call": distribution(appraisal_call_durations),
            "realization_provider_call": distribution(realization_call_durations),
            "realization_total": distribution(turn.get("realization_total_ms") for turn in completed),
        },
        "turns": turns,
    }
    OUTPUT.write_text(json.dumps(output, indent=2))
    print(json.dumps({key: output[key] for key in (
        "run_id", "turn_count", "trace_count", "terminal_counts", "realization_counts",
        "accepted_candidate_count", "terminal_trace_visible_model_calls", "total_application_retries",
        "total_worker_retries", "provider_error_call_count",
        "distributions",
    )}, indent=2))


if __name__ == "__main__":
    main()
