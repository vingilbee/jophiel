"""Her picture of a place, the service side (2026-11-27; the contract is app/ai/place_memory.py).

- **Offer** (``offer``, from her phone tools after each screen comes back): the newest screenshot of the place and
  the action that led to it go in one waiting slot per place in Redis (the screenshot overwrites the one before;
  actions add up), and the place is due at once: the worker is woken and updates on that screen. Screens that come
  while an update of the place runs (one at a time, ``BUSY_SECONDS`` claim) wait together and are the next update,
  on the newest of them, as soon as it ends: the picture is at most about one model call behind her screens.
  One Redis write; nothing in her turn waits on it.
- **Update** (``run_once``, on the ``place-memory`` worker thread): one vision model call over the picture so far
  (every entry with an id), her own lines, the actions, the screenshot it saw last time and the newest one (with
  where she aimed drawn on). It answers with edits (``app/ai/place_memory.apply`` checks them): this sitting's
  lines and entries go in Redis (forgotten ``NOW_SECONDS`` after the place was last used), lasting entries in
  ``place_entries`` under the place's ``place_layouts`` row, per world. Screenshots are never stored: the newest
  waits at most ``PENDING_SECONDS`` and, once read, is kept that long again only as the next update's "before".
- **Her own lines** (``keep_in_mind``): beside this sitting (Redis) or the place (``place_layouts.her_layout``);
  never rewritten by the updater.
- **Reading** (``read``, ``for_turn``): a place's picture with its screens, and the pictures of the places she used
  in the last hour in her moment.
- **Forgetting.** Everything in Redis follows her screens' wipe count (``phone_control._epoch``): when the phone
  reports Phone control off, this sitting's pictures and waiting screenshots are gone (``wipe``). Lasting entries
  are her words about a place, kept until the updater changes them or the user removes them
  (``/api/practice/places``).

Traced to ``var/place_memory.jsonl`` (place, timing, counts, and each edit's why: what the updater saw that made it
change something; never a screenshot or an entry's words).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.ai import place_memory as P
from app.core.config import get_settings
from app.db.models import PlaceEntry, PlaceLayout
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

TRACE_PATH = "var/place_memory.jsonl"
_DUE = "place:due"
WAKEUPS = "macrovision:place-memory:wakeups"  # the place-memory worker's queue (workers/runner.py)
PLACE_CHARS = 80


def _client() -> redis.Redis | None:
    from app.services import phone_actions
    return phone_actions._client()


def _epoch(user_id: uuid.UUID | str) -> int | None:
    from app.services import phone_control
    return phone_control._epoch(user_id)


def _place(place: str | None) -> str:
    return " ".join(str(place or "").split())[:PLACE_CHARS]


def _id(user_id: uuid.UUID | str, surface: str, place: str) -> str:
    return json.dumps([str(user_id), surface, place.lower()])


def _key(kind: str, user_id: uuid.UUID | str, surface: str, place: str) -> str:
    return f"place:{kind}:{_id(user_id, surface, place)}"


def _places_key(user_id: uuid.UUID | str, surface: str) -> str:
    return f"place:places:{user_id}:{surface}"


def _busy(member: str) -> str:
    return f"place:busy:{member}"


# --------------------------------------------------------------------------- #
# Offer
# --------------------------------------------------------------------------- #


def offer(user_id: uuid.UUID | str, place: str | None, jpeg_b64: str | None, *, size: tuple[int, int] | None,
          action: str | None = None, marks: list[dict] | None = None, surface: str = "phone",
          client: redis.Redis | None = None, now: float | None = None) -> None:
    """Her newest screenshot of a place (and the action that led to it, if any) for the updater. Never raises."""
    place = _place(place)
    if not place or not jpeg_b64:
        return
    client = client or _client()
    epoch = _epoch(user_id)
    if client is None or epoch is None:
        return
    now = time.time() if now is None else now
    try:
        pending = _key("pending", user_id, surface, place)
        old = client.get(pending)
        kept = json.loads(old) if old else {}
        if kept.get("epoch") != epoch:
            kept = {}
        actions = (list(kept.get("actions") or []) + ([action] if action else []))[-P.ACTIONS_READ:]
        all_marks = (list(kept.get("marks") or []) + list(marks or []))[-P.ACTIONS_READ:]
        now_key = _key("now", user_id, surface, place)
        if not _now_part(client, user_id, surface, place, epoch):
            client.delete(now_key)  # anything left from before a wipe isn't this sitting's
        record = {"place": place, "jpeg": jpeg_b64, "size": list(size) if size else None, "actions": actions,
                  "marks": all_marks, "epoch": epoch, "at": now}
        pipe = client.pipeline()
        pipe.set(pending, json.dumps(record), ex=P.PENDING_SECONDS)
        pipe.zadd(_DUE, {_id(user_id, surface, place): now}, nx=True)
        pipe.rpush(WAKEUPS, "1")
        pipe.ltrim(WAKEUPS, -10, -1)
        pipe.sadd(_places_key(user_id, surface), place)
        pipe.expire(_places_key(user_id, surface), P.NOW_SECONDS)
        pipe.hset(now_key, mapping={"used": str(now), "epoch": str(epoch)})
        pipe.expire(now_key, P.NOW_SECONDS)
        pipe.execute()
    except (redis.RedisError, ValueError):
        log.warning("place_memory: a screenshot couldn't be offered")


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _layout_row(db: Session, user_id, surface: str, place: str) -> PlaceLayout | None:
    return db.scalar(select(PlaceLayout).where(
        PlaceLayout.user_id == uuid.UUID(str(user_id)), PlaceLayout.surface == surface,
        func.lower(PlaceLayout.place) == place.lower()))


def _lasting_rows(db: Session, row: PlaceLayout | None, world: str) -> list[PlaceEntry]:
    if row is None or row.id is None:
        return []
    return list(db.scalars(select(PlaceEntry).where(
        PlaceEntry.place_id == row.id, func.lower(PlaceEntry.world) == world.lower(),
    ).order_by(PlaceEntry.seen_at.desc(), PlaceEntry.created_at.desc()).limit(P.LASTING_MAX)))


def _now_part(client: redis.Redis | None, user_id, surface: str, place: str, epoch: int | None) -> dict:
    if client is None or epoch is None:
        return {}
    try:
        raw = client.hgetall(_key("now", user_id, surface, place))
    except redis.RedisError:
        return {}
    kept = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in (raw or {}).items()}
    return kept if kept.get("epoch") == str(epoch) else {}


def _state(part: dict) -> dict:
    try:
        state = json.loads(part.get("state") or "{}")
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def _picture(db: Session, client, user_id, surface: str, place: str, epoch: int | None) -> P.Picture:
    part = _now_part(client, user_id, surface, place, epoch)
    state = _state(part)
    world = str(state.get("world") or "")
    row = _layout_row(db, user_id, surface, place)
    rows = _lasting_rows(db, row, world)
    ids = {r.id: f"L{n}" for n, r in enumerate(rows, 1)}
    lasting = tuple(P.Entry(id=ids[r.id], text=r.text, kind=r.kind, near=ids.get(r.near_id), key=str(r.id))
                    for r in rows)
    sitting = tuple(P.Entry(id=str(e["id"]), text=str(e.get("text") or ""))
                    for e in state.get("sitting") or [] if isinstance(e, dict) and e.get("id"))
    return P.Picture(place=row.place if row else place, world=world, doing=str(state.get("doing") or ""),
                     where=str(state.get("where") or ""), sitting=sitting, lasting=lasting,
                     her_now=part.get("her", ""), her_layout=row.her_layout if row else "",
                     now_at=float(part["at"]) if part.get("at") else None)


def read(user_id: uuid.UUID | str, place: str | None, *, surface: str = "phone") -> P.Picture | None:
    """A place's picture (None when there's nothing, or it can't be read)."""
    place = _place(place)
    if not place:
        return None
    try:
        with SessionLocal() as db:
            picture = _picture(db, _client(), user_id, surface, place, _epoch(user_id))
    except Exception:  # noqa: BLE001 - she goes without it this time
        log.exception("place_memory: reading %r", place)
        return None
    return None if picture.empty else picture


