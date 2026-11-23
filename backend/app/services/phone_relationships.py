"""Her relationships with people on the phone, in shadow (affinity B2: docs/PHONE_PHASE_8_AFFINITY_B2.md).

Builds on B1 (services/phone_people.py: people, conversations, her outbound replies). Code supplies facts and
enforces audience boundaries; her mind judges who was addressed, what an exchange meant and whether anything changed.

- **What she can read of a conversation** (``read_conversation``): the messages the phone shows now for that
  conversation (the notification carries the last few: nothing earlier is available), each with who wrote it (a
  ``person:`` handle), whether an ``owner`` message is her own reply come back (B1's echo match) or the phone's
  (the user's, or hers unconfirmed), what it replies to when the phone says, and what she has sent there through
  a reply button. Unknowns are said: a message in a direct chat on the user's account can be for them or for her,
  and nothing here decides which.
- **Her reading of an exchange** (``note_exchange``): the messages it rests on (their ``msg:`` handles), who she
  judged they were addressed to, a short observation scoped to that conversation, the person's exact words when it
  rests on a statement, and the conversations those words explicitly name. One record per set of events
  (``PhoneExchange.event_key``): a replayed notification has one effect. In the same transaction, under the
  affinity row's lock:
  - **relationship proposals** (shadow): the fields she judged changed, each a ``PhoneRelationshipEvidence`` row
    with its value, observation, exchange and audience; the shadow view (``PhoneAffinityShadow``) is recomputed
    from the rows not retracted, so concurrent changes to different fields both stand. Familiarity moves past
    "new" only once they have spoken: she has written where they are and they have addressed her (her own words
    alone never make it mutual). A message to the user can shape her impression; it never makes them familiar.
  - **a personal lesson**, routed to the learner (``learning_assessment`` ``phone`` trigger, then
    ``learning_lessons`` and ``memory_lessons``) only when she took part in that conversation and judged they were
    talking to her; otherwise skipped, with why. The lesson is bound to the person, the conversation and the
    audiences their words named, and stays shadow (``memory_lessons.PhoneScope``).
  Nothing here is read by her active relationship record, ``read_person``, her prompts, her lessons or scheduling.
- **Requests for space** (``note_request_for_space``): the person's exact words, active at once, kept apart from
  proposals and the note (a later note rewrite never touches it). ``everywhere`` or the conversation it was said in,
  and a topic when they named one (other topics stay open). She reads them (``read_person``) and so does the check
  on a reply before it goes out (``phone_reply_check``); no code matches topics.
- **What the user authorized** (``authorize_detail``): a detail for one person in one conversation; a direct chat's
  authorization never covers a group.
- **Inspection and correction** (``review`` / ``retract``): ``GET /api/memory/people/{id}/review``. Deleting a person
  (B1 ``delete_person``) removes every record here, their phone assessments and lessons (cascade).

Review records keep a short scoped observation and references, never the notification's other text; long digit runs
(codes, account numbers) are refused in quotes and masked in observations.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import (
    LearningAssessment, MemoryLesson, PhoneAffinity, PhoneAffinityShadow, PhoneContactBoundary, PhoneConversation,
    PhoneConversationMember, PhoneDisclosure, PhoneExchange, PhoneOutbound, PhonePerson, PhonePersonKey,
    PhoneRelationshipEvidence,
)
from app.db.session import SessionLocal
from app.services import phone_notifications, phone_people, phone_state

log = logging.getLogger(__name__)

EVENT_PREFIX = "msg:"
OBSERVATION_CHARS = 300
WORDS_CHARS = 300
TOPIC_CHARS = 120
DETAIL_CHARS = 500
TEXT_CHARS = 400
EXCHANGES_READ = 6  # earlier readings of a conversation shown to her and to the learner
SENT_READ = 6
DIMENSIONS = ("familiarity", "warmth", "initiative", "note")
ADDRESSED = {"you": "her", "the_user": "owner", "everyone": "group", "unclear": "unclear"}
ADDRESSED_WORDS = {"her": "to you", "owner": "to the user", "group": "to everyone there",
                   "unclear": "unclear who it was for"}
DIGITS = re.compile(r"\d(?:[\s\-.]?\d){3,}")  # four digits or more: a code, an account or card number


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: str | None, limit: int) -> str:
    words = " ".join((value or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _masked(value: str) -> str:
    return DIGITS.sub("[number]", value)


def _words(text: str) -> set[str]:
    from app.services.long_term_memory import _content_words

    return {w for w in _content_words(text or "") if len(w) >= 4}


def _mentions(topic: str, text: str) -> bool:
    """Whether ``text`` touches ``topic``: a content word of it, or (a topic of short words) the topic itself."""
    words = _words(topic)
    return bool(words & _words(text)) if words else _norm(topic) in _norm(text)


def event_handle(conversation_id: str | None, event_id: str) -> str:
    """The short handle she sees for one message ("msg:1a2b3c4d"): a hash, the phone's ids stay out of prompts."""
    return EVENT_PREFIX + hashlib.sha256(f"{conversation_id}\x00{event_id}".encode()).hexdigest()[:8]


