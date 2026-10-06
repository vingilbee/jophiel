"""Send a content-safe live corpus through the production chat WebSocket path.

This script measures only the visible live assistant transport. Cognitive
Shadow latency and quality are read later from the durable trace through
``trace_summary``. It never reads or publishes shadow payloads, and it does not
grant shadow output live authority.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from websockets.asyncio.client import connect


BASE_URL = "http://127.0.0.1:58001"
WS_URL = "ws://127.0.0.1:58001/api/chat/ws"
ORIGIN = "http://127.0.0.1:54173"
OUTPUT = Path("/private/tmp/cognitive-phase1-latency-live-turns.json")
PASSWORD = "Synthetic-cognitive-latency-2026"


CONTINUOUS = (
    ("continuous-01-greeting", "Hi Columbina. I'd like to talk through a quick dinner idea with you."),
    ("continuous-02-proposal", "What vegetarian dinner sounds comforting for four people and could be discussed in a few simple steps?"),
    ("continuous-03-selection", "The chickpea stew idea sounds best. What makes it a good direction for this conversation?"),
    ("continuous-04-explanation", "Could you explain why canned chickpeas help keep the preparation practical?"),
    ("continuous-05-correction", "Small correction: it is for six people, not four, and it needs to be dairy-free."),
    ("continuous-06-follow-up", "Given that correction, what inexpensive side would fit the shared meal idea?"),
    ("continuous-07-description", "How would you describe the revised stew and side so the meal sounds inviting?"),
    ("continuous-08-substantial", "Talk me through a coordinated preparation order that could keep the meal relaxed and efficient."),
    ("continuous-09-summary", "Please summarize the constraints and the meal direction we have established in this conversation."),
    ("continuous-10-reflection", "What is the main tradeoff in the meal we chose, and why might it still work well for the group?"),
)

SINGLE_TURNS = (
    ("greeting-01", "Hello Columbina."),
    ("greeting-02", "Good morning. How is our conversation starting?"),
    ("greeting-03", "Hi there, it is nice to meet you."),
    ("greeting-04", "Thanks for being here to talk with me."),
    ("simple-01-identity", "What is your name?"),
    ("simple-02-date", "What is today's date?"),
    ("simple-03-protein", "In one sentence, what role does protein play in a meal?"),
    ("simple-04-fiber", "What does dietary fiber generally help with?"),
    ("simple-05-macros", "Can you explain macronutrients in plain language?"),
    ("simple-06-balanced", "What usually makes a vegetarian meal feel balanced?"),
    ("substantial-01-compare", "Compare a lentil soup and a chickpea rice bowl for a quick shared dinner, focusing on effort and flexibility."),
    ("substantial-02-options", "Discuss three dairy-free breakfast ideas and the practical tradeoff of each."),
    ("substantial-03-explain", "Explain how someone could think about protein, fiber, and convenience when choosing a weekday lunch."),
    ("substantial-04-plan", "Describe a conversational meal outline for six people using beans, rice, and vegetables, without performing any external action."),
    ("substantial-05-evaluate", "Evaluate whether vegetable chili or lentil curry better suits a casual group meal with mixed preferences."),
    ("substantial-06-summarize", "Give a concise overview of how to make a shared vegetarian dinner feel satisfying without relying on dairy."),
)

PAIRED = (
    (
        ("correction-01-setup", "I'm considering oatmeal with fruit for two people."),
        ("correction-01-change", "Correction: make that five people, and one person cannot have nuts."),
    ),
    (
        ("followup-01-setup", "Tell me briefly why beans can be useful in a vegetarian meal."),
        ("followup-01-expand", "Could you expand on that with one practical example?"),
    ),
    (
        ("followup-02-setup", "Compare brown rice and couscous at a high level."),
        ("followup-02-clarify", "For the quicker option you mentioned, what is the main convenience advantage?"),
    ),
)


async def run_turn(ws, *, label: str, content: str, conversation_id: str | None) -> dict:
    turn_id = str(uuid.uuid4())
    payload = {"type": "run", "turn_id": turn_id, "content": content}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id

    started = time.perf_counter()
    await ws.send(json.dumps(payload))
    first_token_ms = None
    response_characters = 0
    terminal_type = None
    returned_conversation_id = conversation_id
    frame_types: list[str] = []
    async with asyncio.timeout(120):
        while True:
            frame = json.loads(await ws.recv())
            frame_type = frame.get("type")
            frame_types.append(str(frame_type))
            if frame_type == "meta" and frame.get("conversation_id"):
                returned_conversation_id = frame["conversation_id"]
            if frame_type == "token":
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                response_characters += len(frame.get("content") or "")
            if frame_type in {"done", "error", "stopped"}:
                terminal_type = frame_type
                break

    return {
        "label": label,
        "turn_id": turn_id,
        "conversation_id": returned_conversation_id,
        "live_terminal": terminal_type,
        "live_first_token_ms": first_token_ms,
        "live_complete_ms": (time.perf_counter() - started) * 1000,
        "live_response_characters": response_characters,
        "frame_types": frame_types,
    }


async def main() -> None:
    run_id = uuid.uuid4().hex
    signup = {
        "name": "Cognitive Phase 1 Latency QA",
        "email": f"cognitive-phase1-{run_id}@example.com",
        "password": PASSWORD,
    }
    rows: list[dict] = []

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30, headers={"Origin": ORIGIN}) as client:
        response = await client.post("/api/auth/signup", json=signup)
        response.raise_for_status()
        user_id = response.json()["id"]
        response = await client.post("/api/auth/signin", json=signup)
        response.raise_for_status()
        cookie = {"Cookie": f"qa_session={client.cookies.get('qa_session')}"}

        async with connect(WS_URL, additional_headers=cookie, origin=ORIGIN) as ws:
            await ws.send(json.dumps({"type": "sense", "timezone": "America/Vancouver", "route": "/chat"}))

            conversation_id = None
            for label, content in CONTINUOUS:
                row = await run_turn(ws, label=label, content=content, conversation_id=conversation_id)
                conversation_id = row["conversation_id"]
                rows.append(row)
                OUTPUT.write_text(json.dumps({"run_id": run_id, "user_id": user_id, "turns": rows}, indent=2))
                print(json.dumps({key: row[key] for key in (
                    "label", "turn_id", "conversation_id", "live_terminal", "live_first_token_ms", "live_complete_ms",
                )}), flush=True)

            for label, content in SINGLE_TURNS:
                row = await run_turn(ws, label=label, content=content, conversation_id=None)
                rows.append(row)
                OUTPUT.write_text(json.dumps({"run_id": run_id, "user_id": user_id, "turns": rows}, indent=2))
                print(json.dumps({key: row[key] for key in (
                    "label", "turn_id", "conversation_id", "live_terminal", "live_first_token_ms", "live_complete_ms",
                )}), flush=True)

            for pair in PAIRED:
                conversation_id = None
                for label, content in pair:
                    row = await run_turn(ws, label=label, content=content, conversation_id=conversation_id)
                    conversation_id = row["conversation_id"]
                    rows.append(row)
                    OUTPUT.write_text(json.dumps({"run_id": run_id, "user_id": user_id, "turns": rows}, indent=2))
                    print(json.dumps({key: row[key] for key in (
                        "label", "turn_id", "conversation_id", "live_terminal", "live_first_token_ms", "live_complete_ms",
                    )}), flush=True)

    result = {
        "run_id": run_id,
        "user_id": user_id,
        "turn_count": len(rows),
        "continuous_turn_count": len(CONTINUOUS),
        "output": str(OUTPUT),
    }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
