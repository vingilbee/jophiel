"""Her follow-through on conditional promises (services/follow_through.py), end to end through her phone source, the
quick thought's ``act``, the real coordinator and chat worker, her mind's tools and her phone tool. Only the models
are scripted (the quick thought, each evaluation) and the phone's answer to a command; phone state is real Redis
(TEST_REDIS_URL), everything else real PostgreSQL (TEST_DATABASE_URL).

The reproduction: she promised to keep "Into You – Super Slowed" playing and switch back if Spotify moved on; twice
her background mind saw the change and the promise, and both thoughts ended without a command."""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import redis
from sqlalchemy import func, select, update

from app.ai.mind.think import QuickThought
from app.ai.tools import phone as phone_tools
from app.ai.tools.mind import build_mind_tools
from app.core.config import get_settings
from app.db.models import CoordinatorWork, FollowThroughAttempt, WorkingItem
from app.services import (coordinator, follow_through, memory_manager, mind, mind_phone, phone_actions,
                          phone_state)
from app.services import chat as chat_service
from app.workers import chat as chat_worker
from test_coordinator import drain, env, messages, run_job  # noqa: F401
from db_support import database, db_env  # noqa: F401

INTO_YOU = {"app": "Spotify", "title": "Into You - Super Slowed", "artist": "Altare.alt, Eclipse", "state": "playing"}
NEXT_SONG = {"app": "Spotify", "title": "Yume To Hazakura", "artist": "Wotamin", "state": "playing"}
OTHER_SONG = {"app": "Spotify", "title": "Something Else", "artist": "Someone", "state": "playing"}


@pytest.fixture
def phone(env, monkeypatch, tmp_path):
    """Real Redis for the phone's state; a scripted phone for commands (``answers``: what it says to each); her
    evaluations get her real phone tool beside her mind's tools, as on any wake of hers (own turn)."""
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to exercise the phone's state in real Redis")
    bus = redis.from_url(url)
    monkeypatch.setattr(phone_state, "_client", lambda: bus)
    # Her quick thoughts' log stays in the test's own directory, never var/mind.jsonl.
    monkeypatch.setattr(get_settings(), "mind_log_path", str(tmp_path / "mind.jsonl"))
    sent, answers = [], []

    def act(user_id, conversation_id, action, args, *, target=None, view=None, asked_by="user", **_):
        sent.append({"action": action, "args": args, "asked_by": asked_by, "at": time.time()})
        status = answers.pop(0) if answers else "done"
        text = {"done": "Went back a track", "unconfirmed": "Media control sent, no answer yet."}.get(
            status, "Couldn't control the music")
        return phone_actions.Outcome(status, uuid.uuid4(), text)

    monkeypatch.setattr(phone_actions, "act", act)

    async def stream(*, messages, context, **_):
        env.seen.append(SimpleNamespace(messages=messages, context=context))
        built = [*build_mind_tools(context.mind),
                 *phone_tools.build_phone_tools(context.user_id, context.conversation_id, own_turn=True,
                                                evaluation=context.mind)]
        tools = {t.name: t for t in built}

        async def use(name, **args):
            result = await tools[name].ainvoke(args)
            context.mind.actions.append({"tool": name, "status": "success", "result": str(result)[:300]})
            return result

        context.tools = set(tools)
        words = await env.scripts.pop(0)(use, context)
        for token in ([words] if words else []):
            yield {"type": "token", "content": token}

    monkeypatch.setattr(chat_worker, "stream_chat", stream)
    mind._minds.clear()
    yield SimpleNamespace(bus=bus, sent=sent, answers=answers)
    user = str(env.user.id)
    bus.hdel(phone_state.USERS_KEY, user)
    bus.delete(phone_state._key(user))
    bus.close()


def report(env, **fields) -> None:
    phone_state.report(env.user.id, fields, conversation_id=env.conversation.id)


def promise(env, text, when) -> WorkingItem:
    with env.db() as db:
        entry = memory_manager.note_commitment(db, env.user.id, text, trigger={"kind": "event", "text": when})
        db.commit()
        db.refresh(entry)
        db.expunge(entry)
        return entry


