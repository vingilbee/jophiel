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
TRANSCRIBE_PROMPT = "The speaker is talking in English to Jophiel, nicknamed Jofy."
# While the user plays Minecraft with Columbina: game words the model would
# otherwise hear as everyday ones ("sheep" as "ship", "creeper" as "keeper").
# Only nouns, and no command words, for the echo reason above; a transcript made
# of nothing but these words is treated as an echo and dropped.
MINECRAFT_WORDS = (
    "sheep", "wool", "bed", "creeper", "zombie", "skeleton", "spider", "enderman",
    "pickaxe", "crafting table", "furnace", "torch", "cobblestone", "oak logs",
    "planks", "iron", "diamond", "village", "cave", "Nether",
)
MINECRAFT_PROMPT = (
    "The speaker is talking in English to Jophiel (Jofy) while playing Minecraft, so they may "
    "mention " + ", ".join(MINECRAFT_WORDS) + "."
)
_PROMPT_WORDS = {w for phrase in MINECRAFT_WORDS for w in phrase.lower().split()} | {
    "and", "the", "a", "an", "of", "or",
}
# A transcript sharing this many words in a row with a prompt is the prompt read
# back (seen live: "The speaker is talking in English while playing Minecraft,
# so they may mention sheep, ..." and "context: ### The speaker is ...").
_ECHO_RUN = 4
# Her name ("Jo-fy-el") and nickname ("Jofy") spelled the way the transcriber
# guesses them. Only spellings that aren't English words, so a real "jovial" is
# never rewritten; the prompt's names are what steer that one.
_NAME_VARIANTS = re.compile(
    r"\bjo[\s-]?(?:ph|f|ff)(?:i|y|ee|ie)[\s-]?(?:e|ie)?l+e?\b", re.IGNORECASE,
)
_NICKNAME_VARIANTS = re.compile(r"\bjo(?:ph|f|ff)(?:y|ie|ey|ee)\b", re.IGNORECASE)
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


async def transcribe(pcm: bytes, sample_rate: int, *, minecraft: bool = False) -> str:
    """Transcribe one complete utterance of 16-bit mono PCM.

    Returns "" for audio too short to be speech, so a stray VAD trigger is a
    no-op turn rather than an error the user has to see. ``minecraft``: the
    user is playing, so game words are expected.
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

    prompt = MINECRAFT_PROMPT if minecraft else TRANSCRIBE_PROMPT
    text = await _create(client, settings.stt_model, audio, prompt=prompt)
    if is_wrong_script(text) and settings.stt_model != FALLBACK_MODEL:
        logger.warning("STT returned non-English script; retrying with %s", FALLBACK_MODEL)
        text = await _create(client, FALLBACK_MODEL, audio, prompt=prompt)
    if is_wrong_script(text):
        # Still not English: better to hear nothing than to switch languages.
        return ""
    if is_prompt_echo(text):
        logger.warning("STT echoed its prompt; treating it as silence")
        return ""
    return fix_name(text)


def fix_name(text: str) -> str:
    """Write her name as Jophiel and her nickname as Jofy, however the
    transcriber spelled them."""
    return _NICKNAME_VARIANTS.sub("Jofy", _NAME_VARIANTS.sub("Jophiel", text))


def _runs(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


_PROMPT_RUNS = set().union(*(_runs(re.findall(r"[a-z]+", p.lower()), _ECHO_RUN)
                             for p in (TRANSCRIBE_PROMPT, MINECRAFT_PROMPT)))


def is_prompt_echo(text: str) -> bool:
    """True when a transcript is the model reading its prompt back on
    near-silent audio: several game-prompt words and nothing else, or any
    stretch of the prompt's own sentence. One word ("Sheep.") is a real answer
    and is kept."""
    words = re.findall(r"[a-z]+", text.lower())
    if len(words) >= 3 and all(w in _PROMPT_WORDS for w in words):
        return True
    return bool(_runs(words, _ECHO_RUN) & _PROMPT_RUNS)


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
