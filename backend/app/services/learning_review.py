"""Review opportunities: code schedules them, the reviewer judges them
(docs/learning-system/POST-AUDIT-03-broaden-experience-understanding.md; implementation:
docs/learning-system/POST-AUDIT-03-implementation.md).

An opportunity is a ``review`` assessment in the shared lifecycle (``services/learning_assessment.py``): one row
per stretch of activity, which the reviewer reads and says ``examine``, ``wait`` or ``leave``. Code decides only
*when* there is something new to look at, never whether it matters:

- **Conversation** (``conversation``): every completed exchange (a user message after a reply of hers) extends the
  conversation's open review stretch, or starts the next one. ``sweep`` adds the fallback: her last reply in a
  conversation the user has been quiet in for ``LEARNING_REVIEW_PAUSE_SECONDS``. Corrections, complaints and
  memory's corrections are evidence signals on the stretch, not reasons of their own.
- **Games** (``game``): a session's transition (paused, ended, failed, disconnected) and, while it runs, new
  experience older than ``LEARNING_REVIEW_GAME_SECONDS`` (``sweep``), so a pattern can be read before the session
  ends. Failed skills and critical notices keep their own immediate triggers (``learning_assessment.triage``).
- **Her mind** (``nominate``): a hard thought that names something worth learning from points the reviewer at the
  activity it was about. The pointer is shown as a signal: her thought is never evidence.

A stretch stays open while it is unread or parked (left, waiting, stuck, missing evidence): new activity extends it
and resumes it, keeping its inquiry. A concluded stretch is never extended; the next activity starts a new one. A
stretch is bounded for transport (``LEARNING_REVIEW_STRETCH_MESSAGES`` messages, ``GAME_PAGE`` experiences), which
is not an experience boundary: the reviewer can look earlier, ask memory, and reopen other inquiries.

``propose`` hands a reviewer's follow-up (a question, an observation or practice that could settle what records
can't) to memory as an open item of hers: her mind sees it and decides whether and when. Learning never asks.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import Integer, cast, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    ChatMessage, Conversation, DesktopSession, GameExperience, LearningAssessment,
    LearningReset, WorkingItem,
)

log = logging.getLogger(__name__)
GAME_PAGE = 100  # experiences one game stretch carries (transport, not an experience boundary)
FOLLOW_UP_KIND = {"question": "question", "observation": "hypothesis", "practice": "hypothesis"}
# Kinds of stretch outcome that keep a stretch open to more of the activity.
CLOSED = ("concluded",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(db: Session, key: str) -> None:
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))


def _open(row: LearningAssessment | None) -> bool:
    """Whether a stretch can take more of the activity: never read yet, or parked."""
    return row is not None and row.trigger_kind == "review" and row.outcome not in CLOSED


# --------------------------------------------------------------------------- #
# Conversation
# --------------------------------------------------------------------------- #


def latest_conversation_review(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, *,
                               lock: bool = False) -> LearningAssessment | None:
    query = select(LearningAssessment).where(
        LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
        LearningAssessment.trigger_kind == "review", LearningAssessment.game_session_id.is_(None),
    ).order_by(cast(LearningAssessment.trigger["up_to_seq"].astext, Integer).desc(),
               LearningAssessment.created_at.desc()).limit(1)
    return db.scalar(query.with_for_update() if lock else query)


def conversation(db: Session, user_id: uuid.UUID, message: ChatMessage, *, why: str,
                 evidence: Iterable[tuple[str, str, dict]] = ()) -> LearningAssessment:
    """Open or extend the conversation's review stretch up to ``message`` (in the caller's transaction).
    Retry safe: a message already covered only attaches what's new."""
    from app.services import learning_assessment as LA

    conversation_id, seq = message.conversation_id, message.conversation_sequence
    _lock(db, f"review:{conversation_id}")
    now = _now()
    refs = [(f"message:{message.id}", why, {}), *evidence]
    current = latest_conversation_review(db, user_id, conversation_id, lock=True)
    if current is not None and int((current.trigger or {}).get("up_to_seq") or 0) >= seq:
        # Already covered (a retry, a late signal, a nomination): attach to the stretch the message is in.
        holder = db.scalar(select(LearningAssessment).where(
            LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
            LearningAssessment.trigger_kind == "review", LearningAssessment.game_session_id.is_(None),
            cast(LearningAssessment.trigger["from_seq"].astext, Integer) <= seq,
            cast(LearningAssessment.trigger["up_to_seq"].astext, Integer) >= seq,
        ).order_by(LearningAssessment.created_at.desc()).limit(1).with_for_update()) or current
        added = LA.attach(db, holder, refs)
        if added:
            LA._reopen(holder, now, f"new evidence: {', '.join(added)}")
        return holder
    stretch = get_settings().learning_review_stretch_messages
    if _open(current) and seq - int(current.trigger.get("from_seq") or seq) < stretch:
        current.trigger = {**current.trigger, "up_to_seq": seq, "up_to": str(message.id),
                           "why": current.trigger.get("why") or why}
        added = LA.attach(db, current, refs)
        LA._reopen(current, now, f"more of the conversation: {', '.join(added) or 'm' + str(seq)}")
        return current
    start = int(current.trigger.get("up_to_seq") or 0) + 1 if current is not None else seq
    row, _ = LA.open_assessment(
        db, user_id=user_id, kind="review", key=f"review:{conversation_id}:{message.id}", anchor_at=message.created_at,
        conversation_id=conversation_id, anchor_message_id=message.id,
        trigger={"why": why, "from_seq": min(start, seq), "up_to_seq": seq, "up_to": str(message.id)},
        evidence=refs)
    return row


