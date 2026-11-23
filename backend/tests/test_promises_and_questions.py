"""Promises kept on evidence, and open questions with a state (fix/commitments-and-open-questions).

End to end through the real chat worker, coordinator, memory tools and her mind's tools (the harness of
``tests/test_coordinator.py``): only the model's choices are scripted, and so are the two LLM checks (thought memory's
judgement, the questions consumer's reading), so what's tested is everything that carries a choice out. Real
PostgreSQL in a disposable schema (``TEST_DATABASE_URL``); never the live records the bugs were diagnosed from.

Regressions (from conversation 86193723, messages 240-245, and the account's 41 open questions):
- A timed promise had no wake (``note_commitment`` with a time scheduled nothing), and a clarification of how to
  deliver it ("just send me a msg") was recorded as the reminder being delivered, 25 minutes early.
- The same question about one Spotify attempt was added again by every learning review (the writer bypassed thought
  memory's matching), and nothing ever handed one to her mind while the user was quiet.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.db.models import (
    ChatJob, ChatMessage, CoordinatorWork, MemoryEvidence, MemoryEvent, ObservationProcessing, WorkingItem,
)
from app.services import (
    coordinator, learning_review, memory_manager, mind_conversation, observation, observation_adapters,
    open_questions, thought_memory,
)
from app.services.open_questions import Match, Reading
from app.services.thought_memory import Judgement
from test_coordinator import (  # noqa: F401
    _promise_tools, drain, env, evaluations, messages, run_job, said, says, wakes,
)
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _advance(env, seconds: float) -> None:
    """Time passes: every live wake and every promise's time come that much closer (the code reads the real clock)."""
    delta = timedelta(seconds=seconds)
    with env.db() as db:
        for row in db.scalars(select(CoordinatorWork).where(CoordinatorWork.kind == "wake")):
            row.next_attempt_at = row.next_attempt_at - delta
            if row.input.get("due_at"):
                due = coordinator._now().fromisoformat(row.input["due_at"]) - delta
                row.input = {**row.input, "due_at": due.isoformat()}
        for entry in db.scalars(select(WorkingItem).where(WorkingItem.due_at.is_not(None))):
            entry.due_at = entry.due_at - delta
        db.commit()


def _promises(env) -> list[WorkingItem]:
    with env.db() as db:
        return list(db.scalars(select(WorkingItem).where(WorkingItem.kind == "commitment")
                               .order_by(WorkingItem.created_at)))


def _live_wakes(env) -> list[CoordinatorWork]:
    return [w for w in wakes(env) if w.status in coordinator.ACTIVE]


def _ref(result: str) -> str:
    return result.split("[", 1)[1].split("]", 1)[0]


def _resolution(env, item_id) -> list[uuid.UUID]:
    with env.db() as db:
        return list(db.scalars(select(MemoryEvidence.message_id).where(
            MemoryEvidence.item_id == item_id, MemoryEvidence.role == "resolution")))


def _assistant_count(env) -> int:
    return sum(1 for role, _ in messages(env) if role == "assistant")


def _replay_wake(env, wake_id) -> None:
    """Every way a wake can be retried: its conclusion dispatched again, the stored chat job claimed again (a worker
    that died after the delivery commit, before it was acknowledged), and the outbox recovered."""
    coordinator._conclude(wake_id)
    with env.db() as db:
        db.execute(update(ChatJob).values(status="pending", claim_token=None, lease_expires_at=None))
        db.commit()
    while run_job(env):
        pass
    coordinator.recover_follow_up()
    drain(env)
    while run_job(env):
        pass


# --------------------------------------------------------------------------- #
# Promises: the cardio reminder
# --------------------------------------------------------------------------- #


def test_the_cardio_reminder_arrives_once_at_its_time_and_closes_only_when_delivered(env):
    said(env, "actually remind on 1 pm on my phone ,  I need to do cardio actually")

    async def agree(use, context):
        tools = _promise_tools(context)
        kept = await tools["note_commitment"].ainvoke({
            "what": "Remind them at 1 PM to do cardio", "in_seconds": 1500,
            "done_when": "the reminder reaches them at 1 PM"})
        assert kept.startswith("Kept: promise [") and "You'll be woken for it at" in kept
        # As she did for 3 PM: come_back_to as well. It keeps the same promise's time, never a second promise.
        again = await use("come_back_to", what="Remind them to do cardio", asked_by="user", in_seconds=1500)
        assert "It keeps the time of your promise" in again
        return "Got it—cardio at 1 PM. I'll remind you here then."

    env.scripts.append(agree)
    assert run_job(env)
    [promise] = _promises(env)
    [wake] = _live_wakes(env)
    assert wake.input["item"] == str(promise.id) and wake.input["asked_by"] == "user"
    assert abs((promise.due_at - wake.next_attempt_at).total_seconds()) < 1  # the promise and its wake agree
    ref = memory_manager.ref(promise)

    said(env, "no need just send me msg , I will know from notification")

    async def clarify(use, context):
        tools = _promise_tools(context)
        # What happened live: the clarification recorded as the reminder being delivered. Refused now.
        refused = await tools["update_item"].ainvoke(
            {"item": ref, "status": "fulfilled", "reason": "the in-chat reminder is enough; delivered now"})
        assert refused.startswith("Not done") and "hasn't come" in refused
        changed = await tools["change_promise"].ainvoke({
            "item": ref, "reason": "they just want a chat message, no phone alarm",
            "what": "Message them in chat at 1 PM to do cardio (no phone alarm)"})
        assert changed.startswith("Changed (what)") and "Still owed at" in changed
        return "Oh, okay—I'll just message you here at 1 PM."

    env.scripts.append(clarify)
    assert run_job(env)
    [after] = _promises(env)
    assert after.status == "open" and after.closed_at is None
    assert after.text == "Message them in chat at 1 PM to do cardio (no phone alarm)"
    assert after.due_at == promise.due_at and after.done_when == promise.done_when  # what they didn't change stays
    assert [w.id for w in _live_wakes(env)] == [wake.id]  # its wake untouched
    with env.db() as db:
        assert db.scalar(select(MemoryEvent).where(MemoryEvent.item_id == promise.id,
                                                   MemoryEvent.kind == "item_revised")) is not None

    said(env, "ok thanks")  # acknowledgment
    env.scripts.append(says("👍"))
    assert run_job(env)
    assert _promises(env)[0].status == "open"
    before = _assistant_count(env)

    # The clock reaches 1 PM with no message from them.
    drain(env)
    assert not run_job(env)  # not due yet: nothing runs
    _advance(env, 1500)
    drain(env)

    async def remind(use, context):
        assert "came due: a check-in they asked you for" in context.wake
        assert "Message them in chat at 1 PM to do cardio" in context.now
        return "It's 1 PM — time for your cardio!"

    env.scripts.append(remind)
    assert run_job(env)
    assert messages(env)[-1] == ("assistant", "It's 1 PM — time for your cardio!")
    assert _assistant_count(env) == before + 1
    last = evaluations(env)[-1]
    assert (last.reason, last.outcome, last.delivery) == ("due", "said", "delivered")
    [kept] = _promises(env)
    assert kept.status == "fulfilled"
    assert _resolution(env, kept.id) == [last.assistant_message_id]  # kept by the reminder itself

    _replay_wake(env, wake.id)
    assert _assistant_count(env) == before + 1  # no duplicate
    assert _promises(env)[0].status == "fulfilled" and _live_wakes(env) == []


