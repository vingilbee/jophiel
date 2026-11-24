"""Her follow-through on what she promised or meant to do when a change makes it due (docs/MIND_ARCHITECTURE.md,
"Follow-through"). Shared by every source: a phone report, a reminder's condition, anything her background mind sees.

1. A quick thought (app/ai/mind/think.py) that finds an open promise or intention of hers due now, or that sees how an
   attempt of hers turned out, names the item and what to do (``act``), even when it has nothing to say. ``request``
   turns that into one wake of her mind with her tools (``autonomous_speech.submit``, ``wake="follow_through"``): the
   same coordinator, freshness, ownership and permission checks as any thought of hers. One wake per item and
   observed change (``transition``): two observations of the same change, a retry or a restart find the first.
2. In that evaluation she rereads the item, "Now" and her attempts (``lines``), and decides: nothing (cancelled,
   changed, already as they want), an action, or telling them what she can't do. Permissions are the wake's own:
   on the phone the quiet actions of her own turns, never what needs them talking with her.
3. Each device action on such a wake is an attempt (``begin`` / ``finish``), recorded before it's sent. Fences, all
   execution facts: the item still open; nothing else for it still waiting for its result; at most
   ``MAX_PER_CHANGE`` tries for one change and ``MAX_PER_HOUR`` for the item; a check that finds they wrote since the
   attempt sends nothing more (their latest message decides).
4. A command the device took is not the goal reached. When the action's own answer proves it (an alarm set), that
   is the result; otherwise the attempt stays ``pending`` and a check wakes her (``CHECK_DELAYS``) to read a fresh
   report (``observe``: the phone field, reported after the attempt). Only she judges whether what's reported is what
   they wanted (``settle``), and only on a fresh report: without one it can't be counted achieved, failed or even
   unresolved (her checks are what wait for it; settling first would cancel them). After the last timed check, an
   attempt with a field to read keeps waiting for the phone's next report of it (``AWAIT_READING``): the sweep wakes
   her once when one comes (``_out_of_checks``). Still nothing then, or nothing to read: ``unresolved``, never
   success.
5. The item closing (kept, cancelled, changed by them) cancels its pending attempts and their checks
   (``item_closed``, from ``memory_manager``), so a check can't act on a promise that's gone.

Nothing here decides what she does or judges what anything means: it carries, records and fences.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, String

from app.db.models import ChatMessage, CoordinatorWork, FollowThroughAttempt, WorkingItem
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

# What shows an action's result: the phone field reported after it. Missing: the phone's own answer proves it (an
# alarm or timer set, a notification dismissed, a volume set; none has a reported state to read back).
OBSERVED = {"media_control": "media", "set_ringer": "ringer", "set_dnd": "dnd"}
MAX_PER_CHANGE = 2  # tries while it stays unmet (``_episode``): a change elsewhere on the phone adds none
MAX_PER_HOUR = 8  # tries for one item in any hour, across changes: a recurring promise can't loop without end
CHECK_DELAYS = (20, 60)  # seconds after the attempt (then after the first check) that a check wakes her
# After those, how long an attempt waits for the phone's next report of what it observes (the phone reports from the
# background every few minutes, and a command that changes nothing it shows prompts no report of its own); the
# report wakes her once (``READING_DELAY`` later). A pacing bound only.
AWAIT_READING = timedelta(minutes=10)
READING_DELAY = 1
SENDING_STALE = timedelta(minutes=2)  # an attempt never answered (the worker died mid-call) stops blocking then
OPEN = ("sending", "pending")
ITEMS_SHOWN = 6
FOLLOW_TTL_SECONDS = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _short(value) -> str:
    return str(value)[:8]


def key_of(values: dict) -> str:
    """A stable short key for an observed state: two observations of one change share it."""
    raw = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# What she's waiting for, as a moment shows it
# --------------------------------------------------------------------------- #

def _waiting(db, user_id: uuid.UUID, limit: int = ITEMS_SHOWN) -> list[WorkingItem]:
    return list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None),
        ((WorkingItem.kind == "commitment") & (WorkingItem.owner == "columbina"))
        | ((WorkingItem.kind == "intention") & (WorkingItem.owner == "columbina")))
        .order_by(WorkingItem.created_at.desc()).limit(limit)))


def watching(db, user_id: uuid.UUID) -> bool:
    """She has a promise open that waits on something happening (its condition is an event, not a time or her own
    background work)."""
    return db.scalar(select(WorkingItem.id).where(
        WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None), WorkingItem.owner == "columbina",
        WorkingItem.kind.in_(("commitment", "intention")),
        WorkingItem.trigger["kind"].astext == "event").limit(1)) is not None


def waiting_lines(db, user_id: uuid.UUID, now: datetime | None = None) -> list[str]:
    """Her open promises and intentions with their ids, what each waits for, and attempts still waiting for a
    result: what a quick thought needs to see that one is due (``act``)."""
    now = now or _now()
    rows = _waiting(db, user_id)
    if not rows:
        return []
    pending = {}
    for a in db.scalars(select(FollowThroughAttempt).where(
            FollowThroughAttempt.user_id == user_id, FollowThroughAttempt.item_id.in_([r.id for r in rows]),
            FollowThroughAttempt.status.in_(OPEN))):
        pending.setdefault(a.item_id, []).append(a)
    out = []
    for row in rows:
        what = "You promised" if row.kind == "commitment" else "You meant to"
        line = f"- [{_short(row.id)}] {what}: {' '.join(row.text.split())[:200]}"
        if (row.trigger or {}).get("text"):
            line += f" (when: {row.trigger['text']})"
        out.append(line)
        for a in pending.get(row.id, []):
            ago = int((now - a.issued_at).total_seconds())
            out.append(f"  - your attempt [{_short(a.id)}] ({describe(a)}), {ago} s ago, is waiting for its result")
    return out


# --------------------------------------------------------------------------- #
# 1. A change made something due: one wake of her mind
# --------------------------------------------------------------------------- #

def open_item(db, user_id: uuid.UUID, key: str | uuid.UUID) -> WorkingItem | None:
    """An open promise or intention of hers by the id she was shown (or its prefix), or by the id of an attempt of
    hers for one (the moment shows both: an attempt's result is about its promise); None otherwise."""
    from app.services import memory_manager

    ref = key if isinstance(key, uuid.UUID) else str(key).strip("[] ").lower()
    try:
        entry = memory_manager.item(db, user_id, ref)
    except memory_manager.MemoryError:
        entry = None
        if isinstance(ref, uuid.UUID) or len(ref) >= 6:
            found = list(db.scalars(select(FollowThroughAttempt.item_id).where(
                FollowThroughAttempt.user_id == user_id,
                FollowThroughAttempt.id == ref if isinstance(ref, uuid.UUID)
                else func.cast(FollowThroughAttempt.id, String).like(f"{ref}%")).limit(2)))
            if len(found) == 1:
                entry = db.get(WorkingItem, found[0])
        if entry is None or entry.user_id != user_id:
            return None
    if entry.closed_at is not None or entry.owner != "columbina" or entry.kind not in ("commitment", "intention"):
        return None
    return entry


def request(db, *, user_id: uuid.UUID, source: str, activity_id: uuid.UUID, item: str, act: str, transition: str,
            reasons: list[str], speaking: dict | None = None, ttl: float = FOLLOW_TTL_SECONDS,
            say: str | None = None) -> str:
    """Wake her mind, with her tools, about an item a change made due. Once per item and change: a second
    observation of the same change (or a restart) finds the first. Returns what happened, for the log."""
    from app.services import autonomous_speech, coordinator

    entry = open_item(db, user_id, item)
    if entry is None:
        return "Rejected: no open promise or intention of hers with that id."
    transition = (transition or "")[:80]
    event = f"follow:{entry.id}:{transition}"
    request_id = uuid.uuid5(user_id, f"autonomous:{source}:{activity_id}:{event}")
    if db.get(CoordinatorWork, request_id) is not None:
        coordinator._log({"op": "follow_through_seen", "item": str(entry.id), "transition": transition})
        return "Already: this change already woke her for it."
    words = " ".join((act or "").split())[:300]
    return autonomous_speech.submit(
        db, user_id=user_id, activity_id=activity_id, source=source, event_id=event, text=words, reasons=reasons,
        ttl=max(ttl, FOLLOW_TTL_SECONDS), premise={"item": entry.id}, wake="follow_through",
        extra={"item": str(entry.id), "transition": transition,
               **({"say": " ".join(say.split())[:300]} if say else {})},
        **({"commentary_mode": "occasional"} | (speaking or {})))


@dataclass(frozen=True)
class Follow:
    """What an evaluation follows through on: the item, the change that made it due, and the attempt whose check
    woke her (None on the first wake)."""

    item_id: uuid.UUID
    transition: str
    check: uuid.UUID | None = None


def of(ev) -> Follow | None:
    """The follow-through an evaluation is (``mind_turn.Evaluation``, or its wake info), or None."""
    info = ev if isinstance(ev, dict) else (getattr(ev, "wake", None) or {})
    inp = info.get("input") or {}
    try:
        if info.get("kind") == "mind" and inp.get("autonomous") and inp.get("wake") == "follow_through":
            extra = inp.get("extra") or {}
            return Follow(uuid.UUID(extra["item"]), str(extra.get("transition") or ""))
        if info.get("kind") == "wake" and inp.get("follow_check"):
            return Follow(uuid.UUID(inp["follow_item"]), str(inp.get("follow_transition") or ""),
                          uuid.UUID(inp["follow_check"]))
    except (KeyError, ValueError, TypeError):
        return None
    return None


# --------------------------------------------------------------------------- #
# 3. Attempts: recorded before they're sent, fenced, and settled only on evidence
# --------------------------------------------------------------------------- #

def describe(a: FollowThroughAttempt) -> str:
    shown = ", ".join(f"{k} {v}" for k, v in (a.detail or {}).items()
                      if v not in (None, "", []) and not k.startswith("_"))
    return f"{a.action.replace('_', ' ')}" + (f": {shown}" if shown else "")


def _locked_item(db, user_id: uuid.UUID, item_id: uuid.UUID) -> WorkingItem | None:
    return db.scalar(select(WorkingItem).where(WorkingItem.id == item_id, WorkingItem.user_id == user_id)
                     .with_for_update())


def _locked(db, attempt_id: uuid.UUID) -> tuple[WorkingItem | None, FollowThroughAttempt | None]:
    """An attempt and its item, locked in the one order every path uses: the item, then the attempt (``begin``,
    closing an item, ``finish``, ``after``, ``settle``, the sweep). Never the other way round, so two of them on one
    item wait for each other instead of deadlocking."""
    found = db.execute(select(FollowThroughAttempt.user_id, FollowThroughAttempt.item_id)
                       .where(FollowThroughAttempt.id == attempt_id)).first()
    if found is None:
        return None, None
    entry = _locked_item(db, found.user_id, found.item_id)
    row = db.scalar(select(FollowThroughAttempt).where(FollowThroughAttempt.id == attempt_id).with_for_update()
                    .execution_options(populate_existing=True))
    return entry, row


def _wrote_since(db, conversation_id: uuid.UUID | None, at: datetime) -> bool:
    if conversation_id is None:
        return False
    return db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user",
        ChatMessage.created_at > at).limit(1)) is not None