class Thinker:
    """The quick thought's model, scripted: what it notices and whether a promise is due (``act``)."""

    def __init__(self, text, *, act=None, item=None, say=None):
        self.out = QuickThought(text, say, 1.0, act=act, item=item)
        self.read = []

    def think(self, level, text, **_):
        self.read.append(text)
        return self.out


def tick(env, source, previous=None):
    """One phone tick as her mind worker runs it: poll, the moment, and its snapshot for the next one."""
    with env.db() as db:
        rows = [row for row in source.poll(db) if row[0].user_id == env.user.id]
        assert rows, "the phone wasn't due a tick"
        scope, payload = rows[0]
        moment = source.moment(db, scope, payload, previous, time.time(), thoughts=[], thinking=None)
    return scope, moment


def think(scope, moment, source, thinker) -> None:
    """The quick thought on that moment, as ``mind._start_quick`` runs it (without the pool)."""
    data = moment.data
    cause = {"third_party": bool(data.get("third_party")), "arrived": [], "reply_targets": [],
             "transition": data.get("transition")}
    mind._think_quick(source, scope, "light", moment.text, time.time(), thinker, cause)


def follow_wakes(env) -> list[CoordinatorWork]:
    with env.db() as db:
        return list(db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "mind",
            CoordinatorWork.input["wake"].astext == "follow_through").order_by(CoordinatorWork.created_at)))


def checks(env, *, live=True) -> list[CoordinatorWork]:
    with env.db() as db:
        query = select(CoordinatorWork).where(CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "wake",
                                              CoordinatorWork.input["follow_check"].astext.is_not(None))
        if live:
            query = query.where(CoordinatorWork.status.in_(coordinator.ACTIVE))
        return list(db.scalars(query.order_by(CoordinatorWork.created_at)))


def attempts(env) -> list[FollowThroughAttempt]:
    with env.db() as db:
        return list(db.scalars(select(FollowThroughAttempt).where(FollowThroughAttempt.user_id == env.user.id)
                               .order_by(FollowThroughAttempt.issued_at)))


def due_now(env) -> None:
    """The live check comes due (its time passed)."""
    with env.db() as db:
        db.execute(update(CoordinatorWork).where(
            CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "wake",
            CoordinatorWork.status.in_(coordinator.ACTIVE)).values(next_attempt_at=datetime.now(timezone.utc)
                                                                   - timedelta(seconds=1)))
        db.commit()


def write(env, text) -> uuid.UUID:
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        return chat_service.add_message(db, conversation=conversation, role="user", content=text).id


def spotify_moves_on(env, phone):
    """The promise, the song playing, a baseline tick, then Spotify advancing: the tick that sees it."""
    entry = promise(env, 'Keep "Into You - Super Slowed" playing; switch it back if Spotify moves on',
                    "if Spotify moves on to another song")
    report(env, media=INTO_YOU)
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)
    report(env, media=NEXT_SONG)
    scope, moment = tick(env, source, base.data["snapshot"])
    return entry, source, scope, moment


def ref(entry) -> str:
    return str(entry.id)[:8]


# --------------------------------------------------------------------------- #
# Spotify advances one song while the promise holds
# --------------------------------------------------------------------------- #