def test_a_reschedule_moves_the_one_wake_and_a_cancellation_stops_it_firing(env):
    said(env, "remind me to go to the gym in an hour")

    async def agree(use, context):
        kept = await _promise_tools(context)["note_commitment"].ainvoke(
            {"what": "Remind them to go to the gym", "in_seconds": 3600})
        return "Sure." if kept.startswith("Kept") else kept

    env.scripts.append(agree)
    run_job(env)
    [promise] = _promises(env)
    [first] = _live_wakes(env)
    ref = memory_manager.ref(promise)

    said(env, "make it two hours, I need to eat first")

    async def move(use, context):
        changed = await _promise_tools(context)["change_promise"].ainvoke(
            {"item": ref, "reason": "they need to eat first", "in_seconds": 7200})
        assert changed.startswith("Changed (when)") and "its earlier time is replaced" in changed
        return "Two hours it is."

    env.scripts.append(move)
    run_job(env)
    [moved] = _promises(env)
    [second] = _live_wakes(env)
    assert second.id != first.id and second.input["chain"] == str(first.id)
    assert abs((moved.due_at - second.next_attempt_at).total_seconds()) < 1
    assert moved.text == promise.text and moved.due_at > promise.due_at + timedelta(minutes=50)

    said(env, "never mind, I'm not going today")

    async def cancel(use, context):
        done = await _promise_tools(context)["update_item"].ainvoke(
            {"item": ref, "status": "cancelled", "reason": "they're not going today"})
        assert "dropped" in done
        return "Okay, no reminder."

    env.scripts.append(cancel)
    run_job(env)
    before = _assistant_count(env)
    _advance(env, 7300)
    drain(env)
    assert not run_job(env)  # the old action never fires
    assert _assistant_count(env) == before and _promises(env)[0].status == "cancelled"


def test_a_failed_attempt_stays_visible_is_retried_and_delivers_once(env):
    said(env, "remind me to stretch in a minute")

    async def agree(use, context):
        await _promise_tools(context)["note_commitment"].ainvoke({"what": "Remind them to stretch", "in_seconds": 60})
        return "Will do."

    env.scripts.append(agree)
    run_job(env)
    [promise] = _promises(env)
    before = _assistant_count(env)
    _advance(env, 60)
    drain(env)

    async def broken(use, context):
        raise RuntimeError("model down")

    env.scripts.append(broken)
    run_job(env)
    assert evaluations(env)[-1].delivery == "failed"
    [failed] = _promises(env)
    assert failed.status == "open" and "wasn't delivered (failed)" in failed.status_reason  # visible, not kept
    [retry] = _live_wakes(env)
    assert retry.input["chain"] and retry.input["revisions"][-1]["retry"] is True
    assert _assistant_count(env) == before

    _advance(env, 600)
    drain(env)
    env.scripts.append(says("Time to stretch!"))
    run_job(env)
    assert _assistant_count(env) == before + 1 and messages(env)[-1] == ("assistant", "Time to stretch!")
    assert _promises(env)[0].status == "fulfilled" and _live_wakes(env) == []
    _replay_wake(env, retry.id)
    assert _assistant_count(env) == before + 1


