"""A committed barge-in tears the old reply down in the background, and the
utterance that ends right after tears it down again before its own turn.
Seen live (2026-10-10): the background teardown cancelled the reply first, the
second wait got the CancelledError through its shield, and it escaped into the
socket's receive loop, which closed the voice connection mid-utterance."""
from __future__ import annotations

import asyncio
import uuid

from app.api.routes import chat as chat_route


def _slow_turn() -> chat_route._ActiveTurn:
    async def ignores_stop():
        await asyncio.sleep(30)  # a provider between yields: never sees `stop`

    task = asyncio.create_task(ignores_stop())
    return chat_route._ActiveTurn(task, asyncio.Event(), None, uuid.uuid4())


def test_a_second_teardown_survives_the_first_one_cancelling_the_turn() -> None:
    async def scenario():
        active = _slow_turn()
        background = asyncio.create_task(chat_route._await_teardown(active))  # the commit
        await asyncio.sleep(1.6)
        await chat_route._await_teardown(active)  # voice_end: must return, not raise
        await background
        return active.task.cancelled()

    assert asyncio.run(scenario()) is True


def test_cancelling_the_waiter_itself_still_cancels_it() -> None:
    async def scenario():
        active = _slow_turn()
        waiter = asyncio.create_task(chat_route._await_teardown(active))
        await asyncio.sleep(0.2)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            return "cancelled"
        finally:
            active.task.cancel()
        return "returned"

    assert asyncio.run(scenario()) == "cancelled"