def begin(ev, action: str, detail: dict) -> uuid.UUID | str:
    """Before a device action on a follow-through: the attempt's id (recorded as ``sending``), or why nothing may be
    sent (said to her as is)."""
    follow = of(ev)
    if follow is None:
        raise ValueError("not a follow-through")
    now = _now()
    with SessionLocal() as db:
        entry = _locked_item(db, ev.user_id, follow.item_id)
        if entry is None or entry.closed_at is not None:
            state = f" ({entry.status})" if entry is not None else ""
            return (f"Not done: [{_short(follow.item_id)}] is no longer open{state}: nothing was sent. If they "
                    "cancelled or changed it, their latest word stands.")
        if follow.check is not None:
            checked = db.get(FollowThroughAttempt, follow.check)
            if checked is not None and _wrote_since(db, checked.conversation_id, checked.issued_at):
                return ("Not done: they've written since your attempt, so their latest message decides, not this "
                        "check. Nothing was sent. If it's still what they want, the next change will bring it back.")
        for a in db.scalars(select(FollowThroughAttempt).where(
                FollowThroughAttempt.user_id == ev.user_id, FollowThroughAttempt.item_id == entry.id,
                FollowThroughAttempt.status.in_(OPEN)).with_for_update()):
            if a.status == "sending" and now - a.issued_at > SENDING_STALE:
                _interrupted(db, a, ev, now)
            if a.status == "pending" and a.id != follow.check and not _check_alive(db, a, now):
                # Its check was dropped, expired or never ran: the next one; after the last, the phone's next report.
                if _schedule_check(db, a, ev) is None:
                    _out_of_checks(db, a, ev, now)
                    if a.status not in OPEN:
                        continue
            if a.status in OPEN:
                db.commit()
                return (f"Not done: your attempt [{_short(a.id)}] ({describe(a)}) is still waiting for its result, so "
                        "nothing more was sent. Settle it first with settle_attempt if a fresh report shows how it "
                        "went, or let its check wake you.")
        episode = _episode(db, ev.user_id, entry.id, follow, action)
        tried = _tries(db, ev.user_id, entry.id, episode)
        if tried >= MAX_PER_CHANGE:
            db.commit()
            return (f"Not done: you've tried {tried} times for this already and it still isn't as they asked; "
                    "nothing more was sent. Don't claim it worked: tell them what you tried and what you couldn't do.")
        hour = db.scalar(select(func.count()).select_from(FollowThroughAttempt).where(
            FollowThroughAttempt.user_id == ev.user_id, FollowThroughAttempt.item_id == entry.id,
            FollowThroughAttempt.issued_at > now - timedelta(hours=1),
            FollowThroughAttempt.status != "cancelled")) or 0
        if hour >= MAX_PER_HOUR:
            db.commit()
            return (f"Not done: {hour} tries for this in the last hour; nothing more was sent. Tell them it keeps "
                    "happening and ask whether they still want it.")
        row = FollowThroughAttempt(
            id=uuid.uuid4(), user_id=ev.user_id, item_id=entry.id, conversation_id=ev.conversation_id,
            transition=episode, wake_work_id=uuid.UUID(str(ev.wake_work_id)) if ev.wake_work_id else None,
            action=action, detail={**{k: v for k, v in (detail or {}).items() if v is not None},
                                   "_change": follow.transition},
            observe=OBSERVED.get(action), status="sending", issued_at=now)
        db.add(row)
        db.commit()
        _log({"op": "attempt", "attempt": str(row.id), "item": str(entry.id), "action": action,
              "episode": episode, "change": follow.transition})
        return row.id


