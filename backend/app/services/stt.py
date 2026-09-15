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
import wave

from app.core.config import get_settings

# Guard rails around what we'll pay to transcribe. The client's VAD caps
# utterances well below this; these catch a stuck mic or a malicious client.
MIN_UTTERANCE_SECONDS = 0.25
MAX_UTTERANCE_SECONDS = 60.0
logger = logging.getLogger(__name__)


class STTError(Exception):
    """Transcription failed in a way the caller should surface to the user."""


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
        from openai import AsyncOpenAI
    except ImportError as e:  # pragma: no cover
        raise STTError("The openai package is not installed.") from e

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    audio = pcm_to_wav(pcm, sample_rate)

    try:
        result = await client.audio.transcriptions.create(
            model=settings.stt_model,
            file=("utterance.wav", audio, "audio/wav"),
            # The assistant is English-only; pinning the language stops Whisper
            # from mis-detecting short utterances as another language.
            language="en",
        )
    except Exception as e:  # openai raises a family of errors; treat alike
        logger.exception("Speech transcription failed")
        raise STTError("Transcription failed.") from e

    return (getattr(result, "text", "") or "").strip()
