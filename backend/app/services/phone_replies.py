"""Her own replies to other people on the user's phone (affinity B3, docs/PHONE_PHASE_8_AFFINITY_B3.md).

Two ways she may write to someone there herself, both through a notification's live Reply action (opening WhatsApp
and tapping Send on screen is phone Phase 9):

1. **The message that woke her.** When a notification wakes her mind (services/mind_phone.py), the tick binds each
   eligible notification to the message that caused it (``targets_from_news``): the notification's key and
   revision, the phone's opaque conversation id, the message's event id and its sender id. Only that bound exchange
   can be answered on that wake (``reply``): not another notification on the phone, not another conversation.
2. **A follow-up she chose.** ``come_back_to`` with a notification handle keeps who and where by stable ids
   (``follow_up_for``: person, conversation, sender, the exchange it rests on; never a draft, never a notification
   key as identity). When it comes due she rereads the conversation, and may write fresh words only to a live
   notification of that same conversation whose newest message is that same person's (``follow_up_target``).

Eligibility is identity, never content: a chat notification with a reply button, a conversation the phone
identified by its shortcut (a title can't identify an audience), and a newest message with an event id and a sender
id that isn't the phone's owner; in a group the sender must be identified by contact (a name-derived id can be two
people with one name). Who it was for, and whether to answer at all, are her mind's.

Every send goes through ``send``: the full text validated before anything (an over-long draft is held, never cut,
and exactly the text the check read is the text that goes out), the B2 check (services/phone_reply_check.py) bound to
the same notification, revision, conversation and recipient, everything checked again at the last backend send
point (switches, the notification and its revision, who she'd answer, the phone session, what the user authorized
and requests for space), then one claim per attempt (``phone_people.claim_outbound``: a replayed wake, a worker
retry or two concurrent wakes publish at most once) before the dispatcher (services/phone_actions.py) sends it.
Autonomous sends need the phone's Messages switch reported on (unknown counts as paused): the owner's switch, no
backend flag. The handset checks the switch, conversation and revision once more when it executes.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import PhoneConversation, PhoneConversationMember, PhonePerson, PhonePersonKey
from app.db.session import SessionLocal
from app.services import phone_actions, phone_notifications, phone_people, phone_reply_check, phone_state

log = logging.getLogger(__name__)

TARGETS_KEPT = 8
NOTHING_SENT = "Nothing was sent."


@dataclass(frozen=True)
class Target:
    """One reply opportunity, bound to the message it answers."""

    handle: str  # notif:...
    key: str
    revision: int
    conversation_id: str
    is_group: bool
    event_id: str
    sender_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Target | None:
        try:
            return cls(handle=str(data["handle"]), key=str(data["key"]), revision=int(data["revision"]),
                       conversation_id=str(data["conversation_id"]), is_group=bool(data["is_group"]),
                       event_id=str(data["event_id"]), sender_id=str(data["sender_id"]))
        except (KeyError, TypeError, ValueError):
            return None

    def same_exchange(self, other: Target) -> bool:
        return (self.key, self.revision, self.conversation_id, self.is_group, self.event_id, self.sender_id) == (
            other.key, other.revision, other.conversation_id, other.is_group, other.event_id, other.sender_id)


def _strong(conversation_id: str | None, conversation_identity: str | None, is_group: bool, message: dict) -> bool:
    """Whether this conversation and this message's sender are identified well enough to answer."""
    return bool(conversation_id and conversation_identity == "shortcut" and message.get("event_id")
                and message.get("sender_id") and message.get("sender_id") != phone_people.OWNER_SENDER
                and (not is_group or message.get("identity") == "contact"))


