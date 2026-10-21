"""Opt-in real-model evaluation of the recorded Lantern failure, split by stage.

Stage A reads the isolated fixture owner's database; no answer model runs.
Stage B uses frozen evidence and the production complete_chat entrypoint, with
no history, retrieval, working-memory view, or mutation tools. No chat is saved.
This is a case-specific evaluation harness, not a production history retriever.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUESTION = "What were Lantern's meeting times in the order I told you, and what is the current confirmed time and venue?"
EXPECTED = [4, 5, 6]


def stage_a(user_id):
    from sqlalchemy import select, text
    from app.ai.recall.contracts import RecallRequest
    from app.db.session import SessionLocal
    from app.db.models import Conversation, ChatMessage, MemoryEvidence, MemoryEvent, SemanticMemory
    from app.services import recall

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        request = RecallRequest(user_id=user_id, query=QUESTION, purpose="explicit_search",
                                intent="historical", now=datetime.now(timezone.utc), limit=12)
        result = recall.search(db, request)
        six = recall.search(db, replace(request, limit=6))
        records = [{"ref": str(r.ref), "content": r.content, "score": r.score,
                    "context": r.context_label} for r in result.records]
        # Coverage checks are fixture-specific. Merely covering numbers is not
        # treated as successful chronological reconstruction.
        coverage = {str(n): any(re.search(rf"\b{n}\s*pm\b", r.content, re.I)
                                for r in result.records) for n in EXPECTED}
        graph = defaultdict(list)
        for row in db.scalars(select(MemoryEvidence).where(MemoryEvidence.user_id == user_id)):
            if row.item_id and row.message_id:
                a, b = f"item:{row.item_id}", f"message:{row.message_id}"
                graph[a].append(b); graph[b].append(a)
        for row in db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id)):
            if row.item_id:
                graph[f"event:{row.id}"].append(f"item:{row.item_id}")
        facts = list(db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == user_id)))
        for row in facts:
            if row.source_message_id:
                graph[f"fact:{row.id}"].append(f"message:{row.source_message_id}")
        messages = {str(m.id): m for m in db.scalars(select(ChatMessage).join(Conversation).where(
            Conversation.user_id == user_id, ChatMessage.role == "user"))}
        queue = deque((str(r.ref), 0) for r in result.records)
        seen, sources = set(), []
        while queue and len(seen) < 128:
            node, depth = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            if node.startswith("message:") and node[8:] in messages:
                sources.append(messages[node[8:]])
            if depth < 6:
                queue.extend((n, depth + 1) for n in graph[node] if n not in seen)
        transitions = []
        for message in sorted(sources, key=lambda m: (m.created_at, m.conversation_sequence)):
            if "lantern" not in message.content.lower():
                continue
            match = re.search(r"from\s+(\d+)\s*pm\s+to\s+(\d+)\s*pm", message.content, re.I)
            if match:
                transitions.append({"old": int(match[1]), "new": int(match[2]),
                                    "source": str(message.id), "quote": match[0],
                                    "user_text": message.content, "said_at": str(message.created_at)})
        chain, continuous = [], True
        for transition in transitions:
            if not chain:
                chain.append(transition["old"])
            continuous &= chain[-1] == transition["old"]
            chain.append(transition["new"])
        returned = {str(r.ref) for r in result.records}
        current = []
        for fact in facts:
            if f"fact:{fact.id}" in returned and fact.status == "active" and "lantern" in fact.content.lower():
                match = re.search(r"\bat\s+(\d+)\s*pm\b", fact.content, re.I)
                if match:
                    current.append({"value": int(match[1]), "ref": f"fact:{fact.id}", "text": fact.content})
        passed = continuous and chain == EXPECTED and any(f["value"] == chain[-1] for f in current)
        db.rollback()
    return {"six_result_budget_control": {
                "purpose": "explicit historical search capped to six, not a full for_reply replay",
                "refs": [str(r.ref) for r in six.records],
                "value_coverage_only": {str(n): any(re.search(rf"\b{n}\s*pm\b", r.content, re.I)
                                                   for r in six.records) for n in EXPECTED}},
            "production_search": {"status": result.status, "records": records,
                                  "value_coverage_only": coverage, "coverage": result.coverage,
                                  "has_chronology_output": False},
            "bounded_evidence_link_reconstruction": {
                "method": "evaluation-only traversal + explicit from/to parser; not shipped recall code",
                "pass": passed, "sequence": chain, "continuous": continuous,
                "current_facts": current, "transitions": transitions, "visited_nodes": len(seen)}}


def frozen_bundles(fixture):
    # Stage B depends only on the archived fixture, never on Stage A results.
    # Validate the real fixture, then construct a minimal synthetic copy. Do
    # not transmit transcripts, names, original IDs, timestamps or preferences.
    events = {e["id"]: e for e in fixture["events"]}
    assert "4 pm" in events[20]["text"] and "5 pm" in events[20]["text"]
    assert "5 pm" in events[22]["text"] and "6 pm" in events[22]["text"]
    raw = [
        {"source": "fixture-event-1", "source_order": 1, "text":
         "Goal completed: Remember Fixture Club meets Saturdays at 4 pm in the test courtyard "
         "(User updated the meeting time to 5 pm; same place.)"},
        {"source": "fixture-event-2", "source_order": 2, "text":
         "Goal completed: Remember Fixture Club meets Saturdays at 5 pm in the test courtyard "
         "(User updated the meeting time again, replacing the 5 pm plan with 6 pm.)"},
    ]
    fact = next(f for f in fixture["facts"] if f["normalized_key"] == "routine:lantern_meeting")
    assert "6 pm" in fact["content"]
    raw.append({"source": "fixture-current-fact", "status": "active/current",
                "text": "Fixture Club meets Saturday at 6 pm in the test courtyard."})
    sources = [m for m in fixture["messages"] if m["role"] == "user" and
               (m["content"].startswith("Lantern update:") or m["content"].startswith("Another Lantern update"))]
    assert len(sources) == 2
    statements = [
        {"source": "fixture-statement-1", "source_order": 1,
         "text": "Fixture Club's Saturday meeting moved from 4 pm to 5 pm. Same test courtyard."},
        {"source": "fixture-statement-2", "source_order": 2,
         "text": "Fixture Club's Saturday meeting moved again, from 5 pm to 6 pm. Same test courtyard. This replaces 5 pm."},
    ]
    statements.append(raw[-1])
    return {"raw_event_evidence": raw, "source_statement_evidence": statements}


async def stage_b(user_id, fixture, repeats):
    from app.ai.chat import complete_chat
    from app.ai.context import AgentRuntimeContext
    from app.ai.prompts import build_system_prompt
    from app.core.config import get_settings
    rows = []
    for name, entries in frozen_bundles(fixture).items():
        for index in range(repeats):
            order = "chronological" if index % 2 == 0 else "reversed"
            evidence = entries if order == "chronological" else list(reversed(entries))
            recalled = "Memory search: matched. Fixed evidence for this isolated evaluation:\n" + json.dumps(evidence, indent=2)
            context = AgentRuntimeContext(user_id=UUID(int=0), user_name="Evaluation", recall=recalled)
            prompt = build_system_prompt(context, [])
            question = QUESTION.replace("Lantern", "Fixture Club")
            answer = await complete_chat(messages=[{"role": "user", "content": question}], context=context)
            # Conservative screening only. Full prose is retained for human
            # semantic assessment; mentions alone cannot prove an answer correct.
            mentions = [int(n) for n in re.findall(r"\b([456])\s*(?::00\s*)?p\.?m\.?\b", answer, re.I)]
            row = {"condition": name, "repeat": index + 1, "evidence_order": order,
                   "model": get_settings().chat_model, "question": question, "evidence": evidence,
                   "system_prompt": prompt, "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                   "answer": answer, "mentions_all_times_screen": set(EXPECTED) <= set(mentions),
                   "semantic_verdict": "requires human assessment of chronology and current value"}
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("condition", "repeat", "evidence_order", "answer")}), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--stage", choices=("recall", "answer", "all"), default="all")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new path")
    fixture = json.loads(args.fixture.read_text())
    if str(args.user_id) != fixture["user_id"]:
        parser.error("fixture owner differs from requested user")
    output = {"schema": "memory-two-stage-eval-v1", "expected_sequence": EXPECTED,
              "stage_a": stage_a(args.user_id) if args.stage != "answer" else None, "stage_b": [],
              "isolation": "No persisted chats, no answer-stage retrieval/tools/history/working view. Stage B sends only a minimal synthetic meeting fixture, not the archived source text."}
    args.output.write_text(json.dumps(output, indent=2, default=str))
    print("Stage A:", json.dumps(output["stage_a"], default=str), flush=True)
    if args.stage != "recall":
        output["stage_b"] = asyncio.run(stage_b(args.user_id, fixture, args.repeats))
    args.output.write_text(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