def _tries(db, user_id: uuid.UUID, item_id: uuid.UUID, episode: str) -> int:
    return db.scalar(select(func.count()).select_from(FollowThroughAttempt).where(
        FollowThroughAttempt.user_id == user_id, FollowThroughAttempt.item_id == item_id,
        FollowThroughAttempt.transition == episode, FollowThroughAttempt.status != "cancelled")) or 0


def _episode(db, user_id: uuid.UUID, item_id: uuid.UUID, follow: Follow, action: str) -> str:
    """Which unmet stretch an attempt belongs to (its tries share ``MAX_PER_CHANGE``): a check's attempt's own; else
    the last attempt's, unless what it waited for was reached since (an attempt achieved), they wrote since it (their
    latest word renews it), or what it observes changed on the phone after that attempt was settled (not her doing:
    hers shows before she settles). Anything else on the phone changing (the app opened, the screen) is the same
    unmet stretch, so it never buys more tries; an action the phone reports nothing back on (an alarm, a timer)
    has only those first two."""
    if follow.check is not None:
        checked = db.get(FollowThroughAttempt, follow.check)
        if checked is not None:
            return checked.transition
    last = db.scalar(select(FollowThroughAttempt).where(
        FollowThroughAttempt.user_id == user_id, FollowThroughAttempt.item_id == item_id,
        FollowThroughAttempt.status != "cancelled").order_by(FollowThroughAttempt.issued_at.desc()).limit(1))
    if last is None or last.status == "achieved" or _wrote_since(db, last.conversation_id, last.issued_at):
        return f"ep:{uuid.uuid4().hex[:20]}"
    field = OBSERVED.get(action) or last.observe
    if field is not None:
        from app.services import phone_state
        try:
            state = phone_state.read(user_id)
        except Exception:  # noqa: BLE001 - unknown is the same stretch
            state = None
        value = state.values.get(field) if state is not None else None
        settled = (last.settled_at or last.issued_at).timestamp()
        if value is not None and value.since > settled:
            return f"ep:{uuid.uuid4().hex[:20]}"
    return last.transition