def _target_from_news(entry: dict) -> Target | None:
    if entry.get("kind") != "posted" or not entry.get("can_reply") or not entry.get("chat"):
        return None
    lines = entry.get("lines") or []
    if not lines or entry.get("revision") is None:
        return None
    trigger = lines[-1]
    group = bool(entry.get("is_group"))
    if not _strong(entry.get("conversation_id"), entry.get("conversation_identity"), group, trigger):
        return None
    return Target(phone_notifications.ref(entry["key"]), entry["key"], int(entry["revision"]),
                  entry["conversation_id"], group, trigger["event_id"], trigger["sender_id"])


def targets_from_news(news: list[dict]) -> list[dict]:
    """The reply opportunities this news brought, one per notification (its latest news), each bound to its own
    triggering message. A notification that isn't eligible (no reply button, a title-identified conversation, an
    unidentified or name-identified group speaker, an older build's news without ids) brings none."""
    latest: dict[str, dict] = {}
    for entry in news:
        if entry.get("kind") == "posted" and entry.get("key"):
            latest.pop(entry["key"], None)
            latest[entry["key"]] = entry
    out = [t.to_dict() for entry in latest.values() if (t := _target_from_news(entry)) is not None]
    return out[-TARGETS_KEPT:]


def target_from_live(n: phone_notifications.Notification) -> Target | None:
    """The reply opportunity a live notification offers now: its newest message, when that's someone else's and
    everything is strongly identified."""
    if not n.can_reply or not n.messages:
        return None
    newest = n.messages[-1]
    if not _strong(n.conversation_id, n.conversation_identity, n.is_group, newest):
        return None
    return Target(n.ref, n.key, int(n.revision), n.conversation_id, bool(n.is_group), newest["event_id"],
                  newest["sender_id"])


def _match(handle: str, targets: list[Target]) -> Target | None:
    wanted = (handle or "").strip().lower().strip("[]").removeprefix(phone_notifications.REF_PREFIX)
    if len(wanted) < 6:
        return None
    found = [t for t in targets if t.handle.removeprefix(phone_notifications.REF_PREFIX).startswith(wanted)]
    return found[0] if len(found) == 1 else None


# --------------------------------------------------------------------------- #
# The checks at the send point
# --------------------------------------------------------------------------- #

def _live(user_id: uuid.UUID, target: Target) -> str | None:
    """Why this target can't be answered now (None: it can). Read fresh every time."""
    state = phone_state.read(user_id)
    if state is None or state.get("messages_on") is not True:
        return ("their Messages switch isn't on (off, or the phone hasn't said): your replies to other people are "
                "paused")
    n = phone_notifications.find(user_id, target.handle)
    if n is None or n.key != target.key:
        return "that notification is gone from their phone"
    if not n.can_reply:
        return "that notification has no reply button now"
    if int(n.revision) != target.revision:
        return "that notification changed since (a newer message, or it was answered on the phone)"
    now = target_from_live(n)
    if now is None or not now.same_exchange(target):
        return "who you'd be answering there changed"
    return None


def _bound(material: phone_reply_check.Material, target: Target) -> bool:
    """The check read the same notification, revision, conversation and recipient this send is for."""
    return (material.key == target.key and material.revision == target.revision
            and material.conversation_id == target.conversation_id and material.is_group == target.is_group
            and material.recipient_sender == target.sender_id and material.answering_event == target.event_id
            and material.recipient_person_id is not None)


def _binding(material: phone_reply_check.Material) -> str:
    """What must not change between the check and the send: the target, and what the user authorized and the
    requests for space the check read."""
    parts = [material.key, str(material.revision), material.conversation_id or "", material.recipient_sender or "",
             material.answering_event or "",
             str(material.recipient_person_id), *sorted(material.authorized), "\x01", *sorted(material.requests)]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def _held(why: str) -> str:
    return f"Not sent: {why}. {NOTHING_SENT}"


