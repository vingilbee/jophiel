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
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GameEvent:
    """Why a background turn woke her (desktop game events), and how chatty
    the session lets her be. Set only on background turns."""

    reasons: tuple[str, ...]
    commentary_mode: str = "occasional"
    keep_playing: bool = True
    described: tuple[str, ...] = field(default=())
    # The plan she woke up to: if the user changes it while she thinks, her plan changes are refused.
    plan_revision: int | None = None


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

    # Files (documents, Blender projects/meshes) attached to this turn.
    asset_ids: tuple[str, ...] = ()
    # The readable files the user has (newest first, ``(asset_id, filename)``): she gets ``read_attachment``
    # when there's any (services/attachment_text.py).
    files: tuple[tuple[str, str], ...] = ()
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
    # Who she is: the text personality (services/personality.current().text), loaded once per process.
    personality: str | None = None
    # Her working memory (services/memory_manager.py; None when empty): what we're
    # doing, in which context, and what's unfinished, rendered for this turn. ``memory_tools``: she gets
    # the tools that change it. ``source_message_id``: the user's message this turn answers, the evidence
    # for what those tools keep (None for turns without one).
    working_memory: str | None = None
    memory_tools: bool = False
    # Shared recall (services/recall_references.py; None when nothing was searched): the rendered result of this
    # reply's search across every context, which replaces the long-term block in the prompt, and the turn
    # object her read tools add to (its receipt is saved with the reply).
    recall: str | None = None
    recall_turn: object | None = None
    # This evaluation of her mind (services/mind_turn.py). ``cached_memory``: the memories from the last answer,
    # re-read, when no search ran or it failed (replaces ``recall`` in the prompt). ``turn_boundary``: what this
    # evaluation had from memory. ``mind_state``: thinking she started that's still under way and what she
    # scheduled to come back to.
    # ``mind``: the evaluation's record: her mind's tools are built from it and write what she did into it
    # (None: a turn that isn't an evaluation of her mind, e.g. a nudge). ``wake``: why she was woken when it
    # wasn't a user message (rendered by ``coordinator.render_wake``); her reply is then what she says, if anything.
    cached_memory: str | None = None
    turn_boundary: str | None = None
    mind_state: str | None = None
    mind: Any = None
    wake: str | None = None
    # Woken by someone else's words (a phone notification), or by what such a wake started or scheduled: no device
    # action at all this turn (app/ai/chat.py; docs/PHONE_PHASE_8_AFFINITY.md O1).
    third_party: bool = False
    # A wake to look at the phone again after a turn of hers used its screen (services/phone_look.py): the user's
    # message it follows. Her hands on the screen on this wake, for what that message asked (None: not such a wake).
    phone_look: uuid.UUID | None = None
    # The clock and what caused this evaluation (``mind_turn.situation``): read fresh when the evaluation runs,
    # never from a queued job's awareness snapshot.
    now: str | None = None
    source_message_id: uuid.UUID | None = None

    # --- Eyes ---
    # True when the user is sharing their screen this turn. Gates both the
    # `look_at_screen` tool and the prompt section describing it: a user with
    # sharing off pays no context for a sense that isn't there.
    vision_enabled: bool = False
    # The newest screen frame's id in the user's image store, already validated.
    # None while sharing is on but no frame has arrived yet.
    screen_image_id: str | None = None

    # --- The game (desktop agent) ---
    # The game summary for this turn, rendered by `app.services.desktop_game`
    # (trusted session state plus untrusted driver observations, labelled).
    # None when the user has no paired game VM: then no section and no tools.
    game: str | None = None
    # One line for her sense of the moment while a game is on ("Right now you
    # are playing Minecraft..."), added to the awareness facts.
    game_activity: str | None = None
    # A key-pressing game VM is paired: its rules (preview, lag, counted moves) apply.
    game_vm: bool = False
    # Her planner writes the game plan (services/game_planning): replies set goals and one-step
    # commands, background turns don't plan.
    game_planner: bool = False
    # The game as the summary saw it (``app.services.game_moment.Moment``): her game
    # tools tell her what changed since, and a reply it outdates gets corrected.
    game_moment: Any = None
    # Not playing: a short summary, no playing rules, and only the tool that starts a game.
    game_idle: bool = False
    # A background turn about the game (no user message): her game tools
    # become the background set (with ``say``) and the prompt says why she woke.
    game_event: GameEvent | None = None
    # When the user started speaking (or sent a typed message), Unix seconds:
    # the moment they were reacting to. None for turns without a user message.
    spoke_at: float | None = None
    # A small picture of her game at ``spoke_at`` (JPEG), attached to the
    # user's message so she sees what they saw without a tool round trip.
    game_picture: bytes | None = None
    game_picture_note: str | None = None

    # --- Her own desktop (general desktop control) ---
    # One line about her paired Windows desktop (connected? can she see and use it?), from
    # ``services/desktop_control.access_for``. None without one: then no section and no desktop tools.
    desktop: str | None = None
    # Whether she can use it now (``DesktopAccess.usable``). Not usable: the line alone, no tools.
    desktop_usable: bool = True
    # Whether its controller can run commands (0.13.0+): then ``run_command`` is among her desktop tools.
    desktop_commands: bool = False
    # The turn and conversation her desktop actions belong to (their experience records link to them).
    turn_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    # Her desktop's security records (services/security_activity.line_for): one line about monitoring, or None
    # without a paired desktop (then no section and no ``get_security_activity``). ``zone``: the user's IANA time
    # zone when the client reported one, for showing record times in their time.
    security: str | None = None
    # Their phone's senses as one line (services/phone_notifications.prompt_line): None when they have no phone.
    phone: str | None = None
    # What her hands did on their phone lately, in the turns she finished herself (services/phone_control
    # .recent_steps): one line per action, with how long ago and the app. None when there's nothing.
    phone_recent: str | None = None
    # What she knows of the places she used on their phone in the last hour (services/place_memory.for_turn).
    places: str | None = None
    zone: str | None = None
    # Phase 9: this reply turn's use of the phone screen (app/ai/tools/phone_control.PhoneControl), set when her
    # phone-control tools are built; finished when the turn ends.
    phone_control: Any = None

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