def test_when_the_report_finishes_the_summary_is_owed_and_kept_by_its_delivery(env):
    said(env, "Write up the report on the Lisbon trip. When the report finishes, send me the summary.")
    refs = {}

    async def agree(use, context):
        started = await use("think_it_over", about="write the report on the Lisbon trip")
        work = started.split("[work:", 1)[1].split("]", 1)[0]
        tools = _promise_tools(context)
        kept = await tools["note_commitment"].ainvoke({
            "what": "Send them the summary of the Lisbon report", "when": "when the report finishes",
            "awaits": f"work:{work}", "done_when": "the summary reaches them in chat"})
        refs["promise"] = _ref(kept)
        early = await tools["update_item"].ainvoke(
            {"item": refs["promise"], "status": "fulfilled", "reason": "will send"})
        assert early.startswith("Not done") and "hasn't happened yet" in early
        return "On it: I'll send you the summary when the report is done."

    env.scripts.append(agree)
    run_job(env)
    said(env, "thanks!")  # a newer message: the report a promise waits for isn't made moot by it
    env.scripts.append(says("Anytime."))
    run_job(env)
    [promise] = _promises(env)
    assert promise.trigger["kind"] == "work" and promise.trigger_met_at is None
    drain(env)  # the report finishes

    async def deliver(use, context):
        assert "was waiting for this (when the report finishes)" in context.wake
        noted = await _promise_tools(context)["update_item"].ainvoke(
            {"item": refs["promise"], "status": "fulfilled", "reason": "sent the summary"})
        assert noted.startswith("Noted:")
        return "The report's done. Summary: stay in Berlin for a year, then decide."

    env.scripts.append(deliver)
    assert run_job(env)
    [kept] = _promises(env)
    assert kept.trigger_met_at is not None and kept.status == "fulfilled"
    assert _resolution(env, kept.id) == [evaluations(env)[-1].assistant_message_id]


def test_a_promise_marked_kept_in_words_that_are_cut_off_stays_open(env):
    said(env, "tell me the recipe later")

    async def agree(use, context):
        await _promise_tools(context)["note_commitment"].ainvoke({"what": "Send them the recipe"})
        return "Sure."

    env.scripts.append(agree)
    run_job(env)
    ref = memory_manager.ref(_promises(env)[0])
    said(env, "ok send it now")

    async def keep(use, context):
        noted = await _promise_tools(context)["update_item"].ainvoke(
            {"item": ref, "status": "fulfilled", "reason": "sent it"})
        assert noted.startswith("Noted:")
        with env.db() as db:  # it never closes before delivery
            assert db.get(WorkingItem, _promises(env)[0].id).closed_at is None
        raise RuntimeError("the reply failed")

    env.scripts.append(keep)
    run_job(env)
    [promise] = _promises(env)
    assert promise.status == "open" and "wasn't delivered (failed)" in promise.status_reason


# --------------------------------------------------------------------------- #
# Open questions: Spotify
# --------------------------------------------------------------------------- #


@pytest.fixture
def qenv(env):
    with env.db() as db:
        memory_manager.ensure_general(db, env.user.id)
        db.commit()
    return env


def _record_message(env, message_id) -> None:
    """The conversation's observation of a stored message (what the observation pull records)."""
    with env.db() as db:
        message = db.get(ChatMessage, message_id)
        observation.record(db, observation_adapters._message_report(message, env.user.id))
        db.commit()


def _last_assistant(env) -> uuid.UUID:
    with env.db() as db:
        return db.scalar(select(ChatMessage.id).where(ChatMessage.conversation_id == env.conversation.id,
                                                      ChatMessage.role == "assistant")
                         .order_by(ChatMessage.conversation_sequence.desc()).limit(1))


def _add(env, role, content) -> uuid.UUID:
    from app.services import chat as chat_service
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        message = chat_service.add_message(db, conversation=conversation, role=role, content=content,
                                           turn_id=uuid.uuid4())
        return message.id


def _propose(env, *, revision: int, assessment: uuid.UUID, text: str, up_to_seq: int) -> None:
    """A learning review proposing a follow-up, through the real writer (services/learning_review.propose)."""
    row = SimpleNamespace(id=assessment, user_id=env.user.id, conversation_id=env.conversation.id,
                          trigger={"up_to_seq": up_to_seq}, game_session_id=None)
    with env.db() as db:
        learning_review.propose(db, row, revision, {"propose": True, "kind": "question", "text": text,
                                                    "resolves": "whether the Play attempt resumed playback"},
                                {"alternatives": ["it resumed", "it didn't"]})
        db.commit()


def _occurrence_judge(calls):
    """Stands in for thought memory's model: the same occurrence (the latest message of what it is about) is the
    same question, whether it is open or closed; another occurrence is new."""
    def judge(view, candidates):
        calls.append((view, candidates))
        for c in candidates:
            if (view.get("about") or [None])[-1] in c.about:
                return Judgement(keep=True, relation="same", item=c.ref)
        return Judgement(keep=True, relation="new")
    return judge


def _questions(env) -> list[WorkingItem]:
    with env.db() as db:
        return list(db.scalars(select(WorkingItem).where(WorkingItem.kind == "question")
                               .order_by(WorkingItem.created_at)))


def _reader(answer: dict[str, str] | None = None):
    """Stands in for the questions consumer's model: which shown questions the message asks or answers."""
    calls = []

    def judge(task):
        calls.append(task)
        hits = [q["id"] for q in task["open_questions"] if (answer or {}).get(task["task"]) in q["question"]]
        return Reading(matches=[Match(item=h, why=f"{task['task']} it") for h in hits])
    judge.calls = calls
    return judge


def _consume(judge) -> None:
    while open_questions.run_once(judge=judge):
        pass


def _seq(env, message_id) -> int:
    with env.db() as db:
        return db.get(ChatMessage, message_id).conversation_sequence


