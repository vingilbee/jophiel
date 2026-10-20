"""Her inner state, live but without influence (docs/COLUMBINA_INNER_STATE_PLAN.md, step 5.2).

After a reply is delivered, a background thread reads the exchange the way the offline evaluation
did: the frozen teacher's thought about their message (intend_v4), then `appraisal-v3.1` labels
(extract_v8) with her actual reply. Its effects (per incident: shift, cause, size) move her mood
through the frozen layered model (``app/ai/mind/mood_layers.py``,
``Cognitive/simple/character/columbina.inner_state.json``).

- **No influence.** Nothing here reaches a reply, a thought, a plan or an action. The state, and the
  last few thoughts the reading wrote (moment + intention), are shown only in the chat page's mind
  panel (``GET /api/mind/state``) and logged to
  ``MIND_INNER_STATE_LOG_PATH``.
- **Uncertainty stays visible.** Two states are kept side by side, one for each reading of an
  unclear size (small / clear). When they fall in different word bands, the words say both.
- **Each event counts once; updates are atomic.** A per-conversation Redis lock keeps one
  conversation's readings (and its incidents) in order; the per-user state is written with an
  optimistic transaction (WATCH), so readings from two conversations can't overwrite each other.
- **Never fails a turn.** Off unless ``MIND_INNER_STATE_ENABLED``; every error is logged and dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ai.Cognitive.simple.contracts import MOOD_KEYS
from app.ai.Cognitive.simple.live import CHARACTER_DIR
from app.ai.Cognitive.simple.mood import band
from app.ai.mind import mood_layers as ml
from app.core.config import get_settings

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[2]
STATE_KEY = "inner:state:{user}"
INCIDENTS_KEY = "inner:incidents:{conversation}"
LOCK_KEY = "inner:lock:{conversation}"
INCIDENTS_TTL = 7 * 24 * 3600
CAUSES_KEPT = 20
THOUGHTS_KEPT = 5
WINDOW = 8
# The two readings of a shift of unclear size (the frozen config's range).
READINGS = ("small", "clear")


def enabled() -> bool:
    return get_settings().mind_inner_state_enabled


@lru_cache(maxsize=1)
def frozen() -> dict[str, Any]:
    return json.loads((CHARACTER_DIR / "columbina.inner_state.json").read_text())


def config(reading: str) -> ml.Config:
    f = frozen()
    low, high = f["unclear_amount"]["range"]
    return ml.Config(f["baseline"], f["half_life_minutes"], reaction_half_life_minutes=f["reaction_half_life_minutes"],
                     keep=f["keep"], unclear_amount=low if reading == "small" else high,
                     incident_cap=f["incident_cap"], squash=f["squash"],
                     negative_keep_scale=f.get("negative_keep_scale", 1.0))


def _redis():
    from app.services.auth import get_redis
    return get_redis()


# --- after a reply ------------------------------------------------------------------------------

def observe(*, user_id: uuid.UUID, conversation_id: uuid.UUID, history: list[dict[str, Any]], message: str,
            reply: str) -> None:
    """Read this exchange and move her mood, on a background thread. Returns at once."""
    if not enabled() or not reply:
        return
    threading.Thread(target=_safe_observe, name="inner-state", daemon=True,
                     kwargs=dict(user_id=user_id, conversation_id=conversation_id, history=list(history),
                                 message=message, reply=reply, at=time.time())).start()


def _safe_observe(**kwargs) -> None:
    try:
        _observe(**kwargs)
    except Exception:
        logger.exception("inner_state_live: reading failed; her mood is unchanged")


def _observe(*, user_id, conversation_id, history, message, reply, at) -> None:
    r = _redis()
    with r.lock(LOCK_KEY.format(conversation=conversation_id), timeout=180, blocking_timeout=180):
        book_raw = r.get(INCIDENTS_KEY.format(conversation=conversation_id))
        reading = asyncio.run(_read(history=history, message=message, reply=reply,
                                    book=json.loads(book_raw) if book_raw else None))
        r.set(INCIDENTS_KEY.format(conversation=conversation_id), json.dumps(reading["book"]), ex=INCIDENTS_TTL)
        before, after = _apply(r, user_id=user_id, conversation_id=conversation_id, number=reading["number"],
                               message=message, appraisal=reading["appraisal"], thought=reading["thought"], at=at)
    _log(dict(at=datetime.now(timezone.utc).isoformat(), user_id=str(user_id), conversation_id=str(conversation_id),
              number=reading["number"], message=message, reply=reply, thought=reading["thought"],
              effects=reading["appraisal"]["effects"], dropped=reading["appraisal"]["dropped_effects"],
              meant=reading["appraisal"]["meant"], her_part=reading["appraisal"]["her_part"],
              before=before, after=after))


async def _read(*, history, message, reply, book) -> dict[str, Any]:
    """The frozen teacher's thought, then appraisal-v3.1 labels with her actual reply."""
    from app.ai.Cognitive.simple.appraisal import IncidentBook, extract_v31
    from app.ai.Cognitive.simple.contracts import Bundle, Message
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.notice import notice
    from app.ai.Cognitive.simple.rhythm import own_rhythm
    from app.ai.Cognitive.simple.teacher import View, prompt
    from app.ai.Cognitive.simple.world import sample_typed_world
    from app.services.cognitive_live import character_page

    s = get_settings()
    earlier = [h for h in history if h.get("content")]
    if earlier and earlier[-1].get("role") == "user" and earlier[-1]["content"].strip() == message.strip():
        earlier = earlier[:-1]     # the history may already end with this message
    messages = [Message("user" if h["role"] == "user" else "columbina", h["content"], i + 1)
                for i, h in enumerate(earlier)]
    number = len(messages) + 1
    typed = sample_typed_world(CHARACTER_DIR / "columbina.world.json", seed=0)
    bundle = Bundle(turn=number, message=message, window=messages[-WINDOW:], notes=[], memories=[],
                    notice=notice(message, seconds_since_reply=None,
                                  first_user_turn=not any(m.speaker == "user" for m in messages)),
                    # The reading sees her baseline, not her current mood: a reading given a mood
                    # echoes it back as a feeling (docs/qa/inner-state-2026-10-19, 5.1).
                    mood=dict(frozen()["baseline"]), affinity=0.0, time_of_day=_time_of_day(datetime.now().hour),
                    previous=None, rhythm=own_rhythm(messages, [t["text"] for t in typed]), typed_memories=typed)
    view = View(character_page(), bundle, messages, full=True)
    llm = LLM(s.openai_api_key, model=s.mind_inner_state_model, concurrency=2)
    thought = await llm.text("intend", prompt("intend_v4"), view.inputs(), reasoning="low", max_tokens=2500)
    incidents = IncidentBook(**book) if book else IncidentBook()
    got = await extract_v31(llm, view, thought, reply, incidents)
    return dict(number=number, thought=thought, appraisal=got["appraisal"], book=incidents.to_dict())


