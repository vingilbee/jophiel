"""Corrections reach every current memory they affect (docs/COLUMBINA_MEMORY_CORRECTIONS.md): semantic
consolidation hands an explicit correction to the memory manager, which revises only the affected words of
current facts and working items, keeps the past as history, and records the correction so it can be
recalled. Real PostgreSQL (``TEST_DATABASE_URL``). The two model calls (reading the message, judging the
candidates) are scripted: these tests check what code enforces, not what a real model would say."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

import app.core.config as config
import app.db.session as db_session
from app.ai.recall.contracts import MemoryRef, RecallRequest
from app.db.models import (
    Conversation, MemoryCorrection, MemoryEvent, MemoryEvidence, MemoryJob, SemanticMemory, User, WorkingItem,
)
from app.schemas.long_term_memory import CorrectionCandidate, MemoryCandidate, MemoryExtraction
from app.services import (
    chat as chat_service, long_term_memory, memory_corrections, memory_manager, memory_reset, recall,
)
from app.services.memory_corrections import Decision, Judgement
from app.services.memory_manager import Source
from app.workers import memory as memory_worker
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)

KEY = "fact:cpsc310_card:velvet_otter"
TEACH = ("Another bit from my CPSC 310 notes: Factory moves complicated object construction out of the product "
         "class. For REST, GET, PUT and DELETE should be idempotent. I call these notes 'velvet otter', and marked "
         "them with a purple square. This is important because I'm gonna test you later.")
CORRECT = ("Small correction: I changed the velvet otter marker from a purple square to a red crescent. The "
           "Factory and REST topics stay the same.")
FACT_OLD = "'Velvet otter' is the user's CPSC 310 card on Factory and REST, marked with a purple square."
FACT_NEW = "'Velvet otter' is the user's CPSC 310 card on Factory and REST, marked with a red crescent."


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "long_term_memory_enabled": True, "memory_corrections_enabled": True,
        "recall_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "recall_log_path": str(tmp_path / "recall.jsonl"), "redis_url": None})
    for module in (memory_manager, memory_corrections, long_term_memory, recall, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


# --- the scripted models ---------------------------------------------------------------------------


def _reads(memories=(), corrections=()):
    """A scripted consolidation model: always reads these, whatever the message."""
    return lambda user, assistant, earlier=(), known=(): MemoryExtraction(
        memories=list(memories), corrections=list(corrections))


def _correction(previous="purple square", new="red crescent", kind="changed",
                words="I changed the velvet otter marker from a purple square to a red crescent", subject="velvet otter"):
    return CorrectionCandidate(subject=subject, attribute="marker", previous_value=previous,
                               new_value=new, kind=kind, user_words=words)


def _fact(content=FACT_NEW):
    return MemoryCandidate(kind="fact", normalized_key=KEY, content=content, importance=0.75, confidence=0.9)


def _swap(old, new, *, unsure=()):
    """A judge that does what a careful model should: the candidates stating the old value are affected
    (only that value is swapped), the rest aren't; ``unsure`` refs are left to doubt."""
    def judge(correction, candidates):
        out = []
        for c in candidates:
            if c.ref in unsure:
                out.append(Decision(ref=c.ref, verdict="unsure", reason="another thing by that name?"))
                continue
            field = next((f for f, value in c.fields.items() if old in value), None)
            out.append(Decision(ref=c.ref, verdict="affected", field=field, replace=old, replacement=new)
                       if field else Decision(ref=c.ref, verdict="unaffected", reason="doesn't state it"))
        return Judgement(decisions=out)
    return judge


# --- the lab's memories, before the correction -----------------------------------------------------


def _conversation(db, user):
    return chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)


def _exchange(db, conversation, said, answer="Got it."):
    asked = chat_service.add_message(db, conversation=conversation, role="user", content=said, turn_id=uuid.uuid4())
    reply = chat_service.add_message(db, conversation=conversation, role="assistant", content=answer,
                                     turn_id=uuid.uuid4())
    job = long_term_memory.schedule_consolidation(db, user_id=conversation.user_id, conversation_id=conversation.id,
                                                  assistant_message_id=reply.id, commit=False)
    db.commit()
    return asked, job


