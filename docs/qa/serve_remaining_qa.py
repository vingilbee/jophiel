"""Real backend QA server with deterministic edge-case chat responses."""
import asyncio
import threading

from serve_qa import app, chat
from app.workers import chat as chat_worker
from app.workers.runner import run as run_workers


def last_content(messages):
    content = messages[-1].get("content", "") if messages else ""
    # The production history builder merges adjacent user turns. For edge-case
    # routing, inspect only the newest merged segment.
    return content.rsplit("\n\n", 1)[-1]


async def edge_case_chat(messages, **kwargs):
    content = last_content(messages)
    if "QA-ERROR" in content:
        await asyncio.sleep(0.08)
        raise RuntimeError("controlled QA provider failure")
    if "QA-MALFORMED" in content:
        yield {"type": "unknown_provider_event", "content": "ignored"}
        await asyncio.sleep(0.08)
        yield {"type": "token", "content": "Malformed event was ignored safely. "}
        return
    if "QA-MARKDOWN" in content:
        for token in ["# Meal plan\n\n", "```python\n", "print('safe')\n", "```\n"]:
            await asyncio.sleep(0.08)
            yield {"type": "token", "content": token}
        return
    if "Retry after controlled failure" in content:
        for word in "QA retry succeeded after the controlled failure.".split():
            await asyncio.sleep(0.08)
            yield {"type": "token", "content": word + " "}
        return
    for word in "This is a deterministic QA reply for testing streaming and persistence.".split():
        await asyncio.sleep(0.08)
        yield {"type": "token", "content": word + " "}


chat.stream_chat = edge_case_chat
chat_worker.stream_chat = edge_case_chat


if __name__ == "__main__":
    import uvicorn

    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=run_workers,
        args=(worker_stop,),
        name="remaining-qa-workers",
        daemon=True,
    )
    worker_thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=58000, log_level="warning")
    finally:
        worker_stop.set()
        worker_thread.join(timeout=10)
