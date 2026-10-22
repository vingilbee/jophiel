"""Opt-in real-model evaluation of source-backed history on the stored Lantern case
(docs/COLUMBINA_SOURCE_BACKED_RECALL.md "Acceptance"). Makes real model calls; run only when approved.

reconstruct: read-only on the isolated Memory Retest account. The production path
  (``recall_history.collect`` + ``finish``, real reader) runs on the focused follow-up question N times.
  Nothing is seeded: no expected values, fixture ids or context names reach it. Scoring here is
  evaluation-only and case-specific (it looks for 4/5/6 pm); it is not shipped code.
answer: each automatically rebuilt history, unchanged, goes into the answer path:
  fixed: the frozen bundle comparison's context (including the earlier wrong assistant answer), with the
    "relevant passages" recall block (0/5 before) plus the rebuilt history, as the tool would return it.
  live: the same context with the larger recall block and the real read tools (recall_history included),
    so the model decides whether to call it. Read-only; nothing is saved.
Current value and history completeness are scored separately; every answer is kept for human review.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
BACKEND = Path(__file__).resolve().parents[1]
REPORTS = BACKEND.parent / "docs" / "reports"
OWNER = UUID("0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9")
MANIFEST = REPORTS / "memory-bundle-comparison-manifest-2026-10-21.json"
EXPECTED = ["4", "5", "6"]  # evaluation only


def _times(text: str) -> list[str]:
    return re.findall(r"\b([456])\s*(?::00\s*)?p\.?m\b", text or "", re.I)


def _order(found: list[str]) -> list[str]:
    out: list[str] = []
    for t in found:
        if t not in out:
            out.append(t)
    return out


def score_account(account: dict) -> dict:
    steps = [s for s in account["steps"] if s["kind"] in ("first_recorded", "change", "correction")]
    values = _order([t for s in steps for t in _times(s["after"])])
    current = account.get("current") or {}
    return {"history_complete": values == EXPECTED, "sequence": values,
            "current_six": current.get("state") == "established" and "6" in _times(current.get("value", "")),
            "current_state": current.get("state")}


def score_answer(answer: str) -> dict:
    order = _order(_times(answer))
    denies = bool(re.search(r"(no|not|don't|doesn't|isn't)[^.]{0,60}(earlier|before|prior)[^.]{0,40}(time|record)",
                            answer, re.I))
    return {"mentions_4_5_6_in_order": order[:3] == EXPECTED, "order_seen": order,
            "six_present": "6" in order, "denies_earlier_screen": denies,
            "semantic_verdict": "requires human review"}


def reconstruct(output: Path, repeats: int) -> None:
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.services import recall_history

    manifest = json.loads(MANIFEST.read_text())
    question = manifest["history"][-1]["content"]
    settings = get_settings()
    rows = []
    for repeat in range(repeats):
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            run = recall_history.collect(db, user_id=OWNER, question=question, now=datetime.now(timezone.utc),
                                         zone="America/Vancouver")
            db.rollback()
        run = recall_history.finish(run)
        account = recall_history.account_json(run.account)
        for step in account["steps"] + ([account["current"]] if account["current"] else []):
            for cite in step["cites"]:  # for review only: the quoted words back
                cite["quote"] = run.expansion.by_label()[cite["passage"]].text[cite["start"]:cite["end"]]
        row = {"repeat": repeat + 1, "model": settings.recall_history_model,
               "reasoning_effort": settings.recall_history_reasoning_effort, "status": run.account.status,
               "score": score_account(account), "account": account, "rendered": run.rendered,
               "input_digest": run.input_digest,
               "expansion": {"seeds": [str(s) for s in run.expansion.seeds], "visited": run.expansion.visited,
                             "passages": [{"label": p.label, "node": str(p.node), "speaker": p.speaker,
                                           "context": p.context_label, "depth": p.depth} for p in run.expansion.passages],
                             "stop": run.expansion.stop, "truncated": run.expansion.truncated,
                             "gaps": run.expansion.gaps, "ms": run.expansion.ms, "queries": run.expansion.queries},
               "reader": {"ms": run.answer.latency_ms if run.answer else None,
                          "input_tokens": run.answer.input_tokens if run.answer else None,
                          "output_tokens": run.answer.output_tokens if run.answer else None,
                          "error": run.answer.error if run.answer else None}}
        rows.append(row)
        output.write_text(json.dumps({"schema": "recall-history-eval-reconstruct-v1", "question": question,
                                      "runs": rows}, indent=2, default=str))
        print(json.dumps({"repeat": row["repeat"], "status": row["status"], **row["score"],
                          "read_ms": row["reader"]["ms"], "expand_ms": row["expansion"]["ms"]}), flush=True)


async def answer(reconstructions: Path, output: Path, modes: list[str]) -> None:
    from app.ai.agent import BASE_TOOLS
    from app.ai.chat import complete_chat
    from app.ai.context import AgentRuntimeContext
    from app.ai.prompts import build_system_prompt
    from app.core.config import get_settings
    from app.services import recall_references

    assert not BASE_TOOLS, "Unexpected static tools would break isolation"
    manifest = json.loads(MANIFEST.read_text())
    rebuilt = json.loads(reconstructions.read_text())["runs"]
    settings = get_settings()
    result = {"schema": "recall-history-eval-answer-v1", "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
              "reconstructions": str(reconstructions), "model": settings.chat_model, "runs": []}
    for index, row in enumerate(rebuilt):
        for mode in modes:
            turn = None
            if mode == "fixed":
                recall = (manifest["conditions"]["relevant_passages"]
                          + "\n\nWhat your recall_history tool returned for this question:\n" + row["rendered"])
            else:
                recall = manifest["conditions"]["larger_bundle"]
                turn = recall_references.TurnRecall(
                    OWNER, None, "America/Vancouver", datetime.now(timezone.utc), None,
                    recall_references.RecallResult("matched"), recall, None,
                    tool_budget=settings.recall_tool_calls_per_reply)
            context = AgentRuntimeContext(user_id=UUID(int=0) if turn is None else OWNER, recall=recall,
                                          recall_turn=turn, **manifest["common_context"])
            prompt = build_system_prompt(context, [])
            reply = await complete_chat(messages=manifest["history"], context=context)
            entry = {"mode": mode, "reconstruction": index + 1, "answer": reply, "score": score_answer(reply),
                     "tools": (turn.tool_statuses if turn else None),
                     "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                     "system_prompt_chars": len(prompt)}
            result["runs"].append(entry)
            output.write_text(json.dumps(result, indent=2))
            print(json.dumps({k: entry[k] for k in ("mode", "reconstruction", "score", "tools")}), flush=True)


def main() -> None:
    import os

    # The tool under test, whatever the local .env says (read before settings are first loaded).
    os.environ["RECALL_ENABLED"] = "true"
    os.environ["RECALL_HISTORY_ENABLED"] = "true"
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="mode", required=True)
    rec = commands.add_parser("reconstruct")
    rec.add_argument("--output", type=Path, required=True)
    rec.add_argument("--repeats", type=int, default=5, choices=range(1, 11))
    ans = commands.add_parser("answer")
    ans.add_argument("--reconstructions", type=Path, required=True)
    ans.add_argument("--output", type=Path, required=True)
    ans.add_argument("--modes", nargs="+", choices=("fixed", "live"), default=["fixed", "live"])
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new path")
    if args.mode == "reconstruct":
        reconstruct(args.output, args.repeats)
    else:
        asyncio.run(answer(args.reconstructions, args.output, args.modes))


if __name__ == "__main__":
    main()
