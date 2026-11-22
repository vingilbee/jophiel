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
from app.schemas.phone import PhoneActionResult
from app.services import agent_say, fcm, phone_devices, presence

log = logging.getLogger(__name__)

EXPIRES_SECONDS = 60
RESULT_GRACE_SECONDS = 60
ACTION_TTL = EXPIRES_SECONDS + RESULT_GRACE_SECONDS
ACTIVITY_RETRIES = "phone:action:activity-retries"
ACTIVITY_RETRY_SECONDS = 5
ACTIVITY_RETRY_BATCH = 25
WAIT_SECONDS = {"navigate_to": 10, "get_position": 15, "resolve_saved_place": 10}
COORDINATE_ACTIONS = {"get_position", "resolve_saved_place"}
REASONS = {
    "switched_off": "phone actions or location sharing are switched off",
    "location_off": "location is off",
    "no_permission": "location permission is missing",
    "locked": "phone locked",
    "maps_missing": "Google Maps is missing",
    "no_fix": "the phone couldn't get a location fix",
    "timeout": "the phone couldn't get a location fix in time",
    "not_found": "there's no saved place with that name",
    "ambiguous": "more than one saved place has that name",
}


@dataclass(frozen=True)
class Outcome:
    status: str
    action_id: uuid.UUID
    text: str
    point: tuple[float, float] | None = field(default=None, repr=False)


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
    text: str, *, late: bool = False,
) -> None:
    with SessionLocal.begin() as db:
        # Use an insert-select so deleting the conversation doesn't recreate it or leave an orphan.
        values = select(literal(activity_id), Conversation.id, literal(text), literal(late)).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id)
        created_at = db.scalar(
            insert(PhoneActivity).from_select(["id", "conversation_id", "content", "mind_pending"], values)
            .on_conflict_do_nothing(index_elements=[PhoneActivity.id]).returning(PhoneActivity.created_at)
        )
    if created_at is not None and client is not None:
        try:
            client.publish(agent_say.channel(user_id), json.dumps({
                "kind": "phone_activity", "conversation_id": str(conversation_id),
                "message_id": str(activity_id), "text": text, "created_at": created_at.isoformat(),
            }))
        except redis.RedisError:
            # The activity is still available in history.
            log.warning("Phone activity could not be announced")


def _try_record(
    client: redis.Redis | None, user_id: uuid.UUID, conversation_id: uuid.UUID, activity_id: uuid.UUID, text: str,
) -> None:
    try:
        _record_activity(client, user_id, conversation_id, activity_id, text)
    except SQLAlchemyError:
        if client is not None:
            # Retain the history line independently of the short-lived action and its reply.
            entry = json.dumps([str(user_id), str(conversation_id), str(activity_id), text])
            try:
                client.zadd(ACTIVITY_RETRIES, {entry: time.time()}, nx=True)
                return
            except redis.RedisError:
                pass
        log.warning("Phone activity could not be written or queued for retry")


def retry_activities() -> None:
    """Retry failed history writes with the original IDs so multiple workers can safely overlap."""
    client = _client()
    if client is None:
        return
    try:
        for entry in client.zrangebyscore(ACTIVITY_RETRIES, "-inf", time.time(), start=0, num=ACTIVITY_RETRY_BATCH):
            user_id, conversation_id, activity_id, text = json.loads(entry)
            try:
                _record_activity(client, uuid.UUID(user_id), uuid.UUID(conversation_id), uuid.UUID(activity_id), text)
            except SQLAlchemyError:
                client.zadd(ACTIVITY_RETRIES, {entry: time.time() + ACTIVITY_RETRY_SECONDS}, xx=True)
                break
            client.zrem(ACTIVITY_RETRIES, entry)
    except redis.RedisError:
        log.warning("Phone activity retries unavailable")


def record_position_use(user_id: uuid.UUID, conversation_id: uuid.UUID, outcome: Outcome) -> None:
    _try_record(_client(), user_id, conversation_id, outcome.action_id, "Used your location once")


def _result_text(action: str, status: str, reason: str | None, name: str) -> str:
    why = REASONS.get(reason or "")
    if status == "expired":
        return {"navigate_to": "Maps action expired", "get_position": "Phone location request expired",
                "resolve_saved_place": "Saved place request expired"}[action]
    if action == "get_position":
        return "Phone returned a location" if status == "done" else (
            f"Couldn't get your location ({why})" if why else "Couldn't get your location")
    if action == "resolve_saved_place":
        return "Checked a saved place" if status == "done" else (
            f"Couldn't look up the saved place ({why})" if why else "Couldn't look up the saved place")
    if status == "done":
        return f"Opened Maps → {name}"
    suffix = f" ({why})" if why else ""
    if status == "notified":
        return f"Couldn't open Maps{suffix}, sent a notification"
    return f"Couldn't open Maps{suffix}"


def _send(
    client: redis.Redis, user_id: uuid.UUID, session_hash: str, socket_id: str | None, frame: dict,
    expires: float,
) -> str:
    if socket_id and client.publish(socket_channel(socket_id), json.dumps(frame)):
        return "sent"
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
        return "sent"
    return "uncertain" if result.delivery_unknown else "failed"