def sweep_pauses(db: Session, now: datetime, horizon: datetime, *, limit: int = 50) -> int:
    """Her last reply in a conversation the user has been quiet in: the fallback opportunity for it (a reply that
    didn't complete included). Commits nothing."""
    quiet = now - timedelta(seconds=get_settings().learning_review_pause_seconds)
    covered = exists().where(
        LearningAssessment.user_id == Conversation.user_id,
        LearningAssessment.conversation_id == ChatMessage.conversation_id,
        LearningAssessment.trigger_kind == "review",
        cast(LearningAssessment.trigger["up_to_seq"].astext, Integer) >= ChatMessage.conversation_sequence)
    reset = exists().where(LearningReset.user_id == Conversation.user_id, LearningReset.cutoff_at > ChatMessage.created_at)
    rows = list(db.execute(select(ChatMessage, Conversation.user_id).join(
        Conversation, Conversation.id == ChatMessage.conversation_id).where(
        ChatMessage.role == "assistant", ChatMessage.conversation_sequence == Conversation.last_message_sequence,
        ChatMessage.created_at >= horizon, ChatMessage.created_at <= quiet, ~covered, ~reset,
    ).order_by(ChatMessage.created_at).limit(limit)))
    done = 0
    for message, user_id in rows:
        with db.begin_nested():
            conversation(db, user_id, message, why="the user has been quiet since her reply")
        done += 1
    return done


# --------------------------------------------------------------------------- #
# Games
# --------------------------------------------------------------------------- #


def _stretches(db: Session, session: DesktopSession) -> list[LearningAssessment]:
    return list(db.scalars(select(LearningAssessment).where(
        LearningAssessment.user_id == session.owner_user_id, LearningAssessment.game_session_id == session.id,
        LearningAssessment.trigger_key.like(f"stretch:{session.id}:%"))))