def _studied(db, user):
    """What the frontend lab had (docs/reports/memory-links-trace-2026-10-20.md): a fact, a broad goal, a
    sub-goal and an intention, all from the teaching message; two of them state the purple square."""
    conversation = _conversation(db, user)
    taught, job = _exchange(db, conversation, TEACH, "Got it: velvet otter, purple square.")
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    context = memory_manager.open_context(db, user.id, kind="activity", label="CPSC 310 review")
    source = Source("message", taught.id)
    broad = memory_manager.set_goal(db, user.id, "Remember the user's CPSC 310 notes and their labels for a later "
                                    "test", done_when="The user tests me on the notes", source=source)
    sub = memory_manager.set_goal(db, user.id, "Review the user's CPSC 310 notes, including Factory and REST "
                                  "idempotency ('velvet otter,' purple square)", parent_id=broad.id,
                                  done_when="The user tests me on these notes", source=source)
    intention = memory_manager.note_intention(db, user.id, "Remember that 'velvet otter' means the user's CPSC 310 "
                                              "notes on Factory and REST URI/idempotency, marked with a purple square.",
                                              epistemic="reported", source=source)
    fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key=KEY, content=FACT_OLD, importance=0.75,
                          confidence=0.9, source_conversation_id=conversation.id, source_message_id=taught.id)
    db.add(fact)
    db.commit()
    return {"conversation": conversation, "taught": taught, "context": context, "broad": broad, "sub": sub,
            "intention": intention, "fact": fact}


def _consolidate(db, monkeypatch, job, extraction):
    monkeypatch.setattr(long_term_memory, "_extract", extraction)
    assert long_term_memory.process_job(db, job.id) is True
    db.expire_all()


def _apply_next(db, judge) -> bool:
    db.expire_all()
    due = memory_corrections.next_due(db)
    db.rollback()
    if due is None:
        return False
    return memory_corrections.process(db, due, judge=judge, embed=lambda text: None)


def _corrected(db, monkeypatch, user, lab, said=CORRECT, *, memories=(_fact(),), corrections=(_correction(),),
               judge=None):
    asked, job = _exchange(db, lab["conversation"], said, "A red crescent it is.")
    _consolidate(db, monkeypatch, job, _reads(memories, corrections))
    judge = judge or _swap("purple square", "red crescent")
    assert _apply_next(db, judge) is True
    db.expire_all()
    return asked


def _events(db, user, kind=None):
    query = select(MemoryEvent).where(MemoryEvent.user_id == user.id).order_by(MemoryEvent.id)
    return list(db.scalars(query.where(MemoryEvent.kind == kind) if kind else query))


def _links(db, **where):
    query = select(MemoryEvidence)
    for column, value in where.items():
        query = query.where(getattr(MemoryEvidence, column) == value)
    return list(db.scalars(query))


# --- 1. the acceptance case ------------------------------------------------------------------------


