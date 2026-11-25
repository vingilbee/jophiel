"""One-shot commands for a single authenticated phone session.

Redis keeps action IDs and deadlines briefly and relays replies over Pub/Sub. A coordinate reply is
encrypted to the waiting worker's ephemeral key, so it never enters Redis storage or command logs in plaintext.
Only human-readable activity lines go to Postgres.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

import httpx
import redis
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Conversation, PhoneActivity
from app.db.session import SessionLocal
from app.schemas.chat import ChatMessageOut
from app.schemas.phone import (
    PhoneActionData, PhoneActionDraft, PhoneActionOpened, PhoneActionResult, PhoneActionScreen,
)
from app.services import agent_say, fcm, phone_control, phone_devices, phone_people, presence

log = logging.getLogger(__name__)

EXPIRES_SECONDS = 60
RESULT_GRACE_SECONDS = 60
ACTION_TTL = EXPIRES_SECONDS + RESULT_GRACE_SECONDS
ACTIVITY_RETRIES = "phone:action:activity-retries"
ACTIVITY_RETRY_SECONDS = 5
ACTIVITY_RETRY_BATCH = 25
# Phase 9: a control action settles for up to 2 s and takes a screenshot; read_screen may first wait up to 5 s.
WAIT_SECONDS = {"navigate_to": 10, "get_position": 15, "resolve_saved_place": 10, "read_screen": 15}
DEFAULT_WAIT_SECONDS = 10
# A push has to wake the app before it can answer: wait this long at least once the action went by FCM.
PUSH_WAIT_SECONDS = 20
COORDINATE_ACTIONS = {"get_position", "resolve_saved_place"}
# Phase 9 (frontend-phone/PLAN_PHASE_9.md): her using the phone's screen. Each answers ``done`` with the screen after
# it (``SCREEN_ACTIONS``), encrypted to the waiter like a coordinate, never kept in Redis or Postgres; no chat line
# per action (the turn writes one, app/ai/tools/phone_control.py). ``args["turn"]``: the reply turn; a newer turn
# holding the phone answers ``taken_over`` (services/phone_control.py).
SCREEN_ACTIONS = {"read_screen", "tap", "long_press", "type_text", "scroll", "tap_point", "swipe", "press"}
CONTROL_ACTIONS = SCREEN_ACTIONS | {"end_control"}
SEALED_ACTIONS = COORDINATE_ACTIONS | SCREEN_ACTIONS
# What each action is called in "unavailable / sent, no answer yet" lines.
LABELS = {
    "navigate_to": "Maps action", "get_position": "Phone location request", "resolve_saved_place": "Saved place request",
    "media_control": "Media control", "set_volume": "Volume change", "set_alarm": "Alarm", "set_timer": "Timer",
    "dismiss_notification": "Dismissing the notification", "set_ringer": "Ringer change",
    "set_dnd": "Do Not Disturb change", "reply_notification": "Reply", "open_app": "Opening the app",
    "open_link": "Opening the link", "play_music": "Spotify request", "whatsapp_draft": "WhatsApp draft",
    **{name: "Phone control" for name in CONTROL_ACTIONS},
}
# Actions that put something on the screen: from the background the phone may post a notification instead.
OPENING_ACTIONS = {"navigate_to", "open_app", "open_link", "play_music", "whatsapp_draft"}
REASONS = {
    "switched_off": "that's switched off in the phone's settings",
    "location_off": "location is off",
    "no_permission": "location permission is missing",
    "locked": "phone locked",
    "maps_missing": "Google Maps is missing",
    "no_fix": "the phone couldn't get a location fix",
    "timeout": "the phone couldn't get a location fix in time",
    "not_found": "there's no saved place with that name",
    "ambiguous": "more than one saved place has that name",
    "no_notification_access": "the app has no notification access",
    "no_dnd_access": "the app has no Do Not Disturb access",
    "no_contacts_access": "the app has no contacts access",
    "notification_gone": "the notification is gone",
    "no_reply_button": "the notification has no reply button",
    "no_media_session": "nothing is playing that can be controlled",
    "not_clearable": "that notification can't be dismissed",
    "app_not_found": "no app with that name is installed",
    "stale": "the notification changed since",
    "replies_paused": "their Messages switch is off (replies paused)",
}
LOCATION_REASONS = {**REASONS, "switched_off": "phone actions or location sharing are switched off"}
ACTION_REASONS = {"ambiguous": "more than one match", "timeout": "the phone ran out of time"}


@dataclass(frozen=True)
class Outcome:
    status: str
    action_id: uuid.UUID
    text: str
    point: tuple[float, float] | None = field(default=None, repr=False)
    # The phone's reason code (``taken_over``, ``stale_screen``...) and, for a control action that's done, the screen
    # after it (``data.screen`` as the phone sent it: in this process only, never stored).
    reason: str | None = None
    screen: dict | None = field(default=None, repr=False)


class InvalidResult(ValueError):
    """The result does not match the action that was sent."""


class ResultUnavailable(RuntimeError):
    """Temporary result transport or storage error."""


@lru_cache(maxsize=4)
def _redis(url: str) -> redis.Redis:
    return redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def _client() -> redis.Redis | None:
    url = get_settings().redis_url
    return _redis(url) if url else None


def socket_channel(socket_id: uuid.UUID | str) -> str:
    return f"phone:action:socket:{socket_id}"


def _reply_channel(action_id: uuid.UUID) -> str:
    return f"phone:action:reply:{action_id}"


def _pending_key(action_id: uuid.UUID) -> str:
    return f"phone:action:pending:{action_id}"


def target_for(user_id: uuid.UUID) -> tuple[str, str | None] | None:
    try:
        if socket := presence.action_socket(user_id):
            return socket
        with SessionLocal() as db:
            device = phone_devices.action_device(db, user_id, None)
            return (device.session_hash, None) if device else None
    except (redis.RedisError, SQLAlchemyError):
        log.warning("Could not look up a phone for action delivery")
        return None


def _public_key(key: X25519PrivateKey) -> str:
    return base64.urlsafe_b64encode(key.public_key().public_bytes_raw()).decode()


def _cipher(key: X25519PrivateKey, peer: str, action_id: uuid.UUID) -> Fernet:
    secret = key.exchange(X25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(peer)))
    key_bytes = HKDF(algorithm=hashes.SHA256(), length=32, salt=action_id.bytes,
                     info=b"jophiel phone action result").derive(secret)
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def history(db: Session, conversation_id: uuid.UUID) -> list[ChatMessageOut]:
    rows = db.scalars(select(PhoneActivity).where(PhoneActivity.conversation_id == conversation_id)
                      .order_by(PhoneActivity.created_at, PhoneActivity.id))
    return [ChatMessageOut(id=row.id, role="assistant", kind="phone_activity", source_channel="system",
                           content=row.content, created_at=row.created_at) for row in rows]


def pending_news(db: Session) -> dict[uuid.UUID, list[tuple[uuid.UUID, str]]]:
    rows = db.execute(
        select(Conversation.user_id, PhoneActivity.id, PhoneActivity.content)
        .join(Conversation, Conversation.id == PhoneActivity.conversation_id)
        .where(PhoneActivity.mind_pending)
        .order_by(PhoneActivity.created_at, PhoneActivity.id)
        .limit(100)
    )
    users: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for user_id, activity_id, content in rows:
        users.setdefault(user_id, []).append((activity_id, content))
    return users


def acknowledge_news(activity_ids: list[uuid.UUID]) -> None:
    if activity_ids:
        with SessionLocal.begin() as db:
            db.execute(update(PhoneActivity).where(PhoneActivity.id.in_(activity_ids)).values(mind_pending=False))


def _record_activity(
    client: redis.Redis | None, user_id: uuid.UUID, conversation_id: uuid.UUID, activity_id: uuid.UUID,
    text: str, *, late: bool = False, occurred_at: datetime | None = None, replace: bool = False,
) -> None:
    """Write one chat line (once per id). ``replace``: a late answer taking the place of the "sent, no answer
    yet" line it settles (same id: the phone updates the line it shows instead of adding one)."""
    occurred_at = occurred_at or datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal.begin() as db:
        # Use an insert-select so deleting the conversation doesn't recreate it or leave an orphan.
        values = select(literal(activity_id), Conversation.id, literal(text), literal(late), literal(occurred_at))
        values = values.where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        statement = insert(PhoneActivity).from_select(
            ["id", "conversation_id", "content", "mind_pending", "created_at"], values)
        if replace:
            # Keep the line's place (its created_at); only a changed text is news again.
            statement = statement.on_conflict_do_update(
                index_elements=[PhoneActivity.id], set_={"content": statement.excluded.content, "mind_pending": True},
                where=PhoneActivity.content != statement.excluded.content)
        else:
            statement = statement.on_conflict_do_nothing(index_elements=[PhoneActivity.id])
        created_at = db.scalar(statement.returning(PhoneActivity.created_at))
    if created_at is not None and client is not None:
        try:
            client.publish(agent_say.channel(user_id), json.dumps({
                "kind": "phone_activity", "conversation_id": str(conversation_id),
                "message_id": str(activity_id), "text": text, "created_at": created_at.isoformat(),
                **({"replaces": True} if replace else {}),
            }))
        except redis.RedisError:
            # The activity is still available in history.
            log.warning("Phone activity could not be announced")


