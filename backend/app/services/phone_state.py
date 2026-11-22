"""What the user's phone says about itself, kept as its latest value (docs/MIND_ARCHITECTURE.md, "Her phone").

The phone reports what it knows (screen, battery, charging, headphones, network, do not disturb, ringer, motion, a
named place) in its ``sense`` frames while the app is open and over ``PUT /api/phone/state`` when it isn't. Each
report is partial: only the fields it sends change. Kept in Redis per user, one hash field per state field, with
when its value began (``since``) and when it was last confirmed (``as_of``): latest values only, never a history
(transitions worth remembering are memory's, not this store's).

Two readers, one state: her phone source (services/mind_phone.py) ticks her background mind from it, and the "Now"
of every evaluation of her mind (services/mind_turn.py) shows it. Whether the app is open comes from presence
(services/presence.py), read at the same time.

Facts the phone reports about itself: descriptive only. Nothing here authorizes anything or decides what she does.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import get_settings
from app.services import presence

log = logging.getLogger(__name__)

FIELDS = ("screen", "locked", "battery", "charging", "headphones", "network", "wifi_name", "dnd", "ringer",
          "motion", "place")
USERS_KEY = "phone_state:users"
MIND_WAKEUPS = "macrovision:mind:wakeups"  # the mind worker's queue (workers/runner.py): a change ticks at once
WRITE_TRIES = 5  # a report's compare-and-write, retried when another report changed the hash under it
CONVERSATION = "_conversation"  # the conversation the phone app is in (where her lines go)
ZONE = "_zone"  # the IANA time zone the phone app reported
AWAY = "away"  # ``place``: the phone confirmed they're at none of their named places (None: where isn't known)


def _key(user_id: uuid.UUID | str) -> str:
    return f"phone_state:{user_id}"


def _client() -> redis.Redis | None:
    return presence._client()


@dataclass
class Value:
    value: Any
    since: float  # when it became this value
    as_of: float  # when the phone last confirmed it


@dataclass
class PhoneState:
    """One user's phone as last reported. ``reported_at``: the newest report; ``app``/``voice``: from presence
    (``app`` None: unknown)."""

    values: dict[str, Value] = field(default_factory=dict)
    reported_at: float | None = None
    conversation_id: uuid.UUID | None = None
    zone: str | None = None
    app: str | None = None
    voice: bool = False

    def get(self, name: str) -> Any:
        entry = self.values.get(name)
        return entry.value if entry is not None else None

    def since(self, name: str) -> float | None:
        entry = self.values.get(name)
        return entry.since if entry is not None else None


def _observed(observed_at: datetime | None, now: float) -> float:
    """When the phone saw it. A time in the future (a clock ahead) is taken as now; a late one keeps its own time,
    however late, so the ordering check still sees it as older than what's already in."""
    if observed_at is None:
        return now
    at = observed_at.timestamp() if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc).timestamp()
    return min(at, now)


def _merge(stored: dict, fields: dict, at: float, confirm_at: float | None = None) -> tuple[dict, list[str]]:
    """The hash fields to write for one report over what's stored, and the state fields whose value changed.
    ``confirm_at``: the phone is reporting every field live (it sends only changes), so what it didn't send still
    holds as of then."""
    updates, changed = {}, []
    for name, value in fields.items():
        if name not in FIELDS:
            continue
        old = _load(stored.get(name))
        if old is not None and old.value == value:
            updates[name] = json.dumps({"value": value, "since": old.since, "as_of": max(old.as_of, at)})
            continue
        if old is not None and at < old.as_of:
            continue  # an older observation arriving after a newer one: the newer stands
        updates[name] = json.dumps({"value": value, "since": at, "as_of": at})
        changed.append(name)
    if confirm_at is not None:
        for name in FIELDS:
            old = _load(stored.get(name))
            if name not in updates and old is not None and old.as_of < confirm_at:
                updates[name] = json.dumps({"value": old.value, "since": old.since, "as_of": confirm_at})
    return updates, changed


