"""Compressing a screen frame into one line the orchestrator can afford.

A 1280px screenshot costs roughly 1,300 tokens. Carrying one in the awareness
block every turn would make an always-on eye unaffordable, so the eye works in
two tiers: this module turns a frame into ~40 tokens of text that rides along
free, and `app.ai.tools.screen` hands over the actual pixels only when the
agent decides it needs them.

The caption is written by a small model on purpose. Compressing an image to a
sentence is not a reasoning task, and the orchestrator's model is the wrong
place to pay for it.
"""
from __future__ import annotations

import base64
import logging

from langchain_core.messages import HumanMessage

from app.ai.agent import get_chat_model
from app.ai.messages import extract_text
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Written to be *useful to another model*, not to a person. Naming the app and
# the kind of content is what lets the orchestrator decide whether looking
# properly is worth a tool call; prose about layout and colour is not.
_CAPTION_PROMPT = """\
Describe this screenshot of the user's screen in one sentence, for another \
assistant deciding whether it needs to look at the image itself.

Say which application or site is in focus and what is actually on screen — the \
document, page, error, or task. Name concrete things (file names, headings, a \
visible error) when they are legible. If food or a menu is visible, say so \
explicitly.

No preamble, no markdown, no speculation about intent. One sentence, under 30 \
words. If the screen is empty or unreadable, say exactly that."""

# A caption is worth roughly nothing if it costs a paragraph.
_MAX_CAPTION_TOKENS = 120


async def describe_screen(data: bytes, media_type: str) -> str | None:
    """One-line description of a screen frame, or None if it couldn't be made.

    Never raises: a failed caption degrades the eye to "open, but I haven't
    looked yet", which is honest and still leaves `look_at_screen` working.
    """
    settings = get_settings()
    model = get_chat_model(settings.screen_caption_model).bind(
        max_tokens=_MAX_CAPTION_TOKENS
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": _CAPTION_PROMPT},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(data).decode(),
                },
            },
        ]
    )
    try:
        reply = await model.ainvoke([message])
    except Exception:
        logger.exception("Screen caption failed; continuing without one.")
        return None

    caption = " ".join(extract_text(reply.content).split())
    return caption or None
