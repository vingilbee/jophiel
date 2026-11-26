"""Her practice notes, the service side (the tool-learning revamp, 2026-11-26; the contract is app/ai/practice.py).

- **The trail** (Redis only, per user and surface). Each action of her hands is recorded as one step (``record``):
  the tool, what she said to it, the item or point, what came back, the screen after it as text, and, for an action
  aimed by pixel, the picture she aimed on with her marks (``picture``). The latest screen's picture is kept as the
  stretch's last look (``last_look``). Kept at most ``PRACTICE_TRAIL_SECONDS`` and deleted once a review read it;
  wiped with her screen memory when the phone turns control off (``wipe``). Never typed text.
- **The stretch.** Steps of one user on one surface belong together until her hands have been still for
  ``PRACTICE_SETTLE_SECONDS``: a whole attack that the user's words cut into several turns is one stretch.
- **The review** (``run_once``, on the ``practice`` worker thread). One vision model call over the stretch (the user's
  words meanwhile, her replies, the steps, up to ``PICTURES_REVIEWED`` pictures with where she aimed drawn on) and her
  notes for its places; ``app/ai/practice.check`` keeps only what the stretch can carry; the notes change in one
  transaction. Every review is traced to ``var/practice_reviews.jsonl`` (what it read, what it changed, what was
  dropped and why; never the pictures).
- **The notes** (``practice_notes``): per user, surface and place. ``for_place`` is what she reads with a screen;
  ``keep_user_note`` is the user's own note (their words, or hers on their word), which a review never changes.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import practice as P
from app.core.config import get_settings
from app.db.models import ChatMessage, PracticeNote
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

SURFACES = ("phone",)
PICTURES_KEPT = 80  # per stretch in Redis (one turn's action limit); the review reads up to P.PICTURES_REVIEWED
PICTURE_SIDE = 2048  # only larger pictures are shrunk: positions she reads are the pixels she taps
MARK = (230, 30, 30)
TRACE_PATH = "var/practice_reviews.jsonl"
TALK_MESSAGES = 60  # from the first message her hands answered
TALK_FALLBACK = 12
USER_NOTE_CHARS = 600  # the user's own notes (and hers on their word)
_DUE = "practice:due"
KINDS = ("meta", "convs", "asked", "steps", "pics", "shown")


def _client() -> redis.Redis | None:
    from app.services import phone_actions
    return phone_actions._client()


def _key(kind: str, user_id: uuid.UUID | str, surface: str) -> str:
    return f"practice:{kind}:{user_id}:{surface}"


def _member(user_id: uuid.UUID | str, surface: str) -> str:
    return f"{user_id}:{surface}"


# --------------------------------------------------------------------------- #
# The trail
# --------------------------------------------------------------------------- #


def _keep(client: redis.Redis, user_id, surface: str, now: float, *, asked: uuid.UUID | None,
          conversation_id: uuid.UUID | None) -> None:
    ttl = int(get_settings().practice_trail_seconds)
    meta = _key("meta", user_id, surface)
    pipe = client.pipeline()
    pipe.hsetnx(meta, "since", str(now))
    pipe.hset(meta, "last", str(now))
    if conversation_id is not None:
        pipe.sadd(_key("convs", user_id, surface), str(conversation_id))
    if asked is not None:
        pipe.sadd(_key("asked", user_id, surface), str(asked))
    for kind in KINDS:
        pipe.expire(_key(kind, user_id, surface), ttl)
    pipe.zadd(_DUE, {_member(user_id, surface): now})
    pipe.execute()


def picture(user_id: uuid.UUID | str, jpeg_b64: str, *, caption: str, marks: list[dict] | None = None,
            surface: str = "phone", client: redis.Redis | None = None) -> int | None:
    """Keep a picture for the stretch's review (with her marks: ``{"tap": [x, y]}`` or ``{"swipe": [x1, y1, x2,
    y2]}``); returns its index, or None when it couldn't be kept."""
    client = client or _client()
    if client is None or not jpeg_b64:
        return None
    try:
        index = int(client.hincrby(_key("meta", user_id, surface), "pictures", 1)) - 1
        pics = _key("pics", user_id, surface)
        pipe = client.pipeline()
        pipe.hset(pics, str(index), json.dumps({"jpeg": jpeg_b64, "caption": caption[:200], "marks": marks or []}))
        if index >= PICTURES_KEPT:
            pipe.hdel(pics, str(index - PICTURES_KEPT))
        pipe.expire(pics, int(get_settings().practice_trail_seconds))
        pipe.execute()
        return index
    except redis.RedisError:
        log.warning("practice: a picture couldn't be kept")
        return None


