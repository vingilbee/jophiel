"""People she meets through the user's phone (affinity B1: docs/PHONE_PHASE_8_AFFINITY_B1.md).

Who wrote, told apart by the phone's opaque sender ids, which conversations they share, her relationship record
with each (the defaults only in B1: nothing here changes a label or the note) and what she said to whom through a
reply button (``phone_outbound``). Names, opaque ids and her own sent text only: no message text from anyone else
is stored here.

- **Ingestion** (``ingest``, from ``phone_notifications.report`` after its Redis write, its own transaction): a
  conversation per ``conversation_id``, a person per sender id (created with a key and default affinity only
  when the key truly doesn't exist), memberships. Never merged automatically; the same snapshot twice changes only
  ``last_seen_at``; a failure logs and never fails the phone's report.
- **Her replies echoed back** (``record_outbound`` / ``settle_outbound`` from ``phone_actions.act``; matching in
  ingestion): a new ``owner`` message in that conversation within ``ECHO_WINDOW`` whose normalized text equals one
  sent-or-unknown row's, and only one, is hers. Anything ambiguous stays "answered from the phone": the user's
  words are never claimed as hers.
- **What her moments say** (``known`` / ``bracket``): a ``person:`` handle, contact or not, new or seen before,
  whether she has spoken with them, the other conversations they share with the user (titles only). Never a label
  or a score.
- **"How do you feel about Rina?"** (``read_person``): her record for that person, on the user's reply turns.
- **Inspection** (``inspect`` / ``delete_person``): ``/api/memory/people``. Deleting a person removes their keys,
  memberships, affinity and evidence; her sent text stays (``person_id`` dropped).

Facts only: nothing here decides what she does.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import (
    PhoneAffinity, PhoneConversation, PhoneConversationMember, PhoneOutbound, PhonePerson, PhonePersonKey,
)
from app.db.session import SessionLocal
from app.services import phone_state

log = logging.getLogger(__name__)

OWNER_SENDER = "owner"  # phone_notifications.OWNER_SENDER: a message written on the phone itself
HANDLE_PREFIX = "person:"
NAMES_KEPT = 10  # earlier display names kept per person
NAME_CHARS = 40
ECHO_WINDOW = timedelta(minutes=10)
CLOCK_SKEW = timedelta(seconds=60)  # the phone's clock against ours, when an echo is matched
NEW_SECONDS = 600  # a person first seen this close to a notification is "new" in it
SHARED_SHOWN = 3
SENT_SHOWN = 3

# Relationship labels (§5): fixed sets, plain language, labels not steps. Chosen by her mind from B2.
FAMILIARITY = ("new", "acquainted", "familiar", "close")
WARMTH = ("wary", "cool", "neutral", "warm", "fond")
INITIATIVE = ("none", "would_enjoy", "wants_to", "holding_back")
DEFAULTS = {"familiarity": "new", "warmth": "neutral", "initiative": "none"}
LABELS = {"familiarity": FAMILIARITY, "warmth": WARMTH, "initiative": INITIATIVE}

# A reply's result (phone_actions statuses) -> what the outbound row says.
SETTLED = {"done": "sent", "failed": "failed", "refused": "failed", "expired": "failed", "notified": "failed",
           "unconfirmed": "unknown"}


def valid_labels(**labels: str | None) -> bool:
    """Whether every given label is one of its fixed set (``familiarity`` / ``warmth`` / ``initiative``)."""
    return all(name in LABELS and (value is None or value in LABELS[name]) for name, value in labels.items())


def handle(person_id: uuid.UUID | str) -> str:
    """The short handle she sees ("person:7c1e2a9b"): a hash, the ids stay out of prompts."""
    return HANDLE_PREFIX + hashlib.sha256(str(person_id).encode()).hexdigest()[:8]


def _clean(value: str | None, limit: int) -> str:
    words = " ".join((value or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


def _normalized(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _utc(at: float) -> datetime:
    return datetime.fromtimestamp(at, timezone.utc)


def _message_at(message: dict, fallback: datetime) -> datetime:
    raw = message.get("at")
    if not raw:
        return fallback
    try:
        at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def echo_id(message: dict) -> str:
    """A message's own id for echo matching: the phone's ``event_id``, else a hash of what identifies it."""
    if message.get("event_id"):
        return str(message["event_id"])[:128]
    raw = f"{message.get('sender_id')}\x00{message.get('at')}\x00{message.get('text')}"
    return "h:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #

def ingest(user_id: uuid.UUID | str, notifications: Iterable[Any], *, now: float | None = None) -> bool:
    """Record who wrote in these posted notifications (``phone_notifications.Notification``), in one
    transaction of its own. Returns whether it worked; a failure is logged, never raised."""
    notifications = [n for n in notifications if n.messages]
    if not notifications:
        return True
    at = _utc(time.time() if now is None else now)
    user = uuid.UUID(str(user_id))
    try:
        with SessionLocal.begin() as db:
            for notification in notifications:
                _ingest_one(db, user, notification, at)
        return True
    except Exception:  # noqa: BLE001 - the phone's report never fails because of this
        log.warning("phone_people: could not record who wrote for user %s", user_id, exc_info=True)
        return False


def _ingest_one(db: Session, user_id: uuid.UUID, n: Any, now: datetime) -> None:
    conversation = None
    if n.conversation_id:
        values = {"user_id": user_id, "conversation_id": n.conversation_id,
                  "conversation_identity": n.conversation_identity, "package": n.package, "app": n.app,
                  "is_group": bool(n.is_group), "title": (n.conversation or n.title or None), "last_seen_at": now}
        statement = insert(PhoneConversation).values(id=uuid.uuid4(), **values)
        statement = statement.on_conflict_do_update(
            constraint="uq_phone_conversations_conversation",
            set_={"conversation_identity": func.coalesce(statement.excluded.conversation_identity,
                                                         PhoneConversation.conversation_identity),
                  "package": func.coalesce(statement.excluded.package, PhoneConversation.package),
                  "app": func.coalesce(statement.excluded.app, PhoneConversation.app),
                  "is_group": statement.excluded.is_group,
                  "title": func.coalesce(statement.excluded.title, PhoneConversation.title),
                  "last_seen_at": func.greatest(statement.excluded.last_seen_at, PhoneConversation.last_seen_at)})
        conversation = db.scalar(statement.returning(PhoneConversation.id))
    for message in n.messages:
        sender_id = message.get("sender_id")
        if not sender_id or sender_id == OWNER_SENDER:
            continue
        person = _person_for(db, user_id, message, now)
        if conversation is not None:
            statement = insert(PhoneConversationMember).values(
                conversation_id=conversation, person_id=person.id, last_message_at=_message_at(message, now))
            db.execute(statement.on_conflict_do_update(
                index_elements=[PhoneConversationMember.conversation_id, PhoneConversationMember.person_id],
                set_={"last_message_at": func.greatest(PhoneConversationMember.last_message_at,
                                                       statement.excluded.last_message_at)}))
    if n.conversation_id:
        _match_echoes(db, user_id, n, now)


def _person_for(db: Session, user_id: uuid.UUID, message: dict, now: datetime) -> PhonePerson:
    """The person behind a sender id: created with its key and default affinity only when the key doesn't exist."""
    sender_id = str(message["sender_id"])
    name = _clean(message.get("sender"), 120) or None
    key = db.scalar(select(PhonePersonKey).where(PhonePersonKey.user_id == user_id,
                                                 PhonePersonKey.sender_id == sender_id))
    if key is None:
        try:
            with db.begin_nested():
                person = PhonePerson(user_id=user_id, display_name=name, names=[], in_contacts=message.get("in_contacts"),
                                     first_seen_at=now, last_seen_at=now)
                db.add(person)
                db.flush()
                db.add(PhonePersonKey(user_id=user_id, sender_id=sender_id,
                                      identity=message.get("identity") or "name", person_id=person.id))
                db.add(PhoneAffinity(user_id=user_id, person_id=person.id, **DEFAULTS))
                db.flush()
            return person
        except IntegrityError:  # another report created them first
            key = db.scalar(select(PhonePersonKey).where(PhonePersonKey.user_id == user_id,
                                                         PhonePersonKey.sender_id == sender_id))
            if key is None:
                raise
    person = db.get(PhonePerson, key.person_id, with_for_update=True)
    if name and name != person.display_name:
        if person.display_name and person.display_name not in person.names:
            person.names = [*person.names, person.display_name][-NAMES_KEPT:]
        person.display_name = name
    if message.get("in_contacts") is not None and message["in_contacts"] != person.in_contacts:
        person.in_contacts = message["in_contacts"]
    if now > person.last_seen_at:
        person.last_seen_at = now
    return person