def send(user_id: uuid.UUID, chat_conversation_id: uuid.UUID, target: Target, text: str, attempt_key: str, *,
         model: Callable | None = None, now: float | None = None) -> str:
    """Send her ``text`` to ``target`` once for ``attempt_key``, if it passes every check. Returns what happened,
    in words her mind reads."""
    draft = " ".join((text or "").split())
    if not draft:
        return _held("there's no reply text")
    if len(draft) > phone_reply_check.REPLY_CHARS:
        return _held(f"the reply is longer than {phone_reply_check.REPLY_CHARS} characters; it is never cut, "
                     "write a shorter one if you still want to")
    if why := _live(user_id, target):
        return _held(why)
    device = phone_actions.target_for(user_id)
    if device is None:
        return _held("no signed-in phone can take it")
    check = phone_reply_check.check_reply(user_id, target.handle, draft, model=model, now=now)
    if not check.send:
        reveals = f" (it would reveal: {'; '.join(check.reveals)})" if check.reveals else ""
        return (f"Held by the check before sending: {check.why}{reveals}. {NOTHING_SENT} You can say it "
                "otherwise, ask the user privately, or leave it.")
    if check.material is None or not _bound(check.material, target):
        return _held("the check read a different exchange than the one you're answering (it changed meanwhile)")
    # The last backend send point: everything again, after the model call.
    if why := _live(user_id, target):
        return _held(why)
    again = phone_actions.target_for(user_id)
    if again is None or again[0] != device[0]:
        return _held("the phone it would go through changed")
    try:
        fresh = phone_reply_check.material_for(user_id, target.handle, now=now)
    except Exception:  # noqa: BLE001 - what can't be read again is never a reason to send
        log.warning("phone_replies: the conversation couldn't be read again before sending", exc_info=True)
        return _held("the conversation couldn't be read again just before sending")
    if not _bound(fresh, target) or _binding(fresh) != _binding(check.material):
        return _held("what the user authorized, a request for space, or who you're answering changed during the "
                     "check")
    claim = phone_people.claim_outbound(
        user_id, attempt_key, key=target.key, conversation_id=target.conversation_id, revision=target.revision,
        text=draft, person_id=None if target.is_group else fresh.recipient_person_id)
    if claim is None:
        return ("A reply for this was already sent or attempted (it's never sent twice). " + NOTHING_SENT)
    n = phone_notifications.find(user_id, target.handle)
    who = phone_notifications.who(n) if n is not None else "them"
    outcome = phone_actions.act(
        user_id, chat_conversation_id, "reply_notification",
        {"key": target.key, "conversation_id": target.conversation_id, "revision": target.revision, "text": draft},
        target=again, view={"who": who, "text": draft}, asked_by="her", action_id=claim, claimed=True)
    return outcome.text


# --------------------------------------------------------------------------- #
# What a wake may answer
# --------------------------------------------------------------------------- #

def reply(user_id: uuid.UUID, chat_conversation_id: uuid.UUID, handle: str, text: str, *,
          targets: list[dict] | None = None, follow_up: dict | None = None, wake_id: str | None = None,
          model: Callable | None = None) -> str:
    """Her reply on a wake: to one of the messages that woke her (``targets``), or her follow-up come due
    (``follow_up``, once per due occurrence ``wake_id``)."""
    if follow_up is not None:
        target = follow_up_target(user_id, follow_up, handle)
        if isinstance(target, str):
            return _held(target) + " A later message from them can bring a fresh chance."
        return send(user_id, chat_conversation_id, target, text, f"followup:{wake_id}", model=model)
    bound = [t for raw in targets or [] if (t := Target.from_dict(raw)) is not None]
    target = _match(handle, bound)
    if target is None:
        return _held("that isn't a message that woke you; on this turn you can answer only "
                     + (", ".join(f"[{t.handle}]" for t in bound) or "none"))
    return send(user_id, chat_conversation_id, target, text, f"reply:{target.conversation_id}:{target.event_id}",
                model=model)