def record(user_id: uuid.UUID | str, step: P.Step, *, surface: str = "phone", asked: uuid.UUID | None = None,
           conversation_id: uuid.UUID | None = None, client: redis.Redis | None = None,
           now: float | None = None) -> None:
    """One step of her hands, for the stretch's review. Never raises."""
    client = client or _client()
    if client is None:
        return
    now = time.time() if now is None else now
    try:
        client.rpush(_key("steps", user_id, surface), json.dumps(step.__dict__, ensure_ascii=False))
        _keep(client, user_id, surface, now, asked=asked, conversation_id=conversation_id)
    except redis.RedisError:
        log.warning("practice: a step couldn't be recorded")


def last_look(user_id: uuid.UUID | str, jpeg_b64: str | None, *, caption: str, surface: str = "phone",
              client: redis.Redis | None = None) -> None:
    """The screen as it stood when a turn of hers ended (a result screen, a half-done task): kept for the review."""
    client = client or _client()
    if client is None or not jpeg_b64:
        return
    index = picture(user_id, jpeg_b64, caption=caption, client=client, surface=surface)
    if index is None:
        return
    try:
        client.hset(_key("meta", user_id, surface), "last_look", str(index))
    except redis.RedisError:
        pass


def shown(user_id: uuid.UUID | str, note_ids: list[str], *, surface: str = "phone",
          client: redis.Redis | None = None) -> None:
    """These notes were in front of her during the stretch (the review asks whether she followed them)."""
    client = client or _client()
    if client is None or not note_ids:
        return
    try:
        key = _key("shown", user_id, surface)
        client.sadd(key, *note_ids)
        client.expire(key, int(get_settings().practice_trail_seconds))
    except redis.RedisError:
        log.warning("practice: the notes she had couldn't be recorded")


def wipe(user_id: uuid.UUID | str, *, client: redis.Redis | None = None) -> None:
    """Forget the trail (control switched off on the phone: her screens go, and so does what a review would read)."""
    client = client or _client()
    if client is None:
        return
    try:
        for surface in SURFACES:
            client.delete(*[_key(k, user_id, surface) for k in KINDS])
            client.zrem(_DUE, _member(user_id, surface))
    except redis.RedisError:
        log.warning("practice: the trail couldn't be wiped")


def _take(client: redis.Redis, user_id: str, surface: str) -> dict | None:
    """Move a settled stretch out of the way (new steps start the next one) and read it. None: nothing there."""
    token = uuid.uuid4().hex[:8]
    taken: dict[str, str] = {}
    for kind in KINDS:
        src = _key(kind, user_id, surface)
        dst = f"{src}:review:{token}"
        try:
            client.rename(src, dst)
            taken[kind] = dst
        except redis.ResponseError:  # no such key
            continue
    if "steps" not in taken:
        if taken:
            client.delete(*taken.values())
        return None
    try:
        out = {
            "meta": {k.decode(): v.decode() for k, v in client.hgetall(taken["meta"]).items()} if "meta" in taken else {},
            "convs": [v.decode() for v in client.smembers(taken["convs"])] if "convs" in taken else [],
            "asked": [v.decode() for v in client.smembers(taken["asked"])] if "asked" in taken else [],
            "steps": [json.loads(v) for v in client.lrange(taken["steps"], 0, -1)],
            "pics": ({int(k): json.loads(v) for k, v in client.hgetall(taken["pics"]).items()}
                     if "pics" in taken else {}),
            "shown": [v.decode() for v in client.smembers(taken["shown"])] if "shown" in taken else [],
        }
    finally:
        client.delete(*taken.values())
    return out


# --------------------------------------------------------------------------- #
# The notes
# --------------------------------------------------------------------------- #


def _view(row: PracticeNote) -> P.Note:
    return P.Note(id=str(row.id), place=row.place, tool=row.tool, status=row.status, text=row.text,  # type: ignore[arg-type]
                  source=row.source, evidence=row.evidence,  # type: ignore[arg-type]
                  updated=row.updated_at.date().isoformat() if row.updated_at else None)