def _time_of_day(hour: int) -> str:
    return "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "late_night"


def split_thought(text: str) -> dict[str, str]:
    """The teacher's thought (intend_v4) in its two parts, MOMENT and INTENTION. A thought that
    doesn't follow the format is kept whole as the moment."""
    found = dict(re.findall(r"(?ims)^\W*(moment|intention)\b[^:\n]*:?\s*(.+?)(?=^\W*(?:moment|intention)\b|\Z)", text))
    parts = {k.lower(): v.strip() for k, v in found.items()}
    return {"moment": parts.get("moment") or text.strip(), "intention": parts.get("intention", "")}


def _apply(r, *, user_id, conversation_id, number, message, appraisal, thought, at) -> tuple[dict, dict]:
    """One atomic update of her stored state (both readings), retried if another update raced it."""
    import redis

    key = STATE_KEY.format(user=user_id)
    labels = {i["id"]: i["label"] for i in appraisal.get("incidents_after", [])}
    for _ in range(8):
        with r.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                stored = json.loads(raw) if raw else {}
                states = {rd: ml.State.from_dict(stored[rd]) if rd in stored else ml.start(at) for rd in READINGS}
                before = {rd: ml.values(ml.fade(states[rd], config(rd), at), config(rd)) for rd in READINGS}
                for e in appraisal["effects"]:
                    shift = ml.Shift("chat", str(conversation_id), f"m{number}", e["incident"], e["feeling"],
                                     e["status"], e["size"], at)
                    for rd in READINGS:
                        states[rd] = ml.apply(states[rd], shift, config(rd)).state
                states = {rd: ml.fade(st, config(rd), at) for rd, st in states.items()}
                after = {rd: ml.values(states[rd], config(rd)) for rd in READINGS}
                causes = stored.get("causes", []) + [
                    dict(at=at, conversation_id=str(conversation_id), message=message[:160], feeling=e["feeling"],
                         status=e["status"], size=e["size"], incident=labels.get(e["incident"], e["incident"]),
                         quote=e["quote"][:200])
                    for e in appraisal["effects"] if e["status"] != "lingering"]
                thoughts = stored.get("thoughts", []) + [
                    dict(at=at, conversation_id=str(conversation_id), message=message[:160], **split_thought(thought))]
                pipe.multi()
                pipe.set(key, json.dumps({**{rd: states[rd].to_dict() for rd in READINGS},
                                          "causes": causes[-CAUSES_KEPT:], "thoughts": thoughts[-THOUGHTS_KEPT:],
                                          "updated_at": at}))
                pipe.execute()
                return before, after
            except redis.WatchError:
                continue
    raise RuntimeError("inner state: too many concurrent updates")


def _log(record: dict[str, Any]) -> None:
    path = Path(get_settings().mind_inner_state_log_path)
    if not path.is_absolute():
        path = BACKEND / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- what the debug strip shows -----------------------------------------------------------------

def words(values_by_reading: dict[str, dict[str, float]]) -> dict[str, str]:
    """Each value in words. When the two readings fall in different bands, both are named, so an
    uncertain size never reads as a precise feeling."""
    out = {}
    for k in MOOD_KEYS:
        names = []
        for rd in READINGS:
            name = band(k, values_by_reading[rd][k])
            if name not in names:
                names.append(name)
        out[k] = " or ".join(names)
    return out


def view(user_id: uuid.UUID, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    raw = _redis().get(STATE_KEY.format(user=user_id)) if enabled() else None
    stored = json.loads(raw) if raw else {}
    vals = {rd: ml.values(ml.fade(ml.State.from_dict(stored[rd]), config(rd), now), config(rd))
            if rd in stored else dict(frozen()["baseline"]) for rd in READINGS}
    return dict(enabled=enabled(), config=frozen()["chosen"],
                updated_at=stored.get("updated_at"),
                values={k: {rd: round(vals[rd][k], 3) for rd in READINGS} for k in MOOD_KEYS},
                baseline=frozen()["baseline"], words=words(vals),
                causes=list(reversed(stored.get("causes", [])))[:8],
                thoughts=list(reversed(stored.get("thoughts", []))))