def event_key(events: Iterable[str]) -> str:
    return hashlib.sha256("\x00".join(sorted(set(events))).encode()).hexdigest()[:40]


def _message_time(message: dict) -> datetime | None:
    raw = message.get("at")
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# A conversation as the phone shows it now
# --------------------------------------------------------------------------- #

@dataclass
class Line:
    handle: str
    event_id: str
    sender_id: str | None
    sender: str | None
    text: str
    at: datetime | None
    reply_to: str | None  # the handle of the message it replies to, when the phone says
    from_phone: bool  # sender "owner": written on the phone itself
    hers: bool = False  # an owner message matched to one of her sends
    tied: bool = False  # newest only together with another message at the same time (phone_reply_check._newest)


def _lines(user_id: uuid.UUID, notifications: list) -> list[Line]:
    """The messages of these notifications (one conversation), oldest first, each once."""
    seen: dict[str, Line] = {}
    for n in notifications:
        for m in n.messages:
            event = phone_people.echo_id(m)
            if event in seen:
                continue
            reply = m.get("reply_to")
            seen[event] = Line(event_handle(n.conversation_id, event), event, m.get("sender_id"), m.get("sender"),
                               m.get("text") or "", _message_time(m),
                               event_handle(n.conversation_id, str(reply)) if reply else None,
                               m.get("sender_id") == phone_people.OWNER_SENDER)
    lines = list(seen.values())
    mine = phone_people.echoed(user_id, [line.event_id for line in lines if line.from_phone])
    for line in lines:
        line.hers = line.event_id in mine
    return sorted(lines, key=lambda line: (line.at is None, line.at or datetime.min.replace(tzinfo=timezone.utc)))


def _conversation_notifications(user_id: uuid.UUID, handle: str) -> tuple[object | None, list]:
    """The notification she named and every live one of the same conversation (usually just it)."""
    n = phone_notifications.find(user_id, handle)
    if n is None or not n.conversation_id:
        return n, [n] if n is not None else []
    same = [x for x in phone_notifications.live(user_id) or [] if x.conversation_id == n.conversation_id]
    return n, same or [n]


def read_conversation(user_id: uuid.UUID, handle: str, *, now: float | None = None) -> str:
    """What she can read of the conversation behind a notification: its messages with who wrote them, her own
    replies (confirmed or not), what each replies to when known, what she sent there, and her earlier readings."""
    now = time.time() if now is None else now
    n, same = _conversation_notifications(user_id, handle)
    if n is None:
        return f"No notification [{_clean(handle, 20)}] is on their phone now (gone, or never there)."
    lines = _lines(user_id, same)
    where = phone_notifications.who(n)
    out = [f"Conversation behind [{n.ref}]: {where}"
           + (", a group" if n.is_group else ", a direct chat on their account")
           + (", can be replied to" if n.can_reply else "") + f" ({phone_notifications.THIRD_PARTY}).",
           "What the phone shows now (a notification carries only the latest few messages: nothing earlier is "
           "available here):"]
    people = phone_people.brackets(user_id, [(n.conversation_id, line.sender_id) for line in lines
                                             if line.sender_id and not line.from_phone], at=now)
    for line in lines:
        when = f"{phone_state.span(now - line.at.timestamp())} ago" if line.at else "time not given"
        if line.from_phone:
            who = ("you (your reply through a reply button, come back)" if line.hers else
                   "written on their phone (the user's own reply, or yours not confirmed: never assume it's yours)")
        else:
            who = f'"{_clean(line.sender, 40)}"' + (f" [{people[(n.conversation_id, line.sender_id)]}]"
                                                    if (n.conversation_id, line.sender_id) in people else "")
        reply = f", replying to [{line.reply_to}]" if line.reply_to else ""
        out.append(f"- [{line.handle}] {when}, {who}{reply}: \"{_clean(line.text, TEXT_CHARS)}\"")
    if not lines:
        out.append("- no messages (not a chat notification)")
    out.append("Not known from this: who each message is addressed to. On their account a message can be for them "
               "or for you; only the words, and who has been talking with whom, can say."
               + (" In a group, a message may be for anyone there." if n.is_group else ""))
    try:
        with SessionLocal() as db:
            sent = db.scalars(select(PhoneOutbound).where(
                PhoneOutbound.user_id == user_id, PhoneOutbound.conversation_id == n.conversation_id,
                PhoneOutbound.status.in_(("sent", "unknown"))).order_by(PhoneOutbound.created_at.desc())
                .limit(SENT_READ)).all() if n.conversation_id else []
            conversation = db.scalar(select(PhoneConversation).where(
                PhoneConversation.user_id == user_id, PhoneConversation.conversation_id == n.conversation_id)) \
                if n.conversation_id else None
            readings = db.scalars(select(PhoneExchange).where(
                PhoneExchange.user_id == user_id, PhoneExchange.conversation_id == conversation.id)
                .order_by(PhoneExchange.at.desc()).limit(EXCHANGES_READ)).all() if conversation else []
    except SQLAlchemyError:
        log.warning("phone_relationships: a conversation's records could not be read", exc_info=True)
        out.append("What you sent here and your earlier readings couldn't be read just now (don't assume none).")
        return "\n".join(out)
    if sent:
        out.append("What you've sent here through a reply button (newest first):")
        out += [f"- {phone_state.span(now - row.created_at.timestamp())} ago: \"{_clean(row.text, 200)}\""
                + (" (it may not have arrived)" if row.status == "unknown" else "") for row in sent]
    else:
        out.append("You haven't sent anything here.")
    if readings:
        out.append("Your earlier readings of this conversation (newest first):")
        out += [f"- {phone_state.span(now - r.at.timestamp())} ago, {ADDRESSED_WORDS[r.addressed]}: {r.observation}"
                for r in readings]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Her reading of an exchange: shadow proposals, and a personal lesson