def _rows(db: Session, user_id, surface: str, places: list[str]) -> list[PracticeNote]:
    if not places:
        return []
    lowered = [p.lower() for p in places]
    from sqlalchemy import func
    return list(db.scalars(select(PracticeNote).where(
        PracticeNote.user_id == user_id, PracticeNote.surface == surface,
        func.lower(PracticeNote.place).in_(lowered)).order_by(
        PracticeNote.source.desc(), PracticeNote.updated_at.desc())))  # the user's first, then the newest


def for_place(user_id: uuid.UUID, place: str, *, surface: str = "phone") -> list[P.Note]:
    """Her notes for one place, the user's first. A failure is no notes (logged)."""
    try:
        with SessionLocal() as db:
            return [_view(r) for r in _rows(db, user_id, surface, [place])]
    except Exception:  # noqa: BLE001 - she goes without her notes this time
        log.exception("practice: reading notes for %r", place)
        return []


def list_notes(db: Session, user_id: uuid.UUID, *, surface: str | None = None) -> list[PracticeNote]:
    query = select(PracticeNote).where(PracticeNote.user_id == user_id)
    if surface:
        query = query.where(PracticeNote.surface == surface)
    return list(db.scalars(query.order_by(PracticeNote.surface, PracticeNote.place, PracticeNote.updated_at.desc())))


def user_notes(db: Session, user_id: uuid.UUID, place: str, *, surface: str = "phone") -> list[PracticeNote]:
    """The user's own notes for a place, newest first."""
    return [r for r in _rows(db, user_id, surface, [" ".join((place or "").split())[: P.PLACE_CHARS]])
            if r.source == "user"]


def keep_user_note(db: Session, user_id: uuid.UUID, *, place: str, text: str, tool: str | None = None,
                   surface: str = "phone", replaces: list[str] | None = None) -> PracticeNote:
    """The user's own note for a place (their words, or hers on their word). A review never changes it.
    ``replaces``: ids of the user's notes for this place that this one takes the place of (they're removed in the
    same transaction). At most ``P.USER_NOTES_PER_PLACE`` of theirs per place: past it, one has to be replaced."""
    text = " ".join((text or "").split())
    place = " ".join((place or "").split())[: P.PLACE_CHARS]
    if not text or not place:
        raise ValueError("a note needs a place and text")
    if len(text) > USER_NOTE_CHARS:
        raise ValueError(f"a note is at most {USER_NOTE_CHARS} characters")
    theirs = {str(r.id): r for r in user_notes(db, user_id, place, surface=surface)}
    gone = []
    for note_id in replaces or []:
        row = theirs.get(str(note_id).strip())
        if row is None:
            raise ValueError(f"{note_id} isn't one of the user's notes for {place}")
        gone.append(row)
    if len(theirs) - len(gone) >= P.USER_NOTES_PER_PLACE:
        raise ValueError(f"{place} already has {P.USER_NOTES_PER_PLACE} of the user's notes: replace one")
    for row in gone:
        db.delete(row)
    row = PracticeNote(user_id=user_id, surface=surface, place=place, tool=tool, status="known", source="user",
                       text=text)
    db.add(row)
    db.flush()
    return row


def edit_note(db: Session, user_id: uuid.UUID, note_id: uuid.UUID, *, text: str | None = None,
              status: str | None = None) -> PracticeNote | None:
    """The user editing a note (any note: an edit of theirs makes it theirs)."""
    row = db.get(PracticeNote, note_id)
    if row is None or row.user_id != user_id:
        return None
    if text is not None:
        text = " ".join(text.split())
        if not text:
            raise ValueError("a note needs text")
        if len(text) > USER_NOTE_CHARS:
            raise ValueError(f"a note is at most {USER_NOTE_CHARS} characters")
        row.text = text
    if status is not None:
        if status not in ("known", "to_try"):
            raise ValueError("status is known or to_try")
        row.status = status
    row.source = "user"
    row.revision += 1
    return row


