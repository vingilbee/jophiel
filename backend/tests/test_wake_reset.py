"""A wake about looking at her desktop or the phone (``come_back_to(..., on=...)``) moves instead of firing when she
has used it since: the timer resets while she's at it (services/coordinator.py ``_reset``)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import CoordinatorWork
from app.services import coordinator
from test_coordinator import env  # noqa: F401  (fixture)
from db_support import db_env, database  # noqa: F401


def wake(env, *, on, in_seconds=60):
    with env.db() as db:
        row = coordinator.schedule_wake(db, user_id=env.user.id, conversation_id=env.conversation.id,
                                        about="see whether the install finished", on=on,
                                        due_at=datetime.now(timezone.utc) + timedelta(seconds=in_seconds))
        db.commit()
        return row.id


def make_due(env, work_id):
    with env.db() as db:
        row = db.get(CoordinatorWork, work_id)
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()


def claim(env):
    with env.db() as db:
        work, _ = coordinator._claim(db, "reset-test", ("wake",))
        db.commit()
        return work


def test_used_since_it_waits_on_from_her_last_use(env, monkeypatch):
    work_id = wake(env, on="desktop", in_seconds=60)
    with env.db() as db:
        inp = db.get(CoordinatorWork, work_id).input
    assert inp["on"] == "desktop" and 59 <= inp["delay_s"] <= 60 and inp["resets"] == 0
    make_due(env, work_id)
    used = time.time()
    monkeypatch.setattr(coordinator, "_last_use", lambda user_id, body: used if body == "desktop" else None)
    assert claim(env) is None  # she looked again meanwhile: not now
    with env.db() as db:
        row = db.get(CoordinatorWork, work_id)
        assert row.status == "queued" and row.input["resets"] == 1
        assert abs(row.next_attempt_at.timestamp() - (used + row.input["delay_s"])) < 1
    # Left alone that long since: it fires.
    make_due(env, work_id)
    claimed = claim(env)
    assert claimed is not None and claimed.id == work_id


def test_a_wake_not_about_a_device_fires_as_set(env, monkeypatch):
    work_id = wake(env, on=None)
    make_due(env, work_id)
    monkeypatch.setattr(coordinator, "_last_use", lambda user_id, body: time.time())
    claimed = claim(env)
    assert claimed is not None and claimed.id == work_id


def test_moving_it_keeps_what_it_is_about_and_resets_stop_at_the_limit(env, monkeypatch):
    work_id = wake(env, on="phone")
    with env.db() as db:
        moved = coordinator.schedule_wake(db, user_id=env.user.id, conversation_id=env.conversation.id,
                                          about="later", due_at=datetime.now(timezone.utc) + timedelta(seconds=30),
                                          moves=db.get(CoordinatorWork, work_id), reason="they're still downloading")
        db.commit()
        assert moved.input["on"] == "phone"
        moved.input = {**moved.input, "resets": coordinator.MAX_RESETS}
        db.commit()
        moved_id = moved.id
    make_due(env, moved_id)
    monkeypatch.setattr(coordinator, "_last_use", lambda user_id, body: time.time())
    claimed = claim(env)
    assert claimed is not None and claimed.id == moved_id
    with env.db() as db:
        assert [w.status for w in db.scalars(select(CoordinatorWork).where(CoordinatorWork.id == work_id))] == [
            "cancelled"]
