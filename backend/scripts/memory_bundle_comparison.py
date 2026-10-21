"""Freeze the failed synthetic test turn, then compare three evidence formats.

prepare: owner-scoped read-only DB inspection; no model generation.
run: frozen manifest only; production answer entrypoint, no retrieval or tools.
This evaluates selection/organization, not a production recall implementation.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
BACKEND = Path(__file__).resolve().parents[1]
REPORTS = BACKEND.parent / "docs" / "reports"
OWNER = UUID("0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9")
TURN = "7dc68cf6-7b24-42c8-b7a9-4ab0279c7db2"
RECEIPT = 26


def stamp(value):
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def working_view(snapshot, db, now, focus_id):
    """Approximate historical view using frozen item states + production formatting.

    Historical context last_active_at and pending-observation text weren't saved.
    Use last recorded per-context event/item time and omit pending text explicitly.
    """
    from sqlalchemy import select
    from app.db.models import WorkingItem, MemoryContext, MemoryEvidence
    from app.services import memory_manager as manager
    contexts = {c.id: c for c in db.scalars(select(MemoryContext).where(MemoryContext.user_id == OWNER))}
    current = {str(i.id): i for i in db.scalars(select(WorkingItem).where(WorkingItem.user_id == OWNER))}
    by_context = defaultdict(list)
    for saved in snapshot["items"]:
        if saved["status"] not in ("active", "open", "suspended", "needs_review"):
            continue
        live = current[saved["id"]]
        values = {column: getattr(live, column) for column in (
            "id", "context_id", "kind", "parent_id", "owner", "owed_to", "text", "done_when",
            "status", "status_reason", "epistemic", "created_at", "updated_at", "due_at")}
        for key in ("text", "done_when", "status"):
            values[key] = saved[key]
        values["status_reason"] = None
        values["created_at"] = stamp(saved["created_at"])
        values["updated_at"] = stamp(saved["updated_at"])
        by_context[live.context_id].append(SimpleNamespace(**values))
    sourced = set(db.scalars(select(MemoryEvidence.item_id).where(
        MemoryEvidence.user_id == OWNER, MemoryEvidence.role == "origin")))
    mine = by_context[focus_id]
    goals = manager._top_goals(mine)
    goals.sort(key=lambda g: (g.status != "active", -g.created_at.timestamp()))
    goal_lines = ["  " + manager._item_line(g, now, "Memory Retest", sourced, {})
                  for g in goals[:manager.VIEW_GOALS]]
    sections = [["Focus: " + contexts[focus_id].label], goal_lines or ["  No goal set here."]]
    for kind in ("commitment", "question", "intention"):
        sections.append(["  " + manager._item_line(i, now, "Memory Retest", sourced, {})
                         for i in mine if i.kind == kind][:manager.VIEW_ITEMS])
    events = [e for e in snapshot["events"] if e["context_id"] == str(focus_id)
              and e["kind"] not in ("item_added", "item_changed", "context_switched")]
    events = sorted(events, key=lambda e: e["id"])[-manager.VIEW_EVENTS:]
    sections.append(["  Recent: " + "; ".join(manager._ago(stamp(e["occurred_at"]), now) + " " + e["text"]
                                                for e in events)] if events else [])
    sections.append([])  # Pending observations: no historical text snapshot.
    others = []
    for context_id, items in by_context.items():
        if context_id == focus_id:
            continue
        top = manager._top_goals(items)
        rest = [i for i in items if i.kind != "goal"]
        parts = [f'goal [{manager.ref(g)}] "{g.text}"' for g in top[:2]]
        if rest:
            parts.append(f"{len(rest)} other open item{'s' if len(rest) != 1 else ''}")
        times = [stamp(e["occurred_at"]) for e in snapshot["events"] if e["context_id"] == str(context_id)]
        times += [i.updated_at for i in items]
        active_at = max(times)
        others.append("  " + contexts[context_id].label + " (last active " + manager._ago(active_at, now)
                      + "): " + "; ".join(parts))
    sections.extend([["Also open elsewhere:", *others[:manager.VIEW_OTHER]] if others else [], []])
    render = lambda: "\n".join(line for section in sections for line in section)
    for index in (5, 4, 7, 8, 6):
        if len(render()) > manager.VIEW_CHARS:
            sections[index] = []
    return render()


def prepare(output):
    from sqlalchemy import select, text
    from app.db.session import SessionLocal
    from app.db.models import ChatJob, RecallReference
    from app.services import recall, recall_references, memory_manager, cognitive_live
    from app.ai.Cognitive.simple.live import reading_text

    fixture = json.loads((REPORTS / "memory-retest-final.json").read_text())
    settled = json.loads((REPORTS / "memory-retest-settled.json").read_text())
    assert fixture["user_id"] == str(OWNER) == settled["user_id"]
    target = next(m for m in fixture["messages"] if m["id"] == TURN)
    history = [{"role": m["role"], "content": m["content"]} for m in fixture["messages"]
               if m["conversation_id"] == target["conversation_id"]
               and m["conversation_sequence"] <= target["conversation_sequence"]]
    logs = [json.loads(line) for line in (BACKEND / "var/cognitive_live.jsonl").read_text().splitlines()
            if str(OWNER) in line]
    logged = next(row for row in logs if row["message"] == target["content"])
    now = stamp(logged["at"])
    frozen_items = {i["id"]: i for i in settled["items"]}
    record_map, restoration = {}, []
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        references = list(db.scalars(select(RecallReference).where(
            RecallReference.user_id == OWNER, RecallReference.receipt_id == RECEIPT,
            RecallReference.relation == "supplied").order_by(RecallReference.id)))
        for reference in references:
            ref = recall_references._ref_of(reference)
            record = recall.read(db, OWNER, ref, include_evidence=True).records[0]
            restored = False
            if ref.kind == "item":
                saved = frozen_items[ref.id]
                record = replace(record, content=saved["text"] +
                                 (f" (done when: {saved['done_when']})" if saved["done_when"] else ""),
                                 status=saved["status"], updated_at=stamp(saved["updated_at"]),
                                 applicability="current" if saved["status"] in ("active", "open") else "no_longer_current",
                                 lifecycle=None if saved["status"] in ("active", "open") else record.lifecycle,
                                 fingerprint=recall._fingerprint("item", ref.id, saved["revision"],
                                                                saved["status"], saved["text"]))
                restored = True
            assert record.fingerprint == reference.fingerprint, f"Unavailable historical wording: {ref}"
            # Original previous-answer fallback uses its first four supplied IDs.
            record = replace(record, matched=("direct_reference",) if len(record_map) < 4 else ())
            record_map[str(ref)] = record
            restoration.append({"ref": str(ref), "fingerprint_matches": True, "item_state_restored": restored})
        job = db.scalar(select(ChatJob).where(ChatJob.user_id == OWNER, ChatJob.user_message_id == UUID(TURN)))
        awareness = job.payload.get("awareness")
        focus = memory_manager.focus_at(db, OWNER, now).id
        view = working_view(settled, db, now, focus)
        db.rollback()

    assert len(record_map) == 15
    header = "Memory search: matched. Fixed evidence; additional retrieval is disabled.\n"
    line = lambda record: recall._record_line(record, now, recall._zone("America/Vancouver"))
    full = header + "\n".join(line(r) for r in record_map.values())
    relevant_ids = [key for key in record_map if key in ("event:20", "event:22", "fact:f982b188-3a3c-43e3-9ebb-fbc1d6718212")]
    assert len(relevant_ids) == 3
    relevant = [record_map[key] for key in relevant_ids]
    reduced = header + "\n".join(line(r) for r in relevant)
    old_event, next_event = record_map["event:20"], record_map["event:22"]
    old = re.search(r"at (\d+) pm", old_event.content)
    middle = re.search(r"time to (\d+) pm", old_event.content)
    previous = re.search(r"at (\d+) pm", next_event.content)
    latest = re.search(r"with (\d+) pm", next_event.content)
    current = next(r for r in relevant if r.ref.kind == "fact")
    current_time = re.search(r"at (\d+) pm", current.content)
    assert old and middle and previous and latest and current_time
    assert middle[1] == previous[1] and latest[1] == current_time[1]
    reconstruction = {"history": [
        {"value": old[1] + " pm", "validity": "historical", "source": str(old_event.ref), "quote": old[0]},
        {"value": middle[1] + " pm", "validity": "historical", "source": str(old_event.ref), "quote": middle[0]},
        {"value": latest[1] + " pm", "validity": "current", "source": str(next_event.ref), "quote": latest[0]},
    ], "current_confirmation": {"source": str(current.ref), "quote": current.content}}
    organized = header + "Source-backed reconstruction:\n" + json.dumps(reconstruction, indent=2) + "\nOriginal supporting passages:\n" + reduced[len(header):]
    understanding = {"acts": {k: v >= .5 for k, v in logged["reading"]["acts"].items()},
                     "tone": max(logged["reading"]["tone"], key=logged["reading"]["tone"].get),
                     "request": max(logged["reading"]["request"], key=logged["reading"]["request"].get)}
    common = {"user_name": "Memory Retest", "awareness": awareness, "working_memory": view,
              "character_page": cognitive_live.character_page() if logged["character"] else None,
              "cognitive_reading": reading_text({"understand": understanding}) if logged["shown"]["reading"] else None,
              "conversation_notes": None}
    manifest = {"schema": "fixed-memory-bundle-comparison-v1", "test_data_origin":
                "All fixture conversations were generated by this assistant in the isolated Memory Retest account; these are fictional study/club/preferences, not the real user's personal records.",
                "historical_receipt": RECEIPT, "common_context": common, "history": history,
                "conditions": {"larger_bundle": full, "relevant_passages": reduced, "source_backed_reconstruction": organized},
                "source_backed_derivation": reconstruction, "restoration": restoration,
                "limitations": ["No exact full prompt/tool-call trace was saved.",
                    "All 15 supplied records are flattened into the recall block, rather than delivered in the original three tool calls.",
                    "Working view is approximate: per-context last-active timestamps inferred; pending-observation text unavailable.",
                    "Reading reconstructed from rounded logged probabilities; values are away from the decoding boundary.",
                    "Character page and prompt assembly use the current checkout, not a saved historical prompt.",
                    "Retrieval and mutation tools are disabled in every condition.",
                    "No new source passage or underlying fact is added in the reduced or organized conditions."]}
    output.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"manifest": str(output), "fingerprint_matches": len(restoration),
                      "restored_history_messages": len(history), "condition_chars": {k: len(v) for k, v in manifest["conditions"].items()}}))


async def run(manifest_path, output, repeats):
    from app.ai.context import AgentRuntimeContext
    from app.ai.chat import complete_chat
    from app.ai.prompts import build_system_prompt
    from app.ai.agent import BASE_TOOLS
    from app.core.config import get_settings
    assert not BASE_TOOLS, "Unexpected static tools would break isolation"
    manifest = json.loads(manifest_path.read_text())
    result = {"manifest": str(manifest_path), "manifest_sha256": digest(manifest_path.read_text()),
              "model": get_settings().chat_model, "reasoning_effort": get_settings().chat_reasoning_effort,
              "runs": [], "assessment": "pending semantic review of the saved answers"}
    names = list(manifest["conditions"])
    for repeat in range(repeats):
        # Rotate the condition order across rounds; each call has fresh messages.
        for name in names[repeat % 3:] + names[:repeat % 3]:
            context = AgentRuntimeContext(user_id=UUID(int=0), recall=manifest["conditions"][name],
                                          **manifest["common_context"])
            prompt = build_system_prompt(context, [])
            answer = await complete_chat(messages=manifest["history"], context=context)
            row = {"condition": name, "repeat": repeat + 1, "system_prompt": prompt,
                   "system_prompt_sha256": digest(prompt), "system_prompt_chars": len(prompt),
                   "answer": answer}
            result["runs"].append(row)
            output.write_text(json.dumps(result, indent=2))
            print(json.dumps({k: row[k] for k in ("condition", "repeat", "answer")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    prep = commands.add_parser("prepare"); prep.add_argument("--output", type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--manifest", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--repeats", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new path")
    if args.mode == "prepare":
        prepare(args.output)
    else:
        asyncio.run(run(args.manifest, args.output, args.repeats))


if __name__ == "__main__":
    main()