def test_spotify_advancing_wakes_her_once_she_corrects_and_a_fresh_report_settles_it(env, phone):
    entry, source, scope, moment = spotify_moves_on(env, phone)
    # Each track is news while something of hers waits on a condition, and her promise is in the moment, by id.
    assert 'Media: playing "Yume To Hazakura" by Wotamin on Spotify' in moment.fresh
    assert f"[{ref(entry)}] You promised: Keep" in moment.text and "(when: if Spotify moves on" in moment.text

    thinker = Thinker("I should switch it back to Into You, like I said I would.",
                      act="Spotify moved on: switch back to Into You", item=ref(entry))
    think(scope, moment, source, thinker)
    think(scope, moment, source, thinker)  # a second observation of the same change
    [wake] = follow_wakes(env)  # one wake, with no line to say: the thought had nothing to say
    assert wake.input["premise"] == {"item": str(entry.id)}

    async def correct(use, context):
        assert "FOLLOWING THROUGH" in context.wake and f"[{ref(entry)}]" in context.wake
        assert "Spotify moved on: switch back to Into You" in context.wake
        assert {"phone_action", "settle_attempt"} <= context.tools
        first = await use("phone_action", action="media_control", command="previous")
        assert "Went back a track" in first and "isn't the result" in first and "A check wakes you" in first
        again = await use("phone_action", action="media_control", command="previous")
        assert again.startswith("Not done: your attempt") and "still waiting for its result" in again
        [attempt] = attempts(env)
        claim = await use("settle_attempt", attempt=str(attempt.id)[:8], outcome="achieved", evidence="sent")
        assert claim.startswith("Not settled: no report of media from the phone since the attempt")
        await use("stay_silent", reason="waiting for the phone")
        return ""

    env.scripts.append(correct)
    drain(env)
    assert run_job(env)
    assert [(s["action"], s["args"], s["asked_by"]) for s in phone.sent] == [
        ("media_control", {"command": "previous"}, "her")]  # one command, from her, without another message
    [attempt] = attempts(env)
    assert attempt.status == "pending" and attempt.observe == "media" and attempt.checks == 1
    [check] = checks(env)

    report(env, media=INTO_YOU)  # the phone's fresh report after the command

    async def verify(use, context):
        assert f"A check on your attempt [{str(attempt.id)[:8]}]" in context.wake
        assert "the phone reported media" in context.wake and '"Into You - Super Slowed"' in context.wake
        done = await use("settle_attempt", attempt=str(attempt.id)[:8], outcome="achieved",
                         evidence='Spotify reports "Into You - Super Slowed" by Altare.alt, Eclipse again')
        assert done.startswith("Settled: achieved")
        return "Put Into You back on."

    env.scripts.append(verify)
    due_now(env)
    drain(env)
    assert run_job(env)
    [attempt] = attempts(env)
    assert attempt.status == "achieved" and "Into You" in attempt.settled_reason
    assert checks(env) == []  # nothing more to check
    assert ("assistant", "Put Into You back on.") in messages(env)
    with env.db() as db:  # the promise is recurring: it stays open
        assert db.get(WorkingItem, entry.id).closed_at is None
    assert len(phone.sent) == 1

    # The retest's record: observation -> thought -> wake -> command -> device answer -> fresh report -> outcome.
    import runpy
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts" / "follow_through_trace.py"
    printed = []
    monkey_argv = ["follow_through_trace.py", "--item", ref(entry)]
    old_argv, sys.argv = sys.argv, monkey_argv
    try:
        from contextlib import redirect_stdout
        import io
        out = io.StringIO()
        with redirect_stdout(out):
            runpy.run_path(str(script), run_name="__main__")
        printed = out.getvalue().splitlines()
    finally:
        sys.argv = old_argv
    kinds = [line.split("UTC")[1].split()[0] for line in printed[1:]]
    assert kinds.index("thought") < kinds.index("wake") < kinds.index("command") < kinds.index("check")
    assert "outcome" in kinds and any("achieved" in line and "Into You" in line for line in printed)
    assert any("the phone reported media" in line for line in printed)  # the fresh report, kept on the outcome


def test_a_track_change_with_nothing_waiting_is_not_news(env, phone):
    report(env, media=INTO_YOU)
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)
    report(env, media=NEXT_SONG)
    with env.db() as db:
        assert [row for row in source.poll(db) if row[0].user_id == env.user.id] == []  # no tick for a track
    assert "WHAT YOU'RE WAITING FOR" not in base.text


# --------------------------------------------------------------------------- #
# Accepted but wrong, or no fresh report: unresolved, bounded, never success
# --------------------------------------------------------------------------- #

def _first_attempt(env, phone):
    entry, source, scope, moment = spotify_moves_on(env, phone)
    think(scope, moment, source, Thinker("Switch it back.", act="switch back to Into You", item=ref(entry)))

    async def correct(use, context):
        await use("phone_action", action="media_control", command="previous")
        await use("stay_silent", reason="waiting")
        return ""

    env.scripts.append(correct)
    drain(env)
    assert run_job(env)
    return entry


