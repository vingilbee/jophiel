"""Her hands on the user's phone, apart from her mind and mouth (PLAN_PHONE_HANDS.md, migration d9b3e6a1c7f4).

Two parts and one record between them:

- **Her mind / mouth** (reply turns and wakes, as ever) starts a job with a goal and instructions in her own words
  (``start``), changes them (``tell``), pauses, resumes or stops it. It never touches the screen itself, apart from
  a read-only look (``app/ai/tools/hands.py``).
- **Her hands** (``app/ai/hands.py``, on the ``hands`` worker thread) run one job per user in their own model loop
  with the screen tools, look after each step, and speak to nobody. They write the steps, their reports and
  question, and end the job when it's done.
- **The job** (``HandsJob``, ``HandsStep``): goal, instructions (versioned), status, the step log.

How they stay in sync:

- **One writer per field.** Her mind: goal, instructions, status (pause / resume / stop). The hands: steps,
  ``report``, ``question``, the end of a job they finished. Neither writes the other's part.
- **A version on the job.** Each change her mind makes bumps it; before every action the hands check it
  (``check``) and read the new instructions (or stop, or pause) before acting.
- **A pause or stop ends an action that's waiting on the phone**: the job's state is committed first, then the
  phone's control turn moves on (``phone_control.begin_turn``), so the hands' waiting action stops at once
  (``phone_actions.act``) and finds the job no longer running.
- **The banner's Stop is the user's hand**: the phone answers the hands' next action ``taken_over``; the job is
  paused (``paused_by`` user) and she's woken. Only a turn of the user's own words resumes it.
- **Her mind sees the job** every evaluation (``moment``): goal, status, instructions, the last steps, the hands'
  latest report or question.
- **Wakes**: the hands finishing, needing her, reporting, stopping at a bound or at a refusal of the phone, or the
  user's Stop, each wake her mind once (durable ``coordinator_work``, origin ``hands:<job>:<event>``); what to say,
  if anything, is hers.

Bounds are execution checks, never judgement: ``MAX_ACTIONS`` per job, ``MAX_RUN_SECONDS`` from its (re)start, and
a paused job ends by itself after ``PAUSED_MAX_SECONDS``. Screens are never stored: a step keeps what was done and
the screen's first line (``phone_control.record_line``). The chat keeps one line per job ("Used your phone in Clash
of Clans, 31 steps").
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import HandsJob, HandsStep
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

ORIGIN = "hands"
QUEUE = "macrovision:hands:wakeups"
MAX_ACTIONS = 300
MAX_RUN_SECONDS = 15 * 60
PAUSED_MAX_SECONDS = 30 * 60
LEASE_SECONDS = 120  # a claim not renewed this long is taken to be dead (the worker restarted)
# Held from a start or resume until the job's phone turn is recorded, so no run acts on the phone before it.
TAKING = "taking-turn"
RECENT_SECONDS = 30 * 60  # an ended job still shows in her moment this long
STEPS_SHOWN = 8
GOAL_CHARS = 600
INSTRUCTIONS_CHARS = 4000
NOTE_CHARS = 600

# What each event wakes her with (``wake``) and how it reads in her moment.
EVENTS = ("done", "need_her", "report", "user_stop", "phone", "limit", "failed")
OUTCOMES = {"done": "done", "stopped": "stopped by you", "replaced": "replaced by a new job",
            "limit": "stopped at a bound", "failed": "failed", "expired": "ended after a long pause"}
PAUSED_BY = {"her": "you paused it", "user": "they tapped Stop on the banner",
             "phone": "the phone refused", "hands": "your hands need you"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return None if at is None else (at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc))


def _words(text: str | None, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _text(text: str | None, limit: int) -> str:
    """Instructions keep their lines (a list of steps reads as one)."""
    lines = [" ".join(line.split()) for line in (text or "").strip().splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


class HandsError(ValueError):
    """Something her mind asked of her hands that can't be done (said back to her as the tool's answer)."""


# --------------------------------------------------------------------------- #
# The phone's control turn
# --------------------------------------------------------------------------- #


def _hold(user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    """Move the phone's control turn off the job's: an action of the job that's waiting stops waiting, and nothing
    more of it is sent; then the banner goes (off the caller's thread: the phone takes a moment to answer)."""
    from app.services import phone_control

    held = f"held-{uuid.uuid4()}"
    phone_control.begin_turn(user_id, held)

    def end() -> None:
        try:
            from app.services import phone_actions
            phone_actions.act(user_id, conversation_id, "end_control", {"turn": held})
        except Exception:  # noqa: BLE001 - the phone ends control by itself 20 s after the last action
            log.info("hands: end_control after a hold didn't reach the phone")

    threading.Thread(target=end, name="hands-hold", daemon=True).start()


def _take(user_id: uuid.UUID, turn: str) -> None:
    from app.services import phone_control
    phone_control.begin_turn(user_id, turn)


def _poke() -> None:
    """Wake the hands' worker thread (polling is the source of truth)."""
    from app.core.config import get_settings

    url = get_settings().redis_url
    if not url:
        return
    try:
        import redis

        with redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2) as client:
            with client.pipeline() as pipe:
                pipe.rpush(QUEUE, "job")
                pipe.ltrim(QUEUE, -1, -1)
                pipe.execute()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Her mind's side