def _try_record(
    client: redis.Redis | None, user_id: uuid.UUID, conversation_id: uuid.UUID, activity_id: uuid.UUID, text: str,
) -> None:
    occurred_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        _record_activity(client, user_id, conversation_id, activity_id, text, occurred_at=occurred_at)
    except SQLAlchemyError:
        if client is not None:
            # Retain the history line independently of the short-lived action and its reply.
            entry = json.dumps([str(user_id), str(conversation_id), str(activity_id), text, occurred_at.isoformat()])
            try:
                client.zadd(ACTIVITY_RETRIES, {entry: time.time()}, nx=True)
                return
            except redis.RedisError:
                pass
        log.warning("Phone activity could not be written or queued for retry")


def retry_activities() -> None:
    """Retry history writes with their original message IDs and timestamps."""
    client = _client()
    if client is None:
        return
    try:
        entries = client.zrangebyscore(
            ACTIVITY_RETRIES, "-inf", time.time(), start=0, num=ACTIVITY_RETRY_BATCH, withscores=True)
        for entry, due_at in entries:
            user_id, conversation_id, activity_id, text, *timestamp = json.loads(entry)
            # Older queued entries only have the scheduling score as a time reference.
            occurred_at = (datetime.fromisoformat(timestamp[0]) if timestamp
                           else datetime.fromtimestamp(due_at, timezone.utc).replace(tzinfo=None))
            try:
                _record_activity(client, uuid.UUID(user_id), uuid.UUID(conversation_id), uuid.UUID(activity_id),
                                 text, occurred_at=occurred_at)
            except SQLAlchemyError:
                client.zadd(ACTIVITY_RETRIES, {entry: time.time() + ACTIVITY_RETRY_SECONDS}, xx=True)
                break
            client.zrem(ACTIVITY_RETRIES, entry)
    except redis.RedisError:
        log.warning("Phone activity retries unavailable")


