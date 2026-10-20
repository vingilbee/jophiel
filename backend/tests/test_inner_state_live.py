"""Her inner state, live (services/inner_state_live.py): the frozen config's live copy, words that
never hide uncertainty, and stored updates that are atomic and count each event once.

The Redis tests need ``TEST_REDIS_URL`` (a throwaway Redis, never the dev one); they're skipped
without it."""
import json
import os
import threading
import uuid
from pathlib import Path

import pytest

from app.services import inner_state_live as live

DOCS = Path(__file__).resolve().parents[2] / "docs" / "characters"


def test_the_live_config_is_the_frozen_one():
    from app.ai.Cognitive.simple.live import CHARACTER_DIR
    assert (CHARACTER_DIR / "columbina.inner_state.json").read_text() == (DOCS / "columbina.inner_state.json").read_text()


def test_words_name_both_readings_when_they_differ():
    base = live.frozen()["baseline"]
    same = {rd: dict(base) for rd in live.READINGS}
    assert " or " not in live.words(same)["mood"]
    split = {"small": dict(base, mood=0.45), "clear": dict(base, mood=0.6)}   # content vs happy
    assert live.words(split)["mood"] == "content or happy"


def test_observe_does_nothing_when_off(monkeypatch):
    monkeypatch.setattr(live, "enabled", lambda: False)
    monkeypatch.setattr(live, "_redis", lambda: pytest.fail("touched Redis while off"))
    live.observe(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), history=[], message="hi", reply="hey")


@pytest.fixture
def redis_db(monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL (a throwaway Redis) to run the stored-state tests")
    import redis
    r = redis.from_url(url)
    r.flushdb()
    monkeypatch.setattr(live, "_redis", lambda: r)
    monkeypatch.setattr(live, "enabled", lambda: True)
    yield r
    r.flushdb()


def appraisal(feeling="hurt", status="opened", size="clear", incident="i1"):
    return dict(effects=[dict(feeling=feeling, status=status, size=size, incident=incident, quote="it stings")],
                incidents_after=[dict(id=incident, label="being called useless")])


def test_an_update_moves_her_mood_and_records_its_cause(redis_db):
    user, conv = uuid.uuid4(), uuid.uuid4()
    before, after = live._apply(redis_db, user_id=user, conversation_id=conv, number=3, message="you're useless",
                                appraisal=appraisal(), at=1000.0)
    for rd in live.READINGS:
        assert after[rd]["tension"] > before[rd]["tension"]
    shown = live.view(user, now=1000.0)
    assert shown["causes"][0]["incident"] == "being called useless"
    assert shown["values"]["tension"]["small"] > live.frozen()["baseline"]["tension"]


def test_the_same_event_delivered_twice_counts_once(redis_db):
    user, conv = uuid.uuid4(), uuid.uuid4()
    _, once = live._apply(redis_db, user_id=user, conversation_id=conv, number=3, message="x", appraisal=appraisal(),
                          at=1000.0)
    _, twice = live._apply(redis_db, user_id=user, conversation_id=conv, number=3, message="x", appraisal=appraisal(),
                           at=1000.0)
    assert twice == once


def test_concurrent_updates_are_both_kept(redis_db):
    user = uuid.uuid4()
    convs = [uuid.uuid4() for _ in range(6)]
    threads = [threading.Thread(target=live._apply, args=(redis_db,),
                                kwargs=dict(user_id=user, conversation_id=c, number=2, message=f"m{i}",
                                            appraisal=appraisal(feeling="happy", incident=f"i{i}"), at=1000.0))
               for i, c in enumerate(convs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stored = json.loads(redis_db.get(live.STATE_KEY.format(user=user)))
    assert len(stored["causes"]) == len(convs)
    assert len(stored["small"]["applied"]) == len(convs)
