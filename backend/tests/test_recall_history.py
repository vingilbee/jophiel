"""Source-backed history (docs/COLUMBINA_SOURCE_BACKED_RECALL.md): from what search finds, the evidence
links reach the user's earlier words across contexts; the reader's account keeps only what it can trace;
the reply's receipt keeps what it was shown; and recall changes nothing it reads. Real PostgreSQL
(``TEST_DATABASE_URL``); the reader is scripted, no model or embedding calls.

The fixture is deliberately not the Lantern case: a pottery class whose time moved 3 pm -> 4 pm -> 7 pm,
recorded first in General and later in its own context."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import DBAPIError, IntegrityError

import app.core.config as config
from app.ai import prompts
from app.ai.context import AgentRuntimeContext
from app.ai.recall import history
from app.ai.recall.contracts import MemoryRef, RecallRequest
from app.ai.recall.history import ReaderAnswer
from app.ai.tools.memory import recall as recall_tools
from app.db.models import (
    ChatMessage, MemoryEvent, MemoryEvidence, RecallReconstruction, RecallReconstructionSource, SemanticMemory,
    WorkingItem,
)
from app.services import (
    chat as chat_service, memory_manager, recall, recall_expand, recall_history, recall_references,
)
from app.services.memory_manager import Source
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)

NOW = datetime.now(timezone.utc)
FIRST = "Remember: my pottery class meets Tuesdays at 3 pm in the annex, and I like my clay wet."
SECOND = "Pottery update: the class moved from 3 pm to 4 pm. Same annex."
THIRD = "Another pottery update, sorry: it moved again, from 4 pm to 7 pm. That replaces 4 pm."
WRONG = "The earliest time I have for pottery is 4 pm, then 7 pm."


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "recall_enabled": True, "recall_history_enabled": True,
        "recall_association_enabled": False, "long_term_memory_enabled": True,
        "memory_manager_log_path": str(tmp_path / "memory.jsonl"), "recall_log_path": str(tmp_path / "recall.jsonl")})
    getter = lambda: settings  # noqa: E731
    for module in (memory_manager, config, recall, recall_references, recall_history, prompts):
        monkeypatch.setattr(module, "get_settings", getter)
    for module in (recall_references, recall_tools):
        monkeypatch.setattr(module, "SessionLocal", database)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


def _say(db, conversation, text, role="user"):
    return chat_service.add_message(db, conversation=conversation, role=role, content=text, turn_id=uuid.uuid4())


def _pottery(db, user):
    """The three updates, as the memory manager and consolidation would have stored them."""
    general = memory_manager.ensure_general(db, user.id)
    memory_manager.focus(db, user.id, general.id)
    conversation = chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
    m1 = _say(db, conversation, FIRST)
    a = memory_manager.set_goal(db, user.id, "Remember the pottery class meets Tuesdays at 3 pm in the annex; the "
                                "user likes their clay wet", source=Source("message", m1.id))
    db.commit()
    m2 = _say(db, conversation, SECOND)
    memory_manager.resolve(db, user.id, a.id, status="completed", reason="the user moved it to 4 pm",
                           source=Source("message", m2.id))
    pottery = memory_manager.open_context(db, user.id, kind="activity", label="Pottery class")
    b = memory_manager.set_goal(db, user.id, "Remember the pottery class meets Tuesdays at 4 pm in the annex",
                                source=Source("message", m2.id))
    db.commit()
    m3 = _say(db, conversation, THIRD)
    memory_manager.resolve(db, user.id, b.id, status="completed", reason="the user moved it to 7 pm",
                           source=Source("message", m3.id))
    c = memory_manager.set_goal(db, user.id, "Remember the pottery class meets Tuesdays at 7 pm in the annex",
                                source=Source("message", m3.id))
    fact = SemanticMemory(user_id=user.id, kind="routine", normalized_key="routine:pottery_class",
                          content="The user's pottery class meets Tuesdays at 7 pm in the annex.",
                          source_message_id=m3.id, source_conversation_id=conversation.id)
    db.add(fact)
    wrong = _say(db, conversation, WRONG, role="assistant")
    db.commit()
    return {"conversation": conversation, "general": general, "pottery": pottery, "m1": m1, "m2": m2, "m3": m3,
            "a": a, "b": b, "c": c, "fact": fact, "wrong": wrong}


def _record(db, user, ref):
    return recall.read(db, user.id, ref).records[0]


def _expand(db, user, *refs, **kwargs):
    return recall_expand.expand(db, user.id, [_record(db, user, ref) for ref in refs], **kwargs)


def _nodes(expansion):
    return {str(p.node): p for p in expansion.passages}


def _collect(db, user, question="What times has my pottery class had, in order, and what is it now?", **kwargs):
    return recall_history.collect(db, user_id=user.id, question=question, about="the pottery class's meeting time",
                                  now=NOW, zone="Europe/Paris", **kwargs)


def _label(run, node: str) -> str:
    return next(p.label for p in run.expansion.passages if str(p.node) == node)


def _cite(run, node, quote):
    label = _label(run, node)
    assert quote in run.expansion.by_label()[label].text, quote
    return {"passage": label, "quote": quote}


def _good(run, lab):
    """What a careful reader returns for the pottery case."""
    m1, m2, m3 = (f"message:{lab[k].id}" for k in ("m1", "m2", "m3"))
    return {"subject": "pottery class", "property": "meeting time", "same_thing": {"sure": True, "note": ""},
            "steps": [{"kind": "first_recorded", "before": "", "after": "3 pm",
                       "cites": [_cite(run, m1, "meets Tuesdays at 3 pm in the annex")]},
                      {"kind": "change", "before": "3 pm", "after": "4 pm",
                       "cites": [_cite(run, m2, "the class moved from 3 pm to 4 pm")]},
                      {"kind": "change", "before": "4 pm", "after": "7 pm",
                       "cites": [_cite(run, m3, "it moved again, from 4 pm to 7 pm")]}],
            "current": {"known": True, "value": "7 pm", "follows": [3],
                        "cites": [_cite(run, m3, "That replaces 4 pm.")]},
            "gaps": [], "conflicts": []}


def _scripted(raw):
    return lambda text: ReaderAnswer(raw, 1.0, 10, 10)


def _snapshot(db, user):
    db.expire_all()
    items = {i.id: (i.status, i.revision, i.text, i.closed_at) for i in
             db.scalars(select(WorkingItem).where(WorkingItem.user_id == user.id))}
    facts = {f.id: (f.status, f.revision, f.content, f.access_count) for f in
             db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == user.id))}
    counts = [db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id))
              for model in (MemoryEvent, MemoryEvidence)]
    return items, facts, counts


# --- expansion ----------------------------------------------------------------------------------------


def test_from_the_current_plan_the_links_reach_every_earlier_one_across_contexts(db, alice):
    lab = _pottery(db, alice)
    expansion = _expand(db, alice, MemoryRef("item", str(lab["c"].id)))
    found = _nodes(expansion)
    for key in ("m1", "m2", "m3"):
        assert f"message:{lab[key].id}" in found
    for key in ("a", "b", "c"):
        assert f"item:{lab[key].id}" in found
    assert found[f"item:{lab['a'].id}"].context_label == lab["general"].label
    assert found[f"item:{lab['c'].id}"].context_label == "Pottery class"
    assert f"fact:{lab['fact'].id}" in found  # the fact written from the last update
    assert expansion.stop == "exhausted" and not expansion.truncated
    # Who said it: the user's words are theirs, word for word; memories are records.
    assert found[f"message:{lab['m1'].id}"].speaker == "user" and found[f"message:{lab['m1'].id}"].text == FIRST
    assert found[f"item:{lab['a'].id}"].speaker == "memory"
    # Oldest first, and each memory says which passages it's linked to.
    times = [p.said_at for p in expansion.passages]
    assert times == sorted(times)
    m2 = found[f"message:{lab['m2'].id}"].label
    assert ("resolution", m2) in found[f"item:{lab['a'].id}"].links
    assert ("origin", m2) in found[f"item:{lab['b'].id}"].links


def test_starting_from_a_fact_reaches_the_same_history(db, alice):
    lab = _pottery(db, alice)
    found = _nodes(_expand(db, alice, MemoryRef("fact", str(lab["fact"].id))))
    assert {f"message:{lab[k].id}" for k in ("m1", "m2", "m3")} <= set(found)


def test_a_fact_is_reached_through_the_messages_in_its_kept_history(db, alice):
    lab = _pottery(db, alice)
    fact = db.get(SemanticMemory, lab["fact"].id)
    fact.source_message_id = None  # only its history knows where it came from
    fact.metadata_ = {"history": [{"content": "The user's pottery class meets Tuesdays at 4 pm.", "revision": 1,
                                   "source_message_id": str(lab["m2"].id), "reason": "restated",
                                   "replaced_by_message_id": str(lab["m3"].id)}]}
    db.commit()
    found = _nodes(_expand(db, alice, MemoryRef("item", str(lab["b"].id)), budget=recall_expand.Budget(max_depth=2)))
    assert f"fact:{fact.id}" in found  # item b -> its message m2 -> the fact whose history cites m2
    assert 'earlier wording, replaced (restated): "The user\'s pottery class meets Tuesdays at 4 pm."' in \
        found[f"fact:{fact.id}"].text


def test_another_users_rows_never_become_passages_even_through_a_forged_link(db, alice, bob):
    lab = _pottery(db, alice)
    theirs = chat_service.get_or_create_conversation(db, user_id=bob.id, conversation_id=None)
    secret = _say(db, theirs, "Bob's secret: my pottery class is at 9 pm.")
    db.add(SemanticMemory(user_id=bob.id, kind="routine", normalized_key="routine:pottery", content="Bob: 9 pm",
                          source_message_id=lab["m3"].id))  # Bob's fact claiming Alice's message
    # A link that memory_manager.link would refuse, written straight to the table.
    db.execute(insert(MemoryEvidence).values(user_id=alice.id, item_id=lab["c"].id, role="origin", message_id=secret.id))
    db.commit()
    expansion = _expand(db, alice, MemoryRef("item", str(lab["c"].id)))
    texts = " ".join(p.text for p in expansion.passages)
    assert "9 pm" not in texts and f"message:{secret.id}" not in _nodes(expansion)
    assert expansion.unavailable == 1 and any("couldn't be read" in g for g in expansion.gaps)


def test_budgets_cut_visibly_and_never_read_as_no_history(db, alice):
    lab = _pottery(db, alice)
    seed = MemoryRef("item", str(lab["c"].id))
    shallow = _expand(db, alice, seed, budget=recall_expand.Budget(max_depth=1))
    assert shallow.stop == "depth_budget" and f"message:{lab['m1'].id}" not in _nodes(shallow)
    assert "older steps may lie further out" in history.coverage_line(shallow)
    few = _expand(db, alice, seed, budget=recall_expand.Budget(max_nodes=8))
    assert few.stop == "node_budget" and few.visited == 8
    short = _expand(db, alice, seed, budget=recall_expand.Budget(max_chars=300, passage_chars=200))
    assert short.stop == "char_budget" and len(short.passages) < short.visited
    narrow = _expand(db, alice, seed, budget=recall_expand.Budget(max_fanout=1))
    assert narrow.truncated and "much-used source" in narrow.truncated[0]
    clipped = _expand(db, alice, seed, budget=recall_expand.Budget(passage_chars=200))
    assert all(len(p.text) <= 204 for p in clipped.passages)
    slow = _expand(db, alice, seed, budget=recall_expand.Budget(seconds=1e-9))
    assert slow.stop == "time_budget"


def test_links_that_loop_are_visited_once(db, alice):
    lab = _pottery(db, alice)
    expansion = _expand(db, alice, MemoryRef("item", str(lab["c"].id)), MemoryRef("item", str(lab["a"].id)),
                        MemoryRef("fact", str(lab["fact"].id)))
    nodes = [str(p.node) for p in expansion.passages]
    assert len(nodes) == len(set(nodes)) == expansion.visited


def test_a_deleted_source_is_a_gap_not_a_missing_history(db, alice):
    lab = _pottery(db, alice)
    db.delete(db.get(ChatMessage, lab["m1"].id))
    db.commit()
    expansion = _expand(db, alice, MemoryRef("item", str(lab["c"].id)))
    found = _nodes(expansion)
    assert f"message:{lab['m1'].id}" not in found
    # The first plan is still reached (through the update that closed it) and still says 3 pm.
    assert "3 pm" in found[f"item:{lab['a'].id}"].text and expansion.stop == "exhausted"


def test_a_failure_part_way_shows_what_was_collected_as_incomplete(db, alice, monkeypatch):
    lab = _pottery(db, alice)
    real, calls = recall_expand._step, []

    def flaky(walk, frontier, depth):
        calls.append(depth)
        if depth == 2:
            raise RuntimeError("boom")
        return real(walk, frontier, depth)
    monkeypatch.setattr(recall_expand, "_step", flaky)
    expansion = _expand(db, alice, MemoryRef("item", str(lab["c"].id)))
    assert expansion.stop == "failed" and expansion.passages
    assert "incomplete" in history.coverage_line(expansion)


def test_an_explicit_scope_marks_what_lies_outside_it(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice, context_query="Pottery class")
    found = _nodes(run.expansion)
    assert found[f"item:{lab['c'].id}"].in_scope is True
    assert found[f"item:{lab['a'].id}"].in_scope is False  # history from General, outside the asked scope
    assert found[f"message:{lab['m1'].id}"].in_scope is None
    assert all(s.ref.kind in ("fact", "episode") or s.context_id == lab["pottery"].id
               for s in recall.search(db, RecallRequest(
                   user_id=alice.id, query="pottery class time", purpose="explicit_search", now=NOW,
                   context_ids=(lab["pottery"].id,), intent="historical")).records)


# --- one lifecycle on every read path -----------------------------------------------------------------


def test_correction_labels_agree_on_search_read_timeline_rereads_and_history(db, alice):
    lab = _pottery(db, alice)
    c = db.get(WorkingItem, lab["c"].id)
    added = db.scalar(select(MemoryEvent).where(MemoryEvent.item_id == c.id, MemoryEvent.kind == "item_added"))
    fix = _say(db, lab["conversation"], "Correction: it's the east annex, not the annex.")
    memory_manager.revise_item(db, alice.id, c, field="text", replaced="in the annex", replacement="in the east annex",
                               source=Source("message", fix.id), dedupe_key=f"fix:{fix.id}")
    db.commit()
    ref = MemoryRef("event", str(added.id))
    read = recall.read(db, alice.id, ref).records[0]
    timeline = next(r for r in recall.timeline(db, alice.id, lab["pottery"].id).records if r.ref == ref)
    reread = next(r for r in recall.search(db, RecallRequest(user_id=alice.id, query="anything", purpose="reply",
                                                             now=NOW, prior_refs=(ref,))).records if r.ref == ref)
    searched = next(r for r in recall.search(db, RecallRequest(
        user_id=alice.id, query="pottery class 7 pm annex", purpose="explicit_search", now=NOW,
        kinds=("event",), intent="historical")).records if r.ref == ref)
    passage = _nodes(_expand(db, alice, MemoryRef("item", str(c.id))))[str(ref)]
    for record in (read, timeline, reread, searched, passage.record):
        assert record.applicability == "no_longer_current" and "corrected it later" in record.lifecycle
    assert "no longer current" in passage.status
    item = recall.read(db, alice.id, MemoryRef("item", str(c.id))).records[0]  # without include_evidence
    assert item.corrected and "corrected by the user since it was first noted" in recall.render(
        recall.read(db, alice.id, MemoryRef("item", str(c.id))), now=NOW, zone="UTC")


# --- the reader's account, checked --------------------------------------------------------------------


def test_a_traceable_account_is_complete_and_says_what_holds_now(db, alice):
    lab = _pottery(db, alice)
    run = recall_history.finish(_collect(db, alice), reader=_scripted(_good(_collect(db, alice), lab)))
    account = run.account
    assert account.status == "complete", account.dropped
    assert [(s.kind, s.after) for s in account.steps] == [("first_recorded", "3 pm"), ("change", "4 pm"),
                                                          ("change", "7 pm")]
    assert account.current.state == "established" and account.current.value == "7 pm"
    assert all(s.support == "user" for s in account.steps)
    shown = run.rendered
    assert "covers only this question" in shown and "3 pm" in shown and "Now: 7 pm" in shown
    assert FIRST in shown  # the passages come with the summary


def test_steps_listed_out_of_order_are_put_in_the_order_they_were_said(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice)
    raw = _good(run, lab)
    raw["steps"].reverse()
    raw["current"]["follows"] = [1]
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert account.reordered and [s.after for s in account.steps] == ["3 pm", "4 pm", "7 pm"]


def test_an_untraceable_step_is_dropped_and_what_rests_on_it_is_unsettled(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice)
    raw = _good(run, lab)
    raw["steps"][2]["cites"] = [{"passage": raw["steps"][2]["cites"][0]["passage"], "quote": "moved to 7 pm sharp"}]
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert account.status == "partial" and len(account.steps) == 2
    assert any("quote not found" in d for d in account.dropped)
    assert account.current.state == "unsettled"  # 7 pm rested on the step that's gone
    assert "unsettled" in history.render(account, run.expansion, zone="UTC")


def test_a_dropped_earlier_step_leaves_the_account_partial_but_the_current_value_standing(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice)
    raw = _good(run, lab)
    raw["steps"][0]["cites"] = [{"passage": "p999", "quote": "meets Tuesdays"}]
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert account.status == "partial" and any("wasn't given" in d for d in account.dropped)
    assert account.current.state == "established"


def test_her_own_earlier_words_never_settle_a_value(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice)
    wrong = f"message:{lab['wrong'].id}"
    assert _nodes(run.expansion).get(wrong) is None or _nodes(run.expansion)[wrong].speaker == "columbina"
    # Her answer isn't linked to any memory; put it in as a passage to see how it's treated.
    from dataclasses import replace as _replace

    from app.ai.recall.history import NodeRef, Passage
    her = Passage("p99", NodeRef("message", str(lab["wrong"].id)), "columbina", WRONG, False,
                  datetime.now(timezone.utc), "x", 1)
    run.expansion = _replace(run.expansion, passages=[*run.expansion.passages, her])
    raw = _good(run, lab)
    raw["steps"] = [{"kind": "first_recorded", "before": "", "after": "4 pm",
                     "cites": [{"passage": "p99", "quote": "The earliest time I have for pottery is 4 pm"}]},
                    raw["steps"][2]]
    raw["current"] = {"known": True, "value": "7 pm", "follows": [1, 2],
                      "cites": [{"passage": "p99", "quote": "then 7 pm"}]}
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert next(s for s in account.steps if s.after == "4 pm").support == "her_words_only"
    assert account.current.state == "unsettled" and "Columbina" in account.current.note
    assert "ONLY in your own earlier words" in history.render(account, run.expansion, zone="UTC")


def test_under_a_scope_the_current_value_needs_support_inside_it(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice, context_query="Pottery class")
    raw = _good(run, lab)
    a = f"item:{lab['a'].id}"
    raw["current"] = {"known": True, "value": "3 pm", "follows": [1],
                      "cites": [_cite(run, a, "meets Tuesdays at 3 pm in the annex")]}
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert account.current.state == "not_established" and "outside the asked scope" in account.current.note


def test_a_possibility_is_shown_as_considered_never_as_a_change(db, alice):
    lab = _pottery(db, alice)
    run = _collect(db, alice)
    raw = _good(run, lab)
    raw["steps"].insert(1, {"kind": "considered", "before": "3 pm", "after": "the annex",
                            "cites": [_cite(run, f"message:{lab['m2'].id}", "Same annex.")]})
    raw["current"]["follows"] = [4]
    account = recall_history.finish(run, reader=_scripted(raw)).account
    assert account.steps[1].kind == "considered"
    assert "considered, not adopted" in history.render(account, run.expansion, zone="UTC")


def test_a_failed_reading_is_said_as_failed_with_the_passages_still_shown(db, alice):
    _pottery(db, alice)
    run = recall_history.finish(_collect(db, alice), reader=lambda text: ReaderAnswer(None, 1.0, error="Timeout"))
    assert run.account.status == "failed" and "FAILED" in run.rendered and FIRST in run.rendered
    nothing = recall_history.finish(_collect(db, alice, question="What about my violin lessons?"),
                                    reader=_scripted({}))
    assert nothing.account.status in ("no_history", "failed") and "nothing is recorded" not in nothing.rendered


def test_the_reader_is_given_the_question_and_the_scope(db, alice):
    _pottery(db, alice)
    seen = []
    recall_history.finish(_collect(db, alice, context_query="Pottery class"),
                          reader=lambda text: seen.append(text) or ReaderAnswer({}, 1.0))
    assert "QUESTION (the user's words): What times has my pottery class had" in seen[0]
    assert "SCOPE: only Pottery class was asked about." in seen[0] and "OUTSIDE SCOPE" in seen[0]


# --- read-only, and what the reply keeps --------------------------------------------------------------


def test_rebuilding_a_history_changes_nothing_it_reads(db, alice):
    lab = _pottery(db, alice)
    before = _snapshot(db, alice)
    run = recall_history.build(db, user_id=alice.id, question="What was the pottery time before?", now=NOW,
                               zone="UTC", reader=_scripted(_good(_collect(db, alice), lab)))
    db.commit()
    assert run.account.steps and _snapshot(db, alice) == before


def _reply_with_history(db, user, lab, monkeypatch, raw=None):
    monkeypatch.setattr(recall_history, "_reader", lambda: (lambda text: ReaderAnswer(
        raw if raw is not None else _good(_collect(db, user), lab), 1.0, 100, 50)))
    asked = _say(db, lab["conversation"], "What times has my pottery class had, in order, and what is it now?")
    turn = recall_references.for_reply(user.id, lab["conversation"].id, asked.id, asked.content, "UTC")
    tools = {t.name: t for t in recall_tools.build_recall_tools(turn)}
    shown = asyncio.run(tools["recall_history"].ainvoke({"question": asked.content, "about": "pottery class time"}))
    reply = chat_service.add_message(db, conversation=lab["conversation"], role="assistant",
                                     content="It was 3 pm, then 4 pm, and now 7 pm.", turn_id=uuid.uuid4(), commit=False)
    recall_references.save_receipt(db, turn, assistant_message_id=reply.id, conversation_id=lab["conversation"].id,
                                   reply=reply.content)
    db.commit()
    return turn, shown, reply


def test_the_reply_keeps_what_its_history_was_built_from(db, alice, monkeypatch):
    lab = _pottery(db, alice)
    turn, shown, reply = _reply_with_history(db, alice, lab, monkeypatch)
    assert "Now: 7 pm" in shown and turn.tool_calls == 1 and turn.tool_statuses == ["history_complete"]
    row = db.scalar(select(RecallReconstruction).where(RecallReconstruction.user_id == alice.id))
    assert row.status == "complete" and row.model and row.input_digest and row.question.startswith("What times")
    assert row.coverage["stop"] == "exhausted" and row.coverage["input_tokens"] == 100
    assert "quote" not in str(row.account)  # spans, never copied words
    steps = row.account["steps"]
    sources = {s.label: s for s in db.scalars(select(RecallReconstructionSource).where(
        RecallReconstructionSource.reconstruction_id == row.id))}
    first = steps[0]["cites"][0]
    assert sources[first["passage"]].message_id == lab["m1"].id and sources[first["passage"]].cited
    assert sources[first["passage"]].speaker == "user" and sources[first["passage"]].fingerprint
    assert FIRST[first["start"]:first["end"]] == "meets Tuesdays at 3 pm in the annex"
    # The memories among the passages were supplied to the reply, like anything a tool found.
    assert MemoryRef("item", str(lab["a"].id)) in turn.supplied
    inspected = recall_references.inspect(db, alice.id)["receipts"][0][1]["histories"]
    assert inspected[0]["status"] == "complete" and any(s["source"] == f"message:{lab['m1'].id}"
                                                        for s in inspected[0]["sources"])
    # Deleting a source deletes its provenance row, never the reconstruction.
    db.delete(db.get(ChatMessage, lab["m1"].id))
    db.commit()
    db.expire_all()
    left = db.scalars(select(RecallReconstructionSource).where(RecallReconstructionSource.reconstruction_id == row.id))
    assert all(s.message_id != lab["m1"].id for s in left) and db.get(RecallReconstruction, row.id) is not None


def test_a_source_row_must_belong_to_the_owner(db, alice, bob):
    lab = _pottery(db, alice)
    theirs = chat_service.get_or_create_conversation(db, user_id=bob.id, conversation_id=None)
    secret = _say(db, theirs, "Bob's words")
    answer = chat_service.add_message(db, conversation=lab["conversation"], role="assistant", content="!",
                                      turn_id=uuid.uuid4())
    from app.db.models import RecallReceipt

    receipt = RecallReceipt(user_id=alice.id, assistant_message_id=answer.id, conversation_id=lab["conversation"].id,
                            schema_version="t", purpose="reply", status="matched")
    db.add(receipt)
    db.flush()
    rec = RecallReconstruction(receipt_id=receipt.id, user_id=alice.id, schema_version="t", prompt_version="t",
                               status="partial", question="?")
    db.add(rec)
    db.flush()
    db.add(RecallReconstructionSource(reconstruction_id=rec.id, user_id=alice.id, label="p1", speaker="user",
                                      depth=1, cited=False, truncated=False, fingerprint="x", message_id=secret.id))
    with pytest.raises((DBAPIError, IntegrityError)):
        db.flush()
    db.rollback()


def test_without_the_switch_there_is_no_history_tool_and_no_prompt_line(db, alice, on):
    on.recall_history_enabled = False
    lab = _pottery(db, alice)
    turn = recall_references.for_reply(alice.id, lab["conversation"].id, None, "pottery?", "UTC")
    assert "recall_history" not in {t.name for t in recall_tools.build_recall_tools(turn)}
    context = AgentRuntimeContext(user_id=alice.id, recall="x", recall_turn=turn)
    assert "recall_history" not in prompts.build_system_prompt(context, [])
    on.recall_history_enabled = True
    assert "recall_history" in prompts.build_system_prompt(context, [])