def game(db: Session, session: DesktopSession, *, why: str,
         evidence: Iterable[tuple[str, str, dict]] = ()) -> list[LearningAssessment]:
    """Experience recorded since the last stretch, as review opportunities (in the caller's transaction): the open
    stretch takes it while it has room, then new stretches of at most GAME_PAGE. ``evidence`` (a nomination) goes
    on the stretch the newest experience is in; with no new experience, on the latest stretch. Session-locked."""
    from app.services import learning_assessment as LA
    from app.services.game_experience import game_for_profile
    from app.services.memory_reset import learning_cutoff

    if game_for_profile(session.profile_id) is None:
        return []
    db.scalar(select(DesktopSession).where(DesktopSession.id == session.id).with_for_update())
    previous = _stretches(db, session)
    after = max((int((r.trigger or {}).get("up_to") or 0) for r in previous), default=0)
    cutoff = learning_cutoff(db, session.owner_user_id)
    fresh = list(db.scalars(select(GameExperience).where(
        GameExperience.user_id == session.owner_user_id, GameExperience.session_id == session.id,
        GameExperience.id > after, GameExperience.occurred_at >= cutoff).order_by(GameExperience.id)))
    evidence = list(evidence)
    now = _now()
    latest = max(previous, key=lambda r: int((r.trigger or {}).get("up_to") or 0), default=None)
    touched: list[LearningAssessment] = []
    if not fresh:
        if evidence and latest is not None:
            locked = db.scalar(select(LearningAssessment).where(LearningAssessment.id == latest.id).with_for_update())
            added = LA.attach(db, locked, evidence)
            if added:
                LA._reopen(locked, now, f"new evidence: {', '.join(added)}")
            touched.append(locked)
        return touched
    if _open(latest):
        current = db.scalar(select(LearningAssessment).where(LearningAssessment.id == latest.id).with_for_update())
        room = GAME_PAGE - int(current.trigger.get("count") or 0)
        if room > 0:
            part, fresh = fresh[:room], fresh[room:]
            current.trigger = {**current.trigger, "up_to": part[-1].id, "count": int(current.trigger.get("count") or 0)
                               + len(part), "until": (LA._aware(part[-1].occurred_at) + timedelta(seconds=5)).isoformat()}
            LA.attach(db, current, [(f"experience:{e.id}", "part of this stretch", {}) for e in part]
                      + (evidence if not fresh else []))
            LA._reopen(current, now, f"more play: {len(part)} new experiences")
            touched.append(current)
            after = part[-1].id
    for start in range(0, len(fresh), GAME_PAGE):
        segment = fresh[start:start + GAME_PAGE]
        end = segment[-1]
        last = start + GAME_PAGE >= len(fresh)
        row, _ = LA.open_assessment(
            db, user_id=session.owner_user_id, kind="review", key=f"stretch:{session.id}:{end.id}",
            anchor_at=end.occurred_at, conversation_id=session.conversation_id, game_session_id=session.id,
            trigger={"why": why, "from": after, "up_to": end.id, "count": len(segment),
                     "since": LA._aware(segment[0].occurred_at).isoformat(),
                     "until": (LA._aware(end.occurred_at) + timedelta(seconds=5)).isoformat()},
            evidence=[(f"experience:{e.id}", "part of this stretch", {}) for e in segment]
            + (evidence if last else []))
        touched.append(row)
        after = end.id
    from app.services import system_signals
    system_signals.emit(session.owner_user_id, "hands", "learning", "stretch", session.id)
    return touched


def sweep_games(db: Session, now: datetime, *, limit: int = 20) -> int:
    """Running game sessions whose new experience has waited ``LEARNING_REVIEW_GAME_SECONDS``: reviewed while
    play goes on, not only when the session ends. Commits nothing."""
    from app.services.game_experience import game_for_profile

    wait = timedelta(seconds=get_settings().learning_review_game_seconds)
    sessions = list(db.scalars(select(DesktopSession).where(DesktopSession.state == "running")
                               .order_by(DesktopSession.started_at).limit(limit)))
    done = 0
    for session in sessions:
        if game_for_profile(session.profile_id) is None:
            continue
        after = max((int((r.trigger or {}).get("up_to") or 0) for r in _stretches(db, session)), default=0)
        oldest = db.scalar(select(func.min(GameExperience.occurred_at)).where(
            GameExperience.user_id == session.owner_user_id, GameExperience.session_id == session.id,
            GameExperience.id > after))
        if oldest is None or (oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)) > now - wait:
            continue
        with db.begin_nested():
            if game(db, session, why="ongoing play: interpret what is developing before the session ends"):
                done += 1
    return done