def record_line(user_id: uuid.UUID, conversation_id: uuid.UUID, line_id: uuid.UUID, text: str) -> None:
    """One chat line of hers about the phone, once per id (phase 9: a turn's use of the phone screen)."""
    _try_record(_client(), user_id, conversation_id, line_id, text)


def record_position_use(user_id: uuid.UUID, conversation_id: uuid.UUID, outcome: Outcome) -> None:
    _try_record(_client(), user_id, conversation_id, outcome.action_id, "Used your location once")


def _why(action: str, reason: str | None) -> str | None:
    if action in COORDINATE_ACTIONS or action == "navigate_to":
        return LOCATION_REASONS.get(reason or "")
    return ACTION_REASONS.get(reason or "") or REASONS.get(reason or "")


def _quote(text: str | None) -> str:
    return f"'{text}'" if text else "''"


def _done_text(action: str, view: dict, data: dict | None) -> str:
    """What happened, as the chat line says it (replies with their full text)."""
    data = data or {}
    if action == "media_control":
        # What was pressed, not what it did: "previous" may restart the track or go back one, and only the phone's
        # next report shows which.
        return {"play": "Pressed play", "pause": "Pressed pause", "next": "Pressed next",
                "previous": "Pressed previous"}.get(view.get("command"), "Controlled the music")
    if action == "set_volume":
        return f"Set media volume to {view.get('level')}%"
    if action == "set_alarm":
        label = f" ({view['label']})" if view.get("label") else ""
        days = f" on {', '.join(view['days'])}" if view.get("days") else ""
        return f"Set an alarm for {view.get('time')}{days}{label}"
    if action == "set_timer":
        label = f" ({view['label']})" if view.get("label") else ""
        return f"Set a timer for {view.get('duration')}{label}"
    if action == "dismiss_notification":
        return f"Dismissed a notification from {view.get('who') or 'an app'}"
    if action == "set_ringer":
        return f"Set the ringer to {view.get('mode')}"
    if action == "set_dnd":
        return "Turned Do Not Disturb " + ("on" if view.get("on") else "off")
    if action == "reply_notification":
        return f"Replied to {view.get('who') or 'them'}: {_quote(view.get('text'))}"
    if action == "open_app":
        return f"Opened {data.get('opened') or view.get('name')}"
    if action == "open_link":
        return f"Opened {view.get('url')}"
    if action == "play_music":
        return f"Asked Spotify to play {_quote(view.get('query'))}"
    if action == "whatsapp_draft":
        if data.get("target") == "picker":
            return f"Opened WhatsApp with a draft, to pick the chat: {_quote(view.get('text'))}"
        to = f" to {view['to']}" if view.get("to") else ""
        return f"Drafted a WhatsApp message{to} (you press send): {_quote(view.get('text'))}"
    return f"Opened Maps → {view.get('name') or 'destination'}"


