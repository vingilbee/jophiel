"""A revised fact reaches her working memory whatever path changed it (services/memory_reconcile.py): the
revision is queued with the fact, items sharing its old source are held from autonomous use, the change the
user's message supports becomes an ordinary correction, and settling stamps what each held item was checked
against. Real PostgreSQL (``TEST_DATABASE_URL``); the model calls are scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.core.config as config
import app.db.session as db_session
from app.db.models import MemoryCorrection, MemoryDependency, MemoryFactRevision, SemanticMemory, WorkingItem
from app.schemas.long_term_memory import CorrectionCandidate, MemoryCandidate
from app.services import (
    long_term_memory, memory_corrections, memory_dependencies, memory_manager, memory_reconcile,
    recall,
)
from app.services.memory_manager import Source
from app.services.memory_reconcile import FactChange, ItemCheck
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _consolidate, _conversation, _exchange, _reads, _swap

KEY = "fact:family:sister"
TEACH = "My sister Nora lives in Lisbon. I'm visiting her in December. Oh and ask me about my half marathon."
MOVED = "Nora actually moved to Porto last month, so I'm visiting her there now."
OLD = "The user's sister Nora lives in Lisbon."
NEW = "The user's sister Nora moved to Porto last month."


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "long_term_memory_enabled": True, "memory_corrections_enabled": True,
        "recall_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "recall_log_path": str(tmp_path / "recall.jsonl"), "redis_url": None})
    for module in (memory_manager, memory_corrections, memory_reconcile, long_term_memory, recall, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


def _taught(db, user):
    """A fact and three open items from the teaching message: one states the old city, one doesn't, and a
    promise owed to the user about something else entirely."""
    conversation = _conversation(db, user)
    taught, job = _exchange(db, conversation, TEACH, "Lisbon in December, lovely.")
    db.commit()
    source = Source("message", taught.id)
    visit = memory_manager.set_goal(db, user.id, "Visit Nora in Lisbon in December", source=source)
    ask = memory_manager.note_commitment(db, user.id, "Ask the user how the half marathon went", source=source)
    fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key=KEY, content=OLD, importance=0.7,
                          confidence=0.9, source_conversation_id=conversation.id, source_message_id=taught.id)
    db.add(fact)
    from sqlalchemy import update

    from app.db.models import MemoryJob
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    db.commit()
    return {"conversation": conversation, "taught": taught, "visit": visit, "ask": ask, "fact": fact}


def _revised(db, monkeypatch, lab, said=MOVED, new=NEW, corrections=()):
    """The user's message rewords the fact; consolidation names no correction unless given one."""
    asked, job = _exchange(db, lab["conversation"], said, "Porto it is.")
    _consolidate(db, monkeypatch, job, _reads(
        [MemoryCandidate(kind="fact", normalized_key=KEY, content=new, importance=0.7, confidence=0.9)], corrections))
    return asked


def _items(view, *, about="Nora", action="revise"):
    """What a careful reading says about each held item: one about Nora relies on where she lives, the rest
    don't."""
    return [ItemCheck(item=i["ref"], relies="required", action=action, why="where Nora lives")
            if about in i["text"] else ItemCheck(item=i["ref"], relies="none", why="unrelated")
            for i in view["items"]]


def _moved(view):
    return FactChange(same_meaning=False, changes=[CorrectionCandidate(
        subject="Nora", attribute="city", previous_value="Lisbon", new_value="Porto", kind="changed",
        user_words="Nora actually moved to Porto last month")], items=_items(view))


def _revision(db, user) -> MemoryFactRevision:
    db.expire_all()
    return db.scalar(select(MemoryFactRevision).where(MemoryFactRevision.user_id == user.id)
                     .order_by(MemoryFactRevision.created_at.desc()).limit(1))


def _settle_all(db, *, read=_moved, judge=None):
    """Run the worker's order until nothing is left: reconcile, then corrections."""
    judge = judge or _swap("Lisbon", "Porto")
    for _ in range(10):
        db.expire_all()
        due = memory_reconcile.next_due(db)
        db.rollback()
        if due is not None:
            memory_reconcile.process(db, due, read=read)
            continue
        correction = memory_corrections.next_due(db)
        db.rollback()
        if correction is not None:
            memory_corrections.process(db, correction, judge=judge, embed=lambda text: None)
            continue
        if not memory_reconcile.finalize(db):
            break
    db.expire_all()


# --- 1. the e2e failure: the correction was dropped, the fact still changed --------------------------------


