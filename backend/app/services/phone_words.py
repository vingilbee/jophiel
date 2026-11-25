"""The user's words while her hands are on their phone: they reach her in the turn she's working in, never stop it
(2026-11-25, the user's decision after a Clash of Clans session where every sentence of their commentary cut her
turn off, so she could neither keep playing nor keep watching).

- **Hands on** (``hands_on``): from her first step on the phone screen in a turn until that turn ends
  (``hands_off``, from ``PhoneControl`` in app/ai/tools/phone_control.py), Redis holds which turn and conversation
  her hands are in (``HANDS_TTL_SECONDS``, renewed on every step). The turn's own stream says so to the user's apps
  (a ``hands`` frame), so the phone's voice loop doesn't cut her off when they talk over her.
- **Their words** (``offer``): a message they send or say in that conversation meanwhile is saved with its own job
  as usual (routes/chat.py), and the job simply waits: the chat worker never runs two jobs of one conversation at
  once. Its words are also queued for the running turn instead of stopping it.
- **Reaching her** (``take``): each of her phone steps answers with the new screen and, under it, what they said
  since her last step. A message she read there is answered in that turn: its job is closed as completed (only
  while still pending), its second tick goes out, and its own stream ends with a ``done`` naming the turn
  (``joined``). One she never read (the turn ended first) keeps its job and is answered next, as its own turn.
- **Not joined:** a message with attachments, one in another conversation, anything after Stop on the banner
  (``taken_over``: her hands are off) or after the turn ended. Those take the ordinary path (the running turn is
  stopped, the message starts a new one).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import redis

log = logging.getLogger(__name__)

HANDS_TTL_SECONDS = 120
WORDS_TTL_SECONDS = 60 * 60
WORDS_CHARS = 2000  # one message as she reads it under a screen

# Delete the hands key only while it still names this turn (a newer turn may hold it).
_RELEASE = """
local raw = redis.call('GET', KEYS[1])
if raw and cjson.decode(raw)['turn'] == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""


@dataclass(frozen=True)
class Hands:
    turn: str
    conversation_id: str


@dataclass(frozen=True)
class Words:
    job_id: str
    message_id: str
    turn_id: str
    text: str
    at: float


def _hands_key(user_id: uuid.UUID | str) -> str:
    return f"phone:control:hands:{user_id}"


def _words_key(turn: str) -> str:
    return f"phone:control:words:{turn}"


def _client() -> redis.Redis | None:
    from app.services import phone_actions
    return phone_actions._client()


def _publish(client: redis.Redis, turn: uuid.UUID | str, event: dict) -> None:
    """A frame on the turn's own event stream (the one the chat worker writes and the socket follows: see
    ``app.workers.chat.publish``), written here so this module never imports the worker."""
    from app.core.config import get_settings
    key = f"{get_settings().chat_event_stream_prefix}{turn}"
    try:
        with client.pipeline(transaction=False) as pipe:
            pipe.xadd(key, {"event": json.dumps(event)}, maxlen=2000, approximate=True)
            pipe.expire(key, 86400)
            pipe.execute()
    except redis.RedisError:  # a frame the apps miss changes nothing she does
        log.warning("phone_words: publishing %s failed", event.get("type"))


def hands_on(user_id: uuid.UUID, conversation_id: uuid.UUID, turn: str, *,
             client: redis.Redis | None = None) -> None:
    """Her hands are on the phone in this turn (renewed on every step). The first time, the apps are told."""
    client = client or _client()
    if client is None:
        return
    try:
        value = json.dumps({"turn": turn, "conversation": str(conversation_id)})
        previous = client.set(_hands_key(user_id), value, ex=HANDS_TTL_SECONDS, get=True)
    except redis.RedisError:
        log.warning("phone_words: hands couldn't be recorded")
        return
    if previous is None or previous.decode() != value:
        _publish(client, turn, {"type": "hands", "turn_id": turn, "open": True})


def hands_off(user_id: uuid.UUID, turn: str, *, client: redis.Redis | None = None) -> None:
    """The turn is over on the phone (ended, stopped, or Stop on the banner): words take the ordinary path again.
    Words still queued for it stay unread: their jobs run next."""
    client = client or _client()
    if client is None:
        return
    try:
        released = client.eval(_RELEASE, 1, _hands_key(user_id), turn)
        client.delete(_words_key(turn))
    except redis.RedisError:
        log.warning("phone_words: hands couldn't be released")
        return
    if released:
        _publish(client, turn, {"type": "hands", "turn_id": turn, "open": False})