def _write(client: redis.Redis, key: str, fields: dict, at: float, extra: dict,
           confirm_at: float | None) -> list[str]:
    """Compare and write in one transaction: two reports at once (the socket and the REST endpoint) can't both read
    the same state and then land in the wrong order. Retried when the hash changed under it."""
    for _ in range(WRITE_TRIES):
        with client.pipeline() as pipe:
            try:
                pipe.watch(key)
                updates, changed = _merge(_decoded(pipe.hgetall(key) or {}), fields, at, confirm_at)
                pipe.multi()
                pipe.hset(key, mapping={**updates, **extra})
                pipe.expire(key, int(get_settings().phone_state_keep_hours * 3600 * 7))
                pipe.execute()
                return changed
            except redis.WatchError:
                continue
    raise redis.WatchError(f"{key} kept changing")


def report(user_id: uuid.UUID | str, fields: dict, *, conversation_id: uuid.UUID | None = None,
           zone: str | None = None, observed_at: datetime | None = None, live: bool = False,
           now: float | None = None) -> list[str]:
    """Record what the phone reported (only the fields given; unknown names ignored). Returns the fields whose
    value changed. A change wakes the mind worker so her phone source ticks now, not at its next heartbeat. No Redis:
    nothing is kept, and nothing changed.

    ``live``: the report came over the app's socket while the app is on screen or a call is on, when the phone
    watches every field and sends what changes; every field it holds is then confirmed as of now, even in a frame
    that carries none. A background report (``live`` False) confirms only the fields it sends. ``_reported_at`` is
    only when the phone was last in touch, never how current a reading is: that's each field's ``as_of``."""
    client = _client()
    if client is None:
        return []
    now = time.time() if now is None else now
    at = _observed(observed_at, now)
    extra = {"_reported_at": json.dumps(now)}
    if conversation_id is not None:
        extra[CONVERSATION] = json.dumps(str(conversation_id))
    if zone:
        extra[ZONE] = json.dumps(zone)
    try:
        changed = _write(client, _key(user_id), fields, at, extra, now if live else None)
        client.hset(USERS_KEY, str(user_id), json.dumps(now))
        if changed:
            client.rpush(MIND_WAKEUPS, "phone")
            client.ltrim(MIND_WAKEUPS, -100, -1)
    except redis.RedisError:
        log.warning("phone_state: could not record a report for user %s", user_id)
        return []
    return changed


def _decoded(stored: dict) -> dict:
    return {k.decode() if isinstance(k, bytes) else k: v for k, v in stored.items()}


def _load(raw) -> Value | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Value(data["value"], float(data["since"]), float(data["as_of"]))
    except (TypeError, ValueError, KeyError):
        return None


def read(user_id: uuid.UUID | str, *, now: float | None = None) -> PhoneState | None:
    """The phone as last reported, with the app's state from presence. None when nothing was ever reported (or
    Redis can't be read: unknown, not "nothing")."""
    client = _client()
    if client is None:
        return None
    try:
        stored = client.hgetall(_key(user_id)) or {}
    except redis.RedisError:
        return None
    stored = _decoded(stored)
    if not stored:
        return None
    state = PhoneState()
    for name in FIELDS:
        entry = _load(stored.get(name))
        if entry is not None:
            state.values[name] = entry
    try:
        state.reported_at = float(json.loads(stored["_reported_at"])) if "_reported_at" in stored else None
        state.conversation_id = uuid.UUID(json.loads(stored[CONVERSATION])) if CONVERSATION in stored else None
        state.zone = json.loads(stored[ZONE]) if ZONE in stored else None
    except (TypeError, ValueError):
        pass
    app = presence.phone_app(user_id, now=now)
    if app is not None:
        state.app, state.voice = app["app"], app["voice"]
    return state


def users(*, now: float | None = None) -> list[uuid.UUID]:
    """Users whose phone reported within PHONE_STATE_KEEP_HOURS (the ones her phone source ticks for)."""
    client = _client()
    if client is None:
        return []
    now = time.time() if now is None else now
    keep = get_settings().phone_state_keep_hours * 3600
    try:
        rows = client.hgetall(USERS_KEY) or {}
    except redis.RedisError:
        return []
    out = []
    for raw_id, raw_at in rows.items():
        try:
            user_id = uuid.UUID(raw_id.decode() if isinstance(raw_id, bytes) else raw_id)
            if now - float(json.loads(raw_at)) <= keep:
                out.append(user_id)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# How it reads: the same words for her ticks and her "Now".