def test_no_fresh_report_stays_unresolved_after_bounded_checks(env, phone):
    _first_attempt(env, phone)
    for n in range(len(follow_through.CHECK_DELAYS)):
        last = n == len(follow_through.CHECK_DELAYS) - 1

        async def look(use, context, last=last):
            assert "no report of media from the phone since the attempt" in context.wake
            assert ("this is its last check" in context.wake) is last
            [attempt] = attempts(env)
            refused = await use("settle_attempt", attempt=str(attempt.id)[:8], outcome="achieved",
                                evidence="the phone said done")
            assert refused.startswith("Not settled")
            await use("stay_silent", reason="still no report")
            return ""

        env.scripts.append(look)
        due_now(env)
        drain(env)
        assert run_job(env)
    [attempt] = attempts(env)
    assert attempt.status == "unresolved" and "no fresh report" in attempt.settled_reason
    assert attempt.checks == len(follow_through.CHECK_DELAYS) and checks(env) == []  # no endless checking
    assert len(phone.sent) == 1


def test_the_wrong_track_is_not_achieved_and_retries_stop_at_the_bound(env, phone):
    _first_attempt(env, phone)
    report(env, media=OTHER_SONG)  # fresh, but not what they wanted

    async def wrong(use, context):
        [attempt] = attempts(env)
        assert '"Something Else" by Someone' in context.wake
        settled = await use("settle_attempt", attempt=str(attempt.id)[:8], outcome="not_achieved",
                            evidence="Spotify reports Something Else, not Into You")
        assert settled.startswith("Settled: not achieved. 1 more try")
        retry = await use("phone_action", action="media_control", command="previous")
        assert "isn't the result" in retry
        return ""

    env.scripts.append(wrong)
    due_now(env)
    drain(env)
    assert run_job(env)
    report(env, media=NEXT_SONG)

    async def still_wrong(use, context):
        second = attempts(env)[-1]
        await use("settle_attempt", attempt=str(second.id)[:8], outcome="not_achieved", evidence="still wrong")
        third = await use("phone_action", action="media_control", command="previous")
        assert third.startswith("Not done: you've tried 2 times for this already")
        return "I tried twice to put Into You back but Spotify keeps moving on."

    env.scripts.append(still_wrong)
    due_now(env)
    drain(env)
    assert run_job(env)
    assert len(phone.sent) == 2 and [a.status for a in attempts(env)] == ["failed", "failed"]
    assert checks(env) == []


def _wake_and_run(env, source, previous, script, entry, **phone_change):
    """Something else on the phone changes (a new transition), her thought finds the promise still due, and the
    follow-through wake it leads to runs ``script``. Returns the new moment's snapshot."""
    report(env, **phone_change)
    scope, moment = tick(env, source, previous)
    think(scope, moment, source, Thinker("Still not Into You.", act="switch back to Into You", item=ref(entry)))
    env.scripts.append(script)
    drain(env)
    assert run_job(env)
    return moment.data["snapshot"]


def _settle_by_check(env, outcome="not_achieved"):
    async def look(use, context):
        attempt = attempts(env)[-1]
        result = await use("settle_attempt", attempt=str(attempt.id)[:8], outcome=outcome, evidence="still wrong")
        assert result.startswith("Settled")
        return ""

    env.scripts.append(look)
    due_now(env)
    drain(env)
    assert run_job(env)