IN_FLIGHT = timedelta(minutes=10)  # a check whose evaluation was dispatched this recently is still running
CONCLUDE_GRACE = timedelta(seconds=60)  # a check that came due this recently is still being handed on


def _check_alive(db, row: FollowThroughAttempt, now: datetime) -> bool:
    """Whether a pending attempt's check can still wake her: one is scheduled (moved ones included: they keep the
    attempt's id), or one came due and its evaluation is on its way or running."""
    from app.db.models import CoordinatorOutput
    from app.services import coordinator

    wakes = list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == row.user_id, CoordinatorWork.kind == "wake",
        CoordinatorWork.input["follow_check"].astext == str(row.id))))
    for work in wakes:
        if work.status in coordinator.ACTIVE:
            return True
        if work.status not in ("completed", "expired"):
            continue  # (an expired check is still dispatched, late)
        output = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.work_id == work.id,
                                                           CoordinatorOutput.phase == "follow_up"))
        if output is None:
            if now - work.updated_at <= CONCLUDE_GRACE:
                return True  # being handed to her mind; one never handed on in that time was dropped
        elif output.status in ("decided", "dispatched") and now - output.created_at <= IN_FLIGHT:
            return True  # her evaluation of it is running
    return False


def finish(ev, attempt_id: uuid.UUID, status: str, text: str) -> str:
    """The device's answer to an attempt: what it means for the attempt, said back to her after the answer.
    ``status``: the phone action's (done / failed / notified / expired / unconfirmed)."""
    now = _now()
    with SessionLocal() as db:
        entry, row = _locked(db, attempt_id)
        if row is None:
            return ""
        ref = f"[{_short(row.id)}]"
        if row.status != "sending":
            return _late_answer(db, row, status, text, now)
        row.result = (text or "")[:500]
        if entry is None or entry.closed_at is not None:
            row.status, row.settled_at, row.settled_reason = "cancelled", now, "the item closed meanwhile"
            db.commit()
            return f"Attempt {ref}: the item closed meanwhile, so nothing more follows from it."
        if status not in ("done", "unconfirmed"):
            row.status, row.settled_at = "failed", now
            row.settled_reason = f"the device didn't do it: {row.result}"
            db.commit()
            left = MAX_PER_CHANGE - _tried(db, row)
            return (f"Attempt {ref} failed: the device didn't do it. It isn't done; {left} more "
                    f"{'try' if left == 1 else 'tries'} for this change. If there's no way, tell them.")
        if row.observe is None and status == "done":
            row.status, row.settled_at, row.settled_reason = "achieved", now, "the device's own answer confirms it"
            db.commit()
            return f"Attempt {ref}: the device's answer confirms it."
        row.status = "pending"
        work = _schedule_check(db, row, ev)
        db.commit()
        took = "took the command" if status == "done" else "may or may not have done it (no answer yet)"
        when = f"A check wakes you in {CHECK_DELAYS[0]} s" if work is not None else "No check could be scheduled"
        return (f"Attempt {ref}: the device {took}, but that isn't the result. {when} to read a fresh report of "
                f"{row.observe or 'its state'}; until then nothing more is sent for this, and don't say it worked.")


