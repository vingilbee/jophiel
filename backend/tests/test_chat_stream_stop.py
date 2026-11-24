"""A stopped turn stops its model run: no further tool goes out for a reply nobody will hear.

Seen live 2026-11-24: a voice turn stopped by the user's next words ran 8 more seconds (``stream_chat`` waited for
the whole agent run when closed), still set a game goal and resumed the game, and held up the next turn."""

import asyncio
import contextlib
import time

from langchain_core.messages import AIMessageChunk

from app.ai import chat as ai_chat
from app.ai.context import AgentRuntimeContext


def test_closing_the_stream_cancels_the_run_and_its_pending_tool(monkeypatch):
    ran: list[str] = []

    class Agent:
        async def astream(self, inputs, config, stream_mode):
            yield AIMessageChunk(content="On it"), {"langgraph_node": ai_chat.REPLY_NODE}
            await asyncio.sleep(5)  # a tool call still in flight
            ran.append("tool")
            yield AIMessageChunk(content=" later"), {"langgraph_node": ai_chat.REPLY_NODE}

    @contextlib.asynccontextmanager
    async def orchestrator(context, queue=None):
        yield Agent(), []

    monkeypatch.setattr(ai_chat, "_orchestrator_for", orchestrator)
    monkeypatch.setattr(ai_chat, "_build_inputs", lambda messages, context, specs: {})
    monkeypatch.setattr(ai_chat, "_run_config", lambda context: {})

    async def scenario():
        import uuid

        events = ai_chat.stream_chat(messages=[], context=AgentRuntimeContext(user_id=uuid.uuid4()))
        first = await events.__anext__()
        started = time.monotonic()
        await events.aclose()
        return first, time.monotonic() - started

    first, closing = asyncio.run(scenario())
    assert first == {"type": "token", "content": "On it"}
    assert closing < 1.0 and ran == []