def remove_note(db: Session, user_id: uuid.UUID, note_id: uuid.UUID) -> bool:
    row = db.get(PracticeNote, note_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    return True


# --------------------------------------------------------------------------- #
# The review
# --------------------------------------------------------------------------- #


def _talk(db: Session, convs: list[str], asked: list[str]) -> tuple[list[str], list[str]]:
    """The user's words while her hands worked (from the first message her hands' turns answered) and her replies
    since, in each conversation's own order (``conversation_sequence``). The conversations are the ones her hands'
    turns were in (the user's own)."""
    said, replies = [], []
    asked_ids = [uuid.UUID(a) for a in asked]
    from app.services.heard import heard_text
    for conv in convs:
        conv_id = uuid.UUID(conv)
        first = db.scalar(select(ChatMessage.conversation_sequence).where(
            ChatMessage.conversation_id == conv_id, ChatMessage.id.in_(asked_ids)).order_by(
            ChatMessage.conversation_sequence).limit(1)) if asked_ids else None
        query = select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
        if first is not None:
            query = query.where(ChatMessage.conversation_sequence >= first).order_by(
                ChatMessage.conversation_sequence).limit(TALK_MESSAGES)
            rows = list(db.scalars(query))
        else:  # no message to start from: the latest few
            rows = list(reversed(db.scalars(query.order_by(ChatMessage.conversation_sequence.desc())
                                            .limit(TALK_FALLBACK)).all()))
        for r in rows:
            if r.role == "user" and (r.content or "").strip():
                said.append(r.content)
            elif r.role == "assistant":
                heard = heard_text(r.content or "", r.heard_chars)
                if heard.strip():
                    replies.append(heard)
    return said[-30:], replies[-30:]


def _draw(jpeg_b64: str, marks: list[dict]) -> str:
    """The picture, scaled to PICTURE_SIDE, with her marks in red, numbered in order."""
    from PIL import Image, ImageDraw

    image = Image.open(io.BytesIO(base64.b64decode(jpeg_b64))).convert("RGB")
    scale = min(1.0, PICTURE_SIDE / max(image.size))
    if scale < 1.0:
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    draw = ImageDraw.Draw(image)
    radius = max(10, int(min(image.size) * 0.02))
    for n, mark in enumerate(marks, 1):
        if "tap" in mark:
            x, y = (v * scale for v in mark["tap"])
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=MARK, width=4)
            draw.text((x + radius + 3, y - radius), str(n), fill=MARK)
        elif "swipe" in mark:
            x1, y1, x2, y2 = (v * scale for v in mark["swipe"])
            draw.line([x1, y1, x2, y2], fill=MARK, width=4)
            draw.ellipse([x2 - 6, y2 - 6, x2 + 6, y2 + 6], fill=MARK)
            draw.text((x1 + 6, y1 - 14), str(n), fill=MARK)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return base64.b64encode(out.getvalue()).decode()


def _pictures(steps: list[P.Step], pics: dict[int, dict], last_look: int | None) -> tuple[dict[int, int], list[dict]]:
    """Which pictures the review sees: the ones she aimed on, spread evenly over the whole stretch when there are
    too many (the misses are as likely early as late), and the last look. Returns (kept index -> shown index, shown
    pictures in order)."""
    aimed = sorted({s.picture for s in steps if s.picture is not None and s.picture in pics})
    room = P.PICTURES_REVIEWED - (1 if last_look in pics else 0)
    if len(aimed) > room > 0:
        step = (len(aimed) - 1) / (room - 1) if room > 1 else 0
        chosen = sorted({aimed[round(i * step)] for i in range(room)})
    else:
        chosen = aimed if room > 0 else []
    if last_look in pics and last_look not in chosen:
        chosen.append(last_look)
    chosen.sort()
    return {k: i for i, k in enumerate(chosen)}, [pics[k] for k in chosen]


def _judge(text: str, images: list[str]) -> P.Review:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.ai.agent import get_chat_model, invoke_private
    settings = get_settings()
    content: list[dict] = [{"type": "text", "text": text}]
    for i, image in enumerate(images):
        content.append({"type": "text", "text": f"Picture {i}:"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image,
                                                           "detail": "high"}})
    model = get_chat_model(settings.practice_review_model, settings.practice_review_reasoning_effort)
    return invoke_private(model.with_structured_output(P.Review),
                          [SystemMessage(P.system_prompt()), HumanMessage(content=content)])


def tools_for(surface: str) -> set[str]:
    if surface == "phone":
        from app.ai.tools import phone_control
        return set(phone_control.TOOL_NAMES) | set(phone_control.OPTIONAL_TOOL_NAMES)
    return set()