def test_repeated_reviews_of_one_spotify_attempt_make_one_question_asked_once_and_closed_by_the_answer(qenv):
    env = qenv
    _add(env, "user", "Can you play spotify my calm song album")
    play = _add(env, "assistant", "I asked Spotify to play “My Calm Song”. I can’t confirm what started yet.")
    seq = _seq(env, play)
    calls = []
    judge = _occurrence_judge(calls)
    # Three reviews of the same stretch, each proposing the question again in other words.
    for n, text in enumerate(["Ask whether the Play attempt at m2 resumed playback for him.",
                              "At a natural opportunity, ask whether that Spotify Play attempt resumed playback.",
                              "Ask what he was checking and whether the earlier Play attempt resumed playback."], 1):
        _propose(env, revision=n, assessment=uuid.uuid4(), text=text, up_to_seq=seq)
        while thought_memory.run_once(judge=judge):
            pass
    [question] = _questions(env)
    assert question.status == "open" and question.owner == "columbina"
    assert [c.ref for c in calls[1][1]] == [memory_manager.ref(question)]  # the writer saw the prior entry

    # She asks it during a normal reply: it counts as asked, by that message.
    _add(env, "user", "hello")
    asked = _add(env, "assistant", "Hi! Did Spotify resume on your end?")
    _record_message(env, asked)
    reader = _reader({"asks": "Play attempt", "answers": "Play attempt"})
    _consume(reader)
    [question] = _questions(env)
    assert question.status == "asked"
    with env.db() as db:
        assert db.scalar(select(MemoryEvidence.message_id).where(
            MemoryEvidence.item_id == question.id, MemoryEvidence.role == "asked")) == asked

    # A retry of that reading changes nothing.
    with env.db() as db:
        db.execute(update(ObservationProcessing).where(ObservationProcessing.consumer == "questions")
                   .values(status="pending", processed_at=None, next_attempt_at=coordinator._now()))
        db.commit()
    _consume(reader)
    assert _questions(env)[0].status == "asked"

    # Their answer closes it, with their message as the evidence.
    answer = _add(env, "user", "yeah, it started")
    _record_message(env, answer)
    _consume(reader)
    [question] = _questions(env)
    assert question.status == "answered" and _resolution(env, question.id) == [answer]
    assert any(t["task"] == "answers" and t.get("her_message_before") == "Hi! Did Spotify resume on your end?"
               for t in reader.calls)

    # The same attempt reviewed again: recognised as already answered, never reopened or added again.
    _propose(env, revision=4, assessment=uuid.uuid4(), text="Ask whether that Play attempt resumed playback.",
             up_to_seq=seq)
    while thought_memory.run_once(judge=judge):
        pass
    assert [(q.id, q.status) for q in _questions(env)] == [(question.id, "answered")]
    assert any(c.status == "answered" for c in calls[-1][1])  # it saw the closed one

    # A genuinely separate attempt later is its own question.
    _add(env, "user", "play it again")
    later = _add(env, "assistant", "I pressed Play again.")
    _propose(env, revision=1, assessment=uuid.uuid4(), text="Ask whether the second Play attempt resumed playback.",
             up_to_seq=_seq(env, later))
    while thought_memory.run_once(judge=judge):
        pass
    assert [q.status for q in _questions(env)] == ["answered", "open"]


def test_while_they_are_quiet_she_is_handed_one_question_once_and_may_ask_it(qenv):
    env = qenv
    _add(env, "user", "play my calm song album")
    play = _add(env, "assistant", "I asked Spotify to play it.")
    for n, text in enumerate(["Ask whether the Play attempt resumed playback.",
                              "Ask whether the voice interruptions were deliberate."], 1):
        _propose(env, revision=n, assessment=uuid.uuid4(), text=text, up_to_seq=_seq(env, play))
        while thought_memory.run_once(judge=lambda view, candidates: Judgement(keep=True)):
            pass
    first, second = _questions(env)
    with env.db() as db:  # the backlog: handed over by the migration, never woken for
        backlog = memory_manager.add_item(db, env.user.id, kind="question", text_="An old question",
                                          owner="columbina", epistemic="inferred")
        backlog.offered_at = backlog.created_at
        db.commit()
    before = _assistant_count(env)

    with env.db() as db:
        handed = open_questions.hand_over(db)
        again = open_questions.hand_over(db)
        swept = mind_conversation.SOURCE.sweep(db, coordinator._now().timestamp() + 60)
    assert len(handed) == 1 and again == [] and swept == []  # one per quiet stretch, never a burst
    [offered] = [q for q in _questions(env) if q.offered_at is not None and q.id != backlog.id]
    assert offered.id == second.id  # the newest
    drain(env)

    async def ask(use, context):
        assert "An open question of yours" in context.wake and offered.text in context.wake
        return "Quick one: were those voice interruptions on purpose?"

    env.scripts.append(ask)
    assert run_job(env)
    assert _assistant_count(env) == before + 1
    [meant] = [q for q in _questions(env) if q.id == offered.id]
    assert meant.status == "open" and meant.keeping is not None  # meant; the reader decides it was asked
    _record_message(env, _last_assistant(env))
    _consume(_reader({"asks": "voice interruptions"}))
    [asked] = [q for q in _questions(env) if q.id == offered.id]
    assert asked.status == "asked" and asked.keeping is None
    with env.db() as db:
        work = db.scalar(select(CoordinatorWork).where(CoordinatorWork.input["wake"].astext == "question"))
    _replay_wake(env, work.id)
    with env.db() as db:
        assert open_questions.hand_over(db) == []  # same quiet stretch: nothing more
    assert _assistant_count(env) == before + 1

    # Their answer closes it; a new message starts a new stretch, and the other new question may be handed over.
    answer = _add(env, "user", "yeah, I was testing barge-in")
    _record_message(env, answer)
    _consume(_reader({"answers": "voice interruptions"}))
    assert [q.status for q in _questions(env) if q.id == offered.id] == ["answered"]
    _add(env, "assistant", "Makes sense.")
    with env.db() as db:
        handed = open_questions.hand_over(db)
    assert len(handed) == 1
    assert [q.id for q in _questions(env) if q.offered_at is not None and q.id not in (backlog.id, offered.id)] \
        == [first.id]


# --------------------------------------------------------------------------- #
# Review of PR #53
# --------------------------------------------------------------------------- #


def _strict_check(monkeypatch, word: str):
    """The promise check's model, scripted: a reply carries out a promise only when it contains ``word``."""
    from app.services import promise_check
    calls = []

    def judge(task):
        calls.append(task)
        return promise_check.Reading(promises=[promise_check.Verdict(item=p["id"], kept=word in task["her_message"])
                                               for p in task["promises"]])
    monkeypatch.setattr(promise_check, "_judge", judge)
    return calls