def _match_echoes(db: Session, user_id: uuid.UUID, n: Any, now: datetime) -> None:
    """Her replies coming back as ``owner`` messages: matched only when exactly one of her sends fits."""
    for message in n.messages:
        if message.get("sender_id") != OWNER_SENDER or not _normalized(message.get("text")):
            continue
        event = echo_id(message)
        if db.scalar(select(PhoneOutbound.id).where(PhoneOutbound.user_id == user_id,
                                                    PhoneOutbound.echo_event_id == event)):
            continue
        at = _message_at(message, now)
        rows = db.scalars(select(PhoneOutbound).where(
            PhoneOutbound.user_id == user_id, PhoneOutbound.conversation_id == n.conversation_id,
            PhoneOutbound.status.in_(("sent", "unknown")), PhoneOutbound.echo_event_id.is_(None),
            PhoneOutbound.created_at >= at - ECHO_WINDOW, PhoneOutbound.created_at <= at + CLOCK_SKEW,
        ).with_for_update()).all()
        same = [row for row in rows if _normalized(row.text) == _normalized(message.get("text"))]
        if len(same) != 1:
            continue  # the user's own words, or ambiguous: never claimed as hers
        row = same[0]
        try:
            with db.begin_nested():
                row.echo_event_id = event
                if row.status == "unknown":
                    row.status = "sent"
                row.settled_at = row.settled_at or now
                db.flush()
        except IntegrityError:
            continue