def _late_answer(db, row: FollowThroughAttempt, status: str, text: str, now: datetime) -> str:
    """The device's answer after the sweep already moved the attempt on (``_interrupted``): recorded; it settles
    the attempt only when it's conclusive on its own (the device didn't do it, or did it with nothing to read it
    on). Never a second chain of checks."""
    from app.services import coordinator

    ref = f"[{_short(row.id)}]"
    row.result = (text or "")[:500]
    if row.status in ("pending", "unresolved") and (status not in ("done", "unconfirmed")
                                                     or (status == "done" and row.observe is None)):
        row.status, row.settled_at = ("failed", now) if status != "done" else ("achieved", now)
        row.settled_reason = (f"the device didn't do it (answered late): {row.result}" if status != "done"
                              else "the device's own answer confirms it (answered late)")
        for check in db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == row.user_id, CoordinatorWork.kind == "wake",
                CoordinatorWork.status.in_(coordinator.ACTIVE),
                CoordinatorWork.input["follow_check"].astext == str(row.id)).with_for_update()):
            check.status, check.error_code = "cancelled", "settled"
            check.cancellation_generation += 1
    db.commit()
    return f"Attempt {ref} ({row.status}): the device answered late: {row.result or 'no details'}."


def _tried(db, row: FollowThroughAttempt) -> int:
    return db.scalar(select(func.count()).select_from(FollowThroughAttempt).where(
        FollowThroughAttempt.user_id == row.user_id, FollowThroughAttempt.item_id == row.item_id,
        FollowThroughAttempt.transition == row.transition, FollowThroughAttempt.status != "cancelled")) or 0


def _schedule_check(db, row: FollowThroughAttempt, ev, *, reading: bool = False) -> CoordinatorWork | None:
    """The next check of a pending attempt, as a durable wake in its conversation; None when the checks are used up
    or there's no conversation. ``reading``: the one more check a report after the timed ones brings
    (``_out_of_checks``). The caller commits."""
    from app.services import coordinator

    if row.conversation_id is None or row.checks > len(CHECK_DELAYS) or (
            row.checks == len(CHECK_DELAYS) and not reading):
        return None
    delay = READING_DELAY if reading else CHECK_DELAYS[row.checks]
    about = (f"A report came for your attempt [{_short(row.id)}] ({describe(row)}) for [{_short(row.item_id)}]: "
             "check whether it worked" if reading else
             f"Check whether your attempt [{_short(row.id)}] ({describe(row)}) for [{_short(row.item_id)}] worked")
    work = coordinator.schedule_wake(
        db, user_id=row.user_id, conversation_id=row.conversation_id,
        about=about, due_at=_now() + timedelta(seconds=delay), origin=f"follow:{row.id}", asked_by="mind",
        zone=getattr(ev, "zone", None), channel=getattr(ev, "channel", "web") or "web",
        extra={"follow_item": str(row.item_id), "follow_check": str(row.id), "follow_transition": row.transition})
    row.checks += 1
    row.check_work_id = work.id
    return work