# --------------------------------------------------------------------------- #


def open_job(db, user_id: uuid.UUID, *, lock: bool = False) -> HandsJob | None:
    statement = select(HandsJob).where(HandsJob.user_id == user_id, HandsJob.status != "ended")
    return db.scalar(statement.with_for_update() if lock else statement)


def _end(db, job: HandsJob, outcome: str, summary: str | None = None, *, now: datetime | None = None) -> None:
    job.status, job.outcome, job.ended_at = "ended", outcome, now or _now()
    job.summary = _words(summary, NOTE_CHARS) or job.summary
    job.claim_token = None


def start(user_id: uuid.UUID, conversation_id: uuid.UUID, *, goal: str, instructions: str = "",
          message_id: uuid.UUID | None = None, zone: str | None = None) -> HandsJob:
    """Start a job (a running one is ended first: ``replaced``). The hands' worker picks it up at once."""
    goal, instructions = _words(goal, GOAL_CHARS), _text(instructions, INSTRUCTIONS_CHARS)
    if not goal:
        raise HandsError("say what the goal is")
    now = _now()
    replaced = None
    with SessionLocal() as db:
        old = open_job(db, user_id, lock=True)
        if old is not None:
            replaced = old.id
            _end(db, old, "replaced", now=now)
            db.flush()
        job = HandsJob(id=uuid.uuid4(), user_id=user_id, conversation_id=conversation_id, message_id=message_id,
                       goal=goal, instructions=instructions, version=1, status="running",
                       revisions=[{"version": 1, "at": now.isoformat(), "text": instructions}],
                       turn=str(uuid.uuid4()), started_at=now, resumed_at=now, zone=zone,
                       claim_token=TAKING, heartbeat_at=now)
        db.add(job)
        try:
            db.commit()
        except IntegrityError:  # another start won the race for the user's one open job
            db.rollback()
            raise HandsError("another job just started on their phone; look at it again") from None
        db.refresh(job)
        db.expunge(job)
    if replaced is not None:
        _line(replaced)
    # The new turn takes the phone: a replaced job's waiting action stops, and the phone refuses the rest of it.
    # Only then can the hands claim the job.
    _take(user_id, job.turn)
    release(job.id, TAKING)
    job.claim_token = None
    _poke()
    _signal(user_id, "start", job.id)
    return job


