"""The System page's feeds (services/system_parts.py): every part reads back, the watch log and the errors log
show real records only."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db.models import DecisionAttempt, DecisionExecution, MindThought, SemanticMemory
from app.services import system_parts
from tests.db_support import create_user, database, db_env  # noqa: F401  (fixtures)

def test_every_feed_reads_an_empty_history(db_env):
    with db_env.db() as db:
        user = create_user(db)
        for part in system_parts.PARTS:
            assert system_parts.feed(db, user.id, part) is not None, part
        watch = system_parts.highlights(db, user.id)
        assert watch == {"goal": None, "items": []}
        assert system_parts.errors(db, user.id, {"parts": []}) == []


def test_watch_log_holds_her_thoughts_memory_and_hands_runs_newest_first(db_env):
    now = datetime.now(timezone.utc)
    with db_env.db() as db:
        user = create_user(db)
        attempt = uuid.uuid4()
        db.add(MindThought(user_id=user.id, level="hard", text="The cave is too dark to mine safely.",
                           thinking="I keep dying near the lava.", conclusion_kind="decision",
                           created_at=now - timedelta(seconds=30)))
        db.add(SemanticMemory(user_id=user.id, kind="preference", normalized_key="likes:tea",
                              content="Likes green tea.", created_at=now - timedelta(seconds=20)))
        db.add(DecisionAttempt(id=attempt, user_id=user.id, learner="hands", trigger="step", status="ok",
                               input={"plan": {"step": {"do": "mine coal"}}},
                               output={"output": {"status": "working", "note": "coal is right there"},
                                       "action": ["mine", {"block": "coal_ore", "count": 3}]},
                               started_at=now - timedelta(seconds=12), produced_at=now - timedelta(seconds=11),
                               expires_at=now + timedelta(days=1)))
        db.add(DecisionExecution(id=uuid.uuid4(), user_id=user.id, attempt_id=attempt, kind="skill_run",
                                 executor="pack", result="succeeded",
                                 intended={"skill": "mine", "params": {"block": "coal_ore", "count": 3}},
                                 executed={"status": "done", "seconds": 8,
                                           "observed": {"before": {"inventory": {"coal": 1}, "health": 20},
                                                        "after": {"inventory": {"coal": 4}, "health": 18}}},
                                 ended_at=now - timedelta(seconds=2), expires_at=now + timedelta(days=1)))
        db.add(DecisionAttempt(id=uuid.uuid4(), user_id=user.id, learner="mind", trigger="user_message",
                               status="error", error="not JSON", input={"reason": "user_message"}, started_at=now,
                               expires_at=now + timedelta(days=1)))
        db.commit()

        watch = system_parts.highlights(db, user.id)
        assert [i["part"] for i in watch["items"]] == ["mind", "hands", "memory", "mind"]
        assert watch["items"][0]["kind"] == "evaluation" and watch["items"][0]["tone"] == "error"
        run = watch["items"][1]
        assert run["text"].startswith("mine") and "coal +3" in run["detail"]
        assert watch["items"][3]["detail"] == "I keep dying near the lava."

        hands = system_parts.feed(db, user.id, "hands")
        decided = hands["decisions"][0]
        assert decided["claim"] == "working" and decided["note"] == "coal is right there"
        assert decided["run"]["inventory"] == "coal +3" and decided["run"]["pack_status"] == "done"
        assert "health 18" in decided["run"]["after"]

        errors = system_parts.errors(db, user.id, {"parts": [{"id": "memory", "workers": [
            {"name": "memory", "error": "boom", "seen_ago": 3}]}]})
        assert {e["part"] for e in errors} == {"mind", "memory"}
        assert any(e["text"] == "not JSON" for e in errors)


def test_phone_feed_shows_her_replies_and_held_checks(db_env, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from app.db.models import PhoneOutbound

    with db_env.db() as db:
        user = create_user(db)
        other = uuid.uuid4()
        log = tmp_path / "phone_checks.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in (
            {"at": "2026-11-23T10:00:00+00:00", "check": "reply", "kind": "hers", "user": str(user.id), "send": False,
             "why": "It names where the user is.", "reveals": ["the user's location"], "conversation": "c_rina"},
            {"at": "2026-11-23T10:01:00+00:00", "check": "operation", "user": str(other), "allowed": False,
             "why": "someone else's", "operation": "open Maps"},
        )) + "\n")
        monkeypatch.setattr("app.core.config.get_settings",
                            lambda: SimpleNamespace(phone_check_log_path=str(log)))
        db.add(PhoneOutbound(id=uuid.uuid4(), user_id=user.id, conversation_id="c_rina", notification_key="k1",
                             revision=1, text="On my way!", asked_by="her", status="sent"))
        db.commit()
        feed = system_parts.feed(db, user.id, "phone")
        assert [(r["text"], r["asked_by"], r["status"]) for r in feed["replies"]] == [("On my way!", "her", "sent")]
        assert [(c["check"], c["ok"], c["why"]) for c in feed["checks"]] == [
            ("reply", False, "It names where the user is.")]  # another user's line never shows
        watch = system_parts.highlights(db, user.id)
        assert {(i["part"], i["kind"], i["anchor"]) for i in watch["items"]} == {
            ("phone", "reply", "replies"), ("phone", "check", "checks")}
