"""Real-model conversational gate for memory stage 5 (docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): her actual
replies, with her actual tools, on topics unlike the fixtures. Each run gets a disposable schema on a throwaway
database; nothing touches the dev database.

    PROBE_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55433/memtest \\
        uv run python scripts/memory_stage5_probe.py [runs per scenario, default 3] [scenario ...]

Scenarios: said_exact, said_paraphrase, user_said, said_missing, rundown, forget_history. Each records stored ->
supplied -> said: the tool calls she made, what the reply's receipt was given, the reply, and the checks, printed
and appended to var/memory_stage5_probe.jsonl. Automatic checks are word-level; read the replies too.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import session as app_session  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    ChatMessage, Conversation, EpisodicMemory, MemoryEntity, MemoryForget, MemoryJob, SemanticMemory, User, WorkingItem,
)

ZONE = "Europe/London"
OUT = Path(__file__).resolve().parents[1] / "var" / "memory_stage5_probe.jsonl"
SUNSET_Q = "why does the sky go red at sunset?"
SUNSET_A = ("At sunset the light has to cross much more air to reach you, so most of the blue gets scattered "
            "away on the way and mostly the reds and oranges are left. It's called Rayleigh scattering.")
BOILER = "My landlord still hasn't fixed the boiler, it clanks every morning at six like someone hitting a pipe."
THERAPY = "I've been seeing a therapist for my anxiety since the spring, every Tuesday evening."
FORGET = "hey, can you forget what I told you about the therapist and my anxiety? I'd rather you didn't keep that"
NEVER_SAID = re.compile(r"\b(volcano|volcanoes|iceland)\b", re.I)


# --------------------------------------------------------------------------- #
# A disposable world
# --------------------------------------------------------------------------- #


class World:
    def __init__(self, url: str):
        self.schema = "probe5_" + uuid.uuid4().hex[:12]
        self.admin = create_engine(url)
        with self.admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={self.schema}"})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        app_session.SessionLocal.configure(bind=self.engine)
        with self.factory() as db:
            from tests.db_support import columbina_core_memory

            db.add(columbina_core_memory())
            self.user = User(name="Sam", email=f"sam-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
            db.add(self.user)
            db.commit()

    def close(self):
        self.engine.dispose()
        with self.admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()

    def conversation(self, title=None, days_ago=0):
        from app.services import chat as chat_service

        with self.factory() as db:
            conv = chat_service.get_or_create_conversation(db, user_id=self.user.id, conversation_id=None)
            conv.title = title
            db.commit()
            return conv

    def said(self, conv, role, content, days_ago=0.0):
        from app.services import chat as chat_service

        with self.factory() as db:
            conv = db.get(Conversation, conv.id)
            msg = chat_service.add_message(db, conversation=conv, role=role, content=content, turn_id=uuid.uuid4())
            when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
            db.execute(update(ChatMessage).where(ChatMessage.id == msg.id).values(created_at=when))
            db.commit()
            return msg

    def exchange(self, conv, user_text, reply_text, days_ago=0.0):
        """A finished exchange in the past: two linked messages and its memory job (left pending)."""
        from app.services import chat as chat_service, long_term_memory

        with self.factory() as db:
            conv = db.get(Conversation, conv.id)
            asked = chat_service.add_message(db, conversation=conv, role="user", content=user_text,
                                             turn_id=uuid.uuid4())
            answer = chat_service.add_message(db, conversation=conv, role="assistant", content=reply_text,
                                              turn_id=asked.turn_id)
            when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
            db.execute(update(ChatMessage).where(ChatMessage.id.in_([asked.id, answer.id])).values(created_at=when))
            job = long_term_memory.schedule_consolidation(db, user_id=self.user.id, conversation_id=conv.id,
                                                          assistant_message_id=answer.id, commit=False)
            db.commit()
            return asked, answer, job

    def settle_memory(self, limit=30):
        """Run the memory worker until nothing is left (real extractor, real forget judge)."""
        from app.services import transcript_search
        from app.workers import memory as worker

        for _ in range(limit):
            if not worker.run_once():
                break
        while transcript_search.embed_run_once():  # what was said, embedded (the recall-embeddings worker's job)
            pass


async def reply(world: World, conv, user_text: str) -> dict:
    """One real reply: the initial memory check, her prompt and tools, the model; persisted with its receipt."""
    from app.ai.chat import _build_inputs, _orchestrator_for
    from app.ai.context import AgentRuntimeContext
    from app.ai.messages import extract_text
    from app.services import chat as chat_service, long_term_memory, recall_references

    asked = world.said(conv, "user", user_text)
    turn = await asyncio.to_thread(recall_references.for_reply, world.user.id, conv.id, asked.id, user_text, ZONE)
    with world.factory() as db:
        history = chat_service.get_history(db, conversation=db.get(Conversation, conv.id))
    context = AgentRuntimeContext(user_id=world.user.id, user_name="Sam", recall=turn.rendered, recall_turn=turn)
    started = time.monotonic()
    async with _orchestrator_for(context) as (agent, specs):
        result = await agent.ainvoke(_build_inputs(history, context, specs))
    words = extract_text(result["messages"][-1].content).replace("\u2019", "'").replace("\u2018", "'")
    tools = [{"name": c["name"], "args": c["args"]} for m in result["messages"] if isinstance(m, AIMessage)
             for c in (m.tool_calls or [])]
    with world.factory() as db:
        conv_row = db.get(Conversation, conv.id)
        answer = chat_service.add_message(db, conversation=conv_row, role="assistant", content=words,
                                          turn_id=asked.turn_id)
        recall_references.save_receipt(db, turn, assistant_message_id=answer.id, conversation_id=conv.id, reply=words)
        long_term_memory.schedule_consolidation(db, user_id=world.user.id, conversation_id=conv.id,
                                                assistant_message_id=answer.id, commit=False)
        db.commit()
    return {"user": user_text, "reply": words, "tools": tools, "seconds": round(time.monotonic() - started, 1),
            "supplied": sorted(str(r) for r in turn.supplied), "tool_statuses": turn.tool_statuses}


# --------------------------------------------------------------------------- #
# Scenarios: (setup, question, checks)
# --------------------------------------------------------------------------- #


def _used(r, kind=None, name="search_memories"):
    return any(t["name"] == name and (kind is None or kind in (t["args"].get("kinds") or [])) for t in r["tools"])


def _quotes_hers(reply_text: str) -> bool:
    return bool(re.search(r"\bI (said|told|explained|mentioned)|\bmy (answer|explanation)", reply_text, re.I))


async def said_exact(world, run):
    old = world.conversation("Evening sky", days_ago=6)
    world.exchange(old, SUNSET_Q, SUNSET_A, days_ago=6)
    world.settle_memory()
    r = await reply(world, world.conversation(), "What did you tell me last week about why sunsets look red?")
    supplied_msg = any(s.startswith("message:") for s in r["supplied"])
    return r, {"searched_said": _used(r, "said"), "passage_supplied": supplied_msg,
               "content_faithful": bool(re.search(r"scatter", r["reply"], re.I)),
               "her_words_as_hers": _quotes_hers(r["reply"]) or "I " in r["reply"],
               "no_speaker_swap": not re.search(r"\byou (said|told me|explained)\b[^.]*\b(scatter|blue|red)", r["reply"], re.I)}


async def said_paraphrase(world, run):
    old = world.conversation("Evening sky", days_ago=6)
    world.exchange(old, SUNSET_Q, SUNSET_A, days_ago=6)
    world.settle_memory()
    r = await reply(world, world.conversation(),
                    "a while ago you explained something to me about the colours in the evening sky... what was it "
                    "you said exactly?")
    return r, {"searched_said": _used(r, "said"),
               "passage_supplied": any(s.startswith("message:") for s in r["supplied"]),
               "content_faithful": bool(re.search(r"scatter", r["reply"], re.I)),
               "no_speaker_swap": not re.search(r"\byou (said|told me|explained)\b[^.]*\b(scatter|blue)", r["reply"], re.I)}


async def user_said(world, run):
    old = world.conversation("Flat stuff", days_ago=3)
    world.exchange(old, BOILER, "Ugh, that sounds maddening. Has the landlord said when someone's coming?", days_ago=3)
    world.settle_memory()
    r = await reply(world, world.conversation(), "what did I say to you about my boiler the other day?")
    return r, {"searched": any(t["name"] == "search_memories" for t in r["tools"]) or bool(r["supplied"]),
               "content_faithful": bool(re.search(r"clank|six|6", r["reply"], re.I)),
               "credited_to_user": bool(re.search(r"\byou (said|mentioned|told|wrote)", r["reply"], re.I)),
               "no_speaker_swap": not re.search(r"\bI (said|told you)\b[^.]*\bclank", r["reply"], re.I)}


async def said_missing(world, run):
    old = world.conversation("Evening sky", days_ago=6)
    world.exchange(old, SUNSET_Q, SUNSET_A, days_ago=6)
    world.settle_memory()
    r = await reply(world, world.conversation(), "what was it you told me about the volcanoes in Iceland?")
    honest = bool(re.search(r"(can't|cannot|couldn't|don't|do not) (find|see|recall|remember)|not finding|"
                            r"no (record|memory)|don't think (I|we)|not sure (I|we)|haven't (talked|told)", r["reply"], re.I))
    invented = re.search(r"\bI (said|told you|explained|mentioned)( that|:|,)\s*[\"“]?[^.?]*\b(volcan|iceland|lava|magma|erupt)",
                         r["reply"], re.I) or re.search(r"[\"“][^\"”]*(volcan|lava|magma|erupt)[^\"”]*[\"”]", r["reply"], re.I)
    return r, {"tried": bool(r["tools"]), "says_missing": honest, "no_invented_quote": not invented}


async def rundown(world, run):
    with world.factory() as db:
        mei = MemoryEntity(user_id=world.user.id, kind="person", name="Mei", relation="sister")
        miso = MemoryEntity(user_id=world.user.id, kind="pet", name="Miso", relation="cat")
        db.add_all([mei, miso])
        db.flush()
        for key, content, kind, extra in [
            ("fact:mei_city", "Their sister Mei lives in Osaka.", "fact", {"entity_id": mei.id, "attribute": "lives_in", "value": "Osaka"}),
            ("fact:miso", "Their cat is called Miso and is ginger.", "fact", {"entity_id": miso.id, "attribute": "name", "value": "Miso"}),
            ("constraint:shellfish", "The user is allergic to shellfish.", "constraint", {}),
            ("preference:jazz", "The user dislikes jazz.", "preference", {}),
            ("fact:job", "The user works as a ferry engineer.", "fact", {}),
        ]:
            db.add(SemanticMemory(user_id=world.user.id, kind=kind, normalized_key=key, content=content,
                                  importance=0.9 if kind == "constraint" else 0.6, confidence=0.9, **extra))
        db.add(EpisodicMemory(user_id=world.user.id, summary="The user hiked up Snowdon in the rain and got lost "
                              "near the top.", importance=0.6, confidence=0.9))
        db.commit()
    from app.services import memory_entities, memory_manager

    with world.factory() as db:
        plan = memory_manager.add_item(db, world.user.id, kind="question", text_="Ask how Mei's new flat in Osaka is",
                                       owner="columbina", epistemic="inferred")
        memory_entities.link(db, world.user.id, db.get(MemoryEntity, mei.id), role="about", item_id=plan.id)
        db.commit()
    r = await reply(world, world.conversation(), "what do you actually know about me so far?")
    covered = {k for k, pattern in {"sister": r"\bMei\b|Osaka", "cat": r"Miso", "allergy": r"shellfish",
                                    "jazz": r"jazz", "job": r"ferry", "hike": r"Snowdon"}.items()
               if re.search(pattern, r["reply"], re.I)}
    return r, {"used_rundown": _used(r, name="what_i_know"), "covers_4_of_6": len(covered) >= 4,
               "covered": sorted(covered)}


async def forget_history(world, run):
    old = world.conversation("Catching up", days_ago=10)
    world.exchange(old, THERAPY + " Also I finally fixed my bike's gears.",
                   "That's a really good step, and nice work on the bike!", days_ago=10)
    world.settle_memory()
    with world.factory() as db:
        before = [x.content for x in db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == world.user.id))] + \
                 [x.summary for x in db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == world.user.id))]
    ask = world.conversation("Catching up 2", days_ago=1)
    world.exchange(ask, FORGET, "Of course, I'll forget that.", days_ago=1)
    world.settle_memory()

    def stored_about_it():
        with world.factory() as db:
            rows = [x.content for x in db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == world.user.id,
                                                                                 SemanticMemory.status == "active"))]
            rows += [x.summary for x in db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == world.user.id))]
            rows += [x.text for x in db.scalars(select(WorkingItem).where(WorkingItem.user_id == world.user.id))]
            return [x for x in rows if re.search(r"therap|anxi", x, re.I)]

    with world.factory() as db:
        forget = db.scalar(select(MemoryForget).where(MemoryForget.user_id == world.user.id))
        forget_status = forget.status if forget else None
    gone_after_forget = stored_about_it() == []
    unprompted = await reply(world, world.conversation(), "how have I been doing lately, do you think?")
    asked = await reply(world, world.conversation(),
                        "in one of our chats I said something about therapy, what exactly did I say back then?")
    world.settle_memory()  # extraction of both exchanges, real extractor
    relearned = stored_about_it()
    r = {"user": asked["user"], "reply": asked["reply"], "tools": asked["tools"], "supplied": asked["supplied"],
         "seconds": asked["seconds"], "unprompted_reply": unprompted["reply"], "stored_before": before,
         "forget_status": forget_status, "relearned": relearned}
    return r, {"stored_before_had_it": any(re.search(r"therap|anxi", x, re.I) for x in before),
               "forget_finished": forget_status in ("done", "partial"),
               "gone_after_forget": gone_after_forget,
               "not_brought_up_unprompted": not re.search(r"therap|anxi", unprompted["reply"], re.I),
               "explicit_history_answered_or_declined_honestly": bool(re.search(
                   r"therap|anxi|forget|can't find|couldn't find|not (finding|sure)", asked["reply"], re.I)),
               "no_relearning": relearned == []}


SCENARIOS = {f.__name__: f for f in (said_exact, said_paraphrase, user_said, said_missing, rundown, forget_history)}


async def main() -> None:
    url = os.environ.get("PROBE_DATABASE_URL")
    if not url:
        sys.exit("Set PROBE_DATABASE_URL to a throwaway Postgres (never the dev database).")
    from app.core.config import get_settings

    settings = get_settings()
    # Isolated: no dev Redis wake-ups, the probe's own logs.
    settings.redis_url = None
    for name in ("recall_log_path", "memory_manager_log_path"):
        setattr(settings, name, str(OUT.parent / f"memory_stage5_probe_{name.split('_log')[0]}.jsonl"))
    args = sys.argv[1:]
    runs = int(args.pop(0)) if args and args[0].isdigit() else 3
    names = args or list(SCENARIOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    totals: dict[str, list[bool]] = {}
    for name in names:
        for run in range(1, runs + 1):
            world = World(url)
            try:
                result, checks = await SCENARIOS[name](world, run)
            except Exception as exc:  # noqa: BLE001 - one failed run is reported, not fatal
                result, checks = {"error": f"{type(exc).__name__}: {exc}"}, {"ran": False}
            finally:
                world.close()
            flags = {k: v for k, v in checks.items() if isinstance(v, bool)}
            ok = all(flags.values())
            totals.setdefault(name, []).append(ok)
            print(f"\n=== {name} run {run}: {'PASS' if ok else 'CHECK'} {checks}")
            print(f"USER: {result.get('user')}")
            if result.get("unprompted_reply"):
                print(f"(unprompted) HER: {result['unprompted_reply']}")
            print(f"HER: {result.get('reply')}")
            print(f"TOOLS: {json.dumps(result.get('tools'), ensure_ascii=False)}")
            with open(OUT, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "scenario": name, "run": run,
                                         "checks": checks, **result}, ensure_ascii=False, default=str) + "\n")
    print("\n--- summary ---")
    for name, oks in totals.items():
        print(f"{name}: {sum(oks)}/{len(oks)} runs passed every check")


if __name__ == "__main__":
    asyncio.run(main())