def tell(user_id: uuid.UUID, instructions: str, *, replace: bool = False) -> HandsJob:
    """Change or add to the open job's instructions (a new version: the hands read it before their next action)."""
    words = _text(instructions, INSTRUCTIONS_CHARS)
    if not words:
        raise HandsError("say what to tell them")
    with SessionLocal() as db:
        job = open_job(db, user_id, lock=True)
        if job is None:
            raise HandsError("no job is open on their phone (use_phone starts one)")
        job.instructions = words if replace or not job.instructions else _text(
            f"{job.instructions}\n{words}", INSTRUCTIONS_CHARS)
        job.version += 1
        job.revisions = [*(job.revisions or []), {"version": job.version, "at": _now().isoformat(),
                                                  "text": words, "replace": replace}][-20:]
        job.question = None  # what she tells them answers it
        # A job paused on their question (before 2026-11-26 a question paused it): they carry on with it (seen live
        # 2026-11-26: her answer sat unread on a paused job while she said she was pressing Attack). A pause of
        # hers, the user's Stop or the phone's refusal stays paused: resuming those is resume_hands.
        restarted = job.status == "paused" and job.paused_by == "hands"
        if restarted:
            _restart(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    if restarted:
        _after_restart(user_id, job)
    _signal(user_id, "tell", job.id)
    return job


def _restart(job: HandsJob) -> None:
    """A paused job running again, on a new phone turn (one the banner's Stop ended stays ended on the phone),
    claimable once that turn is taken (``_after_restart``). The caller commits."""
    job.status, job.paused_by, job.pause_reason, job.question = "running", None, None, None
    job.turn, job.resumed_at, job.claim_token, job.heartbeat_at = str(uuid.uuid4()), _now(), TAKING, _now()


def _after_restart(user_id: uuid.UUID, job: HandsJob) -> None:
    _take(user_id, job.turn)
    release(job.id, TAKING)
    job.claim_token = None
    _poke()


def pause(user_id: uuid.UUID, reason: str | None = None) -> HandsJob:
    with SessionLocal() as db:
        job = open_job(db, user_id, lock=True)
        if job is None:
            raise HandsError("no job is open on their phone")
        if job.status == "paused":
            db.expunge(job)
            return job
        job.status, job.paused_by, job.pause_reason = "paused", "her", _words(reason, NOTE_CHARS) or None
        job.version += 1
        db.commit()
        db.refresh(job)
        db.expunge(job)
    _hold(user_id, job.conversation_id)
    _signal(user_id, "pause", job.id)
    return job


def resume(user_id: uuid.UUID, instructions: str | None = None) -> HandsJob:
    """Resume a paused job, on a new phone turn (a turn the banner's Stop ended stays ended on the phone)."""
    words = _text(instructions, INSTRUCTIONS_CHARS)
    with SessionLocal() as db:
        job = open_job(db, user_id, lock=True)
        if job is None:
            raise HandsError("no job is open on their phone (use_phone starts one)")
        if job.status == "running":
            db.expunge(job)
            return job
        if words:
            job.instructions = _text(f"{job.instructions}\n{words}" if job.instructions else words,
                                     INSTRUCTIONS_CHARS)
            job.revisions = [*(job.revisions or []), {"version": job.version + 1, "at": _now().isoformat(),
                                                      "text": words, "replace": False}][-20:]
        job.version += 1
        _restart(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    _after_restart(user_id, job)
    _signal(user_id, "resume", job.id)
    return job


def stop(user_id: uuid.UUID, reason: str | None = None) -> HandsJob:
    with SessionLocal() as db:
        job = open_job(db, user_id, lock=True)
        if job is None:
            raise HandsError("no job is open on their phone")
        _end(db, job, "stopped", reason)
        job.version += 1
        db.commit()
        db.refresh(job)
        db.expunge(job)
    _hold(user_id, job.conversation_id)
    _line(job.id)
    _signal(user_id, "stop", job.id)
    return job


# --------------------------------------------------------------------------- #
# The hands' side (fenced by the claim)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Check:
    """What the hands find before an action: ``go`` (carry on), ``moved`` (new instructions: read them first),
    ``halt`` (paused, ended or no longer theirs: stop now)."""

    verdict: str
    version: int
    instructions: str
    status: str
    why: str | None = None


def claim_next(token: str, *, now: datetime | None = None, exclude=()) -> uuid.UUID | None:
    """Claim one running job nobody holds (or whose holder stopped renewing). ``exclude``: jobs this process is
    still running (a long model call isn't a dead holder)."""
    now = now or _now()
    with SessionLocal() as db:
        statement = select(HandsJob).where(
            HandsJob.status == "running",
            (HandsJob.claim_token.is_(None)) | (HandsJob.heartbeat_at < now - timedelta(seconds=LEASE_SECONDS)))
        if exclude:
            statement = statement.where(HandsJob.id.notin_(list(exclude)))
        job = db.scalar(statement.order_by(HandsJob.resumed_at).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        job.claim_token, job.claim_generation, job.heartbeat_at = token, job.claim_generation + 1, now
        db.commit()
        return job.id


def _held(db, job_id: uuid.UUID, token: str) -> HandsJob | None:
    job = db.get(HandsJob, job_id, with_for_update=True)
    return job if job is not None and job.claim_token == token else None


def load(job_id: uuid.UUID) -> HandsJob | None:
    with SessionLocal() as db:
        job = db.get(HandsJob, job_id)
        if job is not None:
            db.expunge(job)
        return job


def check(job_id: uuid.UUID, token: str, version: int, *, now: datetime | None = None) -> Check:
    """Before each action: still running and still theirs (renewing the claim), and which instructions stand."""
    now = now or _now()
    with SessionLocal() as db:
        job = _held(db, job_id, token)
        if job is None:
            return Check("halt", version, "", "lost", "this job is no longer yours")
        if job.status != "running":
            return Check("halt", job.version, job.instructions, job.status,
                         "she paused it" if job.status == "paused" else "it ended")
        job.heartbeat_at = now
        db.commit()
        if (now - _aware(job.resumed_at)).total_seconds() > MAX_RUN_SECONDS:
            return Check("limit", job.version, job.instructions, job.status,
                         f"{MAX_RUN_SECONDS // 60} minutes on this job")
        if job.actions >= MAX_ACTIONS:
            return Check("limit", job.version, job.instructions, job.status, f"{MAX_ACTIONS} actions on this job")
        return Check("moved" if job.version != version else "go", job.version, job.instructions, job.status)


def step(job_id: uuid.UUID, token: str, index: int, did: str, result: str | None, version: int,
         *, app: str | None = None) -> None:
    """One action of theirs (added, or its result filled in once known)."""
    with SessionLocal() as db:
        job = db.get(HandsJob, job_id)
        if job is None:
            return
        row = db.scalar(select(HandsStep).where(HandsStep.job_id == job_id, HandsStep.index == index))
        if row is None:
            db.add(HandsStep(job_id=job_id, index=index, did=did[:300], result=(result or None) and result[:200],
                             version=version))
            job.actions = max(job.actions, index + 1)
        else:
            row.result = (result or None) and result[:200]
        if app and app not in (job.apps or []):
            job.apps = [*(job.apps or []), app][:12]
        db.commit()


def release(job_id: uuid.UUID, token: str) -> None:
    with SessionLocal() as db:
        db.execute(update(HandsJob).where(HandsJob.id == job_id, HandsJob.claim_token == token)
                   .values(claim_token=None))
        db.commit()


def report(job_id: uuid.UUID, token: str, note: str) -> bool:
    words = _words(note, NOTE_CHARS)
    with SessionLocal() as db:
        job = _held(db, job_id, token)
        if job is None or not words:
            return False
        job.report, job.report_at = words, _now()
        db.commit()
        _wake(db, job, "report", words)
    return True


def ask(job_id: uuid.UUID, token: str, question: str) -> bool:
    """The hands ask her something; the job keeps running (the user's decision, 2026-11-26: no pause for a
    question). Her answer is new instructions (``tell``), read before their next action."""
    words = _words(question, NOTE_CHARS)
    with SessionLocal() as db:
        job = _held(db, job_id, token)
        if job is None or not words:
            return False
        job.question = words
        db.commit()
        _wake(db, job, "need_her", words)
    return True


def finish(job_id: uuid.UUID, token: str, event: str, text: str | None = None) -> bool:
    """The hands end their run: ``done`` (ended), ``user_stop`` (the banner's
    Stop: paused), ``phone`` (a refusal of the phone: paused), ``limit`` / ``failed`` (ended). Wakes her once."""
    words = _words(text, NOTE_CHARS)
    ended = False
    with SessionLocal() as db:
        job = _held(db, job_id, token)
        if job is None or job.status != "running":
            return False
        if event == "done":
            _end(db, job, "done", words)
            ended = True
        elif event in ("limit", "failed"):
            _end(db, job, event, words)
            ended = True
        else:
            job.status = "paused"
            job.paused_by = {"user_stop": "user", "phone": "phone"}[event]
            job.pause_reason = words or None
            job.question = None
            job.claim_token = None
        db.commit()
        _wake(db, job, event, words)
        user_id = job.user_id
    if ended:
        _line(job_id)
    _signal(user_id, event, job_id)
    return True


def expire_paused(*, now: datetime | None = None) -> int:
    """A job left paused this long ends by itself (no wake: nothing is happening on the phone)."""
    now = now or _now()
    ended = []
    with SessionLocal() as db:
        for job in db.scalars(select(HandsJob).where(
                HandsJob.status == "paused", HandsJob.updated_at < now - timedelta(seconds=PAUSED_MAX_SECONDS))
                .with_for_update(skip_locked=True)):
            _end(db, job, "expired", now=now)
            ended.append(job.id)
        db.commit()
    for job_id in ended:
        _line(job_id)
    return len(ended)


# --------------------------------------------------------------------------- #
# What the chat and her mind see
# --------------------------------------------------------------------------- #


def line_id(job_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(job_id, "hands")


def chat_line(job: HandsJob) -> str:
    from app.services.phone_control import turn_line

    steps = job.actions
    return turn_line(apps=list(job.apps or []), steps=steps, acted=steps > 1 or job.outcome == "done",
                     taken_over=job.paused_by == "user", at_limit=job.outcome == "limit")


def _line(job_id: uuid.UUID) -> None:
    """The job's one chat line, once it has ended (only a job that did something)."""
    try:
        job = load(job_id)
        if job is None or job.actions == 0:
            return
        from app.services import phone_actions
        phone_actions.record_line(job.user_id, job.conversation_id, line_id(job.id), chat_line(job))
    except Exception:  # noqa: BLE001 - the job itself is unaffected
        log.exception("hands: writing the job's chat line")


def steps_of(db, job_id: uuid.UUID, *, limit: int = STEPS_SHOWN) -> list[HandsStep]:
    rows = list(db.scalars(select(HandsStep).where(HandsStep.job_id == job_id)
                           .order_by(HandsStep.index.desc()).limit(limit)))
    return list(reversed(rows))


def _ago(at: datetime | None, now: datetime) -> str:
    if at is None:
        return ""
    seconds = max(0, int((now - _aware(at)).total_seconds()))
    return f"{seconds} s ago" if seconds < 120 else f"{seconds // 60} min ago"


def _step_line(row: HandsStep) -> str:
    return f"{row.index + 1}. {row.did}" + (f" → {row.result}" if row.result else " → (no answer yet)")


def latest(db, user_id: uuid.UUID, *, now: datetime | None = None) -> HandsJob | None:
    """The open job, or the one that ended in the last ``RECENT_SECONDS``."""
    now = now or _now()
    job = open_job(db, user_id)
    if job is not None:
        return job
    return db.scalar(select(HandsJob).where(
        HandsJob.user_id == user_id, HandsJob.ended_at >= now - timedelta(seconds=RECENT_SECONDS))
        .order_by(HandsJob.ended_at.desc()).limit(1))


def describe(db, job: HandsJob, *, now: datetime | None = None, steps: int = STEPS_SHOWN) -> list[str]:
    """The job as her mind reads it: goal, state, the instructions as they stand, its last steps, the hands'
    latest report or question."""
    now = now or _now()
    if job.status == "running":
        state = f"running (started {_ago(job.started_at, now)})"
    elif job.status == "paused":
        state = f"paused: {PAUSED_BY.get(job.paused_by or '', job.paused_by or 'paused')}"
        if job.pause_reason and job.paused_by != "hands":
            state += f" ({job.pause_reason})"
    else:
        state = f"ended {_ago(job.ended_at, now)}: {OUTCOMES.get(job.outcome or '', job.outcome or 'ended')}"
        if job.summary:
            state += f": {job.summary}"
    lines = [f"Job [{str(job.id)[:8]}]: {job.goal}", f"State: {state}; {job.actions} "
             f"action{'s' if job.actions != 1 else ''} so far" + (f" in {', '.join(job.apps)}" if job.apps else ""),
             f"Your instructions (version {job.version}):"]
    lines += [f"  {line}" for line in (job.instructions or "(none beyond the goal)").splitlines()]
    rows = steps_of(db, job.id, limit=steps)
    if rows:
        skipped = rows[0].index
        lines.append("Their last steps" + (f" (the first {skipped} not shown)" if skipped else "") + ":")
        lines += [f"  {_step_line(row)}" for row in rows]
    if job.question and job.status != "ended":
        lines.append(f"They asked you (still working): {job.question}")
    if job.report:
        lines.append(f"Their latest report ({_ago(job.report_at, now)}): {job.report}")
    return lines


def moment(user_id: uuid.UUID, *, now: datetime | None = None) -> str | None:
    """The "Hands" section of her moment (None: no job open or recently ended)."""
    with SessionLocal() as db:
        job = latest(db, user_id, now=now)
        if job is None:
            return None
        return "\n".join(describe(db, job, now=now))


def has_open(user_id: uuid.UUID) -> bool:
    with SessionLocal() as db:
        return open_job(db, user_id) is not None


# --------------------------------------------------------------------------- #
# Wakes of her mind from her hands
# --------------------------------------------------------------------------- #

_ABOUT = {"done": "Your hands finished on their phone", "need_her": "Your hands on their phone need you",
          "report": "Your hands on their phone report something", "user_stop": "They tapped Stop on the banner",
          "phone": "The phone stopped your hands", "limit": "Your hands stopped at a bound",
          "failed": "Your hands on their phone failed"}


def _wake(db, job: HandsJob, event: str, text: str) -> None:
    """One durable wake of her mind for this event (origin ``hands:<job>:<event>``)."""
    from app.services import coordinator

    try:
        work = coordinator.schedule_wake(
            db, user_id=job.user_id, conversation_id=job.conversation_id, zone=job.zone, asked_by="mind",
            about=_ABOUT[event], due_at=_now(), origin=f"{ORIGIN}:{job.id}:{event}",
            origin_message=job.message_id)
        work.input = {**work.input, "hands": {"job": str(job.id), "event": event, "text": text,
                                              "goal": job.goal, "version": job.version}}
        db.commit()
        coordinator._wake()
    except Exception:  # noqa: BLE001 - the job's record still holds what happened; her next turn sees it
        db.rollback()
        log.exception("hands: waking her mind for %s", event)


def is_wake(inp: dict | None) -> bool:
    return str((inp or {}).get("origin") or "").startswith(f"{ORIGIN}:")


_WAKE_WORDS = {
    "done": "Your hands say they're done: {text}",
    "need_her": "Your hands ask you: {text}. They keep working meanwhile; tell_hands with the answer reaches them "
                "before their next action.",
    "report": "Your hands report, while they keep going: {text}",
    "user_stop": "They tapped Stop on the banner: the job is paused. Only their words can let your hands go on.",
    "phone": "The phone stopped your hands: {text}. The job is paused.",
    "limit": "Your hands stopped at a bound ({text}). The job ended.",
    "failed": "Your hands failed ({text}). The job ended.",
}


def render_wake(info: dict) -> str:
    """The wake as she reads it; the job as it stands now is in "Your hands on their phone"."""
    hands = (info.get("input") or {}).get("hands") or {}
    event = hands.get("event") or "report"
    said = _WAKE_WORDS.get(event, "{text}").format(text=hands.get("text") or "(nothing said)")
    return "\n".join([
        f"[wake:{str(info.get('work_id') or '')[:8]}] {said}",
        f"The job: {hands.get('goal') or '(not kept)'}",
        "Nobody asked you anything just now: whether to tell them, and what, is yours. If there's nothing worth "
        "saying, stay_silent."])


def _signal(user_id: uuid.UUID, what: str, job_id: uuid.UUID) -> None:
    try:
        from app.services import system_signals
        src, dst = ("mind", "phone") if what in ("start", "tell", "pause", "resume", "stop") else ("phone", "mind")
        system_signals.emit(user_id, src, dst, f"hands_{what}", job_id)
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