def _timed_promise(env, what="Remind them to do cardio", seconds=60):
    said(env, f"{what.replace('Remind them', 'remind me')} in a minute")

    async def agree(use, context):
        await _promise_tools(context)["note_commitment"].ainvoke({"what": what, "in_seconds": seconds,
                                                                  "done_when": "the reminder reaches them"})
        return "Will do."

    env.scripts.append(agree)
    run_job(env)
    return _promises(env)[-1]


def test_a_reply_that_does_something_else_never_fulfills_the_promise(env, monkeypatch):
    calls = _strict_check(monkeypatch, "cardio")
    promise = _timed_promise(env)
    _advance(env, 60)
    drain(env)

    async def other(use, context):  # woken for the reminder, she says something else and claims it kept
        await _promise_tools(context)["update_item"].ainvoke(
            {"item": memory_manager.ref(promise), "status": "fulfilled", "reason": "reminded"})
        return "By the way, the weather looks great today."

    env.scripts.append(other)
    run_job(env)
    assert calls and calls[-1]["promises"][0]["owed"] == "Remind them to do cardio"
    [after] = _promises(env)
    assert after.status == "open" and "isn't kept" in after.status_reason and _resolution(env, after.id) == []

    # The check failing keeps nothing (fails closed).
    from app.services import promise_check
    monkeypatch.setattr(promise_check, "_judge", lambda task: (_ for _ in ()).throw(RuntimeError("down")))
    said(env, "so?")

    async def reminder(use, context):
        await _promise_tools(context)["update_item"].ainvoke(
            {"item": memory_manager.ref(promise), "status": "fulfilled", "reason": "reminded"})
        return "Time for cardio!"

    env.scripts.append(reminder)
    run_job(env)
    assert _promises(env)[0].status == "open" and "couldn't be checked" in _promises(env)[0].status_reason


def test_a_message_arriving_while_she_words_the_reminder_doesnt_use_up_its_wake(env):
    _timed_promise(env)
    _advance(env, 60)
    drain(env)

    async def interrupted(use, context):
        from app.services import chat as chat_service
        with env.db() as db:  # they write while she words it: her reminder is fenced off
            conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
            chat_service.add_message(db, conversation=conversation, role="user", content="what's for lunch?",
                                     turn_id=uuid.uuid4())
        return "Time for cardio!"

    env.scripts.append(interrupted)
    run_job(env)
    assert evaluations(env)[-1].delivery == "cancelled"
    [rearmed] = _live_wakes(env)
    assert rearmed.input["revisions"][-1]["retry"] is True and _promises(env)[0].status == "open"
    _advance(env, 60)
    drain(env)
    env.scripts.append(says("Time for cardio!"))
    run_job(env)
    assert _promises(env)[0].status == "fulfilled" and _live_wakes(env) == []


@pytest.mark.parametrize("outcome", ["delivered", "failed"])
def test_a_whatsapp_reminder_counts_kept_only_when_whatsapp_confirms_it_arrived(env, monkeypatch, outcome):
    from app.db.models import ChannelIdentity, MessageDelivery
    from app.services import whatsapp
    from app.workers import chat as chat_worker

    with env.db() as db:
        identity = ChannelIdentity(provider="whatsapp", provider_account_id="p", external_subject="s",
                                   user_id=env.user.id)
        db.add(identity)
        db.commit()
        identity_id = identity.id

    def schedule(db, *, assistant_message_id, channel_identity_id, content):
        db.add(MessageDelivery(assistant_message_id=assistant_message_id, channel="whatsapp",
                               channel_identity_id=channel_identity_id, content=content, status="accepted",
                               provider_message_id=f"wamid.{assistant_message_id}"))

    monkeypatch.setattr(chat_worker.whatsapp_service, "schedule_delivery", schedule)
    monkeypatch.setattr(chat_worker.whatsapp_service, "wake_worker", lambda: None)
    said(env, "remind me to do cardio in a minute", channel="whatsapp", channel_identity_id=str(identity_id))

    async def agree(use, context):
        await _promise_tools(context)["note_commitment"].ainvoke({"what": "Remind them to do cardio", "in_seconds": 60})
        return "Will do."

    env.scripts.append(agree)
    run_job(env)
    _advance(env, 60)
    drain(env)
    env.scripts.append(says("Time for cardio!"))
    run_job(env)
    [waiting] = _promises(env)
    assert waiting.status == "open" and waiting.keeping is not None  # sent, not yet confirmed
    with env.db() as db:
        whatsapp.apply_delivery_status(db, provider_message_id=f"wamid.{waiting.keeping['message_id']}",
                                       status=outcome, error="undeliverable" if outcome == "failed" else None)
        db.commit()
    [settled] = _promises(env)
    if outcome == "delivered":
        assert settled.status == "fulfilled" and _resolution(env, settled.id) == [uuid.UUID(waiting.keeping["message_id"])]
    else:
        assert settled.status == "open" and "couldn't deliver" in settled.status_reason and settled.keeping is None
        assert len(_live_wakes(env)) == 1  # its wake comes back