# --------------------------------------------------------------------------- #

class Refused(ValueError):
    """What she asked can't be recorded as asked: said back to her in plain words."""


@dataclass
class Cited:
    notification: object
    lines: list[Line]
    sender_id: str


def _cited(user_id: uuid.UUID, handle: str, about: list[str]) -> Cited:
    n, same = _conversation_notifications(user_id, handle)
    if n is None:
        raise Refused(f"no notification [{_clean(handle, 20)}] is on their phone now")
    if not n.conversation_id:
        raise Refused("that notification has no conversation the phone can tell apart")
    by_handle = {line.handle: line for line in _lines(user_id, same)}
    wanted = [h.strip().strip("[]").lower() for h in about or [] if h and h.strip()]
    if not wanted:
        raise Refused("name the messages it rests on (their msg: handles, from read_phone_conversation)")
    lines, unknown = [], []
    for h in dict.fromkeys(wanted):
        h = h if h.startswith(EVENT_PREFIX) else EVENT_PREFIX + h
        (lines.append(by_handle[h]) if h in by_handle else unknown.append(h))
    if unknown:
        raise Refused(f"{', '.join(unknown)} isn't a message of that conversation on the phone now")
    senders = {line.sender_id for line in lines if not line.from_phone and line.sender_id}
    if len(senders) != 1:
        raise Refused("it must rest on messages from one person (one sender besides the phone's own): note each "
                      "person's part on its own" if senders else
                      "none of those messages is from someone else (the phone's own messages can't be someone's)")
    return Cited(n, lines, senders.pop())


def _their_words(cited: Cited, words: str | None) -> str | None:
    words = _clean(words, WORDS_CHARS) or None
    if words is None:
        return None
    theirs = [line for line in cited.lines if line.sender_id == cited.sender_id]
    if not any(_norm(words) in _norm(line.text) for line in theirs):
        raise Refused("their_words must be their exact words, from one of the messages it rests on")
    if DIGITS.search(words):
        raise Refused("their_words has a long number in it (a code or account number is never kept): quote "
                      "without it")
    return words


def _resolve_records(db: Session, user_id: uuid.UUID, cited: Cited) -> tuple[PhonePerson, PhoneConversation,
                                                                            PhoneAffinity]:
    """The person, the conversation and the affinity row (locked), as B1 recorded them. A missing one is a
    lookup failure: nothing is created and nothing changes."""
    key = db.scalar(select(PhonePersonKey).where(PhonePersonKey.user_id == user_id,
                                                 PhonePersonKey.sender_id == cited.sender_id))
    person = db.get(PhonePerson, key.person_id) if key is not None else None
    conversation = db.scalar(select(PhoneConversation).where(
        PhoneConversation.user_id == user_id,
        PhoneConversation.conversation_id == cited.notification.conversation_id))
    if person is None or person.status != "active" or conversation is None:
        raise Refused("who wrote isn't recorded yet (couldn't look them up): nothing was changed")
    affinity = db.scalar(select(PhoneAffinity).where(PhoneAffinity.user_id == user_id,
                                                     PhoneAffinity.person_id == person.id).with_for_update())
    if affinity is None:
        raise Refused("their relationship record couldn't be read: nothing was changed")
    return person, conversation, affinity


