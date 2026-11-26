"""Finding a point on the phone's picture from a description, for her ``phone_tap_on``: a model trained to point at
things on screens (UI-TARS-1.5 through OpenRouter by default, ``HANDS_GROUNDING_MODEL``) reads the latest picture and
her words ("the green Attack button, bottom left") and answers with one point.

She decides *what* to tap; this decides *where*. Only for what has no number (a game, a map): with a
number the phone presses the exact item.

- **Coordinates.** UI-TARS-1.5 answers in the pixels of the picture as its processor resized it (Qwen2.5-VL's
  ``smart_resize``: sides a multiple of 28, total pixels bounded). The picture is resized to exactly that size here
  before it's sent, so the provider changes nothing, and the answer is scaled back to the picture the phone sent.
- **Answers.** ``click(start_box='(x,y)')``, ``<point>x y</point>`` or a box ``(x1,y1,x2,y2)`` (its centre): the first
  point or box in the action is taken.
- **Privacy.** The picture goes to OpenRouter's provider (outside setup: ``OPENROUTER_API_KEY``; without it the tool
  isn't offered). Nothing is stored; each call's timing (never the picture or the words) goes to
  ``var/phone_grounding.jsonl``.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings

log = logging.getLogger(__name__)

FACTOR = 28
MIN_PIXELS = 100 * FACTOR * FACTOR
MAX_PIXELS = 1280 * FACTOR * FACTOR  # about 1M: enough to aim, and keeps the upload small
TIMEOUT_SECONDS = 20.0
TARGET_CHARS = 300
TRACE = Path("var/phone_grounding.jsonl")

PROMPT = """\
You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next \
action to complete the task.

## Output Format
```
Action: ...
```

## Action Space
click(point='<point>x1 y1</point>')

## User Instruction
Tap {target}"""

_ACTION = re.compile(r"Action:(.*)", re.S)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_N = r"\s*(-?\d+(?:\.\d+)?)\s*"
_POINT = re.compile(rf"<point>{_N}[ ,]{_N}</point>")
_GROUP = re.compile(rf"[(\[]{_N},{_N}(?:,{_N},{_N})?[)\]]")


@dataclass(frozen=True)
class Found:
    x: int
    y: int
    ms: int


def smart_size(width: int, height: int, *, max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    """The size the model's processor would resize a width x height picture to (Qwen2.5-VL ``smart_resize``)."""
    w = max(FACTOR, round(width / FACTOR) * FACTOR)
    h = max(FACTOR, round(height / FACTOR) * FACTOR)
    if w * h > max_pixels:
        beta = math.sqrt(width * height / max_pixels)
        w = max(FACTOR, math.floor(width / beta / FACTOR) * FACTOR)
        h = max(FACTOR, math.floor(height / beta / FACTOR) * FACTOR)
    elif w * h < MIN_PIXELS:
        beta = math.sqrt(MIN_PIXELS / (width * height))
        w = math.ceil(width * beta / FACTOR) * FACTOR
        h = math.ceil(height * beta / FACTOR) * FACTOR
    return w, h


def prepare(jpeg_b64: str) -> tuple[str, tuple[int, int], tuple[int, int]] | None:
    """The picture as it's sent (base64 JPEG at the model's own size), that size, and the picture's own size."""
    try:
        image = Image.open(io.BytesIO(base64.b64decode(jpeg_b64, validate=True))).convert("RGB")
    except (ValueError, UnidentifiedImageError, OSError):
        return None
    own = image.size
    sent = smart_size(*own)
    if sent != own:
        image = image.resize(sent, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return base64.b64encode(out.getvalue()).decode(), sent, own


def parse(text: str) -> tuple[float, float] | None:
    """The point in the model's answer, in the pixels of the picture it was sent: the first point, or a box's centre."""
    found = _ACTION.search(text or "")
    action = found.group(1) if found else (text or "")
    if point := _POINT.search(action):
        return float(point.group(1)), float(point.group(2))
    if group := _GROUP.search(action):
        x1, y1, x2, y2 = group.groups()
        if x2 is None:
            return float(x1), float(y1)
        return (float(x1) + float(x2)) / 2, (float(y1) + float(y2)) / 2
    numbers = [float(v) for v in _NUMBER.findall(action)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    return None


def scale(point: tuple[float, float], sent: tuple[int, int], to: tuple[int, int]) -> tuple[int, int] | None:
    """A point on the sent picture, in the pixels of a picture of size ``to``; None when it's off the sent one."""
    x, y = point
    if not (0 <= x <= sent[0] and 0 <= y <= sent[1]):
        return None
    return (min(to[0] - 1, int(round(x * to[0] / sent[0]))), min(to[1] - 1, int(round(y * to[1] / sent[1]))))


def available() -> bool:
    return bool(get_settings().openrouter_api_key)


_client = None


def _openai():
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        settings = get_settings()
        _client = AsyncOpenAI(base_url=settings.hands_grounding_base_url, api_key=settings.openrouter_api_key,
                              timeout=TIMEOUT_SECONDS, max_retries=1)
    return _client


async def ask(picture_b64: str, target: str) -> str:
    """The model's answer for one picture (base64 JPEG) and target."""
    reply = await _openai().chat.completions.create(
        model=get_settings().hands_grounding_model,
        temperature=0,
        max_tokens=64,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{picture_b64}"}},
            {"type": "text", "text": PROMPT.format(target=target)},
        ]}],
    )
    return (reply.choices[0].message.content or "") if reply.choices else ""


def _trace(ms: int, ok: bool, why: str | None = None) -> None:
    try:
        TRACE.parent.mkdir(parents=True, exist_ok=True)
        with TRACE.open("a") as f:
            f.write(json.dumps({"at": time.time(), "model": get_settings().hands_grounding_model, "ms": ms, "ok": ok,
                                "why": why}) + "\n")
    except OSError:
        pass


async def locate(jpeg_b64: str, target: str, size: tuple[int, int]) -> Found | str:
    """Where ``target`` is on the picture, in the pixels of a picture of ``size`` (the screen she acts on), or
    why it can't be said."""
    target = " ".join((target or "").split())[:TARGET_CHARS]
    if not target:
        return "say what to tap"
    prepared = prepare(jpeg_b64)
    if prepared is None:
        return "the picture couldn't be read"
    picture, sent, _ = prepared
    started = time.monotonic()
    try:
        answer = await ask(picture, target)
    except Exception as exc:  # noqa: BLE001 - the pointing model failing is an answer to her, not a crash
        ms = int((time.monotonic() - started) * 1000)
        log.info("phone: pointing model failed after %d ms: %s", ms, type(exc).__name__)
        _trace(ms, False, type(exc).__name__)
        return f"the pointing model didn't answer ({type(exc).__name__})"
    ms = int((time.monotonic() - started) * 1000)
    point = parse(answer)
    where = scale(point, sent, size) if point else None
    log.info("phone: pointing model answered in %d ms: %s", ms, "a point" if where else "no point")
    _trace(ms, where is not None, None if where else "no point")
    if where is None:
        return "the pointing model gave no point on the picture"
    return Found(where[0], where[1], ms)
