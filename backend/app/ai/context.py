"""Runtime context threaded into every chatbot turn.

This is *our* context object (not LangGraph's `context_schema`) so it stays
decoupled from framework version churn. It carries everything the agent — and,
once we add them, the agent's tools — need to act on behalf of a specific user.

As integrations land, add the fields/handles their tools need here and thread
them through — the context is what turns a generic agent into *this user's*
agent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class AgentRuntimeContext:
    user_id: uuid.UUID

    # Transport that originated this turn. Generation remains channel-neutral,
    # but prompts and future capability gates can account for surfaces that do
    # not support the web client's rich UI, streaming, screen sense, or voice.
    channel: str = "web"

    # Populate from the authenticated user when available; tools use these to
    # personalize responses or fetch per-user data.
    user_name: str | None = None

    # --- Integrations ---
    # A live Google access token when the user has connected their account,
    # else None. Resolved per turn (tokens expire hourly) by
    # `app.ai.integrations.google.get_access_token`, and handed to the Gmail
    # MCP server for the duration of the turn. None ⇒ no Gmail tools.
    gmail_access_token: str | None = None
    drive_access_token: str | None = None

    # Whether the MacroVision tools are available this turn. Unlike Gmail this
    # needs no per-user connection — it's our own model — so it tracks the
    # `macrovision_tools_enabled` setting rather than anything about the user.
    macrovision_enabled: bool = False

    # Images the user attached to *this* message, newest last, as ids in the
    # per-user image store. The agent never sees the pixels: it passes an id to
    # the food-vision sub-agent, which runs the model on it.
    image_ids: tuple[str, ...] = ()

    # User-owned Blender projects/meshes attached to this turn.
    asset_ids: tuple[str, ...] = ()
    blender_enabled: bool = False

    # True when this turn arrived as speech and the reply will be spoken aloud.
    # Changes both the model (a faster one — the user is waiting to *hear* the
    # answer) and the system prompt (spoken replies have to be much shorter).
    voice: bool = False

    # The agent's sense of the current moment: local time, how long the user
    # has been here, what they've logged today, which senses are switched on.
    # Rendered to plain text by `app.services.awareness` before it gets here —
    # the AI layer takes facts as strings, the same way it takes history as
    # role/content dicts, so it never has to know about ORM rows or app state.
    # None ⇒ no awareness this turn (nothing reported it), and the prompt
    # simply omits the section rather than asserting a default.
    awareness: str | None = None

    # Small, already-ranked set of durable user memories relevant to this
    # turn. Rendered by the service so the AI layer remains storage-agnostic.
    long_term_memory: str | None = None

    # --- Eyes ---
    # True when the user is sharing their screen this turn. Gates both the
    # `look_at_screen` tool and the prompt section describing it: a user with
    # sharing off pays no context for a sense that isn't there.
    vision_enabled: bool = False
    # The newest screen frame's id in the user's image store, already validated.
    # None while sharing is on but no frame has arrived yet.
    screen_image_id: str | None = None

    # --- Speaking first ---
    # Set when this turn is an *unprompted* message the agent is initiating,
    # rather than a reply. Carries the directive from the salience rule that
    # fired (see `app.services.salience`). The user did not just say anything,
    # which changes what a good message looks like — see `_NUDGE_PROMPT`.
    nudge: str | None = None

    # Force a specific model for this turn regardless of the usual rules.
    # Nudges use it to stay on a cheap model: they're one sentence, and the
    # user didn't ask for them, so they must never cost orchestrator money.
    model_override: str | None = None

    # e.g. locale: str | None = None
    # e.g. timezone: str | None = None

    @property
    def gmail_connected(self) -> bool:
        return self.gmail_access_token is not None

    @property
    def has_images(self) -> bool:
        return bool(self.image_ids)

    @property
    def has_assets(self) -> bool:
        return bool(self.asset_ids)

    def capabilities(self) -> set[str]:
        """Names of the capability flags that are live for this turn.

        Matched against each sub-agent's ``requires`` so the registry decides
        what's offered, rather than ``chat.py`` growing an ``if`` per toolset.
        """
        flags = set()
        if self.drive_access_token:
            flags.add("drive_connected")
        if self.gmail_connected:
            flags.add("gmail_connected")
        if self.macrovision_enabled:
            flags.add("macrovision_enabled")
        if self.blender_enabled:
            flags.add("blender_enabled")
        return flags
