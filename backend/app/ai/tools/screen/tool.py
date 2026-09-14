"""The `look_at_screen` tool.

Returns the frame as an actual image block in the tool result, so the model
sees pixels rather than a description of them. langgraph passes a list of
content blocks through to `ToolMessage.content` untouched as long as every
block's ``type`` is in ``TOOL_MESSAGE_BLOCK_TYPES`` — ``image`` is, which is
what makes this work without a custom node.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid

from langchain_core.tools import BaseTool, tool

from app.services import image_store

logger = logging.getLogger(__name__)

# One look is a full screenshot in context (~1.3k tokens). A model that loops
# on it turns one question into five figures of tokens, so the tool stops
# handing over pixels after this many calls in a single turn and says why.
_MAX_LOOKS_PER_TURN = 3


def build_look_at_screen(user_id: uuid.UUID, image_id: str | None) -> BaseTool:
    """A `look_at_screen` tool bound to one user and their newest frame.

    Rebuilt every turn rather than cached, because ``image_id`` is whatever the
    client last uploaded — a cached tool would keep showing a stale screen.
    ``image_id`` is None when sharing is on but no frame has arrived yet, which
    the tool reports rather than failing.
    """
    looks = 0

    @tool(response_format="content")
    async def look_at_screen() -> list[dict] | str:
        """Look at what is currently on the user's screen.

        Use this when answering genuinely depends on seeing their screen — they
        refer to something with "this"/"here", ask about an error, a document,
        or a page in front of them, or ask what you can see. The frame is live,
        captured moments ago.

        Do not call it out of curiosity, to check your own earlier reading, or
        for questions that general knowledge already answers.
        """
        nonlocal looks
        if image_id is None:
            return (
                "Screen sharing is on, but no frame has arrived yet — it can "
                "take a moment after the user enables it. Say so and continue."
            )
        if looks >= _MAX_LOOKS_PER_TURN:
            return (
                f"Already looked {looks} times this turn. The screen has not "
                "changed since. Answer from what you saw, or ask the user."
            )

        try:
            data, media_type = await asyncio.to_thread(
                image_store.read_bytes, user_id, image_id
            )
        except image_store.ImageNotFoundError:
            # Expected: frames expire, and the id can outlive the file by a
            # few seconds. Not an error the model needs to reason about.
            return (
                "That screen frame has expired. A newer one should arrive "
                "shortly; tell the user you couldn't see it just now."
            )
        except OSError:
            logger.exception("Could not read screen frame %s", image_id)
            return "Couldn't read the screen frame. Tell the user it failed."

        looks += 1
        return [
            {"type": "text", "text": "The user's screen, captured moments ago:"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(data).decode(),
                },
            },
        ]

    return look_at_screen