def _out_of_checks(db, row: FollowThroughAttempt, where, now: datetime) -> bool:
    """A pending attempt whose timed checks are used up. With a field the phone reports, it keeps waiting for the
    next report of it (``AWAIT_READING`` from the attempt): one that came wakes her once more (a reading check);
    none yet leaves it pending for the sweep to look again (returns False: nothing moved). Nothing to read, its
    reading check already had, or the wait over: ``unresolved``. The caller commits."""
    if (row.observe is not None and row.conversation_id is not None and row.checks == len(CHECK_DELAYS)
            and now - row.issued_at < AWAIT_READING):
        if not fresh(row.user_id, row)[0]:
            return False  # still waiting for a report
        if _schedule_check(db, row, where, reading=True) is not None:
            return True
    row.status, row.settled_at = "unresolved", now
    if row.checks > len(CHECK_DELAYS):
        row.settled_reason = "no fresh report showed the result, even the one after its checks"
    elif row.observe is not None and now - row.issued_at >= AWAIT_READING:
        row.settled_reason = (f"no report of {row.observe} from the phone within "
                              f"{int(AWAIT_READING.total_seconds() // 60)} min of the attempt")
    else:
        row.settled_reason = (f"no fresh report showed the result after {row.checks} "
                              f"check{'s' if row.checks != 1 else ''}")
    return True


def after(ev) -> None:
    """When a check's evaluation ends with its attempt still pending: the next check; after the last one, the
    phone's next report (``_out_of_checks``), else ``unresolved`` (never success). Never raises."""
    follow = of(ev)
    if follow is None or follow.check is None:
        return
    try:
        with SessionLocal() as db:
            _, row = _locked(db, follow.check)
            if row is None or row.status != "pending":
                return
            if _schedule_check(db, row, ev) is None:
                now = _now()
                if fresh(row.user_id, row)[0]:
                    # She read a fresh report on its last check and left it open: no later check reads more.
                    row.status, row.settled_at = "unresolved", now
                    row.settled_reason = "a fresh report was there on its last check and it was left open"
                else:
                    _out_of_checks(db, row, ev, now)
            db.commit()
            _log({"op": "after_check", "attempt": str(row.id), "status": row.status, "checks": row.checks})
    except Exception:  # noqa: BLE001
        log.exception("follow_through: after a check")


SWEEP_SECONDS = 15.0
_last_sweep = 0.0


def _interrupted(db, row: FollowThroughAttempt, where, now: datetime) -> None:
    """An attempt whose answer never came back (the worker died while the phone had it): it may or may not have
    acted. With something the phone reports to read it on, it waits for that like any unanswered command (a check,
    ``unresolved`` after the last); otherwise ``unresolved``. Never success, never silently dropped."""
    row.result = "its answer never came back (interrupted): it may or may not have reached the phone"
    if row.observe is not None:
        row.status = "pending"
        if _schedule_check(db, row, where) is not None:
            return
    row.status, row.settled_at = "unresolved", now
    row.settled_reason = "its answer never came back (interrupted)"


def sweep(*, force: bool = False, limit: int = 50) -> int:
    """From the coordinator's heartbeat: pending attempts whose check died (dropped, expired, never ran, a worker
    down past it) get their next check, or ``unresolved`` after the last; attempts left ``sending`` by a worker that
    died mid-command (``_interrupted``) are checked or made ``unresolved``. All on their own: no new observation or
    device action is needed to move them. Returns how many it moved. Never raises."""
    import time

    global _last_sweep
    if not force and time.monotonic() - _last_sweep < SWEEP_SECONDS:
        return 0
    _last_sweep = time.monotonic()
    moved = 0
    try:
        with SessionLocal() as db:
            ids = list(db.scalars(select(FollowThroughAttempt.id).where(
                (FollowThroughAttempt.status == "pending")
                | ((FollowThroughAttempt.status == "sending") & (FollowThroughAttempt.issued_at < _now() - SENDING_STALE)))
                .order_by(FollowThroughAttempt.issued_at).limit(limit)))
        for attempt_id in ids:
            moved += _reconcile(attempt_id)
    except Exception:  # noqa: BLE001
        log.exception("follow_through: sweep")
    return moved


def _reconcile(attempt_id: uuid.UUID) -> int:
    from types import SimpleNamespace

    now = _now()
    with SessionLocal() as db:
        entry, row = _locked(db, attempt_id)
        if row is None:
            return 0
        stuck = row.status == "sending" and now - row.issued_at > SENDING_STALE
        if not stuck and (row.status != "pending" or _check_alive(db, row, now)):
            return 0
        if entry is None or entry.closed_at is not None:
            row.status, row.settled_at, row.settled_reason = "cancelled", now, "the item closed meanwhile"
        elif stuck:
            wake = db.get(CoordinatorWork, row.wake_work_id) if row.wake_work_id else None
            _interrupted(db, row, SimpleNamespace(zone=(wake.input or {}).get("zone") if wake else None,
                                                  channel=((wake.input or {}).get("channel") if wake else None)
                                                  or "web"), now)
        else:
            last = db.scalar(select(CoordinatorWork).where(
                CoordinatorWork.user_id == row.user_id, CoordinatorWork.kind == "wake",
                CoordinatorWork.input["follow_check"].astext == str(row.id)).order_by(CoordinatorWork.created_at.desc())
                .limit(1))
            where = SimpleNamespace(zone=(last.input or {}).get("zone") if last else None,
                                    channel=((last.input or {}).get("channel") if last else None) or "web")
            if _schedule_check(db, row, where) is None and not _out_of_checks(db, row, where, now):
                db.rollback()
                return 0  # still waiting for the phone's report: nothing moved
        db.commit()
        _log({"op": "reconciled", "attempt": str(row.id), "status": row.status, "checks": row.checks})
        return 1