def for_turn(user_id: uuid.UUID | str, *, surface: str = "phone", now: float | None = None) -> str | None:
    """The pictures of the places she used in the last hour, for her moment (None when there are none)."""
    client = _client()
    epoch = _epoch(user_id)
    if client is None or epoch is None:
        return None
    now = time.time() if now is None else now
    try:
        places = [p.decode() if isinstance(p, bytes) else p for p in client.smembers(_places_key(user_id, surface))]
        used = []
        for place in places:
            part = _now_part(client, user_id, surface, place, epoch)
            if part.get("used") and now - float(part["used"]) < P.RECENT_PLACES_SECONDS:
                used.append((float(part["used"]), place))
        if not used:
            return None
        with SessionLocal() as db:
            pictures = [_picture(db, client, user_id, surface, place, epoch) for _at, place in sorted(used)]
    except Exception:  # noqa: BLE001
        log.exception("place_memory: reading this turn's places")
        return None
    texts = [P.text_for_her(P.for_moment(p), now=now) for p in pictures if not p.empty]
    return "\n\n".join(texts) or None


# --------------------------------------------------------------------------- #
# Her own lines
# --------------------------------------------------------------------------- #


def _header(db: Session, user_id, surface: str, place: str) -> PlaceLayout:
    row = _layout_row(db, user_id, surface, place)
    if row is None:
        row = PlaceLayout(id=uuid.uuid4(), user_id=uuid.UUID(str(user_id)), surface=surface, place=place)
        db.add(row)
    return row