def _failed_text(action: str) -> str:
    return {
        "media_control": "Couldn't control the music", "set_volume": "Couldn't change the volume",
        "set_alarm": "Couldn't set the alarm", "set_timer": "Couldn't set the timer",
        "dismiss_notification": "Couldn't dismiss the notification", "set_ringer": "Couldn't change the ringer",
        "set_dnd": "Couldn't change Do Not Disturb", "reply_notification": "Couldn't send the reply",
        "open_app": "Couldn't open the app", "open_link": "Couldn't open the link",
        "play_music": "Couldn't play that on Spotify", "whatsapp_draft": "Couldn't make the WhatsApp draft",
    }.get(action, "Couldn't open Maps")


def _result_text(action: str, status: str, reason: str | None, view: dict, data: dict | None = None) -> str:
    why = _why(action, reason)
    if action in CONTROL_ACTIONS:
        # Read by her tools, never a chat line of its own; the reason code travels beside it.
        return {"done": "Done", "expired": "Expired"}.get(status, f"Not done ({reason or status})")
    if status == "expired":
        return f"{LABELS[action]} expired"
    if action == "get_position":
        return "Phone returned a location" if status == "done" else (
            f"Couldn't get your location ({why})" if why else "Couldn't get your location")
    if action == "resolve_saved_place":
        return "Checked a saved place" if status == "done" else (
            f"Couldn't look up the saved place ({why})" if why else "Couldn't look up the saved place")
    if status == "done":
        return _done_text(action, view, data)
    suffix = f" ({why})" if why else ""
    if status == "notified":
        return f"{_failed_text(action)}{suffix}, sent a notification"
    return f"{_failed_text(action)}{suffix}"


def _send(
    client: redis.Redis, user_id: uuid.UUID, session_hash: str, socket_id: str | None, frame: dict,
    expires: float,
) -> str:
    """``socket`` (a live socket took it), ``push`` (FCM accepted it), ``uncertain`` or ``failed``."""
    if socket_id and client.publish(socket_channel(socket_id), json.dumps(frame)):
        return "socket"
    # If that socket went away before publication, use only the same phone. Once a socket accepts publication,
    # never try another transport or phone, even if no reply comes.
    with SessionLocal() as db:
        device = phone_devices.action_device(db, user_id, session_hash)
        token = device.fcm_token if device else None
    ttl = int(expires - time.time())
    if not token or ttl <= 0:
        return "failed"
    data = {**frame, "args": json.dumps(frame["args"], separators=(",", ":"))}
    with httpx.Client(timeout=4.0) as http:
        result = fcm.send(token, data, client=http, ttl_seconds=ttl)
    if result.outcome == "sent":
        return "push"
    return "uncertain" if result.delivery_unknown else "failed"


