"""Turning the agent's reply into Columbina speech.

Two jobs:

1. Chunking. The agent streams tokens, but TTS needs whole clauses -- feeding it
   word fragments produces garbage prosody. `clause_chunker` accumulates tokens
   and emits at clause boundaries so synthesis for clause N overlaps generation
   of clause N+1.

2. Synthesis. Streams 16-bit mono PCM from the sidecar (tts_sidecar/server.py).

Latency context, measured on the M1: RTF is 0.63 -- synthesis outruns playback,
so once audio starts it never stutters. But time-to-first-chunk is ~1.9s and
flat regardless of length, because it's fixed setup cost. That onset is what the
user experiences as lag, and `pick_filler` is how it gets covered.
"""

from __future__ import annotations

import json
import os
import random
import re
import wave
from collections.abc import AsyncIterator, Iterable
from functools import lru_cache
from pathlib import Path

import httpx

from app.core.config import get_settings

FILLER_DIR = Path(__file__).resolve().parent.parent / "ai" / "voice" / "fillers"

# Clause-final punctuation. Splitting on these rather than sentence-final ones
# gets audio started sooner on long replies, and GPT-SoVITS handles a clause as
# a prosodic unit fine.
_CLAUSE_END = re.compile(r"[.!?;:,\n]")
# Below this, a clause is too short to synthesize well -- let it accumulate.
_MIN_CLAUSE_CHARS = 12
# Do not wait forever for punctuation. Model output can begin with a long
# sentence or markdown-free explanation; emitting at a word boundary keeps
# time-to-first-speech bounded without feeding TTS broken word fragments.
_MAX_CLAUSE_CHARS = 96

def _sample_aligned(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Re-chunk a byte stream so every yielded piece has an even length.

    HTTP chunk boundaries are arbitrary and can split a 16-bit PCM sample in
    half. Each chunk here becomes its own websocket frame downstream, and the
    client does `new Int16Array(frame)` per frame -- an odd-length frame either
    throws (dropping audio) or, worse, shifts every subsequent frame's parity,
    turning the rest of the stream into noise. Carry any leftover odd byte
    forward and prepend it to the next chunk instead.
    """
    async def gen():
        carry = b""
        async for chunk in chunks:
            data = carry + chunk
            if len(data) % 2:
                yield data[:-1]
                carry = data[-1:]
            else:
                yield data
                carry = b""
    return gen()

def clause_chunker(
    min_chars: int = _MIN_CLAUSE_CHARS,
    max_chars: int = _MAX_CLAUSE_CHARS,
):
    """Accumulate streamed tokens, yielding whole clauses.

    Usage:
        feed, flush = clause_chunker()
        for token in stream:
            for clause in feed(token):
                ...
        for clause in flush():
            ...
    """
    buf = ""

    def feed(token: str) -> Iterable[str]:
        nonlocal buf
        buf += token
        while True:
            m = _CLAUSE_END.search(buf)
            if m is None:
                if len(buf) >= max_chars:
                    split = buf.rfind(" ", 0, max_chars + 1)
                    if split < min_chars:
                        split = max_chars
                    clause = buf[:split].strip()
                    buf = buf[split:].lstrip()
                    if clause:
                        yield clause
                    continue
                return
            head, rest = buf[: m.end()], buf[m.end() :]
            if len(head.strip()) < min_chars:
                # Too short to stand alone (an abbreviation, a decimal point).
                # Keep scanning past this boundary instead of emitting it.
                nxt = _CLAUSE_END.search(buf, m.end())
                if nxt is None:
                    return
                head, rest = buf[: nxt.end()], buf[nxt.end() :]
            clause = head.strip()
            buf = rest
            if clause:
                yield clause

    def flush() -> Iterable[str]:
        nonlocal buf
        tail = buf.strip()
        buf = ""
        if tail:
            yield tail

    return feed, flush


@lru_cache
def _fillers() -> list[dict]:
    manifest = FILLER_DIR / "manifest.json"
    if not manifest.exists():
        return []
    try:
        return json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return []


# Measured time-to-first-chunk. A filler shorter than this leaves dead air.
_TARGET_FILLER_SECONDS = 1.5


def pick_filler(min_seconds: float = _TARGET_FILLER_SECONDS) -> tuple[bytes, int] | None:
    """Pre-rendered filler as (pcm_s16le, sample_rate), or None.

    Played the instant the user stops speaking, to cover TTS onset. Returns raw
    PCM (header stripped) so it can go down the same path as streamed audio.

    Chooses at random *among fillers long enough to actually cover the gap* --
    picking uniformly across all of them lands on a 0.8s clip a third of the
    time and leaves a second of silence, which is worse than no filler at all.
    Falls back to the longest available when nothing meets the bar.
    """
    if not get_settings().tts_fillers_enabled:
        return None
    choices = _fillers()
    if not choices:
        return None
    long_enough = [c for c in choices if c.get("seconds", 0) >= min_seconds]
    if long_enough:
        entry = random.choice(long_enough)
    else:
        entry = max(choices, key=lambda c: c.get("seconds", 0))
    path = FILLER_DIR / entry["file"]
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as w:
            return w.readframes(w.getnframes()), w.getframerate()
    except (OSError, wave.Error):
        return None


async def synthesize(text: str) -> AsyncIterator[bytes]:
    """Stream PCM for `text` from the sidecar.

    Yields nothing if the sidecar is down or errors -- voice degrades to a
    text-only reply rather than breaking the turn.
    """
    settings = get_settings()
    text = text.strip()
    if not text:
        return

    url = f"{settings.tts_sidecar_url.rstrip('/')}/tts"
    try:
        async with httpx.AsyncClient(timeout=settings.tts_timeout_seconds) as client:
            async with client.stream(
                "POST", url, json={"text": text, "streaming": True}
            ) as resp:
                resp.raise_for_status()
                async def raw_chunks():
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk

                async for aligned in _sample_aligned(raw_chunks()):
                    yield aligned
    except (httpx.HTTPError, OSError):
        return


async def sidecar_healthy() -> bool:
    settings = get_settings()
    url = f"{settings.tts_sidecar_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            return r.status_code == 200 and bool(r.json().get("ok"))
    except (httpx.HTTPError, OSError, ValueError):
        return False