def test_a_revised_fact_without_a_correction_still_reaches_the_items(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    assert db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id)) is None

    row = _revision(db, alice)
    assert (row.old_content, row.new_content, row.status) == (OLD, NEW, "pending")
    assert (row.from_revision, row.to_revision) == (1, 2)
    assert set(row.held_item_ids) == {str(lab["visit"].id), str(lab["ask"].id)}  # shares the source: held
    assert memory_reconcile.held_item_ids(db, alice.id) == {lab["visit"].id, lab["ask"].id}

    _settle_all(db)
    row = _revision(db, alice)
    assert row.status == "done" and row.settled_at is not None
    assert row.result["changes"][0]["new_value"] == "Porto" and len(row.result["correction_ids"]) == 1
    visit, ask = db.get(WorkingItem, lab["visit"].id), db.get(WorkingItem, lab["ask"].id)
    assert visit.text == "Visit Nora in Porto in December" and visit.revision == 2
    assert ask.text == "Ask the user how the half marathon went" and ask.revision == 1  # unrelated: unchanged
    for entry in (visit, ask):  # both checked against the new revision, and the hold is lifted
        assert entry.checked_facts == {str(lab["fact"].id): 2}
    assert memory_reconcile.held_item_ids(db, alice.id) == set()
    roles = dict(db.execute(select(MemoryDependency.item_id, MemoryDependency.role)).all())
    assert roles == {visit.id: "required", ask.id: "none"}  # judged once: the next change knows


def test_a_paraphrase_changes_nothing_and_lifts_the_hold(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab, said="Yep, Nora's in Lisbon.", new="Nora, the user's sister, lives in Lisbon.")
    _settle_all(db, read=lambda view: FactChange(same_meaning=True, items=_items(view, action="keep")))
    row = _revision(db, alice)
    assert row.status == "done" and row.result["same_meaning"] is True and row.result["correction_ids"] == []
    assert db.get(WorkingItem, lab["visit"].id).text == "Visit Nora in Lisbon in December"
    assert db.get(WorkingItem, lab["visit"].id).checked_facts == {str(lab["fact"].id): 2}
    assert memory_reconcile.held_item_ids(db, alice.id) == set()