def _sent_before(user_id: uuid.UUID, conversation_id: str, at: datetime):
    """Her replies that count as taking part in an exchange at ``at``: sent in that conversation before it (the
    phone's clock against ours allowed for). The one cutoff for eligibility and for what the learner is shown."""
    return (PhoneOutbound.user_id == user_id, PhoneOutbound.conversation_id == conversation_id,
            PhoneOutbound.status == "sent", PhoneOutbound.created_at <= at + phone_people.CLOCK_SKEW)


def _participated(db: Session, user_id: uuid.UUID, conversation: PhoneConversation, before: datetime) -> bool:
    """She had written in this conversation (a reply that was sent) before these messages: a message from before
    her first reply there is not an exchange she took part in."""
    return bool(db.scalar(select(exists().where(*_sent_before(user_id, conversation.conversation_id, before)))))


def _spoken(db: Session, user_id: uuid.UUID, person: PhonePerson, *, addressed_now: bool) -> bool:
    """They have spoken: she has written where they are, and they have addressed her (now or before). Her own
    outgoing words alone never count."""
    shared = select(PhoneConversation.conversation_id).join(
        PhoneConversationMember, PhoneConversationMember.conversation_id == PhoneConversation.id).where(
        PhoneConversationMember.person_id == person.id)
    wrote = db.scalar(select(exists().where(PhoneOutbound.user_id == user_id, PhoneOutbound.status == "sent",
                                            PhoneOutbound.conversation_id.in_(shared))))
    addressed = addressed_now or db.scalar(select(exists().where(PhoneExchange.person_id == person.id,
                                                                 PhoneExchange.addressed == "her")))
    return bool(wrote and addressed)


def _audiences(db: Session, person: PhonePerson, here: PhoneConversation, words: str | None,
               names: list[str]) -> tuple[list[str], list[str]]:
    """The other conversations the person's own words explicitly cover: each must be one they share with the user,
    named in their words. Returns (conversation pks, refusals)."""
    out, refused = [], []
    names = [_clean(n, 80) for n in names or [] if n and n.strip()]
    if not names:
        return out, refused
    if not words:
        return out, ["another conversation is covered only when their own words (their_words) name it"]
    rooms = db.scalars(select(PhoneConversation).join(
        PhoneConversationMember, PhoneConversationMember.conversation_id == PhoneConversation.id).where(
        PhoneConversationMember.person_id == person.id, PhoneConversation.id != here.id)).all()
    said = _norm(words)
    for name in names:
        matches = [c for c in rooms if c.title and (_norm(name) in _norm(c.title) or _norm(c.title) in _norm(name))]
        if len(matches) != 1:
            refused.append(f'"{name}": not one conversation they share with the user')
            continue
        title_words = _words(matches[0].title) or {_norm(matches[0].title)}
        if not any(w in said for w in title_words):
            refused.append(f'"{name}": their words don\'t name it')
            continue
        out.append(str(matches[0].id))
    return list(dict.fromkeys(out)), refused


def _recompute(db: Session, user_id: uuid.UUID, person_id: uuid.UUID) -> PhoneAffinityShadow:
    """The shadow view from the evidence not retracted: per field, the latest proposal. Under the affinity lock."""
    rows = db.scalars(select(PhoneRelationshipEvidence).where(
        PhoneRelationshipEvidence.person_id == person_id, PhoneRelationshipEvidence.retracted_at.is_(None),
        PhoneRelationshipEvidence.value.is_not(None))
        .order_by(PhoneRelationshipEvidence.created_at, PhoneRelationshipEvidence.id)).all()
    latest = {row.dimension: row for row in rows}
    shadow = db.get(PhoneAffinityShadow, person_id, with_for_update=True)
    if shadow is None:
        shadow = PhoneAffinityShadow(person_id=person_id, user_id=user_id, version=0)
        db.add(shadow)
    for name in ("familiarity", "warmth", "initiative"):
        setattr(shadow, name, latest[name].value if name in latest else None)
    shadow.note = latest["note"].value if "note" in latest else None
    shadow.note_conversation_id = latest["note"].conversation_id if "note" in latest else None
    shadow.version = (shadow.version or 0) + 1
    shadow.updated_at = _now()
    db.flush()
    return shadow