def test_the_question_reader_never_reads_forgotten_or_unheard_words(qenv):
    from app.db.models import MemoryForgottenSource
    env = qenv
    with env.db() as db:
        q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the Play attempt resume playback?",
                                    owner="columbina", epistemic="inferred")
        db.commit()
    spoken = _add(env, "assistant", "Here's your song. Did the Play attempt resume playback for you?")
    with env.db() as db:  # the voice reply was cut off before the question
        m = db.get(ChatMessage, spoken)
        m.heard_chars = len("Here's your song.")
        m.spoken_at = coordinator._now() - timedelta(minutes=5)
        db.commit()
    _record_message(env, spoken)
    reader = _reader({"asks": "Play attempt", "answers": "Play attempt"})

    def reading(task):
        assert "Did the Play attempt" not in task["message"]  # only what was heard
        assert "yeah the Play attempt" not in task["message"]  # nothing forgotten
        if "Play attempt" not in task["message"]:
            return Reading(matches=[])
        return reader(task)

    _consume(reading)
    assert _questions(env)[0].status == "open"
    answer = _add(env, "user", "yeah the Play attempt resumed")
    with env.db() as db:  # the user had it forgotten
        from app.db.models import MemoryForget
        forget = MemoryForget(id=uuid.uuid4(), user_id=env.user.id, source_message_id=answer,
                              conversation_id=env.conversation.id, status="done", request=None,
                              finished_at=coordinator._now())
        db.add(forget)
        db.flush()
        db.add(MemoryForgottenSource(user_id=env.user.id, forget_id=forget.id, message_id=answer, spans=None))
        db.commit()
    _record_message(env, answer)
    _consume(reading)
    assert _questions(env)[0].status == "open" and q.id == _questions(env)[0].id


def test_a_question_saved_for_later_can_still_be_followed_up(env):
    said(env, "I'll tell you later how the audition went")

    async def save(use, context):
        await _promise_tools(context)["note_question"].ainvoke(
            {"question": "How did the audition go?", "asked_by": "columbina"})
        return "Good luck!"

    env.scripts.append(save)
    run_job(env)
    [q] = _questions(env)
    assert q.offered_at is None
    with env.db() as db:
        assert len(open_questions.hand_over(db)) == 1


def test_the_question_they_answer_is_shown_even_behind_newer_ones(qenv):
    env = qenv
    with env.db() as db:
        target = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the Play attempt resume?",
                                         owner="columbina", epistemic="inferred")
        db.commit()
    asked = _add(env, "assistant", "Did the Play attempt resume?")
    with env.db() as db:
        memory_manager.question_asked(db, env.user.id, target.id, message_id=asked)
        for n in range(open_questions.SHOWN + 3):  # newer questions, updated after it
            memory_manager.add_item(db, env.user.id, kind="question", text_=f"Newer question {n}?",
                                    owner="columbina", epistemic="inferred")
        db.commit()
    answer = _add(env, "user", "yes it did")
    _record_message(env, answer)
    seen = []

    def judge(task):
        seen.append([q["question"] for q in task["open_questions"]])
        return Reading(matches=[Match(item=q["id"]) for q in task["open_questions"] if "Play" in q["question"]])

    _consume(judge)
    assert seen and seen[0][0] == "Did the Play attempt resume?"
    assert [q.status for q in _questions(env) if q.id == target.id] == ["answered"]


# --------------------------------------------------------------------------- #
# Second review of PR #53
# --------------------------------------------------------------------------- #


def _whatsapp_promise(env, parts: int = 1):
    """A promise of hers whose reminder went out on WhatsApp in ``parts`` parts, accepted, waiting on delivery."""
    from app.db.models import ChannelIdentity, MessageDelivery
    with env.db() as db:
        identity = ChannelIdentity(provider="whatsapp", provider_account_id="p", external_subject=str(uuid.uuid4()),
                                   user_id=env.user.id)
        db.add(identity)
        entry = memory_manager.add_item(db, env.user.id, kind="commitment", text_="Remind them to do cardio",
                                        owner="columbina", owed_to="user", epistemic="reported",
                                        due_at=coordinator._now() - timedelta(minutes=1))
        db.commit()
        sent = _add(env, "assistant", "Time for cardio!")
        for part in range(parts):
            db.add(MessageDelivery(assistant_message_id=sent, channel="whatsapp", channel_identity_id=identity.id,
                                   content="Time for cardio!", part_index=part, status="accepted",
                                   provider_message_id=f"wamid.{sent}.{part}"))
        assert memory_manager.await_delivery(db, env.user.id, entry.id, message_id=sent, reason="reminded")
        db.commit()
        return entry.id, sent


def test_parts_delivered_at_the_same_moment_still_keep_the_promise(env):
    import threading
    from app.services import whatsapp
    item_id, sent = _whatsapp_promise(env, parts=2)
    done = threading.Event()

    def second():
        with env.db() as db2:
            whatsapp.apply_delivery_status(db2, provider_message_id=f"wamid.{sent}.1", status="delivered")
            db2.commit()
        done.set()

    with env.db() as db1:
        whatsapp.apply_delivery_status(db1, provider_message_id=f"wamid.{sent}.0", status="delivered")
        racer = threading.Thread(target=second)
        racer.start()
        assert not done.wait(1.0)  # the second receipt waits for the first to settle
        db1.commit()
    racer.join(10)
    [kept] = [p for p in _promises(env) if p.id == item_id]
    assert kept.status == "fulfilled" and kept.keeping is None


def test_a_receipt_for_words_sent_before_the_promise_changed_doesnt_keep_it(env):
    from app.services import whatsapp
    item_id, sent = _whatsapp_promise(env)
    with env.db() as db:
        memory_manager.revise_commitment(db, env.user.id, item_id, reason="they want it again later",
                                         text_="Remind them to do cardio again tonight")
        db.commit()
    with env.db() as db:
        whatsapp.apply_delivery_status(db, provider_message_id=f"wamid.{sent}.0", status="delivered")
        db.commit()
    [still] = [p for p in _promises(env) if p.id == item_id]
    assert still.status == "open" and still.keeping is None and _resolution(env, item_id) == []


