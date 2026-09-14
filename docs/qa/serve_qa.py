"""Real app/persistence, with explicitly deterministic chat and prediction providers."""
import asyncio
import subprocess
import sys
import threading

from qa_env import configure, BACKEND

configure()

LIVE = "--live" in sys.argv
if LIVE:
    import os
    from dotenv import dotenv_values
    key = dotenv_values(BACKEND / ".env").get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("BLOCKED: no configured OpenAI key for bounded live QA")
    os.environ["OPENAI_API_KEY"] = key
    os.environ["MACROVISION_LOAD_VLM"] = "true"
    os.environ["MACROVISION_VLM_BACKEND"] = "openai"
    os.environ["HF_HUB_OFFLINE"] = "1"

if "--migrate" in sys.argv:
    raise SystemExit(subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND))

from app.main import app
from app.api.routes import chat
from app.services import prediction
from app.ai.MacroVision import NutritionPrediction
from app.workers import chat as chat_worker
from app.workers.runner import run as run_workers


async def deterministic_chat(*args, **kwargs):
    # The transport, authentication, orchestration route and persistence are real.
    # Only generated tokens are controlled; this is not a live AI quality test.
    for word in "This is a deterministic QA reply for testing streaming and persistence.".split():
        await asyncio.sleep(0.12)
        yield {"type": "token", "content": word + " "}


class Predictor:
    async def predict_async(self, image, mode="combined", **kwargs):
        await asyncio.sleep(0.15)
        return NutritionPrediction(calories=600.4, protein=35.2, carbs=70.1, fat=20.3,
                                   source="regression", dish=None, portion_scale=None)


if not LIVE:
    chat.stream_chat = deterministic_chat
    # Chat jobs execute in the durable worker, not in the WebSocket process.
    # Patch the worker's provider as well so QA exercises the real queue/Redis
    # stream path without invoking a live model.
    chat_worker.stream_chat = deterministic_chat
    prediction.get_predictor = lambda: Predictor()

# Record the existence of the production token-print bug without retaining tokens.
class RedactedOutput:
    def __init__(self, stream):
        self.stream = stream
        self.pending = ""
    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.startswith("USER:"):
                line = "QA_EVIDENCE: signin attempted to log user and raw session token [REDACTED]"
            self.stream.write(line + "\n")
            self.stream.flush()
        return len(text)
    def flush(self):
        self.stream.flush()
    def isatty(self):
        return False

sys.stdout = RedactedOutput(sys.stdout)

if __name__ == "__main__":
    import uvicorn
    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_workers,
        args=(worker_stop,),
        name="qa-workers",
        daemon=True,
    )
    worker_thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=58001 if LIVE else 58000, log_level="warning")
    finally:
        worker_stop.set()
        worker_thread.join(timeout=10)