def note_exchange(user_id: uuid.UUID, handle: str, *, about: list[str], addressed: str, meaning: str,
                  their_words: str | None = None, changed: dict[str, str] | None = None,
                  worth_learning: bool = False, also_for: list[str] | None = None) -> str:
    """Record her reading of an exchange (see the module docstring). Returns what was recorded, in plain words.
    Every refusal leaves everything as it was."""
    who = ADDRESSED.get(addressed, addressed if addressed in ADDRESSED_WORDS else None)
    if who is None:
        return "Not recorded: addressed must be one of you, the_user, everyone, unclear."
    meaning = _masked(_clean(meaning, OBSERVATION_CHARS))
    if not meaning:
        return "Not recorded: say what the exchange meant, in a few words (meaning)."
    changed = {k: v for k, v in (changed or {}).items() if v not in (None, "")}
    bad = [k for k in changed if k not in DIMENSIONS]
    labels = {k: v for k, v in changed.items() if k != "note"}
    if bad or not phone_people.valid_labels(**labels):
        return (f"Not recorded: {', '.join(bad) or 'a label'} isn't one of the fields or values (familiarity: "
                f"{', '.join(phone_people.FAMILIARITY)}; warmth: {', '.join(phone_people.WARMTH)}; initiative: "
                f"{', '.join(phone_people.INITIATIVE)}; note: a few words).")
    if "note" in changed:
        changed["note"] = _masked(_clean(changed["note"], OBSERVATION_CHARS))
    try:
        cited = _cited(user_id, handle, about)
        words = _their_words(cited, their_words)
    except Refused as exc:
        return f"Not recorded: {exc}."
    events = [line.event_id for line in cited.lines]
    key = event_key(events)
    at = max((line.at for line in cited.lines if line.at), default=_now())
    notes: list[str] = []
    try:
        with SessionLocal.begin() as db:
            person, conversation, affinity = _resolve_records(db, user_id, cited)
            participated = _participated(db, user_id, conversation, at)
            applies, refused = _audiences(db, person, conversation, words, also_for or [])
            notes += [f"not covered: {r}" for r in refused]
            made = db.scalar(insert(PhoneExchange).values(
                id=uuid.uuid4(), user_id=user_id, person_id=person.id, conversation_id=conversation.id,
                event_key=key, events=events, addressed=who, participated=participated, observation=meaning,
                their_words=words, applies_to=applies, at=at,
            ).on_conflict_do_nothing(constraint="uq_phone_exchanges_event").returning(PhoneExchange.id))
            if made is None:
                return ("Already noted: you recorded your reading of these messages before (a notification seen "
                        "again has one effect). Nothing changed.")
            exchange = db.get(PhoneExchange, made)
            if "familiarity" in changed and changed["familiarity"] != "new" \
                    and not _spoken(db, user_id, person, addressed_now=who == "her"):
                notes.append("familiarity not proposed: you two haven't spoken yet (they haven't addressed you "
                             "where you've written; your own messages alone don't make it mutual)")
                changed.pop("familiarity")
            for dimension, value in changed.items():
                db.execute(insert(PhoneRelationshipEvidence).values(
                    id=uuid.uuid4(), user_id=user_id, person_id=person.id, conversation_id=conversation.id,
                    event_id=key, dimension=dimension, observation=meaning, value=value, exchange_id=exchange.id,
                ).on_conflict_do_nothing(constraint="uq_phone_relationship_evidence_event"))
            if changed:
                _recompute(db, user_id, person.id)
            lesson = _route_lesson(db, user_id, person, conversation, exchange, worth_learning)
            if lesson:
                notes.append(lesson)
    except Refused as exc:
        return f"Not recorded: {exc}."
    except SQLAlchemyError:
        log.warning("phone_relationships: an exchange could not be recorded", exc_info=True)
        return "Not recorded: their records couldn't be written just now. Nothing changed."
    said = [f"Noted your reading of {len(events)} message{'s' if len(events) != 1 else ''} with "
            f"{phone_people.handle(person.id)} ({ADDRESSED_WORDS[who]})."]
    if changed:
        said.append("Proposed (shadow: for review, not part of your record yet): "
                    + ", ".join(f"{k} {v}" for k, v in changed.items()) + ".")
    if applies:
        said.append(f"Their words cover {len(applies)} other conversation{'s' if len(applies) != 1 else ''} too.")
    return " ".join(said + [n[0].upper() + n[1:] + "." for n in notes])


def _route_lesson(db: Session, user_id: uuid.UUID, person: PhonePerson, conversation: PhoneConversation,
                  exchange: PhoneExchange, worth_learning: bool) -> str | None:
    """A personal lesson goes to the learner only when she took part and judged they were talking to her."""
    from app.services import learning_assessment

    if not worth_learning:
        return None
    if not exchange.participated or exchange.addressed != "her":
        exchange.lesson = "skipped"
        exchange.lesson_why = ("you haven't taken part in this conversation" if not exchange.participated else
                               "it isn't clear they were talking to you")
        return f"nothing to learn about being with them from this: {exchange.lesson_why}"
    try:
        with db.begin_nested():
            row, _ = learning_assessment.open_assessment(
                db, user_id=user_id, kind="phone", key=f"phone:{exchange.id}", anchor_at=exchange.at,
                trigger={"why": "her mind noted an exchange addressed to her", "exchange": str(exchange.id),
                         "person": phone_people.handle(person.id)},
                evidence=[(f"phone:{exchange.id}", "the exchange her mind noted", {})],
                phone_person_id=person.id)
    except ValueError as exc:  # older than a test-learning reset
        exchange.lesson, exchange.lesson_why = "skipped", _clean(str(exc), 200)
        return f"nothing to learn from it: {exchange.lesson_why}"
    exchange.lesson, exchange.assessment_id = "assessing", row.id
    return "what it says about being with them goes to your learning (it stays for review until it's live)"