def test_a_change_the_message_doesnt_support_is_dropped(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    invented = lambda view: FactChange(same_meaning=False, changes=[CorrectionCandidate(  # noqa: E731
        subject="Nora", previous_value="Lisbon", new_value="Madrid", kind="changed",
        user_words="Nora actually moved to Porto last month")], items=_items(view))
    _settle_all(db, read=invented)
    row = _revision(db, alice)
    assert row.status == "done" and row.result["changes"] == [] and len(row.result["dropped"]) == 1
    assert db.get(WorkingItem, lab["visit"].id).text == "Visit Nora in Lisbon in December"
    # Nothing grounded to reword it with: it may be out of date, and stays held.
    assert memory_dependencies.holds(db, alice.id) == {lab["visit"].id: "review"}


def test_the_messages_own_correction_isnt_handed_over_twice_but_is_waited_for(db, alice, monkeypatch):
    lab = _taught(db, alice)
    explicit = CorrectionCandidate(subject="Nora", previous_value="Lisbon", new_value="Porto", kind="changed",
                                   user_words="Nora actually moved to Porto last month")
    _revised(db, monkeypatch, lab, corrections=[explicit])
    own = db.scalar(select(MemoryCorrection).where(MemoryCorrection.user_id == alice.id))
    db.rollback()
    due = memory_reconcile.next_due(db)
    db.rollback()
    memory_reconcile.process(db, due, read=_moved)
    row = _revision(db, alice)
    assert row.status == "applying" and row.result["correction_ids"] == [str(own.id)]  # waits for it
    assert len(row.result["already_corrected"]) == 1
    assert memory_reconcile.held_item_ids(db, alice.id)  # still held until that correction is applied
    _settle_all(db)
    assert db.scalar(select(MemoryCorrection.id).where(MemoryCorrection.user_id == alice.id,
                                                       MemoryCorrection.id != own.id)) is None
    assert _revision(db, alice).status == "done"
    assert db.get(WorkingItem, lab["visit"].id).text == "Visit Nora in Porto in December"


def test_items_from_the_revising_message_are_not_held(db, alice, monkeypatch):
    lab = _taught(db, alice)
    asked = _revised(db, monkeypatch, lab)
    db.execute(MemoryFactRevision.__table__.delete())
    db.commit()
    fresh = memory_manager.set_goal(db, alice.id, "Book a train to Porto", source=Source("message", asked.id))
    db.commit()
    fact = db.scalar(select(SemanticMemory).where(SemanticMemory.user_id == alice.id))
    memory_reconcile.queue(db, fact=fact, old_content=OLD, old_revision=1, message_id=asked.id, job_id=None)
    db.commit()
    assert str(fresh.id) not in _revision(db, alice).held_item_ids


# --- 2. the hold is enforced in code, where she acts on her own --------------------------------------------


def test_held_items_are_kept_from_autonomous_use_and_marked_for_replies(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)

    autonomous = memory_manager.view(db, alice.id, user_name="Sam", autonomous=True) or ""
    assert "Visit Nora" not in autonomous and "half marathon" not in autonomous
    reply = memory_manager.view(db, alice.id, user_name="Sam")
    assert "Visit Nora in Lisbon" in reply and "on hold" in reply

    with pytest.raises(memory_manager.MemoryError) as refused:
        intention = memory_manager.note_intention(db, alice.id, "Look up Lisbon trams",
                                                  source=Source("message", lab["taught"].id))
        db.commit()
        db.add(MemoryDependency(user_id=alice.id, item_id=intention.id, fact_id=lab["fact"].id, role="required",
                                state="pending", origin="derived", pending_revision_id=_revision(db, alice).id))
        db.commit()
        memory_manager.resolve(db, alice.id, intention.id, status="dropped", reason="no longer", background=True)
    assert refused.value.code == "on_hold"
    db.rollback()

    _settle_all(db)
    assert "Visit Nora in Porto" in (memory_manager.view(db, alice.id, user_name="Sam", autonomous=True) or "")


def test_a_failing_read_retries_daily_then_fails_and_keeps_the_hold(db, alice, monkeypatch):
    """Stage 4 (the user's choice, 2026-10-30): quick retries, then once a day for 7 days, then failed; what it
    held stays held as "may be out of date", never just lifted."""
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)

    def broken(view):
        raise RuntimeError("model down")

    def attempt():
        db.expire_all()
        row = _revision(db, alice)
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        memory_reconcile.process(db, row.id, read=broken)
        return _revision(db, alice)

    for _ in range(memory_reconcile.MAX_ATTEMPTS):
        row = attempt()
    assert row.status == "pending" and row.daily_until is not None  # now once a day
    assert row.next_attempt_at > datetime.now(timezone.utc) + timedelta(hours=23)
    row.daily_until = datetime.now(timezone.utc) + timedelta(seconds=1)
    db.commit()
    assert attempt().status == "pending"  # a daily try that failed again
    row = _revision(db, alice)
    row.daily_until = datetime.now(timezone.utc) - timedelta(seconds=1)  # the 7 days are over
    db.commit()
    row = attempt()
    assert row.status == "failed" and "model down" in row.last_error
    assert memory_dependencies.holds(db, alice.id) == {lab["visit"].id: "review", lab["ask"].id: "review"}
    assert db.get(WorkingItem, lab["visit"].id).checked_facts == {}
    reply = memory_manager.view(db, alice.id, user_name="Sam")
    assert "may be out of date" in reply
    assert "Visit Nora" not in (memory_manager.view(db, alice.id, user_name="Sam", autonomous=True) or "")


# --- 3. no promises that only copy a fact --------------------------------------------------------------------


def test_a_promise_that_only_keeps_a_fact_is_refused():
    for copy in ("Remember that Sam is allergic to peanuts and their partner Leo hates cilantro.",
                 "I'll remember Leo hates cilantro", "Keep in mind: Nora lives in Porto", "Don't forget Ada's tea"):
        assert memory_manager.fact_copy(copy), copy
    for real in ("Remember to send Sam the photos", "Ask the user how the half marathon went",
                 "Remind Sam about the dentist at 5"):
        assert not memory_manager.fact_copy(real), real


def test_note_commitment_refuses_a_fact_copy_but_keeps_real_ones(db, alice):
    with pytest.raises(memory_manager.MemoryError) as refused:
        memory_manager.note_commitment(db, alice.id, "Remember that Leo hates cilantro")
    assert refused.value.code == "not_a_promise"
    db.rollback()
    kept = memory_manager.note_commitment(db, alice.id, "Remember to send Sam the recipe")
    theirs = memory_manager.note_commitment(db, alice.id, "Remember the photos", owner="user")  # theirs to keep
    assert kept.kind == theirs.kind == "commitment"