def keep_in_mind(user_id: uuid.UUID | str, place: str | None, text: str, *, lasting: bool = False,
                 surface: str = "phone", now: float | None = None) -> str:
    """Her own lines for a place, replacing the ones before (empty text clears them). Returns what to tell her."""
    place = _place(place)
    if not place:
        return "Not kept: no app is in front yet (read the screen first)."
    words = " ".join(str(text or "").split())
    if len(words) > P.HER_CHARS:
        return f"Not kept: that's more than {P.HER_CHARS} characters."
    now = time.time() if now is None else now
    if lasting:
        with SessionLocal() as db:
            row = _header(db, user_id, surface, place)
            row.her_layout = words
            row.revision = (row.revision or 0) + 1
            db.commit()
        return f"Kept with what you know of {place}, for next time too." if words else f"Cleared your lines about {place}."
    client = _client()
    epoch = _epoch(user_id)
    if client is None or epoch is None:
        return "Not kept: this sitting's memory can't be reached right now."
    key = _key("now", user_id, surface, place)
    try:
        if not _now_part(client, user_id, surface, place, epoch):
            client.delete(key)  # anything left from before a wipe isn't this sitting's
        pipe = client.pipeline()
        pipe.hset(key, mapping={"her": words, "epoch": str(epoch), "used": str(now)})
        pipe.expire(key, P.NOW_SECONDS)
        pipe.sadd(_places_key(user_id, surface), place)
        pipe.expire(_places_key(user_id, surface), P.NOW_SECONDS)
        pipe.execute()
    except redis.RedisError:
        return "Not kept: this sitting's memory can't be reached right now."
    return f"Kept with what you know of {place} for now." if words else f"Cleared your lines for now in {place}."


# --------------------------------------------------------------------------- #
# The updater
# --------------------------------------------------------------------------- #


def _update(text: str, images: list[str]) -> P.Update:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.ai.agent import get_chat_model, invoke_private
    settings = get_settings()
    model = get_chat_model(settings.place_memory_model, settings.place_memory_reasoning_effort)
    content = [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image, "detail": "high"}}
        for image in images]
    return invoke_private(model.with_structured_output(P.Update),
                          [SystemMessage(P.system_prompt()), HumanMessage(content=content)])