def test_a_correction_revises_only_the_affected_words_of_current_memories(db, alice, monkeypatch):
    lab = _studied(db, alice)
    broad_before, sub_before, intention_before = lab["broad"].text, lab["sub"].text, lab["intention"].text
    due_before = lab["intention"].due_at
    created = {e.item_id: e.text for e in _events(db, alice, "item_added")}
    asked = _corrected(db, monkeypatch, alice, lab)

    fact = db.scalar(select(SemanticMemory).where(SemanticMemory.user_id == alice.id, SemanticMemory.normalized_key == KEY))
    assert fact.content == FACT_NEW and fact.status == "active"
    history = fact.metadata_["history"]
    assert history[-1]["content"] == FACT_OLD and history[-1]["reason"] == "correction"
    assert history[-1]["source_message_id"] == str(lab["taught"].id)
    assert history[-1]["replaced_by_message_id"] == str(asked.id)

    sub, intention, broad = (db.get(WorkingItem, lab[k].id) for k in ("sub", "intention", "broad"))
    assert sub.text == sub_before.replace("purple square", "red crescent")
    assert intention.text == intention_before.replace("purple square", "red crescent")
    for entry in (sub, intention):
        assert "Factory and REST" in entry.text and "purple" not in entry.text and entry.revision == 2
        assert entry.status == "active" and entry.closed_at is None
    assert sub.parent_id == broad.id and sub.done_when == "The user tests me on these notes"
    assert intention.kind == "intention" and intention.owner == "columbina" and intention.due_at == due_before
    assert broad.text == broad_before and broad.revision == 1  # shares the source, states no marker

    # The past stays as it was: the creation events still say purple square.
    assert {e.item_id: e.text for e in _events(db, alice, "item_added")} == created
    revised = _events(db, alice, "item_revised")
    assert {e.item_id for e in revised} == {sub.id, intention.id}
    assert all('("purple square" -> "red crescent")' in e.text for e in revised)

    # The correction itself: one event, when the user said it, linked to their message.
    [event] = _events(db, alice, "corrected")
    assert event.text == 'The user changed the velvet otter marker from "purple square" to "red crescent".'
    assert event.context_id == lab["context"].id and event.epistemic == "reported"
    assert event.occurred_at == asked.created_at.replace(tzinfo=timezone.utc)
    assert {(l.role, l.message_id) for l in _links(db, event_id=event.id)} == {("correction", asked.id)}
    for entry in (sub, intention):
        roles = {(l.role, l.message_id) for l in _links(db, item_id=entry.id)}
        assert roles == {("origin", lab["taught"].id), ("correction", asked.id)}
    assert {(l.role, l.message_id) for l in _links(db, item_id=broad.id)} == {("origin", lab["taught"].id)}

    row = db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id))
    assert (row.status, row.kind, row.previous_value, row.new_value, row.event_id) == (
        "applied", "changed", "purple square", "red crescent", event.id)
    assert {r["ref"] for r in row.result["revised"]} == {f"item:{sub.id}", f"item:{intention.id}"}
    assert {"ref": f"item:{broad.id}", "verdict": "unaffected", "reason": "doesn't state it",
            "why_candidate": "came from the same message"} in row.result["unchanged"]


def test_recall_tells_the_current_marker_from_the_history_of_the_correction(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _corrected(db, monkeypatch, alice, lab)
    general = memory_manager.ensure_general(db, alice.id)
    memory_manager.focus(db, alice.id, general.id)  # a fresh chat elsewhere
    db.commit()
    now = datetime.now(timezone.utc)

    current = recall.search(db, RecallRequest(user_id=alice.id, query="What is the current marker for velvet otter?",
                                              purpose="explicit_search", now=now, intent="current"))
    assert current.status == "matched"
    for record in current.records:
        if "purple square" in record.content:  # only the record of the change itself may still say it
            assert "red crescent" in record.content, record
    contents = " ".join(r.content for r in current.records)
    assert "red crescent" in contents
    fact = next(r for r in current.records if r.ref.kind == "fact")
    assert fact.content == FACT_NEW and fact.lifecycle.startswith('corrected by the user; it said before: "')

    relevant = recall.search(db, RecallRequest(user_id=alice.id, query="velvet otter purple square",
                                               purpose="explicit_search", now=now))
    by_ref = {str(r.ref): r for r in relevant.records}
    created = [e for e in _events(db, alice, "item_added") if e.item_id == lab["intention"].id][0]
    old = by_ref.get(f"event:{created.id}")
    if old is not None:  # still findable, but as what it said then
        assert old.applicability == "no_longer_current" and "corrected it later" in old.lifecycle

    asked = recall.search(db, RecallRequest(user_id=alice.id, query="What did I correct about velvet otter?",
                                            purpose="explicit_search", now=now))
    [change] = [r for r in asked.records if r.subkind == "corrected"]
    assert "purple square" in change.content and "red crescent" in change.content
    assert change.evidence and change.evidence[0].role == "correction"
    rendered = recall.render(asked, now=now, zone="UTC")
    assert "corrected by the user since it was first noted" in rendered
    read = recall.read(db, alice.id, MemoryRef("item", str(lab["intention"].id)), include_evidence=True)
    assert "red crescent" in read.records[0].content


# --- 2. code decides what an edit may change -------------------------------------------------------


def test_edits_that_change_more_than_the_value_are_refused(db, alice, monkeypatch):
    lab = _studied(db, alice)
    sub_ref, broad_ref, intention_ref = (f"item:{lab[k].id}" for k in ("sub", "broad", "intention"))

    def overreaching(correction, candidates):
        return Judgement(decisions=[
            # Drops "Factory and REST" along with the marker.
            Decision(ref=sub_ref, verdict="affected", field="text",
                     replace="Factory and REST idempotency ('velvet otter,' purple square)",
                     replacement="('velvet otter,' red crescent)"),
            # Writes the marker into a goal that never stated it.
            Decision(ref=broad_ref, verdict="affected", field="text", replace="labels",
                     replacement="labels (velvet otter: red crescent)"),
            # Words that aren't there.
            Decision(ref=intention_ref, verdict="affected", field="text", replace="a purple circle",
                     replacement="a red crescent"),
        ])
    _corrected(db, monkeypatch, alice, lab, memories=(), judge=overreaching)
    for key in ("sub", "broad", "intention"):
        assert db.get(WorkingItem, lab[key].id).revision == 1
    row = db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id))
    reasons = {u["ref"]: u["reason"] for u in row.result["unchanged"]}
    assert reasons[sub_ref] == "it removes more than the old value"
    assert reasons[broad_ref] == "the replacement adds more than the new value"
    assert reasons[intention_ref] == "the words to replace aren't there exactly once"
    assert row.status == "applied" and row.result["revised"] == []
    assert len(_events(db, alice, "corrected")) == 1  # the correction is still remembered