def fresh(user_id: uuid.UUID, row: FollowThroughAttempt) -> tuple[bool, str]:
    """Whether a report after the attempt shows its result, and what it shows, in words."""
    from app.services import phone_state

    if row.observe is None:
        return (row.status == "achieved", "the device's own answer is the evidence")
    try:
        state = phone_state.read(user_id)
    except Exception:  # noqa: BLE001 - unknown is never fresh
        state = None
    value = state.values.get(row.observe) if state is not None else None
    issued = row.issued_at.timestamp()
    if value is None or value.as_of <= issued:
        return False, f"no report of {row.observe} from the phone since the attempt"
    if row.observe == "media":
        # Where in the track only from a report after the attempt (a restart shows as the track near its start).
        position = state.media_position if state.media_position and state.media_position["at"] > issued else None
        shown = (phone_state.now_playing(value.value, position) if isinstance(value.value, dict)
                 else "nothing playing")
    else:
        shown = f"{row.observe} {value.value}"
    after_s = int(value.as_of - issued)
    return True, f"the phone reported {row.observe} {after_s} s after the attempt: {shown}"


SETTLED = {"achieved": "achieved", "not_achieved": "failed", "unresolved": "unresolved"}


def settle(ev, attempt: str, outcome: str, evidence: str) -> str:
    """Her judgement of how an attempt turned out, only on a fresh report (or the device's own answer):
    ``achieved`` / ``not_achieved``, or ``unresolved`` when what it shows can't tell. Without one there's nothing to
    judge yet: its checks wait for it, and settling would cancel them (it becomes unresolved on its own if no report
    ever comes). Never closes the promise itself."""
    from app.services import coordinator

    follow = of(ev)
    if follow is None:
        return "Not done: this wake isn't following anything through."
    if outcome not in SETTLED:
        return "Not done: outcome is achieved, not_achieved or unresolved."
    why = " ".join((evidence or "").split())[:300]
    if not why:
        return "Not done: say what shows it."
    key = (attempt or "").strip("[] ").lower()
    with SessionLocal() as db:
        _locked_item(db, ev.user_id, follow.item_id)  # the item first, as everywhere
        rows = [a for a in db.scalars(select(FollowThroughAttempt).where(
            FollowThroughAttempt.user_id == ev.user_id, FollowThroughAttempt.item_id == follow.item_id,
            FollowThroughAttempt.status.in_(OPEN)).with_for_update()) if str(a.id).startswith(key)]
        if len(key) < 6 or len(rows) != 1:
            return "Not done: no attempt of yours for this still waiting with that id."
        row = rows[0]
        seen, shown = fresh(ev.user_id, row)
        if not seen:
            return (f"Not settled: {shown}, so there's nothing to judge yet. Leave it pending: its check reads the "
                    "next report and wakes you; if none ever comes it becomes unresolved on its own.")
        # Her reading, with what the record showed when she gave it (the fresh report, or that there was none).
        row.status, row.settled_at, row.settled_reason = SETTLED[outcome], _now(), f"{why} [{shown}]"[:600]
        for check in db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == row.user_id, CoordinatorWork.kind == "wake",
                CoordinatorWork.status.in_(coordinator.ACTIVE),
                CoordinatorWork.input["follow_check"].astext == str(row.id)).with_for_update()):
            check.status, check.error_code = "cancelled", "settled"  # moved ones too: they keep the attempt's id
            check.cancellation_generation += 1
        db.commit()
        _log({"op": "settled", "attempt": str(row.id), "outcome": row.status})
        left = MAX_PER_CHANGE - _tried(db, row)
    if outcome == "achieved":
        return ("Settled: achieved. The promise itself stays as it is (open while it still holds; update_item "
                "when it's done or they let it go).")
    if outcome == "not_achieved":
        return (f"Settled: not achieved. {left} more {'try' if left == 1 else 'tries'} for this change; if your tools "
                "can't do it, tell them what you couldn't do.")
    return "Settled: unresolved. Don't say it worked; nothing more follows from this attempt unless you act again."


# --------------------------------------------------------------------------- #
# What the evaluation reads
# --------------------------------------------------------------------------- #