def retract(db: Session, user_id: uuid.UUID, evidence_id: uuid.UUID) -> bool:
    """A correction: one proposal stops counting and the shadow view is recomputed (in the caller's transaction)."""
    row = db.get(PhoneRelationshipEvidence, evidence_id)
    if row is None or row.user_id != user_id:
        return False
    db.scalar(select(PhoneAffinity).where(PhoneAffinity.person_id == row.person_id).with_for_update())
    if row.retracted_at is None:
        row.retracted_at = _now()
        db.flush()
        _recompute(db, user_id, row.person_id)
    return True


# --------------------------------------------------------------------------- #
# Requests for space
# --------------------------------------------------------------------------- #

def note_request_for_space(user_id: uuid.UUID, handle: str, *, about: list[str], their_words: str,
                           everywhere: bool, topic: str | None = None) -> str:
    """A person clearly asked for space: their exact words, the scope they meant, a topic when they named one.
    Active at once; a replay has one effect."""
    try:
        cited = _cited(user_id, handle, about)
        words = _their_words(cited, their_words)
    except Refused as exc:
        return f"Not recorded: {exc}."
    if not words:
        return "Not recorded: a request for space rests on their own words (their_words)."
    topic = _clean(topic, TOPIC_CHARS) or None
    if topic and not _mentions(topic, words):
        return "Not recorded: the topic must be what they named in their words."
    key = event_key(line.event_id for line in cited.lines)
    try:
        with SessionLocal.begin() as db:
            person, conversation, _ = _resolve_records(db, user_id, cited)
            made = db.scalar(insert(PhoneContactBoundary).values(
                id=uuid.uuid4(), user_id=user_id, person_id=person.id, conversation_id=conversation.id,
                scope="everywhere" if everywhere else "here", topic=topic, words=words, event_key=key,
            ).on_conflict_do_nothing(constraint="uq_phone_contact_boundaries_event").returning(
                PhoneContactBoundary.id))
    except Refused as exc:
        return f"Not recorded: {exc}."
    except SQLAlchemyError:
        log.warning("phone_relationships: a request for space could not be recorded", exc_info=True)
        return "Not recorded: their records couldn't be written just now. Nothing changed."
    if made is None:
        return "Already noted: this request was recorded before."
    where = "everywhere" if everywhere else "in this conversation"
    what = f"leave {topic} alone" if topic else "not to be reached out to"
    return (f"Recorded, and it holds now: {phone_people.handle(person.id)} asked {where} to {what}"
            + (" (other topics stay open)" if topic else "") + ".")


def active_boundaries(db: Session, user_id: uuid.UUID, person_id: uuid.UUID,
                      conversation_pk: uuid.UUID | None) -> list[PhoneContactBoundary]:
    """The person's requests for space that cover this conversation (everywhere, or said there), not lifted."""
    rows = db.scalars(select(PhoneContactBoundary).where(
        PhoneContactBoundary.user_id == user_id, PhoneContactBoundary.person_id == person_id,
        PhoneContactBoundary.lifted_at.is_(None)).order_by(PhoneContactBoundary.created_at)).all()
    return [r for r in rows if r.scope == "everywhere" or r.conversation_id == conversation_pk]


def lift_boundary(db: Session, user_id: uuid.UUID, boundary_id: uuid.UUID) -> bool:
    row = db.get(PhoneContactBoundary, boundary_id)
    if row is None or row.user_id != user_id:
        return False
    row.lifted_at = row.lifted_at or _now()
    return True


# --------------------------------------------------------------------------- #
# What the user authorized for whom
# --------------------------------------------------------------------------- #