# --------------------------------------------------------------------------- #

def span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    hours, minutes = divmod(seconds // 60, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes and hours < 6 else f"{hours} h"
    return f"{hours // 24} d"


def battery_band(level: int | None, charging: bool | None) -> str | None:
    """The battery as news reads it: a change of band is news, a percent is not."""
    if level is None:
        return None
    if level >= 100 or (charging and level >= 95):
        return "full"
    if level <= 5:
        return "almost empty"
    if level <= 10:
        return "very low"
    if level <= 20:
        return "low"
    return "ok"


_APP = {"open": "open on their screen", "background": "in the background (connected, not on screen)",
        "closed": "closed"}
_MOTION = {"still": "still", "walking": "walking", "running": "running", "cycling": "cycling",
           "vehicle": "in a vehicle"}


STALE_SECONDS = 30 * 60  # a reading the phone hasn't confirmed for this long is said to be old


def lines(state: PhoneState, now: float) -> list[str]:
    """What the phone says now, a line per thing known, each with how long it's been so, and when the phone hasn't
    confirmed it for STALE_SECONDS, how old it is (never an old reading as if it were now)."""
    out = []

    def line(text: str, *names: str) -> None:
        confirmed = [state.values[n].as_of for n in names if n in state.values]
        age = now - min(confirmed) if confirmed else 0
        suffix = (f"; last confirmed {span(age)} ago, may no longer be true" if age >= STALE_SECONDS else "")
        out.append(f"{text}{suffix}.")

    if state.app is not None:
        out.append(f"- The Jophiel app: {_APP.get(state.app, state.app)}"
                   + ("; a voice call with you is on." if state.voice else "."))

    def held(name: str) -> str:
        since = state.since(name)
        return f" (for {span(now - since)})" if since is not None else ""

    screen, locked = state.get("screen"), state.get("locked")
    if screen is not None or locked is not None:
        parts = []
        if screen is not None:
            parts.append(f"screen {screen}{held('screen')}")
        if locked is not None:
            parts.append(("locked" if locked else "unlocked") + held("locked"))
        line(f"- Phone: {', '.join(parts)}", "screen", "locked")
    level, charging = state.get("battery"), state.get("charging")
    if level is not None or charging is not None:
        text = f"- Battery: {level}%" if level is not None else "- Battery: level unknown"
        if charging is not None:
            text += (", charging" if charging else ", not charging") + held("charging")
        line(text, "battery", "charging")
    if state.get("headphones") is not None:
        line("- Headphones: " + ("connected" if state.get("headphones") else "none") + held("headphones"), "headphones")
    network = state.get("network")
    if network is not None:
        name = state.get("wifi_name")
        what = {"wifi": f'Wi-Fi "{name}"' if name else "Wi-Fi", "cellular": "mobile data", "none": "no network"}
        line(f"- Network: {what.get(network, network)}{held('network')}",
             *(("network", "wifi_name") if network == "wifi" and name else ("network",)))
    if state.get("dnd") is not None or state.get("ringer") is not None:
        parts = []
        if state.get("dnd") is not None:
            parts.append("Do Not Disturb " + ("on" if state.get("dnd") else "off"))
        if state.get("ringer") is not None:
            parts.append(f"ringer {state.get('ringer')}")
        line(f"- {', '.join(parts)}", "dnd", "ringer")
    if "motion" in state.values:
        motion = state.get("motion")
        line(f"- Moving: {_MOTION.get(motion, motion) if motion else 'unknown'}{held('motion')}", "motion")
    if "place" in state.values:
        place = state.get("place")
        what = "unknown" if place is None else "none of their named places" if place == AWAY else place
        line(f"- Place: {what}{held('place')}", "place")
    return out


def describe(state: PhoneState | None, now: float) -> list[str]:
    """Her phone as a section of text: what it says, each reading with how old it is (never stale data as if it were
    now), and when the phone was last in touch (contact, not freshness: a frame with no readings says nothing new
    about them)."""
    if state is None:
        return []
    out = lines(state, now)
    if state.reported_at is not None:
        age = now - state.reported_at
        out.insert(0, f"- Last heard from the phone {span(age)} ago." if age >= 60
                   else "- Last heard from the phone just now.")
    return out