def hands(user_id: uuid.UUID | str, *, client: redis.Redis | None = None) -> Hands | None:
    """The turn her hands are on the phone in right now, if any."""
    client = client or _client()
    if client is None:
        return None
    try:
        raw = client.get(_hands_key(user_id))
    except redis.RedisError:
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return Hands(turn=str(data["turn"]), conversation_id=str(data["conversation"]))
    except (ValueError, KeyError, TypeError):
        return None


def offer(turn: str, *, job_id: uuid.UUID, message_id: uuid.UUID, turn_id: uuid.UUID, text: str,
          client: redis.Redis | None = None) -> bool:
    """Queue a saved message's words for the running turn. False when they couldn't be (its job runs next)."""
    client = client or _client()
    if client is None:
        return False
    item = json.dumps({"job": str(job_id), "message": str(message_id), "turn": str(turn_id),
                       "text": text[:WORDS_CHARS], "at": time.time()})
    try:
        with client.pipeline(transaction=True) as pipe:
            pipe.rpush(_words_key(turn), item)
            pipe.expire(_words_key(turn), WORDS_TTL_SECONDS)
            pipe.execute()
    except redis.RedisError:
        log.warning("phone_words: words couldn't be queued")
        return False
    return True


def _pop_all(turn: str, client: redis.Redis) -> list[Words]:
    with client.pipeline(transaction=True) as pipe:
        pipe.lrange(_words_key(turn), 0, -1)
        pipe.delete(_words_key(turn))
        raw, _ = pipe.execute()
    out: list[Words] = []
    for entry in raw or []:
        try:
            data = json.loads(entry)
            out.append(Words(job_id=data["job"], message_id=data["message"], turn_id=data["turn"],
                             text=str(data["text"]), at=float(data["at"])))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _close_jobs(user_id: uuid.UUID, conversation_id: uuid.UUID, words: list[Words], turn: str) -> list[Words]:
    """Close the jobs of the words she's reading now (only those still pending: one already running or done is
    answered there). Returns the words whose jobs this closed."""
    from sqlalchemy import update

    from app.db.models import ChatJob
    from app.db.session import SessionLocal
    from app.services import chat as chat_service

    taken: list[Words] = []
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        for item in words:
            job_id = uuid.UUID(item.job_id)
            closed = db.execute(update(ChatJob).where(
                ChatJob.id == job_id, ChatJob.user_id == user_id, ChatJob.conversation_id == conversation_id,
                ChatJob.status == "pending",
            ).values(status="completed", last_error=f"answered in turn {turn}", processed_at=now,
                     lease_expires_at=None))
            if not closed.rowcount:
                continue
            seen = chat_service.mark_seen(db, uuid.UUID(item.message_id), now)
            db.commit()
            taken.append(item)
            if seen:
                chat_service.announce_seen(user_id, uuid.UUID(item.message_id), conversation_id)
    except Exception:  # noqa: BLE001 - unread words keep their jobs: answered next instead of now
        db.rollback()
        log.exception("phone_words: closing jobs for words she read")
    finally:
        db.close()
    return taken


def take(user_id: uuid.UUID, conversation_id: uuid.UUID, turn: str, *,
         client: redis.Redis | None = None) -> list[Words]:
    """The words queued for this turn since her last step, now hers to answer in it (their jobs closed)."""
    client = client or _client()
    if client is None:
        return []
    try:
        words = _pop_all(turn, client)
    except redis.RedisError:
        log.warning("phone_words: words couldn't be read")
        return []
    if not words:
        return []
    taken = _close_jobs(user_id, conversation_id, words, turn)
    for item in taken:
        _publish(client, item.turn_id, {"type": "done", "turn_id": item.turn_id, "status": "completed", "joined": turn})
    return taken


def render(words: list[Words], *, now: float | None = None) -> str:
    """What she reads under the screen: their words, oldest first, each with how long ago."""
    if not words:
        return ""
    now = time.time() if now is None else now
    lines = []
    for item in words:
        ago = max(0, int(now - item.at))
        lines.append(f'- {ago} s ago: "{item.text}"')
    return ("\n\nThe user said this while you were acting (their messages, now in the conversation; it didn't "
            "stop you):\n" + "\n".join(lines))