def authorize_detail(user_id: uuid.UUID, name: str, detail: str, *, conversation: str | None = None,
                     source_message_id: uuid.UUID | None = None) -> str:
    """The user told her to pass a detail to someone: for their direct chat unless a conversation is named (a group
    needs its own authorization). Only on the user's own reply turns."""
    detail = _clean(detail, DETAIL_CHARS)
    if not detail:
        return "Not recorded: say the detail they authorized."
    try:
        with SessionLocal.begin() as db:
            people = phone_people.resolve(db, user_id, name)
            if len(people) != 1:
                return ("Not recorded: " + ("no one by that name has written on their phone" if not people else
                                            f"{len(people)} people match: ask which one (read_person lists them)")
                        + ".")
            person = people[0]
            rooms = db.scalars(select(PhoneConversation).join(
                PhoneConversationMember, PhoneConversationMember.conversation_id == PhoneConversation.id).where(
                PhoneConversationMember.person_id == person.id)).all()
            if conversation and conversation.strip():
                rooms = [c for c in rooms if c.title and _norm(conversation) in _norm(c.title)]
                missing = f'no conversation called "{_clean(conversation, 40)}" that they share with the user'
            else:
                rooms = [c for c in rooms if not c.is_group]
                missing = "no direct chat with them seen yet: name the conversation it's for"
            if len(rooms) != 1:
                return f"Not recorded: {missing if not rooms else 'more than one conversation matches: name it'}."
            room = rooms[0]
            db.add(PhoneDisclosure(user_id=user_id, person_id=person.id, conversation_id=room.id, detail=detail,
                                   source_message_id=source_message_id))
    except SQLAlchemyError:
        log.warning("phone_relationships: an authorization could not be recorded", exc_info=True)
        return "Not recorded: it couldn't be written just now."
    where = f'the "{_clean(room.title, 40)}" group' if room.is_group else "their direct chat"
    return (f"Recorded: you may tell {phone_people.handle(person.id)} \"{detail}\" in {where} only (anywhere else, "
            "ask them again).")


# --------------------------------------------------------------------------- #
# The learner's view of a phone exchange (learning_assessment ``phone`` trigger)
# --------------------------------------------------------------------------- #

def _exchange_of(db: Session, row: LearningAssessment) -> PhoneExchange | None:
    try:
        exchange = db.get(PhoneExchange, uuid.UUID(str((row.trigger or {}).get("exchange"))))
    except ValueError:
        return None
    return exchange if exchange is not None and exchange.user_id == row.user_id else None


def lesson_scope(db: Session, row: LearningAssessment):
    """The audience lessons from this phone assessment are bound to (None: its exchange is gone)."""
    from app.services.memory_lessons import PhoneScope

    exchange = _exchange_of(db, row)
    if exchange is None:
        return None
    return PhoneScope(exchange.person_id, exchange.conversation_id, tuple(exchange.applies_to or ()))


def assessment_evidence(db: Session, row: LearningAssessment, ev, facts) -> str | None:
    """What a phone assessment reads: the person's words she noted in that conversation (the trigger's and her
    earlier readings there), her own sent replies there, her mind's readings (signals, never evidence) and a fact
    of her taking part. Returns who "user" is in it."""
    from app.ai.learning import assessment as A

    exchange = _exchange_of(db, row)
    if exchange is None:
        ev.signals.append("the phone exchange this was about is gone (forgotten or corrected)")
        return None
    person = db.get(PhonePerson, exchange.person_id)
    conversation = db.get(PhoneConversation, exchange.conversation_id)
    readings = list(reversed(db.scalars(select(PhoneExchange).where(
        PhoneExchange.person_id == exchange.person_id, PhoneExchange.conversation_id == exchange.conversation_id,
        PhoneExchange.at <= exchange.at).order_by(PhoneExchange.at.desc()).limit(EXCHANGES_READ)).all()))
    # The same replies eligibility counted (``_sent_before``): what the learner reads is what made it eligible.
    sent = list(reversed(db.scalars(select(PhoneOutbound).where(
        *_sent_before(row.user_id, conversation.conversation_id, exchange.at))
        .order_by(PhoneOutbound.created_at.desc()).limit(SENT_READ)).all()))
    name = _clean(person.display_name, 40) or "someone"
    where = f'the "{_clean(conversation.title, 40)}" group' if conversation.is_group else "a direct chat"
    timeline: list[tuple[datetime, str, object]] = [(r.at, "them", r) for r in readings]
    timeline += [(o.created_at, "her", o) for o in sent]
    k = 0
    for at, kind, item in sorted(timeline, key=lambda t: t[0]):
        k += 1
        ref, stamp = f"m{k}", at.strftime("%a %Y-%m-%d %H:%M")
        if kind == "her":
            ev.add(A.Item(ref=ref, kind="her", event=f"outbound:{item.id}", roots=(f"outbound:{item.id}",),
                          text=f'{ref} [columbina, sent there, {stamp}] "{_clean(item.text, TEXT_CHARS)}"',
                          words=item.text))
            continue
        started = " (this started it)" if item.id == exchange.id else ""
        if item.their_words:
            ev.add(A.Item(ref=ref, kind="user", event=f"phone:{item.id}", roots=(f"phone:{item.id}",),
                          text=f'{ref} [user ({name}), {stamp}] "{item.their_words}"{started}',
                          words=item.their_words))
        ev.signals.append(f"her mind's reading of {ref if item.their_words else 'an exchange'} at {stamp} "
                          f"({ADDRESSED_WORDS[item.addressed]}): {item.observation}{started}")
    others = [db.get(PhoneConversation, uuid.UUID(c)) for c in exchange.applies_to or []]
    if others:
        ev.signals.append("their words also named: " + ", ".join(
            f'"{_clean(c.title, 40)}"' for c in others if c is not None))
    ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="phone", event=f"phone:{exchange.id}",
                  roots=(f"phone:{exchange.id}",),
                  text=f"she has written in this conversation {len(sent)} time(s) through a reply button before this "
                       "(counted by code). Establishes: she took part there. Does not establish: who each message "
                       "was for, or how it landed."))
    return (f"{name}, someone writing to her on the user's phone in {where} (not the user of the app); "
            "\"columbina\" lines are what she sent there")


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #

