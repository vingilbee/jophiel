"""Claims, entities and time (plan stage 3): a name correction on an older note keeps the rest of it as its
own claims, a renamed dog keeps what else is known about it, two people of one name stay two, "last month"
stays the month before it was said, a plan is one working item with the user's words and time, and an old
fact outside the recent window is still matched. Real PostgreSQL (``TEST_DATABASE_URL``); the model calls are
scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.core.config as config
import app.db.session as db_session
from app.ai.recall.contracts import RecallRequest
from app.db.models import (
    ChatMessage, MemoryEntity, MemoryEntityLink, MemoryEvidence, MemoryJob, SemanticMemory, WorkingItem,
)
from app.schemas.long_term_memory import (
    CorrectionCandidate, EntityMention, MemoryCandidate, MemoryExtraction, TimeReading,
)
from app.services import (
    long_term_memory, memory_conflicts, memory_corrections, memory_entities, memory_forget, memory_manager,
    memory_time, recall, recall_references,
)
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _conversation, _exchange

SAID = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "long_term_memory_enabled": True, "memory_corrections_enabled": True,
        "recall_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "recall_log_path": str(tmp_path / "recall.jsonl"), "redis_url": None})
    for module in (memory_manager, memory_corrections, memory_conflicts, memory_forget, long_term_memory, recall,
                   recall_references, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


def _reads(memories=(), corrections=()):
    def extract(user, assistant, earlier=(), known=(), **context):
        extract.seen = {"known": list(known), **context}
        return MemoryExtraction(memories=list(memories), corrections=list(corrections))
    extract.seen = {}
    return extract


def _said(db, conversation, words, answer="Got it.", at=SAID):
    asked, job = _exchange(db, conversation, words, answer)
    db.execute(update(ChatMessage).where(ChatMessage.id == asked.id).values(created_at=at.replace(tzinfo=None)))
    db.commit()
    return asked, job


def _run(db, monkeypatch, job, extract):
    monkeypatch.setattr(long_term_memory, "_extract", extract)
    assert long_term_memory.process_job(db, job.id) is True
    db.expire_all()


def _search(db, user, query, now=SAID + timedelta(days=1)):
    result = recall.search(db, RecallRequest(user_id=user.id, query=query, purpose="reply", now=now))
    return result, recall.render_bundle(result, now=now, zone="Europe/Lisbon")


def _fact(db, user, key):
    return db.scalar(select(SemanticMemory).where(SemanticMemory.user_id == user.id,
                                                  SemanticMemory.normalized_key == key))


# --------------------------------------------------------------------------- #
# Time readings (pure)
# --------------------------------------------------------------------------- #


def _ground(words, start=None, end=None, precision="month", text=None, role="any", zone="Europe/Lisbon", **extra):
    reading = TimeReading(words=words, start=start, end=end, precision=precision, **extra)
    return memory_time.ground(reading, user_text=text or f"she moved {words}", said_at=SAID, zone=zone, role=role)


def test_last_month_is_the_month_before_it_was_said_whenever_it_is_read():
    values = _ground("last month", "2026-09-01", "2026-09-30")
    assert values["time_precision"] == "month" and values["time_words"] == "last month"
    assert values["time_start"].isoformat() == "2026-09-01T00:00:00+01:00"
    assert values["time_end"].isoformat() == "2026-10-01T00:00:00+01:00"
    assert values["time_said_at"] == SAID and values["time_zone"] == "Europe/Lisbon"
    span = memory_time.span(type("Row", (), values)())
    much_later = datetime(2027, 3, 1, tzinfo=timezone.utc)
    text = memory_time.describe(span, much_later)
    assert text.startswith('"last month" (said on 30 Oct 2026): September 2026') and "now past" in text


def test_december_keeps_its_year_labelled_as_assumed():
    values = _ground("in December", "2026-12-01", "2026-12-31", year_inferred=True, role="future",
                     text="I'm seeing Nora in December")
    assert values["time_inferred"] is True
    text = memory_time.describe(memory_time.span(type("Row", (), values)()), SAID)
    assert "December 2026 (the year wasn't said; assumed), still ahead" in text
    said_year = _ground("in December 2026", "2026-12-01", "2026-12-31", year_inferred=True, role="future",
                        text="I'm seeing Nora in December 2026")
    assert said_year["time_inferred"] is False  # the year was said


@pytest.mark.parametrize("case", [
    dict(words="in spring", text="she moved last month"),  # not the user's words: nothing kept
])
def test_words_the_user_did_not_say_are_never_kept(case):
    assert _ground(case["words"], "2026-03-01", "2026-05-31", "season", text=case["text"]) == {}


@pytest.mark.parametrize("start,end,precision,role", [
    ("2026-09-01", "2026-09-29", "month", "any"),  # not a whole month
    ("2026-09-03", "2026-09-01", "day", "any"),  # ends before it starts
    ("2026-09-01", "2026-09-30", "month", "future"),  # a plan over before it was made
    ("2026-12-01", "2026-12-31", "month", "past"),  # an episode after it was told
    (None, None, "month", "any"),
])
def test_a_reading_that_does_not_fit_keeps_its_words_and_no_bounds(start, end, precision, role):
    values = _ground("last month", start, end, precision, role=role)
    assert values["time_words"] == "last month" and values["time_precision"] == "unknown"
    assert values["time_start"] is None and values["time_end"] is None


def test_a_schedule_repeats_and_an_unknown_zone_is_never_the_servers():
    values = _ground("Thursdays at 7pm", precision="repeating", repeats="weekly", weekdays=["thu"],
                     local_time="19:00", text="pottery is Thursdays at 7pm now", zone=None)
    assert values["time_precision"] == "repeating" and values["time_zone"] is None
    assert values["time_repeats"] == {"repeats": "weekly", "weekdays": ["thu"], "local_time": "19:00"}
    text = memory_time.describe(memory_time.span(type("Row", (), values)()), SAID)
    assert "weekly on Thursday at 19:00 (time zone unknown)" in text


# --------------------------------------------------------------------------- #
# Claims and entities
# --------------------------------------------------------------------------- #


def _dog_note(db, user):
    """The stage-2 run's composite note: the dog's name and its rain aversion in one sentence."""
    conversation = _conversation(db, user)
    taught, job = _said(db, conversation, "my dog Biscuit hates rain", at=SAID - timedelta(days=3))
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    note = SemanticMemory(user_id=user.id, kind="fact", normalized_key="pet:dog_biscuit",
                          content="The user's dog is named Biscuit and hates rain.", importance=0.7, confidence=1.0,
                          source_conversation_id=conversation.id, source_message_id=taught.id)
    db.add(note)
    db.commit()
    return conversation, taught, note


def test_a_name_correction_splits_the_note_and_keeps_the_rain(db, alice, monkeypatch):
    conversation, taught, note = _dog_note(db, alice)
    said = "no wait, my dog is Pretzel. Biscuit was the cat I had growing up"
    corrected, job = _said(db, conversation, said)
    dog = EntityMention(kind="pet", relation="dog", name="Pretzel")
    extract = _reads([
        MemoryCandidate(kind="fact", normalized_key="pet:dog_biscuit", content="The user's dog is named Pretzel.",
                        importance=0.7, confidence=1.0, entity=dog, attribute="name", value="Pretzel"),
        MemoryCandidate(kind="fact", normalized_key="pet:dog:dislikes_rain", content="The user's dog hates rain.",
                        importance=0.6, confidence=1.0, entity=dog, attribute="dislikes", value="rain",
                        split_from="pet:dog_biscuit"),
        MemoryCandidate(kind="fact", normalized_key="pet:childhood_cat:name",
                        content="The user had a cat named Biscuit growing up.", importance=0.5, confidence=1.0,
                        entity=EntityMention(kind="pet", relation="childhood cat", name="Biscuit"),
                        attribute="name", value="Biscuit", historical=True),
    ], [CorrectionCandidate(subject="dog", attribute="name", previous_value="Biscuit", new_value="Pretzel",
                            kind="mistaken", user_words="my dog is Pretzel")])
    _run(db, monkeypatch, job, extract)
    assert "pet:dog_biscuit: The user's dog is named Biscuit and hates rain." in extract.seen["known"]
    assert extract.seen["said"].startswith("SAID: Friday 30 October 2026, 14:00")

    name = db.get(SemanticMemory, note.id)  # the same row: what depended on it still points at it
    assert name.content == "The user's dog is named Pretzel." and name.attribute == "name" and name.revision == 2
    rain = _fact(db, alice, "pet:dog:dislikes_rain")
    assert rain.status == "active" and rain.value == "rain" and rain.entity_id == name.entity_id
    # Said when the note was: its source, its time, and where it was split from.
    assert rain.source_message_id == taught.id and rain.created_at == note.created_at
    assert rain.metadata_["split_from"]["fact_id"] == str(note.id)
    assert db.scalar(select(MemoryEvidence.role).where(MemoryEvidence.fact_id == rain.id)) == "origin"
    assert db.scalar(select(MemoryEvidence.role).where(MemoryEvidence.fact_id == name.id,
                                                       MemoryEvidence.message_id == corrected.id)) == "correction"
    cat = _fact(db, alice, "pet:childhood_cat:name")
    assert cat.entity_id != name.entity_id and cat.historical is True
    entities = {e.relation: e for e in db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == alice.id))}
    assert set(entities) == {"dog", "childhood cat"} and entities["dog"].name == "Pretzel"

    result, text = _search(db, alice, "what's my dog called and does he like walks in the rain?")
    contents = [r.content for r in result.records]
    assert "The user's dog is named Pretzel." in contents and "The user's dog hates rain." in contents
    assert "about: their dog Pretzel" in text
    assert "how things were, not how they are now" in _search(db, alice, "did I have a cat?")[1]


def test_a_note_split_without_keeping_its_key_is_replaced(db, alice, monkeypatch):
    conversation, taught, note = _dog_note(db, alice)
    _, job = _said(db, conversation, "my dog's actually called Pretzel")
    dog = EntityMention(kind="pet", relation="dog", name="Pretzel")
    _run(db, monkeypatch, job, _reads([
        MemoryCandidate(kind="fact", normalized_key="pet:dog:name", content="The user's dog is named Pretzel.",
                        importance=0.7, confidence=1.0, entity=dog, attribute="name", value="Pretzel"),
        MemoryCandidate(kind="fact", normalized_key="pet:dog:dislikes_rain", content="The user's dog hates rain.",
                        importance=0.6, confidence=1.0, entity=dog, attribute="dislikes", value="rain",
                        split_from="pet:dog_biscuit")]))
    old = db.get(SemanticMemory, note.id)
    assert old.status == "superseded" and old.metadata_["history"][-1]["reason"] == "split"
    assert _fact(db, alice, "pet:dog:dislikes_rain").status == "active"


def test_saying_it_again_unchanged_is_support_not_a_new_version(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    candidate = MemoryCandidate(kind="preference", normalized_key="preference:tea", content="The user drinks rooibos.",
                                importance=0.5, confidence=1.0, attribute="drinks", value="rooibos")
    _, job = _said(db, conversation, "I drink rooibos")
    _run(db, monkeypatch, job, _reads([candidate]))
    _, job = _said(db, conversation, "yep, rooibos, always", at=SAID + timedelta(minutes=5))
    _run(db, monkeypatch, job, _reads([candidate]))
    fact = _fact(db, alice, "preference:tea")
    assert fact.revision == 1 and fact.evidence_count == 2
    roles = sorted(db.scalars(select(MemoryEvidence.role).where(MemoryEvidence.fact_id == fact.id)))
    assert roles == ["origin", "support"]


def test_an_older_note_becoming_a_claim_is_not_a_changed_premise(db, alice, monkeypatch):
    conversation, taught, note = _dog_note(db, alice)
    note.historical = False
    db.commit()
    _, job = _said(db, conversation, "yeah Biscuit really hates rain")
    _run(db, monkeypatch, job, _reads([MemoryCandidate(
        kind="fact", normalized_key="pet:dog_biscuit", content="The user's dog is named Biscuit and hates rain.",
        importance=0.7, confidence=1.0, entity=EntityMention(kind="pet", relation="dog", name="Biscuit"),
        attribute="dislikes", value="rain")]))
    fact = db.get(SemanticMemory, note.id)
    assert fact.revision == 1 and fact.evidence_count == 2 and fact.attribute == "dislikes" and fact.entity_id
    assert sorted(db.scalars(select(MemoryEvidence.role).where(MemoryEvidence.fact_id == fact.id))) == ["support"]
    from app.db.models import MemoryFactRevision

    assert db.scalars(select(MemoryFactRevision).where(MemoryFactRevision.fact_id == fact.id)).all() == []


def test_leaving_historical_out_never_makes_a_past_claim_current(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    past = MemoryCandidate(kind="fact", normalized_key="home:leeds", content="The user lived in Leeds for ten years.",
                           importance=0.5, confidence=1.0, attribute="lived_in", value="Leeds", historical=True)
    _, job = _said(db, conversation, "I lived in Leeds for ten years")
    _run(db, monkeypatch, job, _reads([past]))
    _, job = _said(db, conversation, "ten years in Leeds, yeah", at=SAID + timedelta(minutes=3))
    _run(db, monkeypatch, job, _reads([past.model_copy(update={"historical": False})]))
    fact = _fact(db, alice, "home:leeds")
    assert fact.historical is True and fact.revision == 1


def test_two_people_of_one_name_stay_two_and_recall_says_so(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _, job = _said(db, conversation, "my sister Ines lives in Ghent")
    _run(db, monkeypatch, job, _reads([MemoryCandidate(
        kind="fact", normalized_key="relationship:sister:lives_in", content="The user's sister Ines lives in Ghent.",
        importance=0.6, confidence=1.0, entity=EntityMention(kind="person", relation="sister", name="Ines"),
        attribute="lives_in", value="Ghent")]))
    _, job = _said(db, conversation, "my coworker Ines is vegan", at=SAID + timedelta(minutes=5))
    # The model reuses the sister's ref, but the message says coworker: the same name is not enough.
    entities = memory_entities.refs(memory_entities.known(db, alice.id, "my coworker Ines is vegan"))
    sister_ref = next(r for r, e in entities.items() if e.relation == "sister")
    _run(db, monkeypatch, job, _reads([MemoryCandidate(
        kind="fact", normalized_key="relationship:coworker:diet", content="The user's coworker Ines is vegan.",
        importance=0.5, confidence=1.0,
        entity=EntityMention(ref=sister_ref, kind="person", relation="coworker", name="Ines"),
        attribute="diet", value="vegan")]))
    rows = list(db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == alice.id)))
    assert sorted(e.relation for e in rows) == ["coworker", "sister"]
    result, text = _search(db, alice, "what should I cook when Ines visits?")
    assert result.coverage.get("references_ambiguous") and result.coverage["ambiguous_about"] == ["ine"]
    assert "don't pick one" in text


def test_a_ref_the_message_does_not_mention_is_not_attached(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _, job = _said(db, conversation, "my brother Tomas plays cello")
    _run(db, monkeypatch, job, _reads([MemoryCandidate(
        kind="fact", normalized_key="relationship:brother:instrument", content="The user's brother Tomas plays cello.",
        importance=0.5, confidence=1.0, entity=EntityMention(kind="person", relation="brother", name="Tomas"),
        attribute="plays", value="cello")]))
    # A new conversation: nothing before it mentions the brother (an earlier mention would support the ref).
    _, job = _said(db, _conversation(db, alice), "the neighbours are loud again", at=SAID + timedelta(minutes=2))
    _run(db, monkeypatch, job, _reads([MemoryCandidate(
        kind="fact", normalized_key="home:neighbours", content="The user's neighbours are loud.", importance=0.3,
        confidence=0.8, entity=EntityMention(ref="e1", kind="person"), attribute="noise", value="loud")]))
    assert _fact(db, alice, "home:neighbours").entity_id is None


def test_an_old_fact_outside_the_recent_window_is_still_shown(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    old = SemanticMemory(user_id=alice.id, kind="preference", normalized_key="preference:kayak_brand",
                         content="The user paddles a Wavesport kayak.", importance=0.4, confidence=1.0,
                         last_confirmed_at=(SAID - timedelta(days=400)).replace(tzinfo=None))
    db.add(old)
    for i in range(long_term_memory.KNOWN_SHOWN):
        db.add(SemanticMemory(user_id=alice.id, kind="fact", normalized_key=f"fact:filler_{i}",
                              content=f"Filler fact number {i}.", importance=0.1, confidence=1.0))
    db.commit()
    _, job = _said(db, conversation, "I sold the Wavesport kayak last week")
    extract = _reads()
    _run(db, monkeypatch, job, extract)
    assert "preference:kayak_brand: The user paddles a Wavesport kayak." in extract.seen["known"]


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


def _visit(words="I'm going to see my sister Ines in Ghent in December", time=None, key="plan:new", status="planned"):
    return MemoryCandidate(
        kind="plan", normalized_key=key, content="The user plans to visit their sister Ines in Ghent in December.",
        importance=0.7, confidence=1.0, user_words=words, plan_status=status,
        entity=EntityMention(kind="person", relation="sister", name="Ines"),
        participants=[EntityMention(kind="place", name="Ghent")],
        time=time or TimeReading(words="in December", start="2026-12-01", end="2026-12-31", precision="month",
                                 year_inferred=True))


def test_a_plan_is_one_item_with_its_words_time_and_people(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    words = "I'm going to see my sister Ines in Ghent in December"
    asked, job = _said(db, conversation, f"oh and {words}!")
    _run(db, monkeypatch, job, _reads([_visit()]))
    [plan] = db.scalars(select(WorkingItem).where(WorkingItem.user_id == alice.id, WorkingItem.kind == "plan"))
    assert plan.status == "planned" and plan.user_words == words and plan.time_words == "in December"
    assert plan.time_inferred is True and plan.due_at == plan.time_end
    links = {(r.role) for r in db.scalars(select(MemoryEntityLink).where(MemoryEntityLink.item_id == plan.id))}
    assert links == {"about", "place"}
    assert not db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == alice.id)).all()

    result, text = _search(db, alice, "when am I seeing my sister?")
    assert any(r.ref.kind == "item" and r.subkind == "plan" for r in result.records)
    assert 'planned for: "in December" (said on 30 Oct 2026): December 2026 (the year wasn\'t said; assumed)' in text
    assert f'Their words: "{words}"' in text and "Open plans" in text

    # Said again with the date fixed: the same item, revised, never a second plan.
    _, job = _said(db, conversation, "the Ghent trip moved to the 12th of December", at=SAID + timedelta(days=1))
    extract = _reads([_visit("the Ghent trip moved to the 12th of December", key=long_term_memory.plan_key(plan),
                             time=TimeReading(words="the 12th of December", start="2026-12-12", end="2026-12-12",
                                              precision="day", year_inferred=True))])
    _run(db, monkeypatch, job, extract)
    assert extract.seen["plans"][0].startswith(long_term_memory.plan_key(plan))
    [plan] = db.scalars(select(WorkingItem).where(WorkingItem.user_id == alice.id, WorkingItem.kind == "plan"))
    assert plan.time_precision == "day" and plan.revision == 2

    # It happened: closed with its source.
    _, job = _said(db, conversation, "back from Ghent, it was lovely", at=SAID + timedelta(days=45))
    _run(db, monkeypatch, job, _reads([_visit("back from Ghent", key=long_term_memory.plan_key(plan),
                                              status="happened")]))
    assert db.get(WorkingItem, plan.id).status == "happened"


def test_a_plan_whose_time_passed_needs_a_look_and_never_happens_by_itself(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _, job = _said(db, conversation, "I'm going to see my sister Ines in Ghent in December")
    _run(db, monkeypatch, job, _reads([_visit()]))
    memory_manager.tick_due(db, now=datetime(2027, 1, 2, tzinfo=timezone.utc))
    [plan] = db.scalars(select(WorkingItem).where(WorkingItem.user_id == alice.id, WorkingItem.kind == "plan"))
    assert plan.status == "needs_review" and plan.closed_at is None


def test_forgetting_a_plan_takes_the_people_only_it_was_about(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _, job = _said(db, conversation, "I'm going to see my sister Ines in Ghent in December")
    _run(db, monkeypatch, job, _reads([_visit()]))
    [plan] = db.scalars(select(WorkingItem).where(WorkingItem.user_id == alice.id, WorkingItem.kind == "plan"))
    forget = memory_forget.MemoryForget(user_id=alice.id, source_message_id=plan_source(db, plan),
                                        conversation_id=conversation.id, request={"about": ["Ghent"]})
    db.add(forget)
    db.commit()
    assert memory_forget._apply(db, forget.id, 0, [{"kind": "item", "id": str(plan.id)}]) == "done"
    assert db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == alice.id)).all() == []


def plan_source(db, plan) -> uuid.UUID:
    return db.scalar(select(MemoryEvidence.message_id).where(MemoryEvidence.item_id == plan.id,
                                                             MemoryEvidence.role == "origin"))
