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
# After the first clause: long enough that its audio covers the next clause's
# setup time (measured ~0.7-1.4 s on the M1, RTF ~0.85 with other load).
_LATER_MIN_CLAUSE_CHARS = 60
_LATER_MAX_CLAUSE_CHARS = 220

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
    later_min_chars: int = _LATER_MIN_CLAUSE_CHARS,
    later_max_chars: int = _LATER_MAX_CLAUSE_CHARS,
):
    """Accumulate streamed tokens, yielding whole clauses.

    The first clause is short so audio starts soon. Later ones are longer:
    each clause pays ~1 s of fixed sidecar setup before its first byte, and
    clauses are synthesized one after another, so a short clause's audio runs
    out before the next one starts and the reply plays in cut-up pieces.

    Usage:
        feed, flush = clause_chunker()
        for token in stream:
            for clause in feed(token):
                ...
        for clause in flush():
            ...
    """
    buf = ""
    emitted = 0

    def feed(token: str) -> Iterable[str]:
        nonlocal buf, emitted
        buf += token
        while True:
            lo, hi = (min_chars, max_chars) if emitted == 0 else (later_min_chars, later_max_chars)
            # The first boundary that makes a long enough clause. Shorter ones
            # (an abbreviation, a decimal point, a brief phrase) are skipped.
            end = next(
                (m.end() for m in _CLAUSE_END.finditer(buf) if len(buf[: m.end()].strip()) >= lo),
                None,
            )
            if end is None:
                if len(buf) >= hi:
                    split = buf.rfind(" ", 0, hi + 1)
                    if split < lo:
                        split = hi
                    clause = buf[:split].strip()
                    buf = buf[split:].lstrip()
                    if clause:
                        emitted += 1
                        yield clause
                    continue
                return
            clause = buf[:end].strip()
            buf = buf[end:]
            if clause:
                emitted += 1
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
