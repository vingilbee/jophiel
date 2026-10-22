"""Evaluate the conversation Jev assessment (docs/SHARED_STATE_STEP4_SPEC.md, "Acceptance tests and evaluation").

    uv run python scripts/shared_state_assessment_eval.py run [--repeats 3]   # real Jev calls
    uv run python scripts/shared_state_assessment_eval.py show                 # the requests, no calls

Reads the labelled cases and the pass thresholds from docs/qa/shared-state-assessment/. ``run`` refuses while any
threshold is unset or the cases are still marked DRAFT: thresholds come before results. Reports confusion
matrices for both decisions (recall, thinking), false-negative rates for recall and thinking, the unassessed rate, and latency
p50/p95. The report holds case ids and labels, not message text.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.state import assessment as contract  # noqa: E402
from app.services import jev_assessment  # noqa: E402

QA = ROOT / "docs" / "qa" / "shared-state-assessment"


def _load() -> tuple[dict, dict]:
    return json.loads((QA / "cases.json").read_text()), json.loads((QA / "thresholds.json").read_text())


def _state(case: dict) -> dict:
    return contract.state(message=case["message"], recent=case.get("recent", []), interpretation=None,
                          evidence_lines=case.get("memories", []), capabilities=["conversation", "memory_search"])


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def run(repeats: int) -> int:
    cases, thresholds = _load()
    unset = [k for k, v in thresholds.items() if k != "status" and v is None]
    if not thresholds.get("status", "").startswith("AGREED"):
        print("Refusing to run: thresholds are not AGREED (set before results).")
        return 2
    if unset or cases.get("status", "").startswith("DRAFT"):
        print("Refusing to run: " + ("thresholds unset: " + ", ".join(unset) if unset else "")
              + ("; " if unset and cases["status"].startswith("DRAFT") else "")
              + ("cases still DRAFT (review the labels, then change status)" if cases["status"].startswith("DRAFT")
                 else ""))
        return 2
    if not jev_assessment.enabled():
        print("Jev isn't enabled: set SHARED_STATE_ASSESSMENT_ENABLED=true and TYPESAFE_API_KEY.")
        return 2
    rows, latencies = [], []
    for case in cases["cases"]:
        for i in range(repeats):
            a = jev_assessment.assess(trigger_ref=f"eval:{case['id']}:{i}", input_versions={}, state=_state(case))
            rows.append({"id": case["id"], "repeat": i, "status": a.status, "want_recall": case["recall"],
                         "got_recall": a.recall, "want_thinking": case["thinking"], "got_thinking": a.thinking,
                         "model": a.model, "latency_ms": a.latency_ms, "error": a.error})
            if a.latency_ms is not None:
                latencies.append(a.latency_ms)
    n = len(rows)
    recall_ok = sum(r["got_recall"] == r["want_recall"] for r in rows) / n
    thinking_ok = sum(r["got_thinking"] == r["want_thinking"] for r in rows) / n
    want_recall = [r for r in rows if r["want_recall"] == "required"]
    want_think = [r for r in rows if r["want_thinking"] == "required"]
    recall_fn = (sum(r["got_recall"] != "required" for r in want_recall) / len(want_recall)) if want_recall else None
    think_fn = (sum(r["got_thinking"] != "required" for r in want_think) / len(want_think)) if want_think else None
    no_recall = [r for r in rows if r["want_recall"] == "not_required"]
    no_think = [r for r in rows if r["want_thinking"] == "not_required"]
    recall_fp = (sum(r["got_recall"] == "required" for r in no_recall) / len(no_recall)) if no_recall else None
    think_fp = (sum(r["got_thinking"] == "required" for r in no_think) / len(no_think)) if no_think else None
    errors = sum(r["status"] != "ok" for r in rows) / n
    unassessed = sum(r["got_recall"] in (None, "uncertain") or r["got_thinking"] in (None, "uncertain")
                     for r in rows) / n
    measured = {"recall_accuracy": recall_ok, "thinking_accuracy": thinking_ok,
                "recall_false_negative_rate": recall_fn, "thinking_false_negative_rate": think_fn,
                "recall_false_positive_rate": recall_fp, "thinking_false_positive_rate": think_fp,
                "error_rate": errors,
                "unassessed_rate": unassessed, "latency_p50_ms": _p(latencies, 0.5),
                "latency_p95_ms": _p(latencies, 0.95)}
    checks = {
        "recall_accuracy_min": recall_ok >= thresholds["recall_accuracy_min"],
        "thinking_accuracy_min": thinking_ok >= thresholds["thinking_accuracy_min"],
        "recall_false_negative_rate_max": recall_fn is not None and recall_fn <= thresholds["recall_false_negative_rate_max"],
        "thinking_false_negative_rate_max": think_fn is not None and think_fn <= thresholds["thinking_false_negative_rate_max"],
        "recall_false_positive_rate_max": recall_fp is not None and recall_fp <= thresholds["recall_false_positive_rate_max"],
        "thinking_false_positive_rate_max": think_fp is not None and think_fp <= thresholds["thinking_false_positive_rate_max"],
        "error_rate_max": errors <= thresholds["error_rate_max"],
        "unassessed_rate_max": unassessed <= thresholds["unassessed_rate_max"],
        "latency_p95_ms_max": measured["latency_p95_ms"] is not None
                              and measured["latency_p95_ms"] <= thresholds["latency_p95_ms_max"],
    }
    report = {
        "at": datetime.now(timezone.utc).isoformat(), "policy_version": contract.POLICY_VERSION,
        "cases_policy_version": cases.get("policy_version"), "repeats": repeats, "calls": n,
        "thresholds": thresholds, "measured": measured, "passed": checks, "all_passed": all(checks.values()),
        "recall_confusion": {f"{w}->{g}": c for (w, g), c in Counter(
            (r["want_recall"], r["got_recall"]) for r in rows).items()},
        "thinking_confusion": {f"{w}->{g}": c for (w, g), c in Counter(
            (r["want_thinking"], r["got_thinking"]) for r in rows).items()},
        "errors": Counter(r["status"] for r in rows if r["status"] != "ok"),
        "rows": rows,
    }
    out = QA / f"results-{datetime.now():%Y-%m-%d-%H%M}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("measured", "passed", "all_passed", "recall_confusion",
                                             "thinking_confusion")}, indent=1, default=str))
    print(f"report: {out}")
    return 0 if report["all_passed"] else 1


def show() -> int:
    cases, _ = _load()
    for case in cases["cases"][:3]:
        print(json.dumps(contract.body("jev-latest", _state(case)), indent=1)[:1500])
    print(f"... {len(cases['cases'])} cases")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "show"))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    sys.exit(run(args.repeats) if args.command == "run" else show())
