"""Her thoughts reaching memory (docs/COLUMBINA_THOUGHT_MEMORY_SPEC.md §9): the handoff from her mind, the
``thought_memory`` consumer, lifecycles, recall's origin labels, pruning. Real PostgreSQL (``TEST_DATABASE_URL``);
the thinker and the keep/skip judgement are scripted, no model calls."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.ai.mind.contracts import Scope
from app.ai.mind.hard import HardThought, Problem
from app.db.models import (
    MemoryEvidence,
    MemoryManagerState,
    MindThought,
    Observation,
    ObservationProcessing,
    WorkingItem,
)
from app.services import (
    autonomous_speech,
    chat as chat_service,
    cognitive_live,
    memory_manager,
    mind,
    mind_conversation,
    mind_hard,
    observation,
    recall,
    recall_references,
    thought_memory,
)
from app.ai.recall.contracts import RecallRequest
from app.core.config import get_settings
from app.services.thought_memory import Judgement
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


@pytest.fixture
def mind_env(db_env, monkeypatch, tmp_path):
    """A real hard-thought run in a conversation, with a scripted thinker; returns a way to think."""
    said = []
    for name in ("mind_hard_log_path", "mind_log_path", "memory_manager_log_path", "recall_log_path",
                 "observation_log_path"):
        if hasattr(get_settings(), name):
            monkeypatch.setattr(get_settings(), name, str(tmp_path / f"{name}.jsonl"))
    monkeypatch.setattr(mind, "publish", lambda user_id, thought: None)
    monkeypatch.setattr(mind, "_set_thinking", lambda key, topic: None)
    monkeypatch.setattr(autonomous_speech, "submit", lambda db, **kw: said.append(kw) or "Proposed.")
    monkeypatch.setattr(cognitive_live, "load_state", lambda uid, cid: {})
    monkeypatch.setattr(mind_hard, "_character", lambda db: ())
    supplied: list[str] = []

    def for_thought(db, *, user_id, source, activity, query, refs=None):
        if refs is not None:
            refs.extend(supplied)
        return "Memory search: matched." if supplied else None

    monkeypatch.setattr(recall_references, "for_thought", for_thought)
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = chat_service.add_message(db, conversation=conversation, role="user",
                                           content="My sister hasn't called in weeks.", turn_id=uuid.uuid4())
        db.commit()
        ids = SimpleNamespace(user=user.id, conversation=conversation.id, message=message.id)
    scope = Scope(ids.user, "conversation", ids.conversation)

    def think(kind, conclusion, *, thinking="She has gone quiet before when work was hard.", question=None,
              cancelled=None, topic="their sister"):
        att = mind_hard.Attention()

        def answer(inp, situation):
            att.cancelled = cancelled  # cancelled while she thought
            return HardThought(kind, conclusion, thinking=thinking, question=question)

        monkeypatch.setattr(mind_hard, "thinker", lambda: SimpleNamespace(
            think=answer, instructions=lambda trigger, user, situation: "system"))
        mind_hard.run(mind_conversation.SOURCE, scope, att, Problem("quiet", "x", topic, "sister"), 100.0)
        with db_env.db() as db:
            return db.scalar(select(MindThought).where(MindThought.user_id == ids.user)
                             .order_by(MindThought.created_at.desc()).limit(1))

    return SimpleNamespace(think=think, ids=ids, scope=scope, said=said, supplied=supplied, db=db_env.db)


def _judge(keep=True, relation="new", item=None, why="it matters later"):
    calls = []

    def judge(thought, candidates):
        calls.append((thought, candidates))
        return Judgement(keep=keep, relation=relation, item=item, why=why)
    judge.calls = calls
    return judge


def _observations(db, user_id):
    return list(db.scalars(select(Observation).where(Observation.user_id == user_id, Observation.source == "mind")))


def _items(db, user_id, kind=None):
    query = select(WorkingItem).where(WorkingItem.user_id == user_id)
    if kind:
        query = query.where(WorkingItem.kind == kind)
    return list(db.scalars(query.order_by(WorkingItem.created_at)))


def _roles(db, item_id):
    return sorted((e.role, "thought" if e.thought_id else "message" if e.message_id else "observation")
                  for e in db.scalars(select(MemoryEvidence).where(MemoryEvidence.item_id == item_id)))


def test_a_finished_hard_thought_is_handed_over_with_what_she_read(mind_env):
    mind_env.supplied.append("fact:3f0a")
    thought = mind_env.think("guess", "Their sister might be overwhelmed at work.")
    with mind_env.db() as db:
        [report] = _observations(db, mind_env.ids.user)
        receipts = list(db.scalars(select(ObservationProcessing).where(
            ObservationProcessing.observation_id == report.id)))
        focused = memory_manager.focused(db, mind_env.ids.user)
    assert thought.status == "done" and report.thought_id == thought.id
    assert (report.kind, report.epistemic, report.actor, report.context_id) == (
        "thought_concluded", "inferred", "columbina", focused.id)
    assert report.payload["conclusion"] == "Their sister might be overwhelmed at work."
    assert report.payload["reason"] == "She has gone quiet before when work was hard."  # whole, not cut
    assert report.payload["basis"] == {"messages": [str(mind_env.ids.message)], "memories": ["fact:3f0a"],
                                       "observations": []}
    assert [r.consumer for r in receipts] == ["thought_memory"]  # never the code-only memory consumer
    # Replaying the handoff never duplicates it.
    with mind_env.db() as db:
        row = db.get(MindThought, thought.id)
        mind_hard.to_memory(db, mind_conversation.SOURCE, mind_env.scope, Problem("quiet", "x", "their sister", "s"),
                          SimpleNamespace(conversation=(), memory_refs=()), HardThought("guess", row.text), row)
        db.commit()
        assert len(_observations(db, mind_env.ids.user)) == 1


def test_dropped_thoughts_are_not_handed_over(mind_env):
    thought = mind_env.think("guess", "Maybe they're fine.", cancelled="they changed the subject")
    with mind_env.db() as db:
        assert thought.status == "dropped" and _observations(db, mind_env.ids.user) == []


def test_a_guess_becomes_an_open_suspicion_never_a_fact(mind_env):
    thought = mind_env.think("guess", "Their sister might be overwhelmed at work.")
    assert thought_memory.run_once(judge=_judge()) is True
    assert thought_memory.run_once(judge=_judge()) is False  # nothing left: a retry never duplicates
    with mind_env.db() as db:
        [entry] = _items(db, mind_env.ids.user, "hypothesis")
        assert (entry.status, entry.owner, entry.epistemic) == ("open", "columbina", "inferred")
        assert entry.reason == "She has gone quiet before when work was hard."
        assert timedelta(days=13.9) < entry.due_at - entry.created_at < timedelta(days=14.1)
        assert _roles(db, entry.id) == [("basis", "message"), ("origin", "observation"), ("origin", "thought")]
        result = recall.search(db, RecallRequest(user_id=mind_env.ids.user, query="sister overwhelmed work",
                                                 purpose="explicit_search", now=datetime.now(timezone.utc)))
        rendered = recall.render(result, now=datetime.now(timezone.utc), zone="UTC")
    assert "You suspected (not confirmed)" in rendered
    assert "Why you thought so: She has gone quiet before" in rendered
    assert thought.id


def test_reaching_the_same_guess_again_is_a_restatement_not_support(mind_env):
    mind_env.think("guess", "Their sister might be overwhelmed at work.")
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        [entry] = _items(db, mind_env.ids.user, "hypothesis")
    again = mind_env.think("guess", "Work is probably swamping their sister.")
    judge = _judge(relation="same", item=memory_manager.ref(entry))
    thought_memory.run_once(judge=judge)
    assert [c.ref for c in judge.calls[0][1]] == [memory_manager.ref(entry)]  # she saw the open one
    with mind_env.db() as db:
        assert len(_items(db, mind_env.ids.user, "hypothesis")) == 1
        roles = _roles(db, entry.id)
        assert ("restated", "thought") in roles and not any(role == "support" for role, _ in roles)
        evidence = db.scalar(select(MemoryEvidence).where(MemoryEvidence.item_id == entry.id,
                                                          MemoryEvidence.role == "restated",
                                                          MemoryEvidence.thought_id.is_not(None)))
    assert evidence.thought_id == again.id


def test_a_later_decision_supersedes_the_earlier_one_and_both_stay_readable(mind_env):
    mind_env.think("decision", "I'll let them bring the sister up themselves.", topic="how to help")
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        [first] = _items(db, mind_env.ids.user, "decision")
    mind_env.think("decision", "I'll gently ask whether they've reached out to her.", topic="how to help")
    thought_memory.run_once(judge=_judge(relation="replaces", item=memory_manager.ref(first)))
    with mind_env.db() as db:
        old, new = _items(db, mind_env.ids.user, "decision")
        assert (old.id, old.status, old.text) == (first.id, "superseded", first.text)
        assert new.status == "active" and "reached out" in new.text
        assert ("resolution", "thought") in _roles(db, old.id)
        records = [recall.read(db, mind_env.ids.user, f"item:{entry.id}").records[0] for entry in (old, new)]
    assert [recall.origin_label(r) for r in records] == ["You had decided (since changed)", "You decided"]


def test_an_intention_reaches_memory_only_through_the_handoff(mind_env):
    mind_env.think("intention", "Ask how the call with their sister went.")
    with mind_env.db() as db:
        assert _items(db, mind_env.ids.user, "intention") == []  # no direct write any more
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        [entry] = _items(db, mind_env.ids.user, "intention")
        assert entry.context_id == memory_manager.focused(db, mind_env.ids.user).id
        assert entry.status == "active" and entry.text == "Ask how the call with their sister went."


def test_a_question_of_hers_is_kept_as_her_open_question(mind_env):
    mind_env.think("question", "I don't know which sister they mean.", question="Which sister do you mean?")
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        [entry] = _items(db, mind_env.ids.user, "question")
    assert (entry.owner, entry.status, entry.text) == ("columbina", "open", "Which sister do you mean?")


def test_feelings_and_what_the_judgement_skips_leave_no_memory(mind_env):
    mind_env.think("feeling", "I feel a bit sad for them.")
    judge = _judge()
    thought_memory.run_once(judge=judge)
    mind_env.think("decision", "Answer the next message warmly.", topic="tone")
    thought_memory.run_once(judge=_judge(keep=False, why="only mattered right then"))
    with mind_env.db() as db:
        reasons = sorted(r.reason for r in db.scalars(select(ObservationProcessing).where(
            ObservationProcessing.user_id == mind_env.ids.user, ObservationProcessing.status == "skipped")))
        assert _items(db, mind_env.ids.user) == []
    assert judge.calls == []  # a feeling never reaches the judgement
    assert reasons == ["feelings are the inner state's", "not worth keeping: only mattered right then"]


def test_a_failing_judgement_retries_then_fails_and_an_unknown_id_is_new(mind_env, monkeypatch):
    monkeypatch.setattr(get_settings(), "observation_max_attempts", 2)
    mind_env.think("guess", "They may feel guilty about not calling.")

    def broken(thought, candidates):
        raise RuntimeError("model down")

    thought_memory.run_once(judge=broken)
    with mind_env.db() as db:
        receipt = db.scalar(select(ObservationProcessing).where(ObservationProcessing.user_id == mind_env.ids.user))
        assert (receipt.status, receipt.attempts) == ("retry", 1)
        db.execute(update(ObservationProcessing).values(next_attempt_at=func.now()))
        db.commit()
    thought_memory.run_once(judge=broken)
    with mind_env.db() as db:
        receipt = db.scalar(select(ObservationProcessing).where(ObservationProcessing.user_id == mind_env.ids.user))
        assert receipt.status == "failed" and _items(db, mind_env.ids.user) == []
    mind_env.think("guess", "Their sister moved recently.", topic="the move")
    thought_memory.run_once(judge=_judge(relation="same", item="deadbeef"))
    with mind_env.db() as db:
        assert [e.text for e in _items(db, mind_env.ids.user, "hypothesis")] == ["Their sister moved recently."]


def test_a_memory_failure_never_loses_the_thought(mind_env, monkeypatch):
    def refuse(db, report):
        raise RuntimeError("observation store down")

    monkeypatch.setattr(observation, "record", refuse)
    thought = mind_env.think("guess", "They miss her.")
    with mind_env.db() as db:
        assert db.get(MindThought, thought.id).status == "done" and _observations(db, mind_env.ids.user) == []


def test_prune_keeps_the_thoughts_memory_points_to(mind_env):
    kept = mind_env.think("guess", "Their sister might be overwhelmed at work.")
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        loose = MindThought(user_id=mind_env.ids.user, level="hard", text="An idle idea.")
        db.add(loose)
        db.commit()
        db.execute(update(MindThought).values(created_at=datetime.now(timezone.utc) - timedelta(days=30)))
        db.commit()
        mind.prune(db)
        assert db.get(MindThought, kept.id) is not None and db.get(MindThought, loose.id) is None


def test_a_suspicion_nothing_settles_becomes_unresolved_and_only_evidence_settles_it(mind_env):
    mind_env.think("guess", "Their sister might be overwhelmed at work.")
    thought_memory.run_once(judge=_judge())
    with mind_env.db() as db:
        [entry] = _items(db, mind_env.ids.user, "hypothesis")
        with pytest.raises(memory_manager.MemoryError):  # her thinking it is no evidence
            memory_manager.resolve(db, mind_env.ids.user, entry.id, status="supported", reason="I'm sure")
        db.rollback()
        later = datetime.now(timezone.utc) + timedelta(days=15)
        db.execute(update(MemoryManagerState).values(next_check_at=later - timedelta(days=1)))
        db.commit()
        memory_manager.tick_due(db, now=later)
        db.refresh(entry)
    assert (entry.status, entry.status_reason) == ("unresolved", "nothing settled it")