def test_without_a_stated_old_value_the_memory_supplies_it_and_the_record_says_so(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _corrected(db, monkeypatch, alice, lab, "Actually the velvet otter marker is a red crescent.", memories=(),
               corrections=(_correction(previous=None, kind="unspecified", subject="velvet otter marker",
                                        words="the velvet otter marker is a red crescent"),))
    # Found by the subject alone, though the items say "marked", not "marker" (as the real model phrased it).
    assert "red crescent" in db.get(WorkingItem, lab["sub"].id).text
    assert "red crescent" in db.get(WorkingItem, lab["intention"].id).text
    [event] = _events(db, alice, "corrected")
    assert event.text == 'The user corrected the velvet otter marker: "purple square" (as remembered) -> "red crescent".'


# --- 3. grounding: the model's reading is kept only as far as the words support it -----------------


def test_a_reading_the_shown_words_dont_support_is_dropped_or_trimmed():
    said = "Oh wait, I meant the copper heron marker is an orange triangle, not a blue one."
    base = dict(subject="copper heron", attribute="marker", new_value="orange triangle", kind="mistaken",
                user_words="I meant the copper heron marker is an orange triangle")
    kept = memory_corrections.ground(CorrectionCandidate(previous_value="blue triangle", **base),
                                     user_text=said, shown=[])
    assert kept["previous_value"] == "blue triangle" and kept["kind"] == "mistaken"
    guessed = memory_corrections.ground(CorrectionCandidate(previous_value="green circle", **base),
                                        user_text=said, shown=[])
    assert guessed["previous_value"] is None  # nobody said green circle: never guessed
    assert memory_corrections.ground(CorrectionCandidate(previous_value=None, **{
        **base, "user_words": "the marker is now a square"}), user_text=said, shown=[]) is None
    assert memory_corrections.ground(CorrectionCandidate(previous_value=None, **{
        **base, "subject": "silver badger"}), user_text=said, shown=[]) is None
    assert memory_corrections.ground(CorrectionCandidate(previous_value=None, **{
        **base, "new_value": "red crescent"}), user_text=said, shown=[]) is None


def test_the_three_kinds_read_differently(db, alice, monkeypatch):
    row = MemoryCorrection(subject="velvet otter", attribute="marker", previous_value="purple square",
                           new_value="red crescent", kind="mistaken", user_words="x")
    assert memory_corrections.describe(row) == ('The user corrected a mistake: the velvet otter marker is '
                                                '"red crescent", not "purple square".')
    row.kind = "changed"
    assert "changed the velvet otter marker from" in memory_corrections.describe(row)
    row.kind, row.previous_value = "unspecified", None
    assert memory_corrections.describe(row) == 'The user corrected the velvet otter marker: now "red crescent".'


def test_no_correction_read_means_nothing_is_revised_and_a_disabled_flag_records_none(db, alice, monkeypatch, on):
    lab = _studied(db, alice)
    _, job = _exchange(db, lab["conversation"], "The velvet otter card also covers Singleton.")
    _consolidate(db, monkeypatch, job, _reads([_fact(FACT_OLD + " It also covers Singleton.")]))
    assert memory_corrections.next_due(db) is None
    on.memory_corrections_enabled = False
    _, job = _exchange(db, lab["conversation"], CORRECT)
    _consolidate(db, monkeypatch, job, _reads([], [_correction()]))
    assert db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id)) is None
    assert db.get(WorkingItem, lab["sub"].id).revision == 1


