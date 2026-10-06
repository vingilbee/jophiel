"""Speech-to-text for voice turns, via the OpenAI audio API.

The browser captures 16-bit mono PCM (no container -- an AudioWorklet has no
encoder). The transcription API needs a recognized audio format, so the raw
frames get a wav header here rather than shipping an encoder to the client.

Kept deliberately thin: one utterance in, one string out. Endpointing is the
client's job (VAD), so by the time audio arrives here the turn is already known
to be complete.
"""

from __future__ import annotations

import io
import logging
import re
import wave
from functools import lru_cache

from app.core.config import get_settings

# Guard rails around what we'll pay to transcribe. The client's VAD caps
# utterances well below this; these catch a stuck mic or a malicious client.
MIN_UTTERANCE_SECONDS = 0.25
MAX_UTTERANCE_SECONDS = 60.0
logger = logging.getLogger(__name__)

# `language="en"` is only a hint to the gpt-4o transcribe models: a short,
# quiet clip ("wait" over the tail of the assistant's own voice) can still come
# back as 喂 or 웨이, and the agent then answers in that language. A prompt
# biases the model far more strongly than the language code does.
# No example phrases in it: on near-silent audio these models can echo prompt
# words back as a transcript, which would put "stop" in the user's mouth.
TRANSCRIBE_PROMPT = "The speaker is talking in English."
# whisper-1 enforces `language` strictly, so it is the retry when the primary
# model still returns another script. Slower, but only paid in that rare case.
FALLBACK_MODEL = "whisper-1"
# Han, kana, and Hangul. A transcript mostly in these scripts is a language
# mis-detection for this English-only assistant, not something the user said.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")


class STTError(Exception):
    """Transcription failed in a way the caller should surface to the user."""


@lru_cache(maxsize=1)
def _client(api_key: str | None):
    """One client per process, so each utterance reuses a warm HTTPS
    connection instead of paying a new TLS handshake on the critical path."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key)


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def transcribe(pcm: bytes, sample_rate: int) -> str:
    """Transcribe one complete utterance of 16-bit mono PCM.

    Returns "" for audio too short to be speech, so a stray VAD trigger is a
    no-op turn rather than an error the user has to see.
    """
    settings = get_settings()

    seconds = len(pcm) / 2 / max(sample_rate, 1)
    if seconds < MIN_UTTERANCE_SECONDS:
        return ""
    if seconds > MAX_UTTERANCE_SECONDS:
        raise STTError(f"Utterance too long ({seconds:.0f}s).")

    try:
        client = _client(settings.openai_api_key)
    except ImportError as e:  # pragma: no cover
        raise STTError("The openai package is not installed.") from e

    audio = pcm_to_wav(pcm, sample_rate)

    text = await _create(client, settings.stt_model, audio, prompt=TRANSCRIBE_PROMPT)
    if is_wrong_script(text) and settings.stt_model != FALLBACK_MODEL:
        logger.warning("STT returned non-English script; retrying with %s", FALLBACK_MODEL)
        text = await _create(client, FALLBACK_MODEL, audio, prompt=TRANSCRIBE_PROMPT)
    if is_wrong_script(text):
        # Still not English: better to hear nothing than to switch languages.
        return ""
    return text


def is_wrong_script(text: str) -> bool:
    """True when most letters are CJK/Hangul, i.e. the language was misheard."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if _CJK.match(c)) * 2 > len(letters)


async def _create(client, model: str, audio: bytes, *, prompt: str) -> str:
    try:
        result = await client.audio.transcriptions.create(
            model=model,
            file=("utterance.wav", audio, "audio/wav"),
            # The assistant is English-only; see TRANSCRIBE_PROMPT for why
            # this alone isn't enough.
            language="en",
            prompt=prompt,
        )
    except Exception as e:  # openai raises a family of errors; treat alike
        logger.exception("Speech transcription failed")
        raise STTError("Transcription failed.") from e
    return (getattr(result, "text", "") or "").strip()
