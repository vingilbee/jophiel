"""Stage 4: what her working items rely on, and what a changed fact does to them
(services/memory_dependencies.py, services/memory_reconcile.py).

The gate (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md §11): nonempty holds, multi-change corrections,
terminal failures and concurrent updates preserve validity, and nothing unchecked is used autonomously.
Real PostgreSQL (``TEST_DATABASE_URL``); every model call is scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.db.session as db_session
from app.db.models import MemoryDependency, MemoryFactRevision, SemanticMemory, WorkingItem
from app.schemas.long_term_memory import CorrectionCandidate, MemoryCandidate
from app.services import memory_dependencies, memory_manager, memory_reconcile, recall
from app.services.memory_manager import Source
from app.services.memory_reconcile import FactChange, ItemCheck
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _consolidate, _conversation, _exchange, _reads
from tests.test_memory_reconcile import (  # noqa: F401  (``on`` is an autouse fixture)
    KEY, MOVED, NEW, OLD, TEACH, _items, _moved, _revised, _revision, _settle_all, _taught, on,
)

KEY2 = "fact:family:sister-work"
WORK = "The user's sister Nora works at a bakery."
WORK_NEW = "The user's sister Nora now works at a bookshop."


def _holds(db, user):
    db.expire_all()
    return memory_dependencies.holds(db, user.id)


def _deps(db, item_id):
    db.expire_all()
    return {d.fact_id: d for d in db.scalars(select(MemoryDependency).where(MemoryDependency.item_id == item_id))}


def _process_next(db, read=_moved):
    db.expire_all()
    due = memory_reconcile.next_due(db)
    db.rollback()
    assert due is not None
    memory_reconcile.process(db, due, read=read)
    db.expire_all()
    return due


def _fail_for_good(db, user):
    """Run the recheck until its quick and daily retries are over: it fails."""
    def broken(view):
        raise RuntimeError("model down")

    for n in range(memory_reconcile.MAX_ATTEMPTS + 1):
        db.expire_all()
        row = _revision(db, user)
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if n == memory_reconcile.MAX_ATTEMPTS:
            row.daily_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        memory_reconcile.process(db, row.id, read=broken)
    row = _revision(db, user)
    assert row.status == "failed"
    return row


# --- 1. nonempty holds: only what relies on it -----------------------------------------------------------


def test_an_unrelated_item_from_the_same_message_is_usable_once_judged_while_the_related_one_stays_held(
        db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    assert _holds(db, alice) == {lab["visit"].id: "pending", lab["ask"].id: "pending"}  # unknown until judged

    _process_next(db)  # the reading: the visit relies on it (waits for its new wording), the promise doesn't
    assert _revision(db, alice).status == "applying"
    assert _holds(db, alice) == {lab["visit"].id: "pending"}
    autonomous = memory_manager.view(db, alice.id, user_name="Sam", autonomous=True) or ""
    assert "half marathon" in autonomous and "Visit Nora" not in autonomous

    _settle_all(db)
    assert _holds(db, alice) == {}
    assert db.get(WorkingItem, lab["visit"].id).text == "Visit Nora in Porto in December"


def test_an_item_whose_links_say_it_doesnt_rely_on_the_fact_is_never_held(db, alice, monkeypatch):
    lab = _taught(db, alice)
    source = Source("message", lab["taught"].id)
    mention = memory_manager.note_intention(db, alice.id, "Tell Sam about the Lisbon light festival", source=source)
    other = SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:pets:dog", content="Sam's dog is Pretzel.",
                           importance=0.6, confidence=0.9, source_message_id=lab["taught"].id)
    db.add(other)
    walk = memory_manager.note_intention(db, alice.id, "Ask how Pretzel's walks are going", source=source)
    db.flush()
    memory_dependencies.link(db, alice.id, mention.id, lab["fact"].id, role="context")
    memory_dependencies.link(db, alice.id, walk.id, other.id, role="required")
    memory_dependencies.link(db, alice.id, walk.id, lab["fact"].id, role="none", origin="judged")
    db.commit()

    _revised(db, monkeypatch, lab)
    held = _holds(db, alice)
    assert mention.id not in held and walk.id not in held  # an incidental link, and one judged unrelated
    assert set(held) == {lab["visit"].id, lab["ask"].id}
    assert str(mention.id) not in _revision(db, alice).held_item_ids


def test_a_reading_never_weakens_a_stored_required_link(db, alice, monkeypatch):
    lab = _taught(db, alice)
    memory_dependencies.link(db, alice.id, lab["visit"].id, lab["fact"].id, role="required")
    db.commit()
    _revised(db, monkeypatch, lab)
    careless = lambda view: FactChange(same_meaning=False, items=[  # noqa: E731
        ItemCheck(item=i["ref"], relies="none") for i in view["items"]])
    _process_next(db, careless)
    dep = _deps(db, lab["visit"].id)[lab["fact"].id]
    assert (dep.role, dep.state, dep.checked_revision) == ("required", "valid", 2)  # kept as it is: checked


# --- 2. links are written where an item is made ----------------------------------------------------------


def _plan(content, words, relies_on=()):
    return MemoryCandidate(kind="plan", normalized_key="plan:" + "-".join(content.lower().split()[:4]),
                           content=content, importance=0.7, confidence=0.9, user_words=words,
                           relies_on=list(relies_on))


def _fact(content, key=KEY):
    return MemoryCandidate(kind="fact", normalized_key=key, content=content, importance=0.7, confidence=0.9)


def test_a_plan_relies_on_the_fact_it_names_and_one_made_knowing_the_change_isnt_held(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _, job = _exchange(db, conversation, TEACH, "Lisbon in December, lovely.")
    _consolidate(db, monkeypatch, job, _reads([
        _fact(OLD), _plan("Visit Nora in Lisbon in December", "I'm visiting her in December", [KEY]),
        _plan("Run the half marathon", "my half marathon", ["fact:nobody:never-said"])]))
    fact = db.scalar(select(SemanticMemory).where(SemanticMemory.user_id == alice.id, SemanticMemory.normalized_key == KEY))
    plans = {p.text: p for p in db.scalars(select(WorkingItem).where(WorkingItem.user_id == alice.id,
                                                                     WorkingItem.kind == "plan"))}
    visit, race = plans["Visit Nora in Lisbon in December"], plans["Run the half marathon"]
    assert {f: (d.role, d.state, d.checked_revision) for f, d in _deps(db, visit.id).items()} == {
        fact.id: ("required", "valid", 1)}
    assert _deps(db, race.id) == {}  # a key that names no fact of theirs links nothing

    _, job = _exchange(db, conversation, MOVED, "Porto it is.")
    _consolidate(db, monkeypatch, job, _reads([
        _fact(NEW), _plan("Take the train to Porto to see Nora", "I'm visiting her there now", [KEY])]))
    train = db.scalar(select(WorkingItem).where(WorkingItem.user_id == alice.id,
                                                WorkingItem.text == "Take the train to Porto to see Nora"))
    # The visit relies on it; the race plan from the same first message is a candidate until the reading judges
    # it (no link said either way); the train plan was made knowing the change.
    assert _holds(db, alice) == {visit.id: "pending", race.id: "pending"}
    assert _deps(db, race.id)[fact.id].role == "unknown"
    assert _deps(db, train.id)[fact.id].checked_revision == 2


# A thought's links (required for what it relies on, context for the rest it read) are tested end to end in
# tests/test_thought_memory.py::test_a_thought_relies_on_the_facts_the_judgement_names.


def test_links_only_reach_the_users_own_current_facts(db, alice, bob):
    theirs = SemanticMemory(user_id=bob.id, kind="fact", normalized_key="fact:x", content="Bob's secret.",
                            importance=0.5, confidence=0.9)
    gone = SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:y", content="Forgotten.",
                          importance=0.5, confidence=0.9, status="deleted")
    mine = SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:z", content="Sam likes tea.",
                          importance=0.5, confidence=0.9)
    db.add_all([theirs, gone, mine])
    db.flush()
    entry = memory_manager.note_intention(db, alice.id, "Make Sam tea")
    linked, refused = memory_dependencies.link_refs(
        db, alice.id, entry.id, [f"fact:{theirs.id}", f"[fact:{gone.id}]", f"fact:{mine.id}", "event:3", "nonsense"])
    assert linked == [f"fact:{mine.id}"] and len(refused) == 4
    db.commit()


# --- 3. what the change means for each item, as code allows ----------------------------------------------


def test_each_kind_gets_only_the_action_that_fits_it(db, alice, monkeypatch):
    lab = _taught(db, alice)
    source = Source("message", lab["taught"].id)
    made = {
        "question": memory_manager.add_item(db, alice.id, kind="question", text_="Which Lisbon district is Nora in?",
                                            owner="columbina", epistemic="inferred", source=source),
        "intention": memory_manager.note_intention(db, alice.id, "Look up Lisbon trams for Nora", source=source),
        "decision": memory_manager.add_item(db, alice.id, kind="decision", text_="Recommend cafés near Nora in Lisbon",
                                            owner="columbina", epistemic="inferred", source=source),
        "theirs": memory_manager.note_commitment(db, alice.id, "Send Nora's Lisbon address", owner="user",
                                                 source=source),
        "review": memory_manager.note_intention(db, alice.id, "Plan the Lisbon dinner with Nora", source=source),
    }
    for entry in made.values():
        memory_dependencies.link(db, alice.id, entry.id, lab["fact"].id, role="required")
    db.commit()
    actions = {"Which Lisbon": "answer", "Look up": "cancel", "Recommend": "supersede", "Send Nora": "cancel",
               "Plan the": "review", "Visit Nora": "keep"}

    def reading(view):
        return FactChange(same_meaning=False, items=[
            ItemCheck(item=i["ref"], relies="required", why="where Nora lives",
                      action=next((a for k, a in actions.items() if i["text"].startswith(k)), "keep"))
            if "Nora" in i["text"] else ItemCheck(item=i["ref"], relies="none") for i in view["items"]])

    _revised(db, monkeypatch, lab)
    _process_next(db, reading)
    db.expire_all()
    status = {k: db.get(WorkingItem, e.id).status for k, e in made.items()}
    assert status == {"question": "answered", "intention": "dropped", "decision": "superseded",
                      "theirs": "open", "review": "active"}  # the user's promise is theirs to call off
    assert set(_holds(db, alice)) == {made["theirs"].id, made["review"].id}
    assert _holds(db, alice)[made["theirs"].id] == "review"
    assert db.get(WorkingItem, made["question"].id).status_reason.startswith("a fact it relied on changed")


# --- 4. multi-change corrections ---------------------------------------------------------------------------


def test_an_item_relying_on_two_changed_facts_stays_held_until_both_are_rechecked(db, alice, monkeypatch):
    lab = _taught(db, alice)
    work = SemanticMemory(user_id=alice.id, kind="fact", normalized_key=KEY2, content=WORK, importance=0.7,
                          confidence=0.9, source_message_id=lab["taught"].id)
    db.add(work)
    gift = memory_manager.note_intention(db, alice.id, "Bring Nora's bakery colleagues pastries from Lisbon")
    db.flush()
    memory_dependencies.link(db, alice.id, gift.id, lab["fact"].id, role="required")
    memory_dependencies.link(db, alice.id, gift.id, work.id, role="required")
    db.commit()

    _, job = _exchange(db, lab["conversation"], "Nora moved to Porto last month and works at a bookshop now.", "Oh!")
    _consolidate(db, monkeypatch, job, _reads([_fact(NEW), _fact(WORK_NEW, KEY2)]))
    assert db.scalar(select(MemoryFactRevision.id).where(MemoryFactRevision.fact_id == work.id)) is not None
    assert _holds(db, alice)[gift.id] == "pending"

    keep = lambda view: FactChange(same_meaning=False, items=_items(view, action="keep"))  # noqa: E731
    _process_next(db, keep)  # the first change: checked, still fine
    assert _holds(db, alice)[gift.id] == "pending"  # the other change hasn't been read yet
    _process_next(db, keep)
    assert gift.id not in _holds(db, alice)
    assert {d.checked_revision for d in _deps(db, gift.id).values()} == {2}


# --- 5. terminal failure keeps the hold, and how it's lifted ---------------------------------------------


def test_a_failed_recheck_runs_again_when_the_user_says_the_fact_again(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    _fail_for_good(db, alice)
    assert _holds(db, alice)[lab["visit"].id] == "review"
    record = recall.read(db, alice.id, f"item:{lab['visit'].id}").records[0]
    assert "may be out of date" in (record.lifecycle or "")

    _, job = _exchange(db, lab["conversation"], "Yes, Nora's in Porto now.", "Got it.")
    _consolidate(db, monkeypatch, job, _reads([_fact(NEW)]))  # said again, unchanged: support
    row = _revision(db, alice)
    assert (row.status, row.attempts, row.daily_until) == ("pending", 0, None)
    assert _holds(db, alice)[lab["visit"].id] == "pending"
    _settle_all(db)
    assert _holds(db, alice) == {} and _revision(db, alice).status == "done"


def test_the_users_turn_confirming_an_item_lifts_may_be_out_of_date_and_a_background_one_cant(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    _fail_for_good(db, alice)
    visit = db.get(WorkingItem, lab["visit"].id)
    asked, _ = _exchange(db, lab["conversation"], "The December visit is still on.", "Great.")

    with pytest.raises(memory_manager.MemoryError):
        memory_manager.resolve(db, alice.id, visit.id, status="active", reason="it stands", background=True)
    db.rollback()
    assert _holds(db, alice)[visit.id] == "review"  # without the user, it stays held
    memory_manager.resolve(db, alice.id, visit.id, status="active", reason="the user said it stands",
                           source=Source("message", asked.id))
    db.commit()
    assert visit.id not in _holds(db, alice)
    dep = _deps(db, visit.id)[lab["fact"].id]
    assert (dep.state, dep.origin, dep.checked_revision) == ("valid", "confirmed", 2)


# --- 6. concurrent updates ---------------------------------------------------------------------------------


def test_an_item_that_changes_while_the_model_reads_is_read_again(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    calls = []

    def reading(view):
        calls.append(view)
        if len(calls) == 1:  # the user's turn rewords it meanwhile
            with db_session.SessionLocal() as other:
                other.execute(update(WorkingItem).where(WorkingItem.id == lab["visit"].id).values(
                    text="Visit Nora in Lisbon over Christmas", revision=WorkingItem.revision + 1))
                other.commit()
        return _moved(view)

    _process_next(db, reading)
    row = _revision(db, alice)
    assert (row.status, row.attempts, row.result.get("stale")) == ("pending", 0, 1)  # read again, not a failure
    assert _holds(db, alice)[lab["visit"].id] == "pending"
    row.next_attempt_at = datetime.now(timezone.utc)
    db.commit()
    _process_next(db, reading)
    assert any(i["text"] == "Visit Nora in Lisbon over Christmas" for i in calls[1]["items"])
    assert _revision(db, alice).status == "applying"


def test_a_newer_change_of_the_same_fact_takes_the_items_over(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    first = _revision(db, alice)
    _revised(db, monkeypatch, lab, said="Actually Nora moved on to Madrid.", new="The user's sister Nora lives in Madrid.")
    second = _revision(db, alice)
    assert second.id != first.id
    owners = {d.pending_revision_id for d in db.scalars(select(MemoryDependency))}
    assert owners == {second.id}

    db.expire_all()
    memory_reconcile.process(db, first.id, read=lambda view: FactChange(same_meaning=False))
    db.expire_all()
    assert db.get(MemoryFactRevision, first.id).status == "superseded"
    assert set(_holds(db, alice)) == {lab["visit"].id, lab["ask"].id}  # untouched by the older reading

    madrid = lambda view: FactChange(same_meaning=False, items=_items(view, action="keep"))  # noqa: E731
    memory_reconcile.process(db, second.id, read=madrid)
    assert _holds(db, alice) == {}


def test_a_recheck_never_applies_to_an_item_the_users_turn_confirmed_meanwhile(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    asked, _ = _exchange(db, lab["conversation"], "Keep the visit as it is.", "Sure.")

    def reading(view):
        with db_session.SessionLocal() as other:
            memory_dependencies.confirm(other, alice.id, lab["visit"].id)
            other.commit()
        return FactChange(same_meaning=False, items=_items(view, action="review"))

    _process_next(db, reading)
    dep = _deps(db, lab["visit"].id)[lab["fact"].id]
    assert (dep.state, dep.origin) == ("valid", "confirmed")  # the reading's "review" didn't reach it
    assert lab["visit"].id not in _holds(db, alice)


# --- 7. the migration's backfill keeps holds in progress -------------------------------------------------


def test_reads_and_holds_from_stage_two_are_recognised(db, alice, monkeypatch):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    # What the migration does for a stage-2 revision still in progress: its held items become unknown rows.
    db.execute(MemoryDependency.__table__.delete())
    row = _revision(db, alice)
    for item_id in row.held_item_ids:
        db.add(MemoryDependency(user_id=alice.id, item_id=uuid.UUID(item_id), fact_id=row.fact_id, role="unknown",
                                state="pending", origin="candidate", pending_revision_id=row.id))
    db.commit()
    assert set(_holds(db, alice)) == {lab["visit"].id, lab["ask"].id}
    _settle_all(db)
    assert _holds(db, alice) == {}


@pytest.mark.parametrize("bad", [CorrectionCandidate(subject="Nora", previous_value="Lisbon", new_value="Madrid",
                                                     kind="changed", user_words="Nora actually moved to Porto last month")])
def test_revise_without_a_grounded_change_needs_review(db, alice, monkeypatch, bad):
    lab = _taught(db, alice)
    _revised(db, monkeypatch, lab)
    _settle_all(db, read=lambda view: FactChange(same_meaning=False, changes=[bad], items=_items(view)))
    assert _holds(db, alice) == {lab["visit"].id: "review"}
    assert _revision(db, alice).result["items"][str(lab["visit"].id)]["outcome"] == "review"


def test_an_item_ref_the_model_wrote_past_is_still_its_ref():
    """Live (2026-10-31), the reading returned "i1: The visit..." for "i1": no verdict matched any item."""
    from app.services.memory_reconcile import ItemCheck

    assert [ItemCheck(item=v, relies="none").item for v in ("i1", "i1: The\x0e", "[i12] x", "i3:||||")] == \
        ["i1", "i1", "i12", "i3"]