def _record_late(client: redis.Redis, user_id: uuid.UUID, conversation_id: uuid.UUID, action_id: uuid.UUID,
                 action: str, reply: dict) -> None:
    text = reply["text"]
    if action == "get_position" and reply["status"] == "done":
        text = "Phone returned a location after the lookup timed out"
    _record_activity(client, user_id, conversation_id, action_id, text, late=True)


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
        target: tuple[str, str | None] | None = None) -> Outcome:
    action_id = uuid.uuid4()
    client = _client()
    target_label = {"navigate_to": "Maps action", "get_position": "Phone location request",
                    "resolve_saved_place": "Saved place request"}[action]

    def failed(status: str, text: str) -> Outcome:
        # A separate ID lets a late arrival add the definitive outcome.
        receipt_id = uuid.uuid5(action_id, "dispatch") if status == "unconfirmed" else action_id
        _try_record(client, user_id, conversation_id, receipt_id, text)
        return Outcome(status, action_id, text)

    if client is None:
        return failed("failed", f"{target_label} unavailable")
    delivery = None
    try:
        target = target or target_for(user_id)
        if target is None:
            return failed("failed", f"{target_label} unavailable: no signed-in phone")
        session_hash, socket_id = target
        private = X25519PrivateKey.generate() if action in COORDINATE_ACTIONS else None
        channel = _reply_channel(action_id)
        with client.pubsub() as replies:
            replies.subscribe(channel)
            if replies.get_message(timeout=2) is None:
                return failed("failed", f"{target_label} unavailable")
            deadline = time.time() + WAIT_SECONDS[action]
            expires = time.time() + EXPIRES_SECONDS
            pending = {
                "user": str(user_id), "session": session_hash, "conversation": str(conversation_id),
                "action": action, "deadline": deadline,
                "name": args.get("name") or "destination",
                "public_key": _public_key(private) if private else None,
                "waiter_records_activity": True,
            }
            client.set(_pending_key(action_id), json.dumps(pending), ex=ACTION_TTL)
            frame = {
                "type": "phone_action", "action_id": str(action_id), "action": action, "args": args,
                "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="milliseconds"),
            }
            delivery = "uncertain"  # a transport error during publication may leave the delivery unknown
            delivery = _send(client, user_id, session_hash, socket_id, frame, expires)
            if delivery == "failed":
                client.delete(_pending_key(action_id))
                return failed("failed", f"Couldn't send the {target_label.lower()} to the phone")
            while (remaining := deadline - time.time()) > 0:
                message = replies.get_message(ignore_subscribe_messages=True, timeout=remaining)
                if not message:
                    continue
                result = json.loads(message["data"])
                point = None
                if private is not None and "point" in result:
                    point = tuple(json.loads(_cipher(private, result["key"], action_id)
                                             .decrypt(result["point"].encode())))
                if action != "get_position" or result["status"] != "done":
                    _try_record(client, user_id, conversation_id, action_id, result["text"])
                return Outcome(result["status"], action_id, result["text"], point)
    except (redis.RedisError, SQLAlchemyError, InvalidToken):
        log.warning("Phone action transport interrupted")
    if delivery is None:
        return failed("failed", f"{target_label} unavailable")
    if delivery == "failed":
        return failed("failed", f"Couldn't send the {target_label.lower()} to the phone")
    state = "sent" if delivery == "sent" else "delivery unconfirmed"
    outcome = failed("unconfirmed", f"{target_label} {state}, no answer yet. It may or may not have happened.")
    try:
        _close_waiter(client, user_id, conversation_id, action_id, action)
    except (redis.RedisError, SQLAlchemyError):
        log.warning("Phone action waiter closure could not be recorded")
    return outcome


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
        needs_point = action in COORDINATE_ACTIONS and result.status == "done"
        if needs_point != (result.data is not None) or (result.status == "notified" and action != "navigate_to"):
            raise InvalidResult
        with client.lock(f"{key}:lock", timeout=10, blocking_timeout=2):
            if raw := client.get(f"{key}:answered"):
                if raw != b"1" and client.exists(f"{key}:closed"):
                    _record_late(client, user_id, conversation_id, action_id, action, json.loads(raw))
                return True
            text = _result_text(action, result.status, result.reason, pending["name"])
            reply = {"status": result.status, "text": text}
            summary = json.dumps(reply)
            if time.time() >= pending["deadline"] or client.exists(f"{key}:closed"):
                with client.pipeline() as transaction:
                    transaction.set(f"{key}:closed", "1", ex=ACTION_TTL)
                    transaction.set(f"{key}:answered", summary, ex=ACTION_TTL)
                    transaction.execute()
                _record_late(client, user_id, conversation_id, action_id, action, reply)
                return True
            # An action already in flight during rollout may still have an older reply worker.
            if not pending.get("waiter_records_activity") and (action != "get_position" or not needs_point):
                _record_activity(client, user_id, conversation_id, action_id, text)
            if needs_point:
                private = X25519PrivateKey.generate()
                reply["key"] = _public_key(private)
                reply["point"] = _cipher(private, pending["public_key"], action_id).encrypt(
                    json.dumps([result.data.lat, result.data.lng]).encode()
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