# --------------------------------------------------------------------------- #
# What she said to whom
# --------------------------------------------------------------------------- #

def record_outbound(user_id: uuid.UUID, action_id: uuid.UUID, *, key: str, conversation_id: str | None,
                    revision: int, text: str, asked_by: str = "user") -> bool:
    """A ``reply_notification`` about to be dispatched (``sending``). ``person_id`` only for a direct chat with one
    known member. Returns whether it was recorded (a failure is logged: the send itself isn't blocked)."""
    try:
        with SessionLocal.begin() as db:
            person_id = None
            if conversation_id:
                conversation = db.scalar(select(PhoneConversation).where(
                    PhoneConversation.user_id == user_id, PhoneConversation.conversation_id == conversation_id))
                if conversation is not None and not conversation.is_group:
                    members = db.scalars(select(PhoneConversationMember.person_id).where(
                        PhoneConversationMember.conversation_id == conversation.id).limit(2)).all()
                    person_id = members[0] if len(members) == 1 else None
            db.add(PhoneOutbound(id=action_id, user_id=user_id, conversation_id=conversation_id, person_id=person_id,
                                 notification_key=key, revision=revision, text=text, asked_by=asked_by,
                                 status="sending"))
        return True
    except SQLAlchemyError:
        log.warning("phone_people: a reply could not be recorded before sending", exc_info=True)
        return False


def settle_outbound(action_id: uuid.UUID, result: str) -> None:
    """A reply's result: ``done`` -> sent, ``failed`` / ``refused`` / ``expired`` -> failed, no answer
    (``unconfirmed``) -> unknown (never resent). A late answer settles an unknown one, unless an echo already
    showed it was sent."""
    status = SETTLED.get(result)
    if status is None:
        return
    try:
        with SessionLocal.begin() as db:
            db.execute(update(PhoneOutbound).where(
                PhoneOutbound.id == action_id,
                or_(PhoneOutbound.status == "sending",
                    (PhoneOutbound.status == "unknown") & PhoneOutbound.echo_event_id.is_(None)),
            ).values(status=status, settled_at=None if status == "unknown" else func.now()))
    except SQLAlchemyError:
        log.warning("phone_people: a reply's result could not be recorded", exc_info=True)


