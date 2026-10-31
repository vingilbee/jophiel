"""Shared recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §15): what she remembers is found across
contexts, follow-ups point at what her answer was about, and a failed or partial search is never
mistaken for "nothing recorded". Real PostgreSQL (``TEST_DATABASE_URL``); no model or embedding calls."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

import app.core.config as config
from app.ai import prompts
from app.ai.context import AgentRuntimeContext
from app.ai.recall.contracts import MemoryRef, RecallInvalid, RecallRequest, TimeRange
from app.ai.tools.memory import recall as recall_tools
from app.db.models import (
    ChatMessage, DesktopSession, MemoryContext, MemoryEvent, MemoryEventEmbedding, MemoryManagerState,
    RecallReceipt, RecallReference, SemanticMemory, User, WorkingItem,
)
from app.services import (
    chat as chat_service, desktop_sessions, game_learning, memory_manager, memory_reset, mind, observation,
    observation_memory, recall, recall_embeddings, recall_references,
)
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from tests.test_observation import PACK, _fail, _pack_session, _tick, _world


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "observation_enabled": True, "game_learning_enabled": True,
        "recall_enabled": True, "recall_association_enabled": True, "long_term_memory_enabled": True,
        "observation_tick_budget_seconds": 10.0, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "observation_log_path": str(tmp_path / "observation.jsonl"), "recall_log_path": str(tmp_path / "recall.jsonl")})
    getter = lambda: settings  # noqa: E731
    for module in (memory_manager, observation, observation_memory, game_learning, config, recall,
                   recall_references, recall_embeddings):
        monkeypatch.setattr(module, "get_settings", getter)
    for module in (recall_references, recall_embeddings, recall_tools):
        monkeypatch.setattr(module, "SessionLocal", database)
    monkeypatch.setattr(observation, "_last_sweep", 0.0)
    monkeypatch.setattr(recall, "embed", lambda text: None)  # no network: ranking without meaning
    return settings


def _played(db, user, *, end=True):
    """A Minecraft session that failed to mine obsidian and died in lava, then (by default) ended."""
    session = _pack_session(db, user)
    world = _world(db, session)
    _fail(db, session, skill="mine", reason="no pickaxe that can mine obsidian")
    game_learning.record_experience(db, session.id, "notice", {
        "kind": "died", "urgency": "danger", "text": "fell into lava",
        "data": {"dropped": {"iron_pickaxe": 1, "cobblestone": 23}}})
    _tick(db)
    if end:
        desktop_sessions.end_session(db, user.id, session.id, reason="user_end")
    db.expire_all()
    events = {e.kind: e for e in db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user.id))}
    return session, world, events


def _conversation(db, user):
    return chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)


def _say(db, conversation, text):
    return chat_service.add_message(db, conversation=conversation, role="user", content=text, turn_id=uuid.uuid4())


def _answer(db, turn, conversation, text, *, heard_chars=None):
    message = chat_service.add_message(db, conversation=conversation, role="assistant", content=text,
                                       turn_id=uuid.uuid4(), commit=False)
    receipt = recall_references.save_receipt(db, turn, assistant_message_id=message.id,
                                             conversation_id=conversation.id, reply=text)
    if heard_chars is not None:
        message.heard_chars = heard_chars
    db.commit()
    return message, receipt


def _quote_each(pairs):
    """A scripted association: {ref: quote}."""
    return lambda reply, memories: [{"id": ref, "quote": quote} for ref, quote in pairs.items()]


def _focus(db, user):
    db.expire_all()
    return memory_manager.focused(db, user.id)


# --- 1, 2, 4: the decisive scenario --------------------------------------------------------------


def test_after_the_game_ended_a_general_chat_recalls_its_events_and_follows_up(db, alice):
    session, world, events = _played(db, alice)
    general = memory_manager.ensure_general(db, alice.id)
    memory_manager.focus(db, alice.id, general.id)
    db.commit()
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "What happened in Minecraft earlier?")

    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, "Europe/Paris")
    assert turn.result.status == "matched"
    found = {r.ref: r for r in turn.result.records}
    died, failed = MemoryRef("event", str(events["died"].id)), MemoryRef("event", str(events["action_failed"].id))
    assert died in found and failed in found
    assert found[died].context_label == world.label and found[died].occurred_at == events["died"].occurred_at
    assert found[died].lifecycle == "its game session has ended" and "named_context" in found[died].matched
    assert "happened " in turn.rendered and "Europe/Paris" in turn.rendered and "[event:" in turn.rendered
    assert _focus(db, alice).id == general.id  # recalling never moves her

    reply = "You fell into lava, and before that the obsidian wouldn't come up without the right pickaxe."
    _, receipt_id = _answer(db, turn, conversation, reply)
    assert recall_references.associate(receipt_id, ask=_quote_each({
        str(died): "You fell into lava", str(failed): "the obsidian wouldn't come up"})) == "done"

    # "Restart": nothing in memory but the database. The follow-up points at those two events.
    follow = _say(db, conversation, "When was that?")
    again = recall_references.for_reply(alice.id, conversation.id, follow.id, follow.content, "Europe/Paris")
    direct = [r for r in again.result.records if "direct_reference" in r.matched]
    assert {r.ref for r in direct} == {died, failed}
    assert all(r.occurred_at == events[r.subkind].occurred_at for r in direct)
    context = db.get(MemoryContext, world.id)
    assert all(r.occurred_at != context.last_active_at for r in direct)
    assert "from your last answer" in again.rendered and "matched to the words" in again.rendered

    _, receipt_id = _answer(db, again, conversation, "Both happened about the same time, a few minutes apart.")
    recall_references.associate(receipt_id, ask=_quote_each({str(died): "Both happened"}))
    goal_q = _say(db, conversation, "What were we trying to do?")
    goal = recall_references.for_reply(alice.id, conversation.id, goal_q.id, goal_q.content, "Europe/Paris")
    goals = [r for r in goal.result.records if r.ref.kind == "item" and r.subkind == "goal"]
    assert [g.content for g in goals] == ["reach the Nether"]
    assert goals[0].context_label == world.label


# --- 3: two events referenced, a third supplied but unused ------------------------------------------


def test_only_what_the_answer_was_about_is_followed_up(db, alice):
    _, _, events = _played(db, alice)
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "What happened in the Minecraft game?")
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    goal_ref = next(r.ref for r in turn.result.records if r.ref.kind == "item")
    died, failed = MemoryRef("event", str(events["died"].id)), MemoryRef("event", str(events["action_failed"].id))
    assert {died, failed, goal_ref} <= set(turn.supplied)
    _, receipt_id = _answer(db, turn, conversation, "You died in lava and the mining failed.")
    recall_references.associate(receipt_id, ask=_quote_each({str(died): "You died in lava",
                                                              str(failed): "the mining failed"}))
    follow = _say(db, conversation, "when?")
    refs, note = recall_references.prior_references(db, alice.id, conversation.id, follow.id)
    assert set(refs) == {died, failed} and goal_ref not in refs and "matched" in note


# --- 5, 6: no match is qualified; failures are never "nothing" ------------------------------------------


def test_nothing_found_is_no_match_and_failures_are_partial_or_failed(db, alice, monkeypatch):
    _played(db, alice)
    req = RecallRequest(user_id=alice.id, query="Did we build a castle?", purpose="reply",
                        now=datetime.now(timezone.utc))
    result = recall.search(db, req)
    assert result.status == "no_match" and set(result.searched_kinds) == {"fact", "episode", "event", "item"}
    assert "found nothing that matches" in recall.render(result, now=req.now, zone=None)

    def broken(*_):
        raise RuntimeError("store down")

    monkeypatch.setattr(recall, "_events", broken)
    partial = recall.search(db, RecallRequest(user_id=alice.id, query="what happened in Minecraft?",
                                              purpose="reply", now=req.now))
    assert partial.status == "partial" and partial.errors == ["event_unavailable"]
    assert "incomplete" in recall.status_line(partial)
    for name in ("_items", "_facts", "_episodes"):
        monkeypatch.setattr(recall, name, broken)
    failed = recall.search(db, req)
    assert failed.status == "failed" and "FAILED" in recall.status_line(failed)
    turn = recall_references.for_reply(alice.id, None, None, "what happened?", None)
    assert turn.result.status == "failed" and "couldn't check" in turn.rendered
    assert recall.render_legacy(turn.result) is None


# --- 7: pending and broken embeddings -------------------------------------------------------------------


def test_events_are_found_before_embeddings_and_stale_embeddings_are_discarded(db, alice, monkeypatch):
    _, world, events = _played(db, alice, end=False)
    monkeypatch.setattr(recall, "embed", lambda text: (_ for _ in ()).throw(RuntimeError("embeddings down")))
    db.add(SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:pets", content="has a cat",
                          embedding=[1.0, 0.0], importance=0.5, confidence=1.0, status="active"))
    db.commit()
    result = recall.search(db, RecallRequest(user_id=alice.id, query="what happened in minecraft",
                                             purpose="reply", now=datetime.now(timezone.utc)))
    assert result.status == "matched" and any(r.ref.kind == "event" for r in result.records)
    assert "without meaning" in result.coverage["ranking"] and result.coverage["events_without_embedding"] >= 2

    assert recall_embeddings.run_once(embed=lambda texts: [[0.1, 0.2]] * len(texts))
    db.expire_all()
    rows = list(db.scalars(select(MemoryEventEmbedding).where(MemoryEventEmbedding.user_id == alice.id)))
    assert rows and all(r.status == "done" for r in rows)
    assert recall_embeddings.progress(db, alice.id)["not_started"] == 0

    # The label changes while a batch is out: that result is discarded, the row waits for a fresh one.
    memory_manager.record_event(db, alice.id, kind="note", text="found a village", epistemic="observed",
                                context_id=world.id)
    db.commit()

    def rename_then_embed(texts):
        db.execute(update(MemoryContext).where(MemoryContext.id == world.id).values(label="Old world"))
        db.commit()
        return [[0.3, 0.4]] * len(texts)

    recall_embeddings.run_once(embed=rename_then_embed)
    db.expire_all()
    fresh = db.scalar(select(MemoryEventEmbedding).join(MemoryEvent, MemoryEvent.id == MemoryEventEmbedding.event_id)
                      .where(MemoryEvent.text == "found a village"))
    assert fresh.status == "pending" and fresh.vector is None
    # An outage leaves the row to retry later, and recall goes on without it.
    assert not recall_embeddings.run_once(embed=lambda texts: (_ for _ in ()).throw(RuntimeError("down")))
    db.expire_all()
    assert db.get(MemoryEventEmbedding, fresh.event_id).status == "failed"


# --- 8: nothing crosses users -----------------------------------------------------------------------


def test_references_pages_and_receipts_never_cross_users(db, alice, bob):
    _, _, events = _played(db, alice)
    ref = MemoryRef("event", str(events["died"].id))
    assert recall.read(db, bob.id, ref).errors == ["unavailable"]
    assert recall.read(db, alice.id, ref).records[0].content.startswith("Minecraft reported: you died")
    mine = recall.search(db, RecallRequest(user_id=bob.id, query="what happened in minecraft?", purpose="reply",
                                           now=datetime.now(timezone.utc)))
    assert mine.status == "no_match"
    world = db.scalar(select(MemoryContext).where(MemoryContext.user_id == alice.id, MemoryContext.kind == "world"))
    assert recall.timeline(db, bob.id, world.id).errors == ["unavailable"]
    with pytest.raises(RecallInvalid):
        recall.search(db, RecallRequest(user_id=bob.id, query="x", purpose="explicit_search",
                                        now=datetime.now(timezone.utc), context_ids=(world.id,)))
    page = recall._page_token(RecallRequest(user_id=alice.id, query="x", purpose="explicit_search",
                                            now=datetime.now(timezone.utc)), 12)
    with pytest.raises(RecallInvalid):
        recall.search(db, RecallRequest(user_id=bob.id, query="x", purpose="explicit_search",
                                        now=datetime.now(timezone.utc), page_token=page))
    # The database refuses a receipt or a reference with the wrong owner.
    conversation = _conversation(db, alice)
    message = chat_service.add_message(db, conversation=conversation, role="assistant", content="hi", turn_id=uuid.uuid4())
    db.add(RecallReceipt(user_id=bob.id, assistant_message_id=message.id, conversation_id=conversation.id,
                         schema_version="t", purpose="reply", status="matched"))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()
    theirs = _conversation(db, bob)
    receipt = RecallReceipt(user_id=bob.id, assistant_message_id=chat_service.add_message(
        db, conversation=theirs, role="assistant", content="hi", turn_id=uuid.uuid4()).id,
        conversation_id=theirs.id, schema_version="t", purpose="reply", status="matched")
    db.add(receipt)
    db.flush()
    db.add(RecallReference(receipt_id=receipt.id, user_id=bob.id, relation="supplied", event_id=events["died"].id,
                           fingerprint="x", method="structured"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- 9, 10: deleted and corrected between turns; historical versus current ----------------------------


def test_a_memory_changed_or_removed_after_an_answer_is_read_fresh_and_never_resurrected(db, alice):
    _, _, events = _played(db, alice)
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "what happened in minecraft?")
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    goal = next(r for r in turn.result.records if r.ref.kind == "item")
    died = MemoryRef("event", str(events["died"].id))
    _, receipt_id = _answer(db, turn, conversation, "You died, and the Nether is still the goal.")
    recall_references.associate(receipt_id, ask=_quote_each({str(died): "You died",
                                                              str(goal.ref): "the Nether is still the goal"}))
    entry = db.get(WorkingItem, uuid.UUID(goal.ref.id))
    memory_manager.resolve(db, alice.id, memory_manager.ref(entry), status="completed", reason="we got there",
                           source=memory_manager.Source("message", asked.id))
    db.execute(delete(MemoryEvent).where(MemoryEvent.id == events["died"].id))
    db.commit()
    follow = _say(db, conversation, "and then?")
    again = recall_references.for_reply(alice.id, conversation.id, follow.id, follow.content, None)
    direct = [r for r in again.result.records if "direct_reference" in r.matched]
    assert [r.ref for r in direct] == [goal.ref]
    assert direct[0].status == "completed" and direct[0].applicability == "no_longer_current"
    assert "since been removed" in again.rendered and "Minecraft reported: you died" not in again.rendered
    receipt = db.scalar(select(RecallReceipt).where(RecallReceipt.id == receipt_id))
    assert receipt.counts["supplied"] == len(turn.supplied)  # the count outlives the deleted memory

    db.add_all([SemanticMemory(user_id=alice.id, kind="preference", normalized_key="preference:drink",
                               content="likes coffee", importance=0.5, confidence=1.0, status="superseded"),
                SemanticMemory(user_id=alice.id, kind="preference", normalized_key="preference:drink2",
                               content="likes tea now", importance=0.5, confidence=1.0, status="active")])
    db.commit()
    now = datetime.now(timezone.utc)
    current = recall.search(db, RecallRequest(user_id=alice.id, query="likes coffee tea", purpose="reply", now=now))
    assert [r.content for r in current.records if r.ref.kind == "fact"] == ["likes tea now"]
    past = recall.search(db, RecallRequest(user_id=alice.id, query="likes coffee tea", purpose="explicit_search",
                                           now=now, intent="historical"))
    old = next(r for r in past.records if r.content == "likes coffee")
    assert old.applicability == "no_longer_current" and "no longer current" in recall.render(past, now=now, zone=None)


# --- 11: time ------------------------------------------------------------------------------------------


def test_time_ranges_are_local_half_open_and_never_silently_fixed(db, alice):
    jakarta = ZoneInfo("Asia/Jakarta")
    day = recall_tools._range("2026-10-20", "2026-10-20", "occurred", jakarta)
    assert day.start == datetime(2026, 10, 19, 17, tzinfo=timezone.utc)
    assert day.end == datetime(2026, 10, 20, 17, tzinfo=timezone.utc)
    with pytest.raises(RecallInvalid):
        recall_tools._range("2026-10-21", "2026-10-19", "occurred", jakarta)
    with pytest.raises(RecallInvalid):
        recall_tools._range("yesterday", None, "occurred", jakarta)
    with pytest.raises(RecallInvalid):
        TimeRange("occurred", datetime(2026, 1, 1))  # no zone

    _, world, events = _played(db, alice)
    died = events["died"]
    now = datetime.now(timezone.utc)
    inside = recall.search(db, RecallRequest(
        user_id=alice.id, query="", purpose="explicit_search", now=now,
        time_range=TimeRange("occurred", died.occurred_at - timedelta(seconds=1), died.occurred_at + timedelta(seconds=1))))
    assert MemoryRef("event", str(died.id)) in inside.refs() and "temporal" in inside.records[0].matched
    assert inside.coverage["unplaceable"]["item"] >= 1  # a goal has no time it happened: not placed in the range
    later = recall.search(db, RecallRequest(user_id=alice.id, query="", purpose="explicit_search", now=now,
                                            time_range=TimeRange("occurred", now + timedelta(hours=1))))
    assert later.status == "no_match"
    due = memory_manager.note_commitment(db, alice.id, "show the base", owner="user",
                                         due_at=now + timedelta(days=1), context_id=world.id)
    db.commit()
    scheduled = recall.search(db, RecallRequest(user_id=alice.id, query="", purpose="explicit_search", now=now,
                                                time_range=TimeRange("scheduled", now, now + timedelta(days=2))))
    assert scheduled.refs() == [MemoryRef("item", str(due.id))]
    assert "due " in recall.render(scheduled, now=now, zone="Asia/Jakarta")


# --- 12, 13: names, look-alikes and duplicates -----------------------------------------------------------


def test_a_named_context_gives_its_timeline_and_look_alike_worlds_are_never_merged(db, alice):
    _, world, events = _played(db, alice)
    other = memory_manager.open_context(db, alice.id, kind="world", label="Minecraft world: two", source=PACK)
    memory_manager.record_event(db, alice.id, kind="note", text="built a small hut", epistemic="observed",
                                context_id=other.id)
    db.commit()
    now = datetime.now(timezone.utc)
    found = recall.search(db, RecallRequest(user_id=alice.id, query="what happened in minecraft", purpose="reply",
                                            now=now))
    assert len(found.coverage["contexts_matched"]) == 2
    assert {r.context_label for r in found.records if r.ref.kind == "event"} == {world.label, other.label}
    line = recall.timeline(db, alice.id, world.id)
    texts = [r.content for r in line.records if r.ref.kind == "event"]
    assert any("you died" in t for t in texts) and "built a small hut" not in texts
    happened = [r.occurred_at for r in line.records if r.ref.kind == "event"]
    assert happened == sorted(happened)
    # "Goal: reach the Nether" restates the goal: once, as the goal.
    goal_items = [r for r in found.records if r.ref.kind == "item" and r.content == "reach the Nether"]
    echoes = [r for r in found.records if r.ref.kind == "event" and r.content == "Goal: reach the Nether"]
    assert goal_items and not echoes
    turn = recall_references.TurnRecall(alice.id, None, "UTC", now, None, found, "", None)
    timeline_tool = next(t for t in recall_tools.build_recall_tools(turn) if t.name == "read_timeline")
    answer = asyncio.run(timeline_tool.ainvoke({"context": "minecraft"}))
    assert "several contexts have that name" in answer and str(world.id) in answer and str(other.id) in answer


# --- 14: conversations and what was heard ---------------------------------------------------------------


def test_follow_ups_stay_in_their_conversation_and_unheard_words_are_not_antecedents(db, alice):
    _, _, events = _played(db, alice)
    died, failed = MemoryRef("event", str(events["died"].id)), MemoryRef("event", str(events["action_failed"].id))
    first, second = _conversation(db, alice), _conversation(db, alice)  # each call starts a new one
    assert first.id != second.id
    asked = _say(db, first, "what happened in minecraft?")
    turn = recall_references.for_reply(alice.id, first.id, asked.id, asked.content, None)
    reply = "The mining failed. Then, which you didn't hear, you died in lava."
    _, receipt_id = _answer(db, turn, first, reply, heard_chars=len("The mining failed."))
    recall_references.associate(receipt_id, ask=_quote_each({str(failed): "The mining failed",
                                                              str(died): "you died in lava"}))
    follow = _say(db, first, "when?")
    refs, _ = recall_references.prior_references(db, alice.id, first.id, follow.id)
    assert refs == [failed]  # the death was only in words the user never heard
    elsewhere = _say(db, second, "when?")
    assert recall_references.prior_references(db, alice.id, second.id, elsewhere.id) == ([], None)


# --- 15: association off or broken ----------------------------------------------------------------------


def test_without_association_what_was_given_is_a_candidate_marked_unclear(db, alice, on, monkeypatch):
    _played(db, alice)
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "what happened in minecraft?")
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    _, receipt_id = _answer(db, turn, conversation, "Some things went wrong.")
    assert recall_references.associate(receipt_id, ask=lambda *_: (_ for _ in ()).throw(RuntimeError("x"))) == "failed"
    follow = _say(db, conversation, "when?")
    refs, note = recall_references.prior_references(db, alice.id, conversation.id, follow.id)
    assert set(refs) == set(list(turn.supplied)[:4]) and "unclear" in note

    # A proposal that isn't in what was given, or whose words aren't in the answer, is never kept.
    monkeypatch.setattr(recall_references, "get_settings", lambda: on)
    turn = recall_references.for_reply(alice.id, conversation.id, follow.id, follow.content, None)
    _, receipt_id = _answer(db, turn, conversation, "It was a bad evening.")
    assert recall_references.associate(receipt_id, ask=_quote_each({"event:999999": "bad evening",
                                                                     str(next(iter(turn.supplied))): "not said"})) == "ambiguous"
    assert not db.scalars(select(RecallReference).where(RecallReference.receipt_id == receipt_id,
                                                        RecallReference.relation == "referenced")).all()


# --- 16: tool budget, pages and truncation ----------------------------------------------------------------


def test_tools_share_a_budget_and_pages_and_truncation_are_explicit(db, alice):
    _, world, _ = _played(db, alice)
    for n in range(20):
        memory_manager.record_event(db, alice.id, kind="note", text=f"placed torch number {n}", epistemic="observed",
                                    context_id=world.id)
    db.commit()
    now = datetime.now(timezone.utc)
    first = recall.search(db, RecallRequest(user_id=alice.id, query="placed torch", purpose="explicit_search", now=now))
    assert len(first.records) == 12 and first.next_page and first.coverage["more_matches"]
    second = recall.search(db, RecallRequest(user_id=alice.id, query="placed torch", purpose="explicit_search",
                                             now=now, page_token=first.next_page))
    assert second.records and not set(second.refs()) & set(first.refs())
    small = recall.search(db, RecallRequest(user_id=alice.id, query="placed torch", purpose="explicit_search",
                                            now=now, budget_chars=500))
    assert small.coverage["truncated"] == "content budget" and "more matched" in recall.status_line(small)

    turn = recall_references.TurnRecall(alice.id, None, "UTC", now, None, first, "", None)
    tools = {t.name: t for t in recall_tools.build_recall_tools(turn)}
    assert "placed torch" in asyncio.run(tools["search_memories"].ainvoke({"query": "placed torch"}))
    assert "Not searched" in asyncio.run(tools["read_memory"].ainvoke({"memory": "nonsense"}))
    asyncio.run(tools["read_timeline"].ainvoke({"context": str(world.id)}))
    used_up = asyncio.run(tools["search_memories"].ainvoke({"query": "anything"}))
    assert "used up" in used_up and "not a sign nothing is recorded" in used_up
    assert turn.tool_calls == 3 and turn.tool_statuses[-1] == "budget_exhausted"
    assert len(turn.supplied) > 12  # what the tools found is part of what the reply was given


# --- 17: hard thoughts read the same service -----------------------------------------------------------


def test_a_hard_thought_recalls_observed_game_events_without_a_game_summary(db, alice):
    session, world, events = _played(db, alice, end=False)
    rendered = recall_references.for_thought(db, user_id=alice.id, source="minecraft", activity=session.id,
                                             query="why do I keep dying")
    assert "Minecraft reported: you died" in rendered and f"in: {world.label}" in rendered


# --- the reply prompt ----------------------------------------------------------------------------------


def test_the_reply_prompt_has_one_memory_section(db, alice):
    context = AgentRuntimeContext(user_id=alice.id, recall="Memory search ran (looked in: events):\n- [event:1] died",
                                  typed_memory="What you know about them:\n- x", long_term_memory="- [fact] x")
    prompt = prompts.build_system_prompt(context)
    assert "What you recall" in prompt and "by whose it is" not in prompt and "Relevant long-term memory" not in prompt
    assert "never switch_context just to talk about it" in prompt


# --- 18, 19: resets, deletion, and the old block ----------------------------------------------------------


def test_reset_and_account_deletion_remove_receipts_and_the_old_block_still_works(db, alice):
    _played(db, alice)
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "what happened in minecraft?")
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    _answer(db, turn, conversation, "You died.")
    recall_embeddings.run_once(embed=lambda texts: [[0.5]] * len(texts))
    counts = memory_reset.reset(db, user_ids=[alice.id], scopes=["recall"])
    assert counts["recall_receipts"] == 1 and counts["memory_event_embeddings"] >= 2
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    _answer(db, turn, conversation, "You died again.")
    db.execute(delete(User).where(User.id == alice.id))
    db.commit()
    assert not db.scalars(select(RecallReceipt)).all() and not db.scalars(select(RecallReference)).all()


def test_the_old_block_comes_from_the_same_ranking(db, alice, on, monkeypatch):
    from app.services import long_term_memory

    monkeypatch.setattr(long_term_memory, "SessionLocal", lambda: db.__class__(bind=db.get_bind()))
    db.add(SemanticMemory(user_id=alice.id, kind="preference", normalized_key="preference:tone",
                          content="prefers short answers", importance=0.9, confidence=1.0, status="active"))
    db.commit()
    assert long_term_memory.retrieve_for_prompt(alice.id, "how was your day?") == "- [preference] prefers short answers"