def dispatch_line_id(action_id: uuid.UUID) -> uuid.UUID:
    """The id of an action's "sent, no answer yet" line: a late answer replaces that line."""
    return uuid.uuid5(action_id, "dispatch")


def _record_late(client: redis.Redis, user_id: uuid.UUID, conversation_id: uuid.UUID, action_id: uuid.UUID,
                 action: str, reply: dict) -> None:
    if action in CONTROL_ACTIONS:
        return  # no line per control action (the turn writes one); a late screen is simply dropped
    text = reply["text"]
    if action == "reply_notification":
        phone_people.settle_outbound(action_id, reply["status"])
    if action == "get_position" and reply["status"] == "done":
        text = "Phone returned a location after the lookup timed out"
    _record_activity(client, user_id, conversation_id, dispatch_line_id(action_id), text, late=True, replace=True)


def _close_waiter(client: redis.Redis, user_id: uuid.UUID, conversation_id: uuid.UUID,
                  action_id: uuid.UUID, action: str) -> None:
    key = _pending_key(action_id)
    # Pair this transaction with the receiver's write of :answered and check of :closed. Whichever
    # runs second sees both markers. The saved reply contains only its status and human-readable text.
    with client.pipeline() as transaction:
        transaction.set(f"{key}:closed", "1", ex=ACTION_TTL)
        transaction.get(f"{key}:answered")
        _, raw = transaction.execute()
    if raw and raw != b"1":
        _record_late(client, user_id, conversation_id, action_id, action, json.loads(raw))