def test_a_promise_awaiting_a_report_that_already_finished_can_be_kept(env):
    said(env, "Write up the report on the Lisbon trip.")
    refs = {}

    async def start(use, context):
        started = await use("think_it_over", about="write the report on the Lisbon trip")
        refs["work"] = started.split("[work:", 1)[1].split("]", 1)[0]
        return "On it."

    env.scripts.append(start)
    run_job(env)
    drain(env)  # the report finishes before any promise waits for it
    env.scripts.append(says("The report's written."))
    run_job(env)
    said(env, "When the report finishes, send me the summary.")

    async def promise(use, context):
        tools = _promise_tools(context)
        kept = await tools["note_commitment"].ainvoke({
            "what": "Send them the summary of the Lisbon report", "when": "when the report finishes",
            "awaits": f"work:{refs['work']}"})
        noted = await tools["update_item"].ainvoke({"item": _ref(kept), "status": "fulfilled", "reason": "sent it"})
        assert noted.startswith("Noted:")
        return "It already finished. Summary: stay in Berlin for a year."

    env.scripts.append(promise)
    run_job(env)
    [kept] = _promises(env)
    assert kept.trigger_met_at is not None and kept.status == "fulfilled"


@pytest.mark.parametrize("how", ["left_out", "never_delivered"])
def test_a_question_she_meant_to_ask_but_didnt_get_across_stays_unasked(qenv, how):
    env = qenv
    said(env, "I start the new job on Monday")

    async def mean(use, context):
        await _promise_tools(context)["note_question"].ainvoke(
            {"question": "How did the first day at the new job go?", "asked_by": "columbina", "asking_now": True})
        return "Congrats on the new job!"  # the question never made it into her words

    env.scripts.append(mean)
    run_job(env)
    [q] = _questions(env)
    assert q.status == "open" and q.keeping is not None
    reply = _last_assistant(env)
    if how == "never_delivered":
        from app.db.models import ChannelIdentity, MessageDelivery
        with env.db() as db:
            identity = ChannelIdentity(provider="whatsapp", provider_account_id="p", external_subject="s2",
                                       user_id=env.user.id)
            db.add(identity)
            db.flush()
            db.add(MessageDelivery(assistant_message_id=reply, channel="whatsapp", channel_identity_id=identity.id,
                                   content="x", status="failed"))
            db.commit()
    _record_message(env, reply)
    reader = _reader({"asks": "first day"})
    _consume(lambda task: reader(task) if "first day" in task["message"] else Reading(matches=[]))
    [after] = _questions(env)
    assert after.status == "open" and after.keeping is None and after.offered_at is None  # can be followed up
    with env.db() as db:
        assert len(open_questions.hand_over(db)) == 1


def test_a_busy_conversation_doesnt_hold_back_a_quiet_ones_question(qenv, monkeypatch):
    from app.services import chat as chat_service
    env = qenv
    _add(env, "user", "hi")
    _add(env, "assistant", "hey")
    with env.db() as db:
        quiet_q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the plant survive?",
                                          owner="columbina", epistemic="inferred")
        db.commit()
        quiet_id = quiet_q.id
    with env.db() as db:  # newer, about a conversation they're writing in right now (their message unanswered)
        busy = chat_service.create_conversation(db, user_id=env.user.id)
        chat_service.add_message(db, conversation=busy, role="user", content="wait one sec", turn_id=uuid.uuid4())
        busy_q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the train arrive?",
                                         owner="columbina", epistemic="inferred")
        db.commit()
        where = {busy_q.id: busy.id, quiet_id: env.conversation.id}
    monkeypatch.setattr(open_questions, "conversation_of", lambda db, entry: where[entry.id])
    with env.db() as db:
        handed = open_questions.hand_over(db)
    assert len(handed) == 1 and quiet_id.hex[:6] in handed[0]


def test_a_full_queue_leaves_the_question_for_a_later_sweep(qenv, monkeypatch):
    env = qenv
    _add(env, "user", "hi")
    _add(env, "assistant", "hey")
    with env.db() as db:
        memory_manager.add_item(db, env.user.id, kind="question", text_="Did the plant survive?",
                                owner="columbina", epistemic="inferred")
        db.commit()
    real = coordinator.get_settings()  # the real admission path, with its own commit on refusal
    full = SimpleNamespace(**{**real.model_dump(), "coordinator_max_pending": 0})
    monkeypatch.setattr(coordinator, "get_settings", lambda: full)
    from app.services import autonomous_speech
    submit, refusals = autonomous_speech.submit, []

    def refused_then_stopped(db, **kw):  # the worker stops right after the refusal commits
        refusals.append(submit(db, **kw))
        raise SystemExit("worker stopped")

    monkeypatch.setattr(autonomous_speech, "submit", refused_then_stopped)
    with env.db() as db, pytest.raises(SystemExit):
        open_questions.hand_over(db)
    assert "queue capacity" in refusals[0]
    [q] = _questions(env)
    assert q.offered_at is None  # nothing committed it offered without a wake
    monkeypatch.setattr(autonomous_speech, "submit", submit)
    monkeypatch.setattr(coordinator, "get_settings", lambda: real)
    with env.db() as db:
        assert len(open_questions.hand_over(db)) == 1
    assert _questions(env)[0].offered_at is not None


# --------------------------------------------------------------------------- #
# Third review of PR #53
# --------------------------------------------------------------------------- #