def _trace(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
        with open(TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("place_memory: trace not written")


def run_once(*, client: redis.Redis | None = None, now: float | None = None, model=_update) -> bool:
    """Update one place that has a new screen and isn't being updated already. True when there was one."""
    client = client or _client()
    if client is None:
        return False
    now = time.time() if now is None else now
    try:
        member = None
        for found in client.zrangebyscore(_DUE, 0, now, start=0, num=20):
            found = found.decode() if isinstance(found, bytes) else found
            # One update per place at a time: a place being updated keeps its new screens for when that one ends.
            if client.set(_busy(found), "1", nx=True, ex=P.BUSY_SECONDS):
                member = found
                break
        if member is None:
            return False
        if not client.zrem(_DUE, member):  # another worker took it
            client.delete(_busy(member))
            return True
    except (redis.RedisError, ValueError):
        log.warning("place_memory: taking a due place failed")
        return False
    try:
        return _update_place(client, member, model)
    finally:
        try:
            client.delete(_busy(member))
        except redis.RedisError:
            log.warning("place_memory: a place's update claim wasn't released (it lapses)")


def _update_place(client: redis.Redis, member: str, model) -> bool:
    try:
        user_id, surface, lowered = json.loads(member)
        pending = _key("pending", user_id, surface, lowered)
        before_key = _key("before", user_id, surface, lowered)
        pipe = client.pipeline(transaction=True)
        pipe.get(pending)
        pipe.delete(pending)
        pipe.get(before_key)
        raw, _deleted, raw_before = pipe.execute()
    except (redis.RedisError, ValueError):
        log.warning("place_memory: taking a due place failed")
        return False
    if not raw:
        return True
    record = json.loads(raw)
    trace = {"at": datetime.now(timezone.utc).isoformat(), "user_id": user_id, "surface": surface,
             "place": record.get("place"), "actions": len(record.get("actions") or [])}
    from app.services import phone_control
    epoch = _epoch(user_id)
    if epoch is None or record.get("epoch") != epoch or phone_control._control_off(user_id):
        trace["outcome"] = "dropped: her screens were wiped or Phone control is off"
        _trace(trace)
        return True
    place = record["place"]
    before_jpeg = None
    if raw_before:
        try:
            kept = json.loads(raw_before)
            before_jpeg = kept.get("jpeg") if kept.get("epoch") == epoch else None
        except ValueError:
            before_jpeg = None
    started = time.time()
    try:
        from app.services.practice import _draw
        newest = _draw(record["jpeg"], record.get("marks") or []) if record.get("marks") else record["jpeg"]
        size = tuple(record["size"]) if record.get("size") else None
        caption = ("her taps and swipes since your last update are drawn in red on the newest, numbered in order, "
                   "where she aimed them (on the screens they were made on)") if record.get("marks") \
            else "nothing drawn on it"
        with SessionLocal() as db:
            before = _picture(db, client, user_id, surface, place, epoch)
            text = P.update_text(before, list(record.get("actions") or []), size=size, caption=caption,
                                 had_before=before_jpeg is not None)
            update = model(text, ([before_jpeg] if before_jpeg else []) + [newest])
            state = _state(_now_part(client, user_id, surface, place, epoch))
            applied = P.apply(before, update, next_sitting=int(state.get("next") or 1))
            if _epoch(user_id) != epoch:  # wiped while it thought: nothing of it is kept
                trace["outcome"] = "dropped: wiped during the update"
                _trace(trace)
                return True
            lasting = _apply_lasting(db, user_id, surface, place, before, applied)
            db.commit()
        new_state = {"world": applied.world, "doing": applied.doing, "where": applied.where,
                     "sitting": [{"id": e.id, "text": e.text} for e in applied.sitting],
                     "next": applied.next_sitting}
        key = _key("now", user_id, surface, place)
        pipe = client.pipeline()
        pipe.hset(key, mapping={"state": json.dumps(new_state, ensure_ascii=False), "at": str(time.time()),
                                "epoch": str(epoch)})
        pipe.expire(key, P.NOW_SECONDS)
        # The screenshot just read is the next update's "before", kept no longer than a waiting one.
        pipe.set(before_key, json.dumps({"jpeg": record["jpeg"], "epoch": epoch}), ex=P.PENDING_SECONDS)
        pipe.execute()
        trace.update(outcome="updated", ms=int((time.time() - started) * 1000), images=2 if before_jpeg else 1,
                     world_changed=applied.world.lower() != before.world.lower(), edits=len(update.edits),
                     sitting=len(applied.sitting), lasting=lasting, whys=applied.whys, refused=applied.refused)
    except Exception as exc:  # noqa: BLE001 - the picture stays as it was; her turns are unaffected
        log.exception("place_memory: updating %r failed", place)
        trace["outcome"] = f"failed: {type(exc).__name__}: {exc}"[:300]
    _trace(trace)
    return True


def _apply_lasting(db: Session, user_id, surface: str, place: str, before: P.Picture, applied: P.Applied) -> dict:
    """The lasting edits written to place_entries (under the place's header row). Returns counts for the trace."""
    counts = {"added": 0, "changed": 0, "removed": 0}
    if not (applied.lasting_add or applied.lasting_change or applied.lasting_remove):
        return counts
    row = _header(db, user_id, surface, place)
    now = datetime.now(timezone.utc)
    keys = {e.id: e.key for e in before.lasting}
    for key, text in applied.lasting_change.items():
        entry = db.get(PlaceEntry, uuid.UUID(key))
        if entry is not None and entry.place_id == row.id and entry.text != text:
            entry.text, entry.revision, entry.seen_at = text, entry.revision + 1, now
            counts["changed"] += 1
    for key in applied.lasting_remove:
        entry = db.get(PlaceEntry, uuid.UUID(key))
        if entry is not None and entry.place_id == row.id:
            db.delete(entry)
            counts["removed"] += 1
    for e in applied.lasting_add:
        near = keys.get(e.near) if e.near else None
        if near in applied.lasting_remove:
            near = None
        db.add(PlaceEntry(place_id=row.id, world=applied.world, kind=e.kind or "thing", text=e.text,
                          near_id=uuid.UUID(near) if near else None, seen_at=now))
        counts["added"] += 1
    row.revision = (row.revision or 0) + 1
    return counts


def wipe(user_id: uuid.UUID | str, *, surface: str = "phone", client: redis.Redis | None = None) -> None:
    """Forget this sitting's pictures and waiting screenshots now (her screens were wiped). The wipe count already
    hides them; this frees them."""
    client = client or _client()
    if client is None:
        return
    try:
        places = [p.decode() if isinstance(p, bytes) else p for p in client.smembers(_places_key(user_id, surface))]
        keys = [_key(kind, user_id, surface, place) for place in places for kind in ("now", "pending", "before")]
        client.delete(*keys, _places_key(user_id, surface))
        if places:
            client.zrem(_DUE, *[_id(user_id, surface, place) for place in places])
    except redis.RedisError:
        log.warning("place_memory: the wipe couldn't free this sitting's pictures")


# --------------------------------------------------------------------------- #
# The owner's view (/api/practice/places)
# --------------------------------------------------------------------------- #


def list_places(db: Session, user_id: uuid.UUID, *, surface: str | None = None) -> list[PlaceLayout]:
    query = select(PlaceLayout).where(PlaceLayout.user_id == user_id).options(selectinload(PlaceLayout.entries))
    if surface:
        query = query.where(PlaceLayout.surface == surface)
    return list(db.scalars(query.order_by(PlaceLayout.updated_at.desc())))


def edit_place(db: Session, user_id: uuid.UUID, place_id: uuid.UUID, *, her_layout: str | None = None
               ) -> PlaceLayout | None:
    row = db.get(PlaceLayout, place_id)
    if row is None or row.user_id != user_id:
        return None
    if her_layout is not None:
        row.her_layout = P.cut(her_layout, P.HER_CHARS)
    row.revision += 1
    return row


def remove_place(db: Session, user_id: uuid.UUID, place_id: uuid.UUID) -> bool:
    row = db.get(PlaceLayout, place_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    return True


def _own_entry(db: Session, user_id: uuid.UUID, entry_id: uuid.UUID) -> PlaceEntry | None:
    entry = db.get(PlaceEntry, entry_id)
    if entry is None or entry.layout.user_id != user_id:
        return None
    return entry


def edit_entry(db: Session, user_id: uuid.UUID, entry_id: uuid.UUID, *, text: str) -> PlaceEntry | None:
    entry = _own_entry(db, user_id, entry_id)
    if entry is None:
        return None
    entry.text = P.cut(text, P.ENTRY_CHARS)
    entry.revision += 1
    return entry


def remove_entry(db: Session, user_id: uuid.UUID, entry_id: uuid.UUID) -> bool:
    entry = _own_entry(db, user_id, entry_id)
    if entry is None:
        return False
    db.delete(entry)
    return True