def echoed(user_id: uuid.UUID | str, events: Iterable[str]) -> set[str]:
    """Which of these owner messages are her replies coming back (an empty set when unknown: never claimed)."""
    events = list(dict.fromkeys(events))
    if not events:
        return set()
    try:
        with SessionLocal() as db:
            return set(db.scalars(select(PhoneOutbound.echo_event_id).where(
                PhoneOutbound.user_id == uuid.UUID(str(user_id)), PhoneOutbound.echo_event_id.in_(events))))
    except SQLAlchemyError:
        log.warning("phone_people: echoes could not be read", exc_info=True)
        return set()


# --------------------------------------------------------------------------- #
# Who wrote, as her moments say it
# --------------------------------------------------------------------------- #

@dataclass
class Shared:
    conversation_id: str
    title: str | None
    is_group: bool
    app: str | None


@dataclass
class Known:
    person_id: uuid.UUID
    display_name: str | None
    names: list[str]
    in_contacts: bool | None
    first_seen_at: datetime
    last_seen_at: datetime
    conversations: list[Shared] = field(default_factory=list)
    sent: int = 0
    last_sent_at: datetime | None = None

    @property
    def handle(self) -> str:
        return handle(self.person_id)


def _known(db: Session, user_id: uuid.UUID, people: list[PhonePerson]) -> dict[uuid.UUID, Known]:
    ids = [p.id for p in people]
    out = {p.id: Known(p.id, p.display_name, list(p.names or []), p.in_contacts, p.first_seen_at, p.last_seen_at)
           for p in people}
    if not ids:
        return out
    rows = db.execute(select(PhoneConversationMember.person_id, PhoneConversation.conversation_id,
                             PhoneConversation.title, PhoneConversation.is_group, PhoneConversation.app)
                      .join(PhoneConversation, PhoneConversation.id == PhoneConversationMember.conversation_id)
                      .where(PhoneConversationMember.person_id.in_(ids))
                      .order_by(PhoneConversationMember.last_message_at.desc().nulls_last()))
    for person_id, conversation_id, title, is_group, app in rows:
        out[person_id].conversations.append(Shared(conversation_id, title, is_group, app))
    for person_id, count, last in db.execute(
            select(PhoneOutbound.person_id, func.count(), func.max(PhoneOutbound.created_at))
            .where(PhoneOutbound.user_id == user_id, PhoneOutbound.person_id.in_(ids), PhoneOutbound.status == "sent")
            .group_by(PhoneOutbound.person_id)):
        out[person_id].sent, out[person_id].last_sent_at = count, last
    return out


def known(user_id: uuid.UUID | str, sender_ids: Iterable[str]) -> dict[str, Known] | None:
    """What she knows about each sender id that has a person (a sender id with none is left out). None when the
    records can't be read (unknown, never "new")."""
    sender_ids = [s for s in dict.fromkeys(sender_ids) if s and s != OWNER_SENDER]
    if not sender_ids:
        return {}
    user = uuid.UUID(str(user_id))
    try:
        with SessionLocal() as db:
            keys = db.execute(select(PhonePersonKey.sender_id, PhonePerson)
                              .join(PhonePerson, PhonePerson.id == PhonePersonKey.person_id)
                              .where(PhonePersonKey.user_id == user, PhonePersonKey.sender_id.in_(sender_ids))).all()
            found = _known(db, user, [person for _, person in keys])
            return {sender_id: found[person.id] for sender_id, person in keys}
    except SQLAlchemyError:
        log.warning("phone_people: who wrote could not be looked up", exc_info=True)
        return None


def _where(shared: Shared) -> str:
    title = _clean(shared.title, NAME_CHARS)
    if shared.is_group:
        return f'"{title}" group' if title else "a group"
    return "a direct chat" + (f" ({_clean(shared.app, 20)})" if shared.app else "")