def test_the_track_staying_wrong_while_the_app_opens_and_closes_buys_no_more_tries(env, phone):
    """Review of #54: the transition key changes with any phone reading, so opening the app mustn't reset the
    budget for a song that never changed back."""
    entry = _first_attempt(env, phone)
    report(env, media=NEXT_SONG)  # fresh, the same wrong song: Spotify ignored "previous"
    _settle_by_check(env)
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)

    async def second(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert "isn't the result" in result  # the second of two tries for this unmet stretch
        return ""

    snap = _wake_and_run(env, source, base.data["snapshot"], second, entry, charging=True)
    report(env, media=NEXT_SONG)
    _settle_by_check(env)

    async def third(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert result.startswith("Not done: you've tried 2 times for this already")
        return "Spotify keeps playing Yume To Hazakura; going back a track didn't bring Into You back."

    snap = _wake_and_run(env, source, snap, third, entry, charging=False)  # the app's state changing again
    assert len(phone.sent) == 2 and len({a.transition for a in attempts(env)}) == 1

    # Spotify itself moving on later is a distinct change: tries again.
    report(env, media=OTHER_SONG)

    async def later(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert "isn't the result" in result
        return ""

    _wake_and_run(env, source, snap, later, entry, screen="on")
    assert len(phone.sent) == 3 and len({a.transition for a in attempts(env)}) == 2


def test_a_moved_check_stays_the_attempts_check(env, phone):
    _first_attempt(env, phone)
    [check] = checks(env)
    with env.db() as db:
        row = db.get(CoordinatorWork, check.id)
        moved = coordinator.schedule_wake(db, user_id=env.user.id, conversation_id=env.conversation.id,
                                          about=row.input["about"], due_at=datetime.now(timezone.utc)
                                          + timedelta(minutes=5), moves=row, reason="give the phone longer")
        db.commit()
        moved_id = moved.id
    [live] = checks(env)
    attempt = attempts(env)[0]
    assert live.id == moved_id and live.input["follow_check"] == str(attempt.id)
    assert live.input["follow_item"] == str(attempt.item_id) and live.input["follow_transition"] == attempt.transition

    async def look(use, context):
        assert f"A check on your attempt [{str(attempt.id)[:8]}]" in context.wake and "settle_attempt" in context.tools
        await use("stay_silent", reason="no report yet")
        return ""

    env.scripts.append(look)
    due_now(env)
    drain(env)
    assert run_job(env)
    [attempt] = attempts(env)
    assert attempt.status == "pending" and attempt.checks == 2  # the moved check ran as its check: the next came
    # Settling cancels the live check whatever row it's on now.
    report(env, media=INTO_YOU)
    ev = SimpleNamespace(wake={"kind": "wake", "input": {"follow_item": str(attempt.item_id),
                                                         "follow_check": str(attempt.id)}}, user_id=env.user.id)
    assert follow_through.settle(ev, str(attempt.id)[:8], "achieved", "Into You").startswith("Settled: achieved")
    assert checks(env) == []


def test_a_check_that_died_never_leaves_the_promise_blocked(env, phone):
    """Review of #54: a check that expired (a worker down past it) or was dropped never reaches after(): the next
    try reconciles it instead of refusing forever."""
    entry = _first_attempt(env, phone)
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)

    def kill_checks():
        with env.db() as db:
            db.execute(update(CoordinatorWork).where(
                CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "wake",
                CoordinatorWork.status.in_(coordinator.ACTIVE)).values(
                status="expired", updated_at=datetime.now(timezone.utc) - timedelta(hours=1)))
            db.commit()

    kill_checks()

    async def waiting(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert result.startswith("Not done: your attempt")  # still unknown: but a new check now watches it
        return ""

    snap = _wake_and_run(env, source, base.data["snapshot"], waiting, entry, charging=True)
    [attempt] = attempts(env)
    assert attempt.status == "pending" and attempt.checks == 2 and len(checks(env)) == 1
    kill_checks()  # the last check dies too

    async def again(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert "isn't the result" in result
        return ""

    _wake_and_run(env, source, snap, again, entry, charging=False)
    first, second = attempts(env)
    assert first.status == "unresolved" and "its check ended without a reading" in first.settled_reason
    assert second.status == "pending" and len(phone.sent) == 2


def test_after_a_restart_past_its_check_the_attempt_moves_on_with_the_phone_untouched(env, phone):
    """Review of #54: nothing on the phone changes and she does nothing; the worker's sweep alone gives a dead check
    its successor, then ``unresolved`` after the last."""
    _first_attempt(env, phone)
    assert follow_through.sweep(force=True) == 0  # a live check is left alone

    def worker_was_down():
        with env.db() as db:
            db.execute(update(CoordinatorWork).where(
                CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "wake",
                CoordinatorWork.status.in_(coordinator.ACTIVE)).values(
                status="expired", updated_at=datetime.now(timezone.utc) - timedelta(hours=1)))
            db.commit()

    worker_was_down()
    assert follow_through.sweep(force=True) == 1
    [attempt] = attempts(env)
    assert attempt.status == "pending" and attempt.checks == 2 and len(checks(env)) == 1
    worker_was_down()
    assert follow_through.sweep(force=True) == 1
    [attempt] = attempts(env)
    assert attempt.status == "unresolved" and "its check ended without a reading" in attempt.settled_reason
    assert checks(env) == [] and len(phone.sent) == 1
    assert follow_through.sweep(force=True) == 0  # settled: nothing more


def test_the_sweep_runs_on_the_coordinators_heartbeat(monkeypatch):
    from app.services import coordinator_runtime

    called = []
    monkeypatch.setattr(follow_through, "sweep", lambda **kw: called.append(kw) or 0)
    runtime = coordinator_runtime.runtime()
    monkeypatch.setattr(runtime, "_reap_and_renew", lambda: None)
    monkeypatch.setattr(coordinator, "expire_due", lambda: 0)
    monkeypatch.setattr(runtime, "closed", False)
    try:
        runtime.tick()
    except Exception:  # noqa: BLE001 - the rest of the tick needs a database; the sweep came first
        pass
    assert called


def _follow_ev(env, entry, transition="t"):
    return SimpleNamespace(wake={"kind": "mind", "input": {"autonomous": True, "wake": "follow_through",
                                                           "extra": {"item": str(entry.id), "transition": transition}}},
                           user_id=env.user.id, conversation_id=env.conversation.id, wake_work_id=None, zone=None,
                           channel="web")


def test_completion_and_admission_take_the_item_lock_first(env, phone):
    """Review of #54: begin locked item -> attempt, finish attempt -> item, so a second wake admitted while the
    phone answered the first could deadlock and lose the answer. Both now lock the item first."""
    import threading

    from sqlalchemy import text

    entry = promise(env, "Keep Into You playing", "if Spotify moves on")
    report(env, media=NEXT_SONG)
    ev = _follow_ev(env, entry)
    attempt_id = follow_through.begin(ev, "media_control", {"command": "previous"})
    assert isinstance(attempt_id, uuid.UUID)

    holder = env.db()
    holder.execute(select(WorkingItem).where(WorkingItem.id == entry.id).with_for_update())  # a wake mid-admission
    done = []
    worker = threading.Thread(target=lambda: done.append(
        follow_through.finish(ev, attempt_id, "done", "Went back a track")))
    worker.start()
    worker.join(timeout=1.0)
    assert worker.is_alive() and not done  # the phone's answer waits for the item...
    # ...without holding the attempt: the admitting side can still take it (it would deadlock otherwise).
    holder.execute(text("SET LOCAL lock_timeout = '500ms'"))
    holder.execute(select(FollowThroughAttempt).where(FollowThroughAttempt.id == attempt_id).with_for_update())
    holder.commit()
    holder.close()
    worker.join(timeout=10)
    assert done and "isn't the result" in done[0]
    [attempt] = attempts(env)
    assert attempt.status == "pending" and attempt.result == "Went back a track"


def test_overlapping_completion_and_admission_for_one_item_never_lose_the_answer(env, phone):
    from concurrent.futures import ThreadPoolExecutor

    entry = promise(env, "Keep Into You playing", "if Spotify moves on")
    report(env, media=NEXT_SONG)
    for n in range(5):
        ev = _follow_ev(env, entry, transition=f"t{n}")
        attempt_id = follow_through.begin(ev, "media_control", {"command": "previous"})
        assert isinstance(attempt_id, uuid.UUID)
        with ThreadPoolExecutor(max_workers=2) as pool:
            answered = pool.submit(follow_through.finish, ev, attempt_id, "done", "Went back a track")
            admitted = pool.submit(follow_through.begin, _follow_ev(env, entry, f"other{n}"), "media_control",
                                   {"command": "previous"})
            note, second = answered.result(timeout=20), admitted.result(timeout=20)
        assert "isn't the result" in note  # recorded, no deadlock
        assert isinstance(second, str)  # the first is pending: the second wake sends nothing
        with env.db() as db:  # settle it so the next round starts clean
            row = db.get(FollowThroughAttempt, attempt_id)
            row.status, row.settled_at = "achieved", datetime.now(timezone.utc)
            db.execute(update(CoordinatorWork).where(CoordinatorWork.user_id == env.user.id,
                                                     CoordinatorWork.status.in_(coordinator.ACTIVE))
                       .values(status="cancelled"))
            db.commit()


def test_an_unanswered_command_is_checked_not_assumed(env, phone):
    phone.answers.append("unconfirmed")
    _first_attempt(env, phone)
    [attempt] = attempts(env)
    assert attempt.status == "pending" and len(checks(env)) == 1


# --------------------------------------------------------------------------- #
# Duplicates, restarts, and a later distinct change
# --------------------------------------------------------------------------- #

def test_one_correction_in_flight_per_change_across_restarts_and_a_later_change_can_correct_again(env, phone):
    entry = _first_attempt(env, phone)
    # A worker restart: a new phone source and a fresh mind; the same change seen again wakes nobody.
    mind._minds.clear()
    source = mind_phone.PhoneSource()
    scope, moment = tick(env, source)
    think(scope, moment, source, Thinker("Switch back.", act="switch back to Into You", item=ref(entry)))
    assert len(follow_wakes(env)) == 1

    # Another change while the attempt is still pending (another reading changed): her wake can't send again.
    report(env, charging=True)
    scope, moment2 = tick(env, source, moment.data["snapshot"])
    assert moment2.data["transition"] != moment.data["transition"]
    think(scope, moment2, source, Thinker("Still the wrong song.", act="switch back", item=ref(entry)))
    assert len(follow_wakes(env)) == 2

    async def blocked(use, context):
        refused = await use("phone_action", action="media_control", command="previous")
        assert refused.startswith("Not done: your attempt") and "still waiting for its result" in refused
        return ""

    env.scripts.append(blocked)
    drain(env)
    assert run_job(env)
    assert len(phone.sent) == 1

    # The first attempt settles on a fresh report; later Spotify moves on again: a distinct change, corrected again.
    report(env, media=INTO_YOU)
    with env.db() as db:
        row = db.scalar(select(FollowThroughAttempt).where(FollowThroughAttempt.user_id == env.user.id))
        ev = SimpleNamespace(wake={"kind": "wake", "input": {"follow_item": str(entry.id), "follow_check": str(row.id),
                                                             "follow_transition": row.transition}},
                             user_id=env.user.id)
    assert follow_through.settle(ev, str(row.id)[:8], "achieved", "Into You again").startswith("Settled: achieved")
    scope, moment3 = tick(env, source, moment2.data["snapshot"])
    report(env, media=NEXT_SONG)
    scope, moment4 = tick(env, source, moment3.data["snapshot"])
    think(scope, moment4, source, Thinker("It moved on again.", act="switch back to Into You", item=ref(entry)))
    assert len(follow_wakes(env)) == 3

    async def again(use, context):
        result = await use("phone_action", action="media_control", command="previous")
        assert "isn't the result" in result
        return ""

    env.scripts.append(again)
    drain(env)
    assert run_job(env)
    assert len(phone.sent) == 2


# --------------------------------------------------------------------------- #
# Cancelled while verification is pending
# --------------------------------------------------------------------------- #

def test_cancelling_while_a_check_is_pending_stops_any_further_command(env, phone):
    entry = _first_attempt(env, phone)
    [check] = checks(env)
    message = write(env, "Never mind, you can stop switching it back.")
    with env.db() as db:
        memory_manager.resolve(db, env.user.id, entry.id, status="cancelled", reason="they said to stop",
                               source=memory_manager.Source("message", message))
        db.commit()
    assert checks(env) == []  # the check went with it
    [attempt] = attempts(env)
    assert attempt.status == "cancelled"
    # Even a check that was already on its way can't send anything.
    ev = SimpleNamespace(wake={"kind": "wake", "input": {"follow_item": str(entry.id),
                                                         "follow_check": str(attempt.id),
                                                         "follow_transition": attempt.transition}},
                         user_id=env.user.id, conversation_id=env.conversation.id, wake_work_id=str(check.id))
    refused = follow_through.begin(ev, "media_control", {"command": "previous"})
    assert isinstance(refused, str) and refused.startswith(f"Not done: [{ref(entry)}] is no longer open")
    assert len(phone.sent) == 1


def test_a_message_since_the_attempt_keeps_its_check_from_acting(env, phone):
    entry = _first_attempt(env, phone)
    report(env, media=OTHER_SONG)
    write(env, "Actually let it play whatever")

    async def look(use, context):
        [attempt] = attempts(env)
        await use("settle_attempt", attempt=str(attempt.id)[:8], outcome="not_achieved", evidence="other song")
        refused = await use("phone_action", action="media_control", command="previous")
        assert refused.startswith("Not done: they've written since your attempt")
        return ""

    env.scripts.append(look)
    due_now(env)
    drain(env)
    assert run_job(env)
    assert len(phone.sent) == 1


# --------------------------------------------------------------------------- #
# The same shared path for any condition; nothing for what isn't due
# --------------------------------------------------------------------------- #

def test_arriving_home_keeps_an_alarm_promise_through_the_same_path(env, phone):
    entry = promise(env, "Set an alarm for 07:00 when they get home", "when they get home")
    report(env, place="Office")
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)
    report(env, place="Home")
    scope, moment = tick(env, source, base.data["snapshot"])
    assert "Arrived at Home" in moment.fresh and f"[{ref(entry)}]" in moment.text
    think(scope, moment, source, Thinker("They're home: time for that alarm.", act="set the 07:00 alarm",
                                         item=ref(entry), say="Welcome home!"))
    [wake] = follow_wakes(env)
    with env.db() as db:  # its line rides on the follow-through: one wake, not two
        assert db.scalar(select(func.count()).select_from(CoordinatorWork).where(
            CoordinatorWork.user_id == env.user.id, CoordinatorWork.kind == "mind")) == 1

    async def alarm(use, context):
        assert "set the 07:00 alarm" in context.wake
        assert "A line it thought of saying (yours to use or not): Welcome home!" in context.wake
        result = await use("phone_action", action="set_alarm", time="07:00")
        assert "the device's answer confirms it" in result
        return "Alarm set for 7."

    env.scripts.append(alarm)
    drain(env)
    assert run_job(env)
    [attempt] = attempts(env)
    assert attempt.status == "achieved" and attempt.observe is None and checks(env) == []


def test_an_unrelated_observation_or_an_unknown_item_produces_no_action(env, phone):
    entry = promise(env, "Set an alarm for 07:00 when they get home", "when they get home")
    report(env, charging=False)
    source = mind_phone.PhoneSource()
    _, base = tick(env, source)
    report(env, charging=True)
    scope, moment = tick(env, source, base.data["snapshot"])
    think(scope, moment, source, Thinker("Charging up, good."))  # nothing due
    think(scope, moment, source, Thinker("Hm.", act="do something", item="deadbeef"))  # not one of hers
    assert follow_wakes(env) == [] and attempts(env) == [] and phone.sent == []
    with env.db() as db:
        memory_manager.resolve(db, env.user.id, entry.id, status="cancelled", reason="no longer needed",
                               source=memory_manager.Source("message", write(env, "forget the alarm")))
        db.commit()
    think(scope, moment, source, Thinker("Alarm?", act="set the alarm", item=ref(entry)))  # closed
    assert follow_wakes(env) == []


def test_someone_elses_words_never_lead_to_a_follow_through(env, phone):
    entry = promise(env, "Tell them when Rina writes", "when Rina writes")
    report(env, media=INTO_YOU)
    source = mind_phone.PhoneSource()
    scope, moment = tick(env, source)
    cause = {"third_party": True, "arrived": [], "reply_targets": [], "transition": "x"}
    mind._think_quick(source, scope, "light", moment.text, time.time(),
                      Thinker("Rina wrote.", act="tell them", item=ref(entry)), cause)
    assert follow_wakes(env) == []


def test_a_follow_through_wake_has_only_her_own_turns_quiet_actions(env, phone):
    entry, source, scope, moment = spotify_moves_on(env, phone)
    think(scope, moment, source, Thinker("Switch back.", act="put Into You back on", item=ref(entry)))

    async def tools(use, context):
        with pytest.raises(Exception):  # choosing a track by name needs them talking with her
            await use("phone_action", action="play_music", query="Into You Super Slowed")
        return "I can only skip back on my own; I can't pick Into You by name without you."

    env.scripts.append(tools)
    drain(env)
    assert run_job(env)
    assert phone.sent == [] and attempts(env) == []