def review(user_id: uuid.UUID, surface: str, taken: dict, *, judge=_judge) -> dict:
    """Review one taken stretch and change her notes. Returns the trace record."""
    steps = [P.Step(**s) for s in taken["steps"]]
    meta = taken.get("meta") or {}
    last_look = int(meta["last_look"]) if meta.get("last_look") not in (None, "") else None
    shown, chosen = _pictures(steps, taken.get("pics") or {}, last_look)
    for s in steps:
        s.picture = shown.get(s.picture) if s.picture is not None else None
    images, captions = [], []
    for pic in chosen:
        try:
            images.append(_draw(pic["jpeg"], pic.get("marks") or []))
            captions.append(pic.get("caption") or "")
        except Exception:  # noqa: BLE001 - a picture that won't open is left out, said in its caption
            log.warning("practice: a picture couldn't be drawn")
            images.append(None)
            captions.append("(couldn't be opened)")
    keep = [i for i, im in enumerate(images) if im is not None]
    images = [images[i] for i in keep]
    remap = {old: new for new, old in enumerate(keep)}
    captions = [captions[i] for i in keep]
    for s in steps:
        s.picture = remap.get(s.picture) if s.picture is not None else None
    places = sorted(P.acted_places(steps))
    trace: dict = {"at": datetime.now(timezone.utc).isoformat(), "user_id": str(user_id), "surface": surface,
                   "steps": len(steps), "pictures": len(images), "places": places,
                   "had": sorted(taken.get("shown") or [])}
    with SessionLocal() as db:
        asked, replies = _talk(db, taken.get("convs") or [], taken.get("asked") or [])
        rows = _rows(db, user_id, surface, places)
        had = set(taken.get("shown") or [])
        stretch = P.Stretch(surface=surface, asked=asked, replies=replies, steps=steps, pictures=captions,
                            notes=[replace(_view(r), had=str(r.id) in had) for r in rows])
        if not any(s.tool != "read_phone_screen" for s in steps):
            trace["outcome"] = "only looked: nothing to learn about using the tools"
            return trace
        result = judge(P.render_stretch(stretch), images)
        kept, dropped = P.check(result, stretch, tools=tools_for(surface))
        by_id = {str(r.id): r for r in rows}
        changed = []
        for c in kept:
            if c.action == "create":
                row = PracticeNote(user_id=user_id, surface=surface, place=c.place, tool=c.tool, status=c.status,
                                   source="practice", text=c.text, evidence=c.evidence or None)
                db.add(row)
                by_id[str(row.id)] = row
            elif c.action == "rewrite":
                row = by_id[c.note]
                row.text, row.status, row.tool = c.text, c.status, c.tool or row.tool
                row.evidence = c.evidence or row.evidence
                row.revision += 1
            else:
                db.delete(by_id.pop(c.note))
            changed.append({"action": c.action, "note": c.note, "place": c.place, "tool": c.tool,
                            "status": c.status, "text": c.text, "evidence": c.evidence})
        db.commit()
    trace.update(outcome="reviewed", summary=result.summary, user_notes=result.user_notes,
                 tallies=[t.model_dump() for t in result.tallies], changes=changed, dropped=dropped)
    return trace


def _trace(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
        with open(TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.warning("practice: the review trace couldn't be written")


def run_once(*, client: redis.Redis | None = None, now: float | None = None, judge=_judge) -> bool:
    """Review one stretch whose hands have been still long enough. True when one was reviewed."""
    client = client or _client()
    if client is None:
        return False
    now = time.time() if now is None else now
    settle = float(get_settings().practice_settle_seconds)
    try:
        due = client.zrangebyscore(_DUE, 0, now - settle, start=0, num=1)
    except redis.RedisError:
        return False
    if not due:
        return False
    member = due[0].decode()
    if not client.zrem(_DUE, member):  # another worker took it
        return True
    user, surface = member.rsplit(":", 1)
    taken = _take(client, user, surface)
    if taken is None:
        return True
    try:
        record = review(uuid.UUID(user), surface, taken, judge=judge)
    except Exception as exc:  # noqa: BLE001 - the stretch is lost; what she does is unaffected
        log.exception("practice: reviewing a stretch failed")
        record = {"at": datetime.now(timezone.utc).isoformat(), "user_id": user, "surface": surface,
                  "outcome": f"failed: {type(exc).__name__}: {exc}"[:300]}
    _trace(record)
    log.info("practice: %s", {k: record.get(k) for k in ("user_id", "surface", "steps", "outcome")})
    return True
