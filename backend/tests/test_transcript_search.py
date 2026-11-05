"""Transcript search (services/transcript_search.py; docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): what was said, by
whom, as far as it was heard. Real PostgreSQL (``TEST_DATABASE_URL``); no model calls."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

import app.core.config as config
import app.db.session as db_session
from app.ai.recall.contracts import MemoryRef, RecallInvalid, TimeRange
from app.db.models import ChatMessage, MemoryForget, MemoryForgottenSource, RecallReference
from app.services import chat as chat_service
from app.services import recall, recall_references, transcript_search
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)

RIVERS = ("Rivers carry fresh water from land to the sea, while oceans hold salt water and cover about seventy "
          "percent of the planet. The Amazon carries more water than any other river.")
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "recall_log_path": str(tmp_path / "recall.jsonl"), "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "redis_url": None})
    for module in (recall, recall_references, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(recall_references, "SessionLocal", database)
    monkeypatch.setattr(recall, "embed", lambda text: None)


def _conversation(db, user, title=None):
    conversation = chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
    if title:
        conversation.title = title
        db.commit()
    return conversation


def _say(db, conversation, role, content, **values):
    message = chat_service.add_message(db, conversation=conversation, role=role, content=content,
                                       turn_id=uuid.uuid4(), status=values.pop("status", "completed"))
    if values:
        db.execute(update(ChatMessage).where(ChatMessage.id == message.id).values(**values))
    db.commit()
    db.refresh(message)
    return message


def _search(db, user, query, **kw):
    return transcript_search.search(db, user.id, query, now=NOW, **kw)


def test_her_answer_is_found_word_for_word_and_credited_to_her(db, alice):
    conversation = _conversation(db, alice, "Geography chat")
    asked = _say(db, conversation, "user", "what's the difference between rivers and oceans?")
    answer = _say(db, conversation, "assistant", RIVERS)
    _say(db, conversation, "user", "cool, and what about lakes?")

    found = _search(db, alice, "rivers oceans", who="me")
    assert found.status == "matched" and [r.ref for r in found.records] == [MemoryRef("message", str(answer.id))]
    record = found.records[0]
    assert record.subkind == "columbina" and "Amazon carries more water" in record.content
    assert record.content in RIVERS or record.content.strip("…") in RIVERS  # her words, never rebuilt
    line = recall.render(found, now=NOW, zone="UTC")
    assert "Your own words (you, Jophiel" in line and '"Geography chat"' in line and "not a checked fact" in line

    theirs = _search(db, alice, "rivers oceans", who="user")
    assert [r.ref.id for r in theirs.records] == [str(asked.id)]
    assert "The user's own words" in recall.render(theirs, now=NOW, zone="UTC")
    # Either speaker; the message being answered is left out.
    both = _search(db, alice, "rivers", exclude=(asked.id,))
    assert [r.ref.id for r in both.records] == [str(answer.id)]


def test_any_word_finds_a_reworded_question_and_stemming_matches_forms(db, alice):
    conversation = _conversation(db, alice)
    answer = _say(db, conversation, "assistant", RIVERS)
    # Not every word is there ("bodies"): the "any word" pass still finds it.
    assert [r.ref.id for r in _search(db, alice, "bodies of water like oceans").records] == [str(answer.id)]
    assert [r.ref.id for r in _search(db, alice, "ocean").records] == [str(answer.id)]
    assert _search(db, alice, "the and of").status == "no_match"


def test_only_what_was_heard_is_searchable(db, alice):
    conversation = _conversation(db, alice)
    heard_part = "Rivers flow into oceans. "
    unheard = "Glaciers feed some rivers in the mountains."
    cut = _say(db, conversation, "assistant", heard_part + unheard, heard_chars=len(heard_part) + 3)
    assert _search(db, alice, "glaciers").status == "no_match"  # never heard
    found = _search(db, alice, "oceans")
    assert [r.ref.id for r in found.records] == [str(cut.id)]
    assert "Glaciers" not in found.records[0].content and "didn't hear the rest" in found.records[0].lifecycle
    assert "[interrupted" not in found.records[0].content
    # A cut in the middle of a word: the half-heard word isn't a match.
    # ("whale" of "whales" was heard: the stored vector has it, the heard text snapped to whole words doesn't.)
    text_ = "Seals swim near whales"
    half = _say(db, conversation, "assistant", text_, heard_chars=len(text_) - 1)
    whales = _search(db, alice, "whale")
    assert all(r.ref.id != str(half.id) for r in whales.records)
    assert whales.coverage.get("dropped_unheard") == 1
    # Nothing heard, or never delivered: not searchable.
    _say(db, conversation, "assistant", "Volcanoes erupt.", heard_chars=0)
    _say(db, conversation, "assistant", "Tornadoes spin.", status="failed")
    assert _search(db, alice, "volcanoes").status == "no_match"
    assert _search(db, alice, "tornadoes").status == "no_match"


def test_another_users_conversations_are_never_found_or_read(db, alice, bob):
    mine = _say(db, _conversation(db, alice), "assistant", RIVERS)
    _say(db, _conversation(db, bob), "assistant", "Oceans are deep and rivers are long.")
    assert [r.ref.id for r in _search(db, alice, "oceans").records] == [str(mine.id)]
    assert transcript_search.read(db, bob.id, mine.id) is None
    assert recall.read(db, bob.id, f"message:{mine.id}").status == "no_match"
    assert recall.reread(db, bob.id, [MemoryRef("message", str(mine.id))]).records == []


def test_pages_and_filters(db, alice):
    first = _conversation(db, alice, "first")
    second = _conversation(db, alice, "second")
    for i in range(10):
        _say(db, first if i % 2 else second, "user", f"tell me about rivers number {i}")
    page = _search(db, alice, "rivers", limit=4)
    assert len(page.records) == 4 and page.next_page
    rest = _search(db, alice, "rivers", limit=4, page_token=page.next_page)
    assert not {r.ref for r in page.records} & {r.ref for r in rest.records}
    with pytest.raises(RecallInvalid):
        _search(db, alice, "oceans", page_token=page.next_page)
    only = _search(db, alice, "rivers", conversation_id=first.id, limit=8)
    assert {r.conversation_id for r in only.records} == {first.id}
    later = TimeRange("recorded", start=NOW + timedelta(days=1))
    assert _search(db, alice, "rivers", time_range=later).status == "no_match"


def test_a_supplied_passage_is_a_receipt_reference_owned_and_reread(db, alice, bob):
    conversation = _conversation(db, alice)
    answer = _say(db, conversation, "assistant", RIVERS)
    reply = _say(db, conversation, "assistant", "I said rivers are fresh water.")
    found = _search(db, alice, "rivers oceans")
    turn = recall_references.TurnRecall(alice.id, conversation.id, "UTC", NOW, None, found, "", None)
    turn.supply(found.records)
    recall_references.save_receipt(db, turn, assistant_message_id=reply.id,
                                                conversation_id=conversation.id, reply=reply.content)
    db.commit()
    rows = db.query(RecallReference).filter(RecallReference.message_id.is_not(None)).all()
    assert {r.message_id for r in rows} >= {answer.id}
    refs, _note = recall_references.prior_references(db, alice.id, conversation.id, None)
    assert MemoryRef("message", str(answer.id)) in refs
    assert [r.ref for r in recall.reread(db, alice.id, refs).records if r.ref.kind == "message"]
    # A reference row can't point at someone else's message.
    other = _say(db, _conversation(db, bob), "user", "rivers")
    receipt = db.query(recall_references.RecallReceipt).filter_by(assistant_message_id=reply.id).one()
    db.add(RecallReference(receipt_id=receipt.id, user_id=alice.id, relation="supplied", message_id=other.id,
                           fingerprint="x", method="structured"))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()
    # Deleting the conversation deletes the passage: the reread says it's gone.
    assert chat_service.delete_conversation(db, user_id=alice.id, conversation_id=conversation.id)
    db.commit()
    db.expire_all()
    assert recall.reread(db, alice.id, [MemoryRef("message", str(answer.id))]).records == []


def test_a_message_something_was_forgotten_from_is_history_marked_and_not_carried_on(db, alice):
    conversation = _conversation(db, alice)
    knee = _say(db, conversation, "user", "my knee has been sore since the race")
    asked = _say(db, conversation, "user", "please forget what I told you about my knee")
    forget = MemoryForget(user_id=alice.id, source_message_id=asked.id, conversation_id=conversation.id,
                          status="done", finished_at=NOW, removed={"episode": 1})
    db.add(forget)
    db.flush()
    db.add(MemoryForgottenSource(forget_id=forget.id, message_id=knee.id, user_id=alice.id, spans=None))
    db.commit()
    found = _search(db, alice, "knee")
    assert {r.ref.id for r in found.records} == {str(knee.id), str(asked.id)}  # the request stays history too
    marked = {r.ref.id: r.forgotten for r in found.records}
    assert marked[str(knee.id)] is True and marked[str(asked.id)] is False
    assert "asked you to forget" in recall.render(found, now=NOW, zone="UTC")
    # Asked about, it can be read; carried to the next reply as memory, it can't.
    assert recall.read(db, alice.id, f"message:{knee.id}").status == "matched"
    again = recall.reread(db, alice.id, [MemoryRef("message", str(knee.id))])
    assert again.records == [] and again.coverage["references_set_aside"] == [f"message:{knee.id}"]


def test_her_tools_search_what_was_said_and_give_a_rundown_within_one_budget(db, alice, monkeypatch):
    import asyncio

    from app.ai.recall.contracts import RecallResult
    from app.ai.tools.memory.recall import build_recall_tools
    from app.db.models import SemanticMemory

    monkeypatch.setattr("app.ai.tools.memory.recall.SessionLocal", db_session.SessionLocal)
    conversation = _conversation(db, alice, "Geography chat")
    answer = _say(db, conversation, "assistant", RIVERS)
    asking = _say(db, conversation, "user", "what did you tell me about rivers and oceans?")
    db.add(SemanticMemory(user_id=alice.id, kind="preference", normalized_key="preference:sea",
                          content="The user loves the sea.", importance=0.5, confidence=0.9))
    db.commit()
    turn = recall_references.TurnRecall(alice.id, conversation.id, "UTC", NOW, None, RecallResult("not_searched"),
                                        "", None, user_message_id=asking.id)
    tools = {t.name: t for t in build_recall_tools(turn)}
    said = asyncio.run(tools["search_memories"].ainvoke({"query": "rivers oceans", "kinds": ["said"], "who": "me"}))
    assert "Your own words" in said and "Amazon" in said and str(answer.id) in said and str(asking.id) not in said
    both = asyncio.run(tools["search_memories"].ainvoke({"query": "sea oceans", "kinds": ["facts", "said"]}))
    assert "The user loves the sea." in both and "Your own words" in both
    know = asyncio.run(tools["what_i_know"].ainvoke({}))
    assert "What you know about them" in know and "The user loves the sea." in know
    assert MemoryRef("message", str(answer.id)) in turn.supplied
    assert turn.tool_calls == 3 and "know_matched" in turn.tool_statuses
    assert "used up" in asyncio.run(tools["what_i_know"].ainvoke({}))


def test_a_passage_her_last_answer_used_comes_along_to_the_next_reply_unless_it_was_forgotten(db, alice):
    conversation = _conversation(db, alice)
    answer = _say(db, conversation, "assistant", RIVERS)
    reply = _say(db, conversation, "assistant", "I told you rivers carry fresh water.")
    found = _search(db, alice, "rivers")
    turn = recall_references.TurnRecall(alice.id, conversation.id, "UTC", NOW, None, found, "", None)
    turn.supply(found.records)
    recall_references.save_receipt(db, turn, assistant_message_id=reply.id, conversation_id=conversation.id,
                                   reply=reply.content)
    db.commit()
    follow = _say(db, conversation, "user", "and which river carries the most?")
    nxt = recall_references.for_reply(alice.id, conversation.id, follow.id, follow.content, "UTC")
    carried = [r for r in nxt.result.records if r.ref == MemoryRef("message", str(answer.id))]
    assert carried and "direct_reference" in carried[0].matched
    assert "Your own words" in nxt.rendered and "The past" in nxt.rendered
    # Once something in it is forgotten, it isn't carried on.
    forget = MemoryForget(user_id=alice.id, source_message_id=follow.id, conversation_id=conversation.id,
                          status="done", finished_at=NOW)
    db.add(forget)
    db.flush()
    db.add(MemoryForgottenSource(forget_id=forget.id, message_id=answer.id, user_id=alice.id, spans=[[0, 10]]))
    db.commit()
    again = recall_references.for_reply(alice.id, conversation.id, follow.id, follow.content, "UTC")
    assert all(r.ref != MemoryRef("message", str(answer.id)) for r in again.result.records)
    assert again.result.coverage.get("references_set_aside") == [f"message:{answer.id}"]


def test_a_question_worded_unlike_the_answer_is_found_by_meaning(db, alice, monkeypatch):
    from app.db.models import ChatMessageEmbedding

    conversation = _conversation(db, alice)
    answer = _say(db, conversation, "assistant", "Light scatters more over the long path at dusk, so reds remain.")
    other = _say(db, conversation, "user", "my boiler clanks every morning")
    _say(db, conversation, "assistant", "Never heard.", status="failed")

    def vec(text_):
        return [1.0, 0.0, 0.0] if ("scatter" in text_ or "evening sky" in text_) else [0.0, 1.0, 0.0]
    assert transcript_search.embed_run_once(embed=lambda texts: [vec(t) for t in texts]) is True
    assert {r.message_id for r in db.query(ChatMessageEmbedding).all()} == {answer.id, other.id}  # never-delivered: no
    assert transcript_search.embed_run_once(embed=lambda texts: [vec(t) for t in texts]) is False  # caught up
    monkeypatch.setattr(recall, "embed", lambda q: [0.95, 0.05, 0.0] if "evening sky" in q else [0.0, 0.0, 1.0])
    found = _search(db, alice, "colours in the evening sky", who="me")
    assert [r.ref.id for r in found.records] == [str(answer.id)] and found.records[0].matched == ("semantic",)
    assert "meaning" in found.coverage["meaning"]
    # What was heard changed since it was embedded: the old vector isn't used.
    db.execute(update(ChatMessage).where(ChatMessage.id == answer.id).values(heard_chars=5))
    db.commit()
    assert _search(db, alice, "colours in the evening sky", who="me").status == "no_match"
    # No query embedding: words only, and it says so.
    monkeypatch.setattr(recall, "embed", lambda q: None)
    assert "not searched by meaning" in _search(db, alice, "colours in the evening sky").coverage["meaning"]