# --- 4. successive corrections, retries, out of order ----------------------------------------------


def test_successive_corrections_apply_in_turn_and_keep_both_in_history(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _corrected(db, monkeypatch, alice, lab)
    _corrected(db, monkeypatch, alice, lab, "Changed it again: the velvet otter marker is now a green star, not a "
               "red crescent.", memories=(_fact(FACT_NEW.replace("red crescent", "green star")),),
               corrections=(_correction(previous="red crescent", new="green star",
                                        words="the velvet otter marker is now a green star"),),
               judge=_swap("red crescent", "green star"))
    for key in ("sub", "intention"):
        entry = db.get(WorkingItem, lab[key].id)
        assert "green star" in entry.text and "crescent" not in entry.text and entry.revision == 3
    assert [e.text for e in _events(db, alice, "corrected")] == [
        'The user changed the velvet otter marker from "purple square" to "red crescent".',
        'The user changed the velvet otter marker from "red crescent" to "green star".']
    history = db.scalar(select(SemanticMemory).where(SemanticMemory.normalized_key == KEY,
                                                     SemanticMemory.user_id == alice.id)).metadata_["history"]
    assert [h["content"] for h in history] == [FACT_OLD, FACT_NEW]
    # The first revision is history now too.
    first = [e for e in _events(db, alice, "item_revised") if "-> \"red crescent\"" in e.text]
    def later(event):
        return recall.read(db, alice.id, f"event:{event.id}").records[0].applicability == "no_longer_current"
    assert first and all(later(e) for e in first)
    assert not any(later(e) for e in _events(db, alice, "corrected"))


def test_a_late_consolidation_job_waits_its_turn_and_never_overwrites_a_newer_statement(db, alice, monkeypatch):
    lab = _studied(db, alice)
    first, first_job = _exchange(db, lab["conversation"], CORRECT)
    second, second_job = _exchange(db, lab["conversation"],
                                   "And now the velvet otter marker is a green star instead of the red crescent.")
    # The later message's job finishes first.
    _consolidate(db, monkeypatch, second_job, _reads(
        [_fact(FACT_NEW.replace("red crescent", "green star"))],
        [_correction(previous="red crescent", new="green star",
                     words="the velvet otter marker is a green star instead of the red crescent")]))
    assert memory_corrections.next_due(db) is None  # the earlier job may hold an earlier correction
    _consolidate(db, monkeypatch, first_job, _reads([_fact()], [_correction()]))
    fact = db.scalar(select(SemanticMemory).where(SemanticMemory.normalized_key == KEY,
                                                  SemanticMemory.user_id == alice.id))
    assert "green star" in fact.content  # the late job didn't put red crescent back

    assert _apply_next(db, _swap("purple square", "red crescent")) is True
    db.expire_all()
    assert [r.source_message_id for r in db.scalars(select(MemoryCorrection).where(
        MemoryCorrection.status == "applied"))] == [first.id]
    assert _apply_next(db, _swap("red crescent", "green star")) is True
    db.expire_all()
    for key in ("sub", "intention"):
        assert "green star" in db.get(WorkingItem, lab[key].id).text
    assert second.id  # applied second, as said


def test_a_failed_or_repeated_pass_changes_nothing_twice(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _, job = _exchange(db, lab["conversation"], CORRECT)
    _consolidate(db, monkeypatch, job, _reads([_fact()], [_correction()]))
    row_id = db.scalar(select(MemoryCorrection.id))

    def broken(correction, candidates):
        raise RuntimeError("model unavailable")
    assert _apply_next(db, broken) is False
    db.expire_all()
    row = db.get(MemoryCorrection, row_id)
    assert (row.status, row.attempts) == ("pending", 1) and "model unavailable" in row.last_error
    assert row.next_attempt_at > datetime.now(timezone.utc)
    assert memory_corrections.next_due(db) is None  # backing off
    assert db.get(WorkingItem, lab["sub"].id).revision == 1 and not _events(db, alice, "corrected")

    db.execute(update(MemoryCorrection).values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    assert _apply_next(db, _swap("purple square", "red crescent")) is True
    assert memory_corrections.process(db, row_id, judge=_swap("purple square", "red crescent")) is False
    # A second consolidation of the same message keeps one correction.
    memory_corrections.record(db, job=db.get(MemoryJob, job.id), message_id=db.get(MemoryCorrection, row_id)
                              .source_message_id, user_text=CORRECT, shown=[], corrections=[_correction()])
    db.commit()
    db.expire_all()
    assert db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id)
                     .order_by(MemoryCorrection.id)).id == row_id
    assert len(list(db.scalars(select(MemoryCorrection)))) == 1
    assert len(_events(db, alice, "corrected")) == 1 and len(_events(db, alice, "item_revised")) == 2
    assert db.get(WorkingItem, lab["sub"].id).revision == 2


def test_a_memory_changed_while_judging_is_read_again(db, alice, monkeypatch, database):
    lab = _studied(db, alice)
    _, job = _exchange(db, lab["conversation"], CORRECT)
    _consolidate(db, monkeypatch, job, _reads([_fact()], [_correction()]))
    calls = []

    def judge(correction, candidates):
        calls.append(len(candidates))
        if len(calls) == 1:  # the user edits the sub-goal meanwhile (another session, committed)
            with database() as other:
                memory_manager.resolve(other, alice.id, lab["sub"].id, status="suspended", reason="later")
                other.commit()
        return _swap("purple square", "red crescent")(correction, candidates)
    assert _apply_next(db, judge) is False
    db.expire_all()
    row = db.scalar(select(MemoryCorrection))
    assert row.status == "pending" and "changed while" in row.last_error
    assert "purple square" in db.get(WorkingItem, lab["intention"].id).text  # nothing half-applied
    db.execute(update(MemoryCorrection).values(next_attempt_at=datetime.now(timezone.utc)))
    db.commit()
    assert _apply_next(db, judge) is True
    db.expire_all()
    sub = db.get(WorkingItem, lab["sub"].id)
    assert "red crescent" in sub.text and sub.status == "suspended"  # the correction kept its status


def test_the_worker_hands_a_consolidated_correction_to_the_manager(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _exchange(db, lab["conversation"], CORRECT)
    monkeypatch.setattr(long_term_memory, "_extract", _reads([_fact()], [_correction()]))
    monkeypatch.setattr(memory_corrections, "_judge", _swap("purple square", "red crescent"))
    monkeypatch.setattr(memory_worker, "SessionLocal", db_session.SessionLocal)
    assert memory_worker.run_once() is True  # the consolidation job
    assert memory_worker.run_once() is True  # the correction it handed over
    assert memory_worker.run_once() is False
    db.expire_all()
    assert "red crescent" in db.get(WorkingItem, lab["intention"].id).text


# --- 5. ambiguous targets and look-alikes ----------------------------------------------------------


def test_a_look_alike_in_another_context_is_left_alone_when_in_doubt(db, alice, monkeypatch):
    lab = _studied(db, alice)
    board = memory_manager.open_context(db, alice.id, kind="activity", label="Board game night")
    toy = memory_manager.note_intention(db, alice.id, "Bring the velvet otter figurine with the purple square base",
                                        context_id=board.id)
    twice = memory_manager.set_goal(db, alice.id, "Compare the purple square and the purple square stickers on "
                                    "velvet otter", context_id=lab["context"].id)
    db.commit()
    _corrected(db, monkeypatch, alice, lab, judge=_swap("purple square", "red crescent", unsure={f"item:{toy.id}"}))
    assert db.get(WorkingItem, toy.id).revision == 1
    assert db.get(WorkingItem, twice.id).revision == 1  # "purple square" twice: which one?
    row = db.scalar(select(MemoryCorrection))
    verdicts = {u["ref"]: (u["verdict"], u["reason"]) for u in row.result["unchanged"]}
    assert verdicts[f"item:{toy.id}"][0] == "unsure"
    assert verdicts[f"item:{twice.id}"] == ("refused", "the words to replace aren't there exactly once")
    assert "red crescent" in db.get(WorkingItem, lab["sub"].id).text


def test_closed_items_and_closed_contexts_stay_as_they_were(db, alice, monkeypatch):
    lab = _studied(db, alice)
    memory_manager.resolve(db, alice.id, lab["intention"].id, status="dropped", reason="no longer needed")
    old_world = memory_manager.open_context(db, alice.id, kind="world", label="Old notes")
    kept = memory_manager.set_goal(db, alice.id, "Velvet otter with a purple square, as filed", context_id=old_world.id)
    memory_manager.close_context(db, alice.id, old_world.id, reason="archived")
    db.commit()
    _corrected(db, monkeypatch, alice, lab)
    assert "purple square" in db.get(WorkingItem, lab["intention"].id).text
    assert "purple square" in db.get(WorkingItem, kept.id).text
    refs = {u["ref"] for u in db.scalar(select(MemoryCorrection)).result["unchanged"]}
    assert f"item:{lab['intention'].id}" not in refs and f"item:{kept.id}" not in refs  # not even candidates


# --- 6. ownership, deletion, reset -----------------------------------------------------------------


def test_another_users_memories_are_never_candidates_or_sources(db, alice, bob, monkeypatch):
    lab = _studied(db, alice)
    theirs = _studied(db, bob)
    _corrected(db, monkeypatch, alice, lab)
    assert "purple square" in db.get(WorkingItem, theirs["sub"].id).text
    assert db.get(WorkingItem, theirs["sub"].id).revision == 1
    refs = {r["ref"] for r in db.scalar(select(MemoryCorrection)).result["revised"]}
    assert all(str(theirs[k].id) not in " ".join(refs) for k in ("sub", "intention", "broad"))
    # A correction can't point at someone else's message, nor at an assistant message.
    bob_message = theirs["taught"]
    with pytest.raises((IntegrityError, DBAPIError)):
        db.add(MemoryCorrection(user_id=alice.id, source_message_id=bob_message.id, subject="x", new_value="y",
                                kind="changed", user_words="x y", said_at=datetime.now(timezone.utc),
                                fingerprint="f"))
        db.flush()
    db.rollback()


def test_deleting_the_conversation_or_resetting_keeps_the_rules(db, alice, monkeypatch):
    lab = _studied(db, alice)
    _corrected(db, monkeypatch, alice, lab)
    [event] = _events(db, alice, "corrected")
    memory_reset.reset(db, user_ids=[alice.id], scopes=["conversations"])
    db.expire_all()
    assert db.scalar(select(MemoryCorrection)) is None  # went with the message
    assert db.get(MemoryEvent, event.id) is not None  # the memory stands on its own
    assert not _links(db, event_id=event.id) and "red crescent" in db.get(WorkingItem, lab["sub"].id).text

    memory_reset.reset(db, user_ids=[alice.id], scopes=["memory"])  # the fact key is unique per user
    lab = _studied(db, alice)
    _corrected(db, monkeypatch, alice, lab)
    memory_reset.reset(db, user_ids=[alice.id], scopes=["working"])
    db.expire_all()
    row = db.scalar(select(MemoryCorrection))
    assert row.status == "applied" and row.event_id is None
    memory_reset.reset(db, user_ids=[alice.id], scopes=["memory"])
    assert db.scalar(select(MemoryCorrection)) is None
    db.execute(delete(User).where(User.id == alice.id))
    db.commit()
    assert db.scalar(select(Conversation).where(Conversation.user_id == alice.id)) is None