def bracket(person: Known | None, *, conversation_id: str | None, at: float, looked_up: bool = True) -> str:
    """What she knows about who wrote, for a notification line: "person:7c1e2a9b, a contact, you two haven't
    spoken; also in "Band" group". ``looked_up`` False (the records couldn't be read) or no person recorded:
    "couldn't look them up", never "new"."""
    if person is None or not looked_up:
        return "couldn't look them up"
    parts = [person.handle]
    if person.in_contacts is True:
        parts.append("a contact")
    elif person.in_contacts is False:
        parts.append("not in their contacts")
    age = at - person.first_seen_at.timestamp()
    parts.append("new" if age < NEW_SECONDS else f"seen before (first {phone_state.span(age)} ago)")
    if person.sent:
        parts.append(f"you've written to them {person.sent} time{'s' if person.sent != 1 else ''}, last "
                     f"{phone_state.span(at - person.last_sent_at.timestamp())} ago")
    else:
        parts.append("you two haven't spoken")
    text = ", ".join(parts)
    others = [s for s in person.conversations if s.conversation_id != conversation_id][:SHARED_SHOWN]
    if others:
        text += "; also in " + ", ".join(_where(s) for s in others)
    return text


def brackets(user_id: uuid.UUID | str, senders: Iterable[tuple[str | None, str]], *, at: float) -> dict[tuple, str]:
    """``bracket`` for each (conversation id, sender id), with one lookup."""
    senders = [(c, s) for c, s in dict.fromkeys(senders) if s and s != OWNER_SENDER]
    if not senders:
        return {}
    found = known(user_id, [s for _, s in senders])
    return {(c, s): bracket((found or {}).get(s), conversation_id=c, at=at, looked_up=found is not None)
            for c, s in senders}


# --------------------------------------------------------------------------- #
# "How do you feel about Rina?" and inspection
# --------------------------------------------------------------------------- #

def resolve(db: Session, user_id: uuid.UUID, name: str) -> list[PhonePerson]:
    """People by a handle ("person:7c1e2a9b") or a name (shown now or before; exact first, else a part of it)."""
    wanted = " ".join((name or "").split())
    if not wanted:
        return []
    people = list(db.scalars(select(PhonePerson).where(PhonePerson.user_id == user_id,
                                                       PhonePerson.status == "active")
                             .order_by(PhonePerson.last_seen_at.desc())))
    if wanted.lower().startswith(HANDLE_PREFIX):
        return [p for p in people if handle(p.id).startswith(wanted.lower())]
    needle = wanted.casefold()
    names = {p.id: [n.casefold() for n in [p.display_name, *(p.names or [])] if n] for p in people}
    exact = [p for p in people if needle in names[p.id]]
    return exact or [p for p in people if any(needle in n for n in names[p.id])]


def _span_ago(at: datetime | None, now: float) -> str:
    return f"{phone_state.span(now - at.timestamp())} ago" if at else "never"


def _shared_line(person: Known) -> str:
    if not person.conversations:
        return "no conversation recorded"
    return ", ".join(_where(s) for s in person.conversations[:6])


