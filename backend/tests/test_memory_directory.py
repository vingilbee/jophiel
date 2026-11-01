"""The rundown (services/memory_directory.py; docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): every area she knows
about, from all three stores, with counts that agree with what's shown and nothing left out silently. Real
PostgreSQL (``TEST_DATABASE_URL``); no model calls."""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import EpisodicMemory, MemoryEntity, SemanticMemory
from app.services import memory_directory, memory_entities, memory_manager
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_forget import on  # noqa: F401  (autouse settings fixture)

NOW = datetime.now(timezone.utc)


def _fact(db, user, key, content, kind="fact", **values):
    fact = SemanticMemory(user_id=user.id, kind=kind, normalized_key=key, content=content,
                          importance=values.pop("importance", 0.5), confidence=0.9, **values)
    db.add(fact)
    db.commit()
    return fact


def _life(db, user):
    nora = MemoryEntity(user_id=user.id, kind="person", name="Nora", relation="sister")
    pretzel = MemoryEntity(user_id=user.id, kind="pet", name="Pretzel", relation="dog")
    biscuit = MemoryEntity(user_id=user.id, kind="pet", name="Biscuit", relation="childhood cat")
    pottery = MemoryEntity(user_id=user.id, kind="activity", name="pottery class", relation=None)
    db.add_all([nora, pretzel, biscuit, pottery])
    db.commit()
    _fact(db, user, "fact:nora_city", "Their sister Nora lives in Porto.", entity_id=nora.id, attribute="lives_in",
          value="Porto")
    _fact(db, user, "fact:pretzel_name", "Their dog is called Pretzel.", entity_id=pretzel.id, attribute="name",
          value="Pretzel")
    _fact(db, user, "fact:pretzel_rain", "Pretzel hates the rain.", entity_id=pretzel.id, attribute="dislikes",
          value="rain")
    _fact(db, user, "fact:biscuit", "They had a cat called Biscuit as a child.", entity_id=biscuit.id,
          attribute="name", value="Biscuit", historical=True)
    _fact(db, user, "fact:pottery_time", "Pottery class is on Thursdays at 7pm.", entity_id=pottery.id,
          attribute="meets", value="Thursday 19:00")
    _fact(db, user, "constraint:peanut_allergy", "The user is allergic to peanuts.", kind="constraint", importance=0.9)
    _fact(db, user, "preference:no_cilantro", "The user dislikes cilantro.", kind="preference")
    _fact(db, user, "fact:knee", "The user's left knee is weak.", kind="fact")  # health: no special hiding
    db.add(EpisodicMemory(user_id=user.id, summary="The user went camping by a lake and saw a heron.",
                          importance=0.5, confidence=0.9))
    plan = memory_manager.add_item(db, user.id, kind="question", text_="Ask how the glaze firing went",
                                   owner="columbina", epistemic="inferred")
    memory_entities.link(db, user.id, pottery, role="about", item_id=plan.id)
    memory_manager.add_item(db, user.id, kind="question", text_="Which trail did they want to try?",
                            owner="columbina", epistemic="inferred")
    db.commit()


def test_the_rundown_covers_every_area_and_store_with_counts_that_agree(db, alice):
    _life(db, alice)
    d = memory_directory.build(db, alice.id, now=NOW)
    labels = [a.label for a in d.areas]
    assert labels[0].startswith("Standing constraints")
    assert {"Family", "Pets", "Activities", "Preferences", "Experiences", "Open questions",
            "Other things about them"} <= set(labels)
    assert d.totals == {"fact": 8, "episode": 1, "item": 2}
    assert sum(len(a.records) for a in d.areas) + sum(len(a.records) for a in d.hidden) == 11
    pets = next(a for a in d.areas if a.label == "Pets")
    assert len(pets.records) == 3 and set(pets.names) == {"their dog Pretzel", "their childhood cat Biscuit"}
    activities = next(a for a in d.areas if a.label == "Activities")
    assert {r.ref.kind for r in activities.records} == {"fact", "item"}  # facts and open items, one area
    text = memory_directory.render(d, now=NOW, zone="UTC")
    assert "## Pets (their dog Pretzel, their childhood cat Biscuit) · 3 facts" in text
    assert "The user's left knee is weak." in text  # no area is hidden as sensitive
    assert "That is everything in memory about them as it stands." in text
    assert "camping" in text  # a standalone episode, about no one


def test_what_isnt_shown_is_always_said(db, alice, monkeypatch):
    _life(db, alice)
    monkeypatch.setattr(memory_directory, "MAX_AREAS", 3)
    monkeypatch.setattr(memory_directory, "EXAMPLES", 1)
    d = memory_directory.build(db, alice.id, now=NOW)
    text = memory_directory.render(d, now=NOW, zone="UTC")
    assert len(d.areas) == 3 and d.hidden
    assert "areas not shown:" in text and "Not everything is shown here" in text
    assert "That is everything" not in text
    hidden = next(a for a in d.hidden)
    assert hidden.label in text
    # Replaced facts aren't current: counted, not shown.
    fact = db.query(SemanticMemory).filter_by(normalized_key="preference:no_cilantro").one()
    fact.status = "superseded"
    db.commit()
    text = memory_directory.render(memory_directory.build(db, alice.id, now=NOW), now=NOW, zone="UTC")
    assert "1 replaced or forgotten facts" in text


def test_a_topic_gives_that_area_in_full_and_pages(db, alice, monkeypatch):
    _life(db, alice)
    d = memory_directory.build(db, alice.id, topic="my dog", now=NOW)
    assert [a.label for a in d.areas] == ["Pets"] and len(d.records) == 3
    sister = memory_directory.build(db, alice.id, topic="sister", now=NOW)
    assert [r.content for r in sister.records] == ["Their sister Nora lives in Porto."]
    monkeypatch.setattr(memory_directory, "PAGE", 2)
    first = memory_directory.build(db, alice.id, topic="pets", now=NOW)
    assert len(first.records) == 2 and first.next_page
    rest = memory_directory.build(db, alice.id, topic="pets", page_token=first.next_page, now=NOW)
    assert len(rest.records) == 1 and not {r.ref for r in first.records} & {r.ref for r in rest.records}
    none = memory_directory.build(db, alice.id, topic="spaceships", now=NOW)
    assert none.status == "no_match" and "No area matches" in memory_directory.render(none, now=NOW, zone="UTC")


def test_a_new_user_has_nothing_yet_and_another_users_memories_never_show(db, alice):
    bob = _user(db, "bob")
    _life(db, bob)
    d = memory_directory.build(db, alice.id, now=NOW)
    assert d.status == "no_match" and "Nothing is stored" in memory_directory.render(d, now=NOW, zone="UTC")