def review(db: Session, user_id: uuid.UUID, person_id: uuid.UUID) -> dict | None:
    """Everything B2 holds about one person, for backend review: the active record next to the shadow view, every
    proposal with its provenance, her readings, requests for space, authorizations, phone assessments and lessons."""
    person = db.scalar(select(PhonePerson).where(PhonePerson.id == person_id, PhonePerson.user_id == user_id))
    if person is None:
        return None
    affinity = db.scalar(select(PhoneAffinity).where(PhoneAffinity.person_id == person_id))
    shadow = db.get(PhoneAffinityShadow, person_id)
    titles = {c.id: c.title for c in db.scalars(select(PhoneConversation).where(
        PhoneConversation.user_id == user_id))}

    def room(pk) -> dict | None:
        return None if pk is None else {"id": pk, "title": titles.get(pk)}

    return {
        "person": {"id": person.id, "handle": phone_people.handle(person.id), "display_name": person.display_name},
        "active": None if affinity is None else {
            "familiarity": affinity.familiarity, "warmth": affinity.warmth, "initiative": affinity.initiative,
            "note": affinity.note, "note_conversation": room(affinity.note_conversation_id),
            "version": affinity.version},
        "shadow": None if shadow is None else {
            "familiarity": shadow.familiarity, "warmth": shadow.warmth, "initiative": shadow.initiative,
            "note": shadow.note, "note_conversation": room(shadow.note_conversation_id), "version": shadow.version,
            "updated_at": shadow.updated_at},
        "evidence": [{"id": e.id, "dimension": e.dimension, "value": e.value, "observation": e.observation,
                      "event_key": e.event_id, "exchange_id": e.exchange_id, "audience": room(e.conversation_id),
                      "created_at": e.created_at, "retracted_at": e.retracted_at}
                     for e in db.scalars(select(PhoneRelationshipEvidence).where(
                         PhoneRelationshipEvidence.person_id == person_id)
                         .order_by(PhoneRelationshipEvidence.created_at))],
        "exchanges": [{"id": x.id, "audience": room(x.conversation_id), "events": x.events, "addressed": x.addressed,
                       "participated": x.participated, "observation": x.observation, "their_words": x.their_words,
                       "applies_to": [room(uuid.UUID(c)) for c in x.applies_to or []], "lesson": x.lesson,
                       "lesson_why": x.lesson_why, "assessment_id": x.assessment_id, "at": x.at}
                      for x in db.scalars(select(PhoneExchange).where(PhoneExchange.person_id == person_id)
                                          .order_by(PhoneExchange.at))],
        "boundaries": [{"id": b.id, "scope": b.scope, "topic": b.topic, "words": b.words,
                        "said_in": room(b.conversation_id), "created_at": b.created_at, "lifted_at": b.lifted_at}
                       for b in db.scalars(select(PhoneContactBoundary).where(
                           PhoneContactBoundary.person_id == person_id).order_by(PhoneContactBoundary.created_at))],
        "authorized": [{"id": d.id, "detail": d.detail, "audience": room(d.conversation_id),
                        "created_at": d.created_at, "revoked_at": d.revoked_at}
                       for d in db.scalars(select(PhoneDisclosure).where(PhoneDisclosure.person_id == person_id)
                                           .order_by(PhoneDisclosure.created_at))],
        "assessments": [{"id": a.id, "state": a.state, "outcome": a.outcome, "revision": a.revision,
                         "explanation": a.explanation}
                        for a in db.scalars(select(LearningAssessment).where(
                            LearningAssessment.user_id == user_id, LearningAssessment.phone_person_id == person_id))],
        "lessons": [{"id": m.id, "kind": m.kind, "status": m.status, "statement": m.statement,
                     "applies_when": m.applies_when, "their_words": m.user_words, "live": m.phone_live,
                     "learned_in": room(m.phone_conversation_id),
                     "applies_to": [room(uuid.UUID(c)) for c in m.phone_applies_to or []]}
                    for m in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                                   MemoryLesson.phone_person_id == person_id))],
    }
