"""Score the live semantic parser against the regression corpus.

    uv run python -m app.ai.Cognitive.system.v0.semantic_parser_regression [--model ID] [--offline] [--no-append]

Live mode builds the provider for the explicit ``--model`` (default
``gpt-4o-mini``, the live-shadow parser) with ``OPENAI_API_KEY`` and makes one
parser call per scored turn; ``--offline`` replays the reviewed frozen
candidates instead.  Each run appends exactly one
JSON line (versions, typed provenance, summary, per-turn scores) to the
append-only results file; earlier lines are never rewritten.  Only synthetic
corpus text is sent; nothing is logged.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.ai.Cognitive.system.v0.interpretation_v0.regression import (
    DEFAULT_CORPUS, DEFAULT_RESULTS, REGRESSION_SCORER_VERSION, FrozenCandidateModel, iter_turns, load_corpus,
    run_turn, summarize,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--model", default="gpt-4o-mini", help="explicit semantic-parser model id")
    parser.add_argument("--timeout", type=int, default=20, help="provider timeout in seconds")
    parser.add_argument("--offline", action="store_true", help="replay frozen candidates; no provider call")
    parser.add_argument("--no-append", action="store_true", help="print the summary without recording it")
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    provenance = None
    if args.offline:
        scores = [run_turn(turn, FrozenCandidateModel(turn.frozen_candidate)) for turn in iter_turns(corpus)]
    else:
        from app.ai.Cognitive.system.v0.semantic_parser_provider import OpenAISemanticCandidateModel
        from app.core.config import get_settings

        model = OpenAISemanticCandidateModel(
            model_id=args.model, api_key=get_settings().openai_api_key, timeout_seconds=args.timeout,
        )
        provenance = model.provenance.model_dump(mode="json")
        scores = [run_turn(turn, model) for turn in iter_turns(corpus)]
    summary = summarize(scores)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "offline_frozen" if args.offline else "live",
        "corpus_version": corpus["corpus_version"], "scorer_version": REGRESSION_SCORER_VERSION,
        "provenance": provenance, "summary": summary, "turns": [item.as_record() for item in scores],
    }
    print(json.dumps(summary, indent=2))
    for item in scores:
        if not item.passed:
            print(f"FAIL {item.case_id} [{item.failure_category}]: {'; '.join(item.mismatches)}")
    if not args.no_append:
        with Path(args.results).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return 0 if summary["passed"] == summary["turns"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
