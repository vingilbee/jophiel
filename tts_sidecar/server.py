"""HTTP sidecar exposing the Columbina voice.

Runs in its own venv and its own process, separate from the FastAPI backend, for
two reasons: GPT-SoVITS pins transformers<=4.50 while the backend needs a much
newer one for Qwen3-VL, and keeping it separate means the ~GB of model weights
isn't resident when nobody is using voice.

    .venv/bin/uvicorn server:app --port 8787

The backend talks to this over plain HTTP (app/services/tts.py).
"""

from __future__ import annotations

import io
import os
import wave
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import engine

_state: dict = {}


def _rss_mb() -> float:
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline = engine.load_pipeline(device=os.environ.get("TTS_DEVICE", "cpu"))
    engine.warm(pipeline)
    _state["pipeline"] = pipeline
    _state["rss_mb"] = _rss_mb()
    print(f"[tts] ready, resident {_state['rss_mb']:.0f} MB")
    yield
    _state.clear()


app = FastAPI(title="Columbina TTS sidecar", lifespan=lifespan)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # Streaming trades a little quality for a much earlier first chunk.
    streaming: bool = True


@app.get("/health")
def health() -> dict:
    return {
        "ok": "pipeline" in _state,
        "device": os.environ.get("TTS_DEVICE", "cpu"),
        "mode": engine.MODE,
        "reference_cached": (
            engine.reference_cached(_state["pipeline"])
            if "pipeline" in _state else False
        ),
        "rss_mb": round(_rss_mb(), 1),
        "startup_rss_mb": round(_state.get("rss_mb", 0.0), 1),
    }


@app.post("/tts")
def tts(req: SpeakRequest) -> StreamingResponse:
    """Stream raw PCM as it is generated.

    Raw int16 mono rather than wav: the caller forwards these straight over a
    WebSocket to be queued for playback, and a wav header would have to lie
    about a length we don't know yet. Sample rate travels in a header.
    """
    pipeline = _state.get("pipeline")
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Model still loading")

    # Pull the first chunk eagerly so the sample rate is known for the header,
    # then drain the *same* generator -- re-calling synthesize() would redo the
    # whole utterance.
    gen = engine.synthesize(pipeline, req.text, streaming=req.streaming)
    first = next(gen, None)
    if first is None:
        raise HTTPException(status_code=500, detail="Synthesis produced no audio")
    sample_rate, first_chunk = first

    def body():
        yield first_chunk.tobytes()
        for _sr, chunk in gen:
            yield chunk.tobytes()

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": str(sample_rate), "X-Audio-Format": "pcm_s16le_mono"},
    )


@app.post("/tts.wav")
def tts_wav(req: SpeakRequest):
    """Whole utterance as a wav file. Used to pre-render the filler cache."""
    pipeline = _state.get("pipeline")
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Model still loading")

    sample_rate, parts = None, []
    for sr, chunk in engine.synthesize(pipeline, req.text, streaming=False):
        sample_rate = sr
        parts.append(chunk.tobytes())
    if not parts:
        raise HTTPException(status_code=500, detail="Synthesis produced no audio")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"".join(parts))
    return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="audio/wav")