def act(user_id: uuid.UUID, conversation_id: uuid.UUID, action: str, args: dict, *,
        target: tuple[str, str | None] | None = None, view: dict | None = None, asked_by: str = "user",
        action_id: uuid.UUID | None = None, claimed: bool = False) -> Outcome:
    """Send one action to the user's phone and wait for its answer. ``view``: what the chat line says about it
    (defaults to ``args``; a reply's line names who it went to). The wait is the action's own, longer once it went
    by push (the app has to wake first). A ``reply_notification`` is recorded as what she said to whom
    (services/phone_people.py: ``sending`` before dispatch, settled by its answer, ``unknown`` without one);
    ``asked_by``: ``user`` (they asked for it) or ``her``. ``action_id`` + ``claimed``: a reply of hers already
    claimed as its ``sending`` row (``phone_people.claim_outbound``, affinity B3): settled here, never recorded
    again."""
    action_id = action_id or uuid.uuid4()
    recorded = claimed and action == "reply_notification"
    client = _client()
    target_label = LABELS[action]
    control = action in CONTROL_ACTIONS
    turn = str(args.get("turn")) if control and args.get("turn") else None
    if control:
        view = {}  # nothing of what she types or where she taps is kept beside the action
    if view is None:
        # Never a coordinate in the stored action: Maps and location lines need only the name.
        view = {"name": args.get("name")} if action in COORDINATE_ACTIONS | {"navigate_to"} else args
    view = {k: v for k, v in view.items() if v is not None}

    def failed(status: str, text: str) -> Outcome:
        # The "no answer yet" line has its own ID: a late answer replaces it with the definitive outcome.
        receipt_id = dispatch_line_id(action_id) if status == "unconfirmed" else action_id
        if recorded:
            phone_people.settle_outbound(action_id, status)
        if not control:
            _try_record(client, user_id, conversation_id, receipt_id, text)
        return Outcome(status, action_id, text)

    if client is None:
        return failed("failed", f"{target_label} unavailable")
    if turn is not None and phone_control.superseded(user_id, turn, client=client):
        # The user's newer words took the phone: nothing of this turn's is sent any more.
        return Outcome("refused", action_id, "Not done (taken_over)", reason="taken_over")
    delivery = None
    try:
        target = target or target_for(user_id)
        if target is None:
            return failed("failed", f"{target_label} unavailable: no signed-in phone")
        session_hash, socket_id = target
        private = X25519PrivateKey.generate() if action in SEALED_ACTIONS else None
        channel = _reply_channel(action_id)
        turns = phone_control.turns_channel(user_id) if turn is not None else None
        with client.pubsub() as replies:
            replies.subscribe(*([channel, turns] if turns else [channel]))
            if replies.get_message(timeout=2) is None:
                return failed("failed", f"{target_label} unavailable")
            started = time.time()
            deadline = started + WAIT_SECONDS.get(action, DEFAULT_WAIT_SECONDS)
            expires = started + EXPIRES_SECONDS
            pending = {
                "user": str(user_id), "session": session_hash, "conversation": str(conversation_id),
                "action": action, "deadline": deadline, "view": view,
                "name": args.get("name") or "destination",
                "public_key": _public_key(private) if private else None,
                "waiter_records_activity": True,
            }
            client.set(_pending_key(action_id), json.dumps(pending), ex=ACTION_TTL)
            frame = {
                "type": "phone_action", "action_id": str(action_id), "action": action, "args": args,
                "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="milliseconds"),
            }
            if action == "reply_notification" and not recorded:
                recorded = phone_people.record_outbound(
                    user_id, action_id, key=args["key"], conversation_id=args.get("conversation_id"),
                    revision=int(args.get("revision") or 0), text=args["text"], asked_by=asked_by)
            delivery = "uncertain"  # a transport error during publication may leave the delivery unknown
            delivery = _send(client, user_id, session_hash, socket_id, frame, expires)
            if delivery == "failed":
                client.delete(_pending_key(action_id))
                return failed("failed", f"Couldn't send the {target_label.lower()} to the phone")
            from app.services import system_signals
            system_signals.emit(user_id, "mind", "phone", "reply" if action == "reply_notification" else action,
                                action_id)
            if delivery in ("push", "uncertain") and started + PUSH_WAIT_SECONDS > deadline:
                # The phone has to be woken first. Move the deadline the result endpoint checks too, so an
                # answer inside the longer wait isn't taken as late.
                deadline = started + PUSH_WAIT_SECONDS
                client.set(_pending_key(action_id), json.dumps({**pending, "deadline": deadline}), ex=ACTION_TTL)
            while (remaining := deadline - time.time()) > 0:
                message = replies.get_message(ignore_subscribe_messages=True, timeout=remaining)
                if not message:
                    continue
                if turns is not None and _name(message.get("channel")) == turns:
                    if _name(message["data"]) != turn:
                        # A newer turn took the phone while this one waited: stop waiting (the phone refuses
                        # this turn's actions from now on too; any late answer is dropped).
                        delivery = "superseded"
                        break
                    continue
                result = json.loads(message["data"])
                point = screen = None
                if private is not None and "point" in result:
                    point = tuple(json.loads(_cipher(private, result["key"], action_id)
                                             .decrypt(result["point"].encode())))
                if private is not None and "screen" in result:
                    screen = json.loads(_cipher(private, result["key"], action_id).decrypt(result["screen"].encode()))
                if recorded:
                    phone_people.settle_outbound(action_id, result["status"])
                if not control and (action != "get_position" or result["status"] != "done"):
                    _try_record(client, user_id, conversation_id, action_id, result["text"])
                return Outcome(result["status"], action_id, result["text"], point, reason=result.get("reason"),
                               screen=screen)
    except (redis.RedisError, SQLAlchemyError, InvalidToken):
        log.warning("Phone action transport interrupted")
    if delivery is None:
        return failed("failed", f"{target_label} unavailable")
    if delivery == "superseded":
        try:
            _close_waiter(client, user_id, conversation_id, action_id, action)
        except (redis.RedisError, SQLAlchemyError):
            log.warning("Phone action waiter closure could not be recorded")
        return Outcome("refused", action_id, "Not done (taken_over)", reason="taken_over")
    if delivery == "failed":
        return failed("failed", f"Couldn't send the {target_label.lower()} to the phone")
    state = "sent" if delivery in ("socket", "push") else "delivery unconfirmed"
    outcome = failed("unconfirmed", f"{target_label} {state}, no answer yet. It may or may not have happened.")
    try:
        _close_waiter(client, user_id, conversation_id, action_id, action)
    except (redis.RedisError, SQLAlchemyError):
        log.warning("Phone action waiter closure could not be recorded")
    return outcome


