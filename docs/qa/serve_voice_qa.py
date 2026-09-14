"""Real backend QA server with deterministic STT, chat, and TTS providers."""
import asyncio
import os
import threading

from serve_qa import app, chat, deterministic_chat
from app.core.config import get_settings

# serve_qa applies the normal QA defaults (voice off) before importing the app.
# Override the cached settings for this voice-only harness after that setup.
os.environ["VOICE_ENABLED"] = "true"
get_settings.cache_clear()
from app.services import stt as stt_service
from app.services import tts as tts_service
from app.workers.runner import run as run_workers


async def deterministic_transcribe(pcm: bytes, sample_rate: int) -> str:
    if not pcm:
        return ""
    return "Deterministic voice QA turn"


async def deterministic_synthesize(text: str):
    # Small valid 16-bit PCM chunks, enough to exercise audio framing.
    for _ in range(3):
        await asyncio.sleep(0.01)
        yield b"\x00\x00" * 160


stt_service.transcribe = deterministic_transcribe
tts_service.synthesize = deterministic_synthesize
tts_service.pick_filler = lambda: None
chat.stream_chat = deterministic_chat


if __name__ == "__main__":
    import uvicorn

    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_workers,
        args=(worker_stop,),
        name="voice-qa-workers",
        daemon=True,
    )
    worker_thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=58000, log_level="warning")
    finally:
        worker_stop.set()
        worker_thread.join(timeout=10)