# --------------------------------------------------------------------------- #
# Her mind's nominations
# --------------------------------------------------------------------------- #


def nominate(db: Session, user_id: uuid.UUID, *, source: str, activity: uuid.UUID, thought_id: uuid.UUID,
             text: str) -> bool:
    """Her mind pointed at something worth learning from in an activity. Attaches the pointer to that activity's
    review (opening one when needed). Runs in a savepoint; never fails the caller. Returns whether it was taken."""
    words = " ".join((text or "").split())[:500]
    if not words:
        return False
    evidence = [(f"nomination:{thought_id}", "her mind pointed at this", {"text": words})]
    try:
        with db.begin_nested():
            if source == "security":
                # A security investigation: its own conversation, with the investigation attached as evidence.
                from app.db.models import SecurityInvestigation

                investigation = db.get(SecurityInvestigation, activity)
                if investigation is None or investigation.user_id != user_id or investigation.conversation_id is None:
                    return False
                evidence.append((f"security:{investigation.id}", "the security records her mind looked into",
                                 {"revision": investigation.revision}))
                source, activity = "conversation", investigation.conversation_id
            if source == "conversation":
                last = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == activity)
                                 .order_by(ChatMessage.conversation_sequence.desc()).limit(1))
                owner = db.scalar(select(Conversation.user_id).where(Conversation.id == activity))
                if last is None or owner != user_id:
                    return False
                conversation(db, user_id, last, why="her mind pointed at something worth learning from",
                             evidence=evidence)
                return True
            session = db.get(DesktopSession, activity)
            if session is None or session.owner_user_id != user_id:
                return False
            return bool(game(db, session, why="her mind pointed at something worth learning from",
                             evidence=evidence))
    except Exception:  # noqa: BLE001 - a nomination is never worth failing a thought for
        log.exception("learning review: a nomination from %s %s was not taken", source, activity)
        return False


# --------------------------------------------------------------------------- #
# Follow-up proposals: to memory, for her mind
# --------------------------------------------------------------------------- #


def propose(db: Session, row: LearningAssessment, revision: int, follow_up: dict, inquiry: dict) -> str | None:
    """A reviewer's follow-up as an open item of hers (a question to ask, or a hypothesis to check by observation
    or practice), so her mind can take it up or not. One per assessment while it stays open. In the caller's
    transaction (a savepoint). Returns the item ref, or None."""
    from app.services import memory_manager

    if not follow_up.get("propose") or not follow_up.get("text"):
        return None
    marker = f"learning follow-up (assessment {row.id})"
    kind = FOLLOW_UP_KIND.get(follow_up.get("kind") or "question", "question")
    existing = db.scalar(select(WorkingItem).where(
        WorkingItem.user_id == row.user_id, WorkingItem.closed_at.is_(None), WorkingItem.kind == kind,
        WorkingItem.reason.like(f"{marker}%")).limit(1))
    if existing is not None:
        return f"item:{existing.id}"
    alternatives = "; ".join(inquiry.get("alternatives") or [])
    reason = (f"{marker}, revision {revision}: it could resolve {follow_up.get('resolves') or 'what records leave open'}"
              + (f"; other explanations: {alternatives}" if alternatives else "")
              + ". A proposal: whether and when is her choice.")[:1000]
    try:
        with db.begin_nested():
            context_id = None
            if row.game_session_id is not None:
                session = db.get(DesktopSession, row.game_session_id)
                if session is not None:
                    context_id = memory_manager.game_context(db, session).id
            entry = memory_manager.add_item(db, row.user_id, kind=kind, text_=follow_up["text"], owner="columbina",
                                            epistemic="inferred", context_id=context_id, reason=reason)
            return f"item:{entry.id}"
    except Exception:  # noqa: BLE001 - a proposal that can't be kept is logged, never a failed assessment
        log.exception("learning review: follow-up of assessment %s was not kept", row.id)
        return None


__all__ = ["conversation", "game", "nominate", "propose", "sweep_pauses", "sweep_games",
           "latest_conversation_review", "GAME_PAGE"]