def _name(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _valid_result(action: str, result: PhoneActionResult) -> bool:
    """The answer fits the action: coordinates only (and always) from a coordinate action that's done, an app's
    label only from ``open_app``, a draft's target only from ``whatsapp_draft``, a screen only (and always) from a
    control action that's done (``end_control`` has none); "notified" only from an action that opens something on
    the screen."""
    if result.status == "notified" and action not in OPENING_ACTIONS:
        return False
    data = result.data
    if action in COORDINATE_ACTIONS and result.status == "done":
        return isinstance(data, PhoneActionData)
    if action in SCREEN_ACTIONS and result.status == "done":
        return isinstance(data, PhoneActionScreen)
    if data is None:
        return True
    if result.status != "done":
        return False
    return ((action == "open_app" and isinstance(data, PhoneActionOpened))
            or (action == "whatsapp_draft" and isinstance(data, PhoneActionDraft)))


def accept_result(
    user_id: uuid.UUID, session_hash: str, action_id: uuid.UUID, result: PhoneActionResult,
) -> bool:
    """Accept the first valid answer from the session to which this action was sent."""
    client = _client()
    if client is None:
        raise ResultUnavailable
    key = _pending_key(action_id)
    try:
        raw = client.get(key)
        if not raw:
            return False
        pending = json.loads(raw)
        if pending["user"] != str(user_id) or pending["session"] != session_hash:
            return False
        action = pending["action"]
        conversation_id = uuid.UUID(pending["conversation"])
        if not _valid_result(action, result):
            raise InvalidResult
        needs_point = action in COORDINATE_ACTIONS and result.status == "done"
        needs_screen = action in SCREEN_ACTIONS and result.status == "done"
        with client.lock(f"{key}:lock", timeout=10, blocking_timeout=2):
            if raw := client.get(f"{key}:answered"):
                if raw != b"1" and client.exists(f"{key}:closed"):
                    _record_late(client, user_id, conversation_id, action_id, action, json.loads(raw))
                return True
            view = pending.get("view") or {"name": pending["name"]}
            data = (result.data.model_dump() if result.data is not None and not (needs_point or needs_screen)
                    else None)
            text = _result_text(action, result.status, result.reason, view, data)
            reply = {"status": result.status, "text": text}
            if action in CONTROL_ACTIONS and result.reason:
                reply["reason"] = result.reason
            summary = json.dumps(reply)
            if time.time() >= pending["deadline"] or client.exists(f"{key}:closed"):
                with client.pipeline() as transaction:
                    transaction.set(f"{key}:closed", "1", ex=ACTION_TTL)
                    transaction.set(f"{key}:answered", summary, ex=ACTION_TTL)
                    transaction.execute()
                _record_late(client, user_id, conversation_id, action_id, action, reply)
                return True
            # An action already in flight during rollout may still have an older reply worker.
            if (not pending.get("waiter_records_activity") and action not in CONTROL_ACTIONS
                    and (action != "get_position" or not needs_point)):
                _record_activity(client, user_id, conversation_id, action_id, text)
            if needs_point:
                private = X25519PrivateKey.generate()
                reply["key"] = _public_key(private)
                reply["point"] = _cipher(private, pending["public_key"], action_id).encrypt(
                    json.dumps([result.data.lat, result.data.lng]).encode()
                ).decode()
            if needs_screen:
                # The screen goes to the waiting worker only, sealed: never in a stored key, never readable on the bus.
                private = X25519PrivateKey.generate()
                reply["key"] = _public_key(private)
                reply["screen"] = _cipher(private, pending["public_key"], action_id).encrypt(
                    result.data.screen.model_dump_json(exclude_none=True).encode()
                ).decode()
            with client.pipeline() as transaction:
                transaction.set(f"{key}:answered", summary, ex=ACTION_TTL)
                transaction.exists(f"{key}:closed")
                transaction.publish(_reply_channel(action_id), json.dumps(reply))
                _, closed, listeners = transaction.execute()
            if closed or not listeners:
                _close_waiter(client, user_id, conversation_id, action_id, action)
            return True
    except (redis.RedisError, SQLAlchemyError):
        raise ResultUnavailable from None