def read_person(user_id: uuid.UUID, name: str, *, now: float | None = None) -> str:
    """Her record for one person on the phone, as her read tool returns it (several matches: listed, to ask)."""
    now = time.time() if now is None else now
    try:
        with SessionLocal() as db:
            people = resolve(db, user_id, name)
            if not people:
                return (f'No one called "{_clean(name, NAME_CHARS)}" has written on their phone that you can tell '
                        "apart (someone appears here once they've written in a chat notification). Nothing about "
                        "them is recorded.")
            found = _known(db, user_id, people)
            if len(people) > 1:
                lines = [f"{len(people)} people match \"{_clean(name, NAME_CHARS)}\": ask them which one they mean."]
                for p in people[:8]:
                    k = found[p.id]
                    contact = {True: "a contact", False: "not in their contacts"}.get(k.in_contacts, "contact unknown")
                    lines.append(f'- [{k.handle}] "{_clean(k.display_name, NAME_CHARS)}", {contact}; '
                                 f"{_shared_line(k)}; last seen {_span_ago(k.last_seen_at, now)}")
                return "\n".join(lines)
            person = people[0]
            k = found[person.id]
            affinity = db.scalar(select(PhoneAffinity).where(PhoneAffinity.person_id == person.id))
            sent = db.scalars(select(PhoneOutbound).where(
                PhoneOutbound.user_id == user_id, PhoneOutbound.person_id == person.id,
                PhoneOutbound.status.in_(("sent", "unknown"))).order_by(PhoneOutbound.created_at.desc())
                .limit(SENT_SHOWN)).all()
    except SQLAlchemyError:
        log.warning("phone_people: a person could not be read", exc_info=True)
        return "Couldn't read what you know about them just now. Don't say nothing is recorded."
    contact = {True: "a contact of theirs", False: "not in their contacts"}.get(k.in_contacts, "contact unknown")
    earlier = f" (earlier shown as {', '.join(repr(n) for n in k.names[-3:])})" if k.names else ""
    lines = [f'[{k.handle}] "{_clean(k.display_name, NAME_CHARS)}"{earlier}: {contact}; first seen '
             f"{_span_ago(k.first_seen_at, now)}, last seen {_span_ago(k.last_seen_at, now)}.",
             f"Conversations they share with the user: {_shared_line(k)}."]
    labels = {name: getattr(affinity, name) if affinity else DEFAULTS[name] for name in DEFAULTS}
    lines.append("Your relationship record (yours to put in your own words; never shown to them, not a trust "
                 f"level): familiarity {labels['familiarity']}, warmth {labels['warmth']}, initiative "
                 f"{labels['initiative'].replace('_', ' ')}.")
    if affinity and affinity.note:
        lines.append(f"Your note: {affinity.note}")
    if affinity and affinity.preferences:
        lines.append("What they've asked for: " + "; ".join(f"{key}: {value}" for key, value in
                                                             affinity.preferences.items()))
    lines.append("Last direct exchange: " + (_span_ago(affinity.last_direct_exchange_at, now)
                                             if affinity and affinity.last_direct_exchange_at else "none yet") + ".")
    if sent:
        lines.append("What you've sent them through a reply button (newest first):")
        lines.extend(f"- {_span_ago(row.created_at, now)}: '{_clean(row.text, 200)}'"
                     + (" (it may not have arrived)" if row.status == "unknown" else "") for row in sent)
    else:
        lines.append("You haven't sent them anything.")
    if labels == DEFAULTS and not sent and not (affinity and affinity.last_direct_exchange_at):
        lines.append("In plain terms: you two haven't really spoken; whatever you feel about them can only come "
                     "from what you've seen.")
    return "\n".join(lines)


def inspect(db: Session, user_id: uuid.UUID) -> list[dict]:
    """Every person, newest seen first, with their affinity record, shared conversations and what she sent them."""
    people = list(db.scalars(select(PhonePerson).where(PhonePerson.user_id == user_id)
                             .order_by(PhonePerson.last_seen_at.desc()).limit(500)))
    found = _known(db, user_id, people)
    affinities = {a.person_id: a for a in db.scalars(select(PhoneAffinity).where(
        PhoneAffinity.person_id.in_([p.id for p in people])))} if people else {}
    keys: dict[uuid.UUID, list[str]] = {}
    for person_id, identity in db.execute(select(PhonePersonKey.person_id, PhonePersonKey.identity).where(
            PhonePersonKey.user_id == user_id)):
        keys.setdefault(person_id, []).append(identity)
    out = []
    for person in people:
        k, affinity = found[person.id], affinities.get(person.id)
        out.append({
            "id": person.id, "handle": k.handle, "display_name": person.display_name, "names": k.names,
            "in_contacts": person.in_contacts, "status": person.status, "identities": keys.get(person.id, []),
            "first_seen_at": person.first_seen_at, "last_seen_at": person.last_seen_at,
            "conversations": [{"title": s.title, "is_group": s.is_group, "app": s.app} for s in k.conversations],
            "affinity": None if affinity is None else {
                "familiarity": affinity.familiarity, "warmth": affinity.warmth, "initiative": affinity.initiative,
                "note": affinity.note, "preferences": affinity.preferences,
                "last_direct_exchange_at": affinity.last_direct_exchange_at, "version": affinity.version,
                "updated_at": affinity.updated_at},
            "sent": k.sent, "last_sent_at": k.last_sent_at,
        })
    return out


def delete_person(db: Session, user_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    """Forget one person: keys, memberships, affinity and evidence go (cascade); her sent text stays."""
    person = db.scalar(select(PhonePerson).where(PhonePerson.id == person_id, PhonePerson.user_id == user_id))
    if person is None:
        return False
    db.delete(person)
    db.flush()
    return True
