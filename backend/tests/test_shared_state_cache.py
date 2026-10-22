"""The fast path's memory cache (services/recall_references.cached_for_reply): the last answer's references,
re-read by memory, so a deletion or change since is seen and never served from an old copy. Real PostgreSQL
(``TEST_DATABASE_URL``); no model or embedding calls."""
from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.ai.recall.contracts import MemoryRef
from app.db.models import MemoryEvent, WorkingItem
from app.services import memory_manager, recall_references
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from tests.test_recall import _answer, _conversation, _played, _quote_each, _say, on  # noqa: F401  (autouse)


def test_the_cache_is_reread_so_a_deleted_memory_drops_out_and_a_change_shows(db, alice):
    _, _, events = _played(db, alice)
    conversation = _conversation(db, alice)
    asked = _say(db, conversation, "what happened in minecraft?")
    turn = recall_references.for_reply(alice.id, conversation.id, asked.id, asked.content, None)
    goal = next(r for r in turn.result.records if r.ref.kind == "item")
    died = MemoryRef("event", str(events["died"].id))
    _, receipt_id = _answer(db, turn, conversation, "You died, and the Nether is still the goal.")
    recall_references.associate(receipt_id, ask=_quote_each({str(died): "You died",
                                                              str(goal.ref): "the Nether is still the goal"}))
    follow = _say(db, conversation, "and then?")
    before = recall_references.cached_for_reply(alice.id, conversation.id, follow.id, None)
    assert {r.ref for r in before.result.records} == {died, goal.ref} and before.result.status == "matched"

    entry = db.get(WorkingItem, uuid.UUID(goal.ref.id))
    memory_manager.resolve(db, alice.id, memory_manager.ref(entry), status="completed", reason="done",
                           source=memory_manager.Source("message", asked.id))
    db.execute(delete(MemoryEvent).where(MemoryEvent.id == events["died"].id))
    db.commit()
    after = recall_references.cached_for_reply(alice.id, conversation.id, follow.id, None)
    [kept] = after.result.records
    assert kept.ref == goal.ref and kept.status == "completed" and kept.applicability == "no_longer_current"
    assert kept.fingerprint != next(r for r in before.result.records if r.ref == goal.ref).fingerprint
    # Its reference row went with it: the receipt's count is what says it's gone.
    assert after.result.coverage["references_deleted"] == 1 and "references_gone" not in after.result.coverage
    assert "since been removed" in after.rendered and "re-read now" in after.rendered
    # No search ran: the result is the cache, and its receipt supplies it again for the next follow-up.
    assert after.result.coverage["source"] == "cache" and list(after.supplied) == [goal.ref]

    other = _conversation(db, alice)
    empty = recall_references.cached_for_reply(alice.id, other.id, None, None)
    assert empty.result.status == "not_searched" and "none" in empty.rendered


def test_another_users_references_are_never_read(db, alice, bob):
    _, _, events = _played(db, alice)
    died = MemoryRef("event", str(events["died"].id))
    from app.services import recall
    result = recall.reread(db, bob.id, [died])
    assert result.records == [] and result.coverage["references_gone"] == [str(died)]