def test_a_second_proactive_attempt_after_one_that_left_the_question_out_runs(qenv):
    env = qenv
    _add(env, "user", "hi")
    _add(env, "assistant", "hey")
    with env.db() as db:
        q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the plant survive the move?",
                                    owner="columbina", epistemic="inferred")
        db.commit()
    with env.db() as db:
        assert len(open_questions.hand_over(db)) == 1
    drain(env)
    env.scripts.append(says("Hope your evening's going well."))  # woken for it, she leaves it out
    assert run_job(env)
    _record_message(env, _last_assistant(env))
    reader = _reader({"asks": "plant"})
    judge = lambda task: reader(task) if "plant" in task["message"] else Reading(matches=[])  # noqa: E731
    _consume(judge)
    assert _questions(env)[0].offered_at is None  # back to eligible

    _add(env, "user", "it's going ok")  # a new quiet stretch
    _add(env, "assistant", "Good to hear.")
    with env.db() as db:
        assert len(open_questions.hand_over(db)) == 1
    drain(env)

    async def ask(use, context):
        assert "Did the plant survive" in context.wake
        return "Did the plant survive the move?"

    env.scripts.append(ask)
    assert run_job(env)  # the second attempt is its own wake, and it runs
    _record_message(env, _last_assistant(env))
    _consume(judge)
    assert _questions(env)[0].status == "asked" and q.id == _questions(env)[0].id


def test_a_verdict_on_an_earlier_version_of_a_promise_doesnt_keep_the_new_one(env):
    with env.db() as db:
        entry = memory_manager.add_item(db, env.user.id, kind="commitment", text_="Remind them to do cardio",
                                        owner="columbina", owed_to="user", epistemic="reported",
                                        due_at=coordinator._now() - timedelta(minutes=1))
        db.commit()
        checked = entry.revision
        memory_manager.revise_commitment(db, env.user.id, entry.id, reason="changed in another chat",
                                         text_="Remind them to stretch instead")
        db.commit()
        sent = _add(env, "assistant", "Time for cardio!")
        assert memory_manager.keep_promise(db, env.user.id, entry.id, message_id=sent, reason="reminded",
                                           revision=checked) is None
        assert memory_manager.await_delivery(db, env.user.id, entry.id, message_id=sent, reason="reminded",
                                             revision=checked) is None
        db.commit()
    [still] = _promises(env)
    assert still.status == "open" and still.keeping is None and "earlier version" in still.status_reason


def test_a_whatsapp_question_delivered_after_the_reader_gave_up_still_counts_as_asked(qenv, monkeypatch):
    from app.db.models import ChannelIdentity, MessageDelivery
    from app.services import whatsapp
    env = qenv
    with env.db() as db:
        q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the plant survive the move?",
                                    owner="columbina", epistemic="inferred")
        identity = ChannelIdentity(provider="whatsapp", provider_account_id="p", external_subject="late",
                                   user_id=env.user.id)
        db.add(identity)
        db.commit()
    sent = _add(env, "assistant", "Did the plant survive the move?")
    with env.db() as db:
        db.add(MessageDelivery(assistant_message_id=sent, channel="whatsapp", channel_identity_id=identity.id,
                               content="x", status="accepted", provider_message_id=f"wamid.{sent}"))
        memory_manager.meant_to_ask(db, env.user.id, q.id, message_id=sent)
        db.commit()
    monkeypatch.setattr(open_questions, "DELIVERY_GIVE_UP", timedelta(0))  # their phone is off overnight
    _record_message(env, sent)
    judge = _reader({"asks": "plant"})
    _consume(judge)
    assert _questions(env)[0].status == "open"
    with env.db() as db:  # it reaches them in the morning
        whatsapp.apply_delivery_status(db, provider_message_id=f"wamid.{sent}", status="delivered")
        db.commit()
    _consume(judge)
    assert _questions(env)[0].status == "asked"


# --------------------------------------------------------------------------- #
# Fourth review of PR #53
# --------------------------------------------------------------------------- #


def test_a_receipt_landing_while_the_reader_gives_up_is_never_lost(qenv, monkeypatch):
    from app.db.models import ChannelIdentity, MessageDelivery
    from app.services import whatsapp
    env = qenv
    with env.db() as db:
        q = memory_manager.add_item(db, env.user.id, kind="question", text_="Did the plant survive the move?",
                                    owner="columbina", epistemic="inferred")
        identity = ChannelIdentity(provider="whatsapp", provider_account_id="p", external_subject="race",
                                   user_id=env.user.id)
        db.add(identity)
        db.commit()
    sent = _add(env, "assistant", "Did the plant survive the move?")
    with env.db() as db:
        db.add(MessageDelivery(assistant_message_id=sent, channel="whatsapp", channel_identity_id=identity.id,
                               content="x", status="accepted", provider_message_id=f"wamid.{sent}"))
        memory_manager.meant_to_ask(db, env.user.id, q.id, message_id=sent)
        db.commit()
    real = open_questions._arrived
    seen = []

    def arrived(db, message):
        seen.append(1)
        if len(seen) == 1:  # the reader decides it's late; the receipt commits before the reader does
            with env.db() as other:
                whatsapp.apply_delivery_status(other, provider_message_id=f"wamid.{sent}", status="delivered")
                other.commit()
            return "late"
        return real(db, message)

    monkeypatch.setattr(open_questions, "_arrived", arrived)
    _record_message(env, sent)
    _consume(_reader({"asks": "plant"}))
    assert _questions(env)[0].status == "asked"


def test_a_reply_whose_promise_closed_before_the_check_still_records_its_question(env):
    _timed_promise(env)
    _advance(env, 60)
    drain(env)

    async def cancel_and_ask(use, context):  # woken for it, she lets it go and asks something instead
        tools = _promise_tools(context)
        await tools["update_item"].ainvoke({"item": memory_manager.ref(_promises(env)[0]), "status": "cancelled",
                                            "reason": "they said they're skipping cardio today"})
        await tools["note_question"].ainvoke({"question": "Are you resting your knee today?",
                                              "asked_by": "columbina", "asking_now": True})
        return "No cardio then. Are you resting your knee today?"

    env.scripts.append(cancel_and_ask)
    run_job(env)
    [q] = [i for i in _questions(env) if "knee" in i.text]
    assert q.keeping is not None and q.keeping["message_id"] == str(_last_assistant(env))
