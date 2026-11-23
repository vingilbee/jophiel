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
    [asked] = [q for q in _questions(env) if q.id == offered.id]
    assert asked.status == "asked"
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