def lines(db, user_id: uuid.UUID, follow: Follow, *, act: str | None = None, say: str | None = None) -> list[str]:
    """The item, what made it due, her attempts for it and what fresh evidence shows: every line from a record."""
    entry = db.get(WorkingItem, follow.item_id)
    if entry is None or entry.user_id != user_id:
        return ["FOLLOWING THROUGH", "- The item it was about can no longer be read: nothing to carry out."]
    state = entry.status + (f": {entry.status_reason}" if entry.status_reason else "")
    when = f" (when: {entry.trigger['text']})" if (entry.trigger or {}).get("text") else ""
    out = ["FOLLOWING THROUGH",
           f"- {'Your promise' if entry.kind == 'commitment' else 'Your intention'} [{_short(entry.id)}]: "
           f"\"{entry.text}\"{when}; {'still open' if entry.closed_at is None else 'CLOSED'} ({state})."]
    if act:
        out.append(f"- What your background thinking saw: {act}")
    if say:
        out.append(f"- A line it thought of saying (yours to use or not): {say}")
    rows = list(db.scalars(select(FollowThroughAttempt).where(
        FollowThroughAttempt.user_id == user_id, FollowThroughAttempt.item_id == entry.id)
        .order_by(FollowThroughAttempt.issued_at.desc()).limit(5)))
    if not rows:
        out.append("- Nothing attempted for it yet.")
    for a in rows:
        this = " (the one this check is about)" if follow.check == a.id else ""
        line = f"- Attempt [{_short(a.id)}]{this}: {describe(a)} at {a.issued_at:%H:%M:%S} UTC: {a.status}"
        if a.result:
            line += f"; the device answered \"{a.result[:160]}\""
        if a.settled_reason:
            line += f"; {a.settled_reason[:200]}"
        if a.status in OPEN:
            seen, shown = fresh(user_id, a)
            line += f"; {shown}" + ("" if seen else " (so its result isn't known)")
            if a.checks > len(CHECK_DELAYS) or (a.checks == len(CHECK_DELAYS) and a.observe is None):
                line += "; this is its last check: if you leave it pending it becomes unresolved"
            elif a.checks == len(CHECK_DELAYS):
                line += (f"; its last timed check: if you leave it pending, the phone's next report of {a.observe} "
                         f"(within {int(AWAIT_READING.total_seconds() // 60)} min of the attempt) wakes you once more")
        out.append(line)
    current = next((a for a in rows if a.status != "cancelled"), None)
    if current is not None and current.status != "achieved":
        tried = sum(1 for a in rows if a.transition == current.transition and a.status != "cancelled")
        out.append(f"- Tries while it hasn't been as they asked: {tried} of {MAX_PER_CHANGE} (a change elsewhere on the "
                   "phone doesn't add more).")
    return out


def render(follow: Follow, extra_lines: list[str]) -> list[str]:
    """Why she's awake, for a follow-through (``coordinator.render_wake``)."""
    if follow.check is None:
        head = [f"Something you promised or meant to do [{_short(follow.item_id)}] may be due now: your background "
                "thinking noticed a change (not a user request)."]
    else:
        head = [f"A check on your attempt [{_short(follow.check)}] for [{_short(follow.item_id)}] came due."]
    return [*head, *extra_lines,
            "Read it against \"Now\" and the conversation: if they cancelled or changed it, their latest word stands; "
            "if it's already as they want, do nothing (stay_silent). Otherwise do what your tools here allow. A "
            "command the device takes isn't the result: only a fresh report is. Judge a pending attempt with "
            "settle_attempt only on a fresh report (achieved / not_achieved, or unresolved when it can't tell); "
            "without one, leave it pending for its check. If your tools "
            "can't do what they asked, tell them so instead of trying something else. Never say it worked without a "
            "fresh report showing it."]


def item_closed(db, entry: WorkingItem) -> int:
    """The item closed: its attempts still waiting are cancelled, and their checks with them. The caller commits."""
    from app.services import coordinator

    now = _now()
    rows = list(db.scalars(select(FollowThroughAttempt).where(
        FollowThroughAttempt.user_id == entry.user_id, FollowThroughAttempt.item_id == entry.id,
        FollowThroughAttempt.status.in_(OPEN)).with_for_update()))
    for row in rows:
        row.status, row.settled_at = "cancelled", now
        row.settled_reason = f"the item closed ({entry.status})"
    for work in db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.user_id == entry.user_id, CoordinatorWork.kind == "wake",
            CoordinatorWork.status.in_(coordinator.ACTIVE),
            CoordinatorWork.input["follow_item"].astext == str(entry.id)).with_for_update()):
        work.status, work.error_code = "cancelled", "item_closed"
        work.cancellation_generation += 1
    return len(rows)


def _log(entry: dict) -> None:
    from app.services import coordinator

    coordinator._log({**entry, "part": "follow_through"})