def _person_for(user_id: uuid.UUID, conversation_id: str, sender_id: str) -> uuid.UUID | None:
    with SessionLocal() as db:
        conversation = db.scalar(select(PhoneConversation).where(
            PhoneConversation.user_id == user_id, PhoneConversation.conversation_id == conversation_id))
        key = db.scalar(select(PhonePersonKey).where(PhonePersonKey.user_id == user_id,
                                                     PhonePersonKey.sender_id == sender_id))
        if conversation is None or key is None:
            return None
        member = db.scalar(select(PhoneConversationMember.person_id).where(
            PhoneConversationMember.conversation_id == conversation.id,
            PhoneConversationMember.person_id == key.person_id))
        return member if member is not None and db.get(PhonePerson, member) is not None else None


def follow_up_for(user_id: uuid.UUID, handle: str) -> dict:
    """Who and where a follow-up is with, by stable ids, from a notification she names now. Raises ValueError."""
    n = phone_notifications.find(user_id, handle)
    if n is None:
        raise ValueError(f"there's no notification {handle} on their phone now")
    target = target_from_live(n)
    if target is None:
        raise ValueError("that conversation or who wrote last there can't be identified reliably (or the newest "
                         "message isn't someone else's), so no follow-up there can be kept")
    try:
        person_id = _person_for(user_id, target.conversation_id, target.sender_id)
    except SQLAlchemyError:
        raise ValueError("who they are couldn't be read just now") from None
    if person_id is None:
        raise ValueError("who wrote there isn't recorded yet")
    return {"person_id": str(person_id), "conversation_id": target.conversation_id, "is_group": target.is_group,
            "sender_id": target.sender_id, "event_id": target.event_id}


def follow_up_target(user_id: uuid.UUID, follow_up: dict, handle: str | None) -> Target | str:
    """The reply opportunity for a follow-up come due, or why there's none: a live notification of the same
    conversation whose newest message is the same person's, strongly identified."""
    conversation_id, sender_id = follow_up.get("conversation_id"), follow_up.get("sender_id")
    live = [n for n in phone_notifications.live(user_id) or [] if n.conversation_id == conversation_id]
    if handle:
        n = phone_notifications.find(user_id, handle)
        if n is not None and n.conversation_id != conversation_id:
            return "that notification is another conversation than the one this follow-up is for"
        live = [n] if n is not None else []
    if not live:
        return "that conversation has no notification on their phone now, so there's no reply button to use"
    for n in live:
        target = target_from_live(n)
        if target is None:
            continue
        if target.is_group != bool(follow_up.get("is_group")):
            return "that conversation isn't the same kind of chat it was"
        if target.sender_id != sender_id:
            continue
        try:
            person = _person_for(user_id, conversation_id, sender_id)
        except SQLAlchemyError:
            return "who they are couldn't be read just now"
        if person is None or str(person) != str(follow_up.get("person_id")):
            return "the person there isn't the one this follow-up is for"
        return target
    return ("the newest message there isn't from the person this follow-up is for (someone else wrote last, or it "
            "was answered), so there's nobody there you'd be answering")


def follow_up_lines(user_id: uuid.UUID, follow_up: dict) -> list[str]:
    """Her "Now" lines for a follow-up come due: who and where, and whether there's a live chance to write."""
    out = [f"- It's a follow-up with [{phone_people.handle(follow_up.get('person_id') or '')}] in one conversation "
           "on their phone" + (" (a group)" if follow_up.get("is_group") else " (a direct chat)")
           + ". Reread it (read_phone_conversation) before deciding: they may have answered, asked for space or "
           "moved on."]
    try:
        target = follow_up_target(user_id, follow_up, None)
    except Exception:  # noqa: BLE001 - unknown is said, never guessed
        log.warning("phone_replies: a follow-up's conversation couldn't be read", exc_info=True)
        return [*out, "- Whether there's a live reply button there couldn't be read just now."]
    if isinstance(target, str):
        out.append(f"- Nothing can be sent there now: {target}. A later message from them can bring a fresh chance.")
    else:
        out.append(f"- Its notification is on their phone now with a reply button: [{target.handle}].")
    return out
