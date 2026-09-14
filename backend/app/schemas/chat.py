"""Pydantic schemas for the chatbot.

History is stored server-side, so the client no longer sends the full
conversation. Over the WebSocket the client sends typed command frames — each
carries a ``type`` discriminator so a single socket can handle more than one
kind of request:

- ``run``   → generate a reply to a new user message.
- ``stop``  → interrupt the reply currently being generated.
- ``sense`` → report the client's senses (time zone, route, mic state), which
  feed the agent's awareness of the moment rather than requesting anything.

The REST endpoints expose stored conversations and their messages.
"""
import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ChatRunRequest(BaseModel):
    """Start generating a reply to a new user message."""

    type: Literal["run"]
    # None → start a new conversation; otherwise append to this one.
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    content: str = Field(min_length=1, max_length=8000)
    # Ids of images already uploaded via POST /api/images. Sending ids rather
    # than pixels keeps this frame small and means the agent hands the id
    # straight to the food-vision specialist. Capped so one turn can't attach
    # an unbounded number of photos.
    image_ids: list[str] = Field(default_factory=list, max_length=8)
    asset_ids: list[str] = Field(default_factory=list, max_length=4)


class ChatStopRequest(BaseModel):
    """Interrupt the reply currently streaming for this connection.

    Also the barge-in signal: when the user starts talking over the spoken
    reply, the client sends this to kill both generation and playback.
    """

    type: Literal["stop"]
    # The conversation whose in-flight reply should be stopped (advisory —
    # the server only ever has one active turn per socket).
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None


class VoiceStartRequest(BaseModel):
    """Open an utterance. Binary frames that follow are its audio.

    Audio rides the same socket as text so a spoken turn and an attached image
    reach the same agent in the same conversation. Sent as raw binary frames
    rather than base64 JSON: a voice loop is latency-critical and base64 costs
    33% on every frame.
    """

    type: Literal["voice_start"]
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    # Sample rate of the 16-bit mono PCM frames that follow.
    sample_rate: int = Field(default=16000, ge=8000, le=48000)


class VoiceEndRequest(BaseModel):
    """The utterance is complete: transcribe it and run the turn.

    Endpointing happens client-side (VAD), so this arriving means the user has
    genuinely stopped speaking, not that a fixed timer expired.
    """

    type: Literal["voice_end"]
    turn_id: uuid.UUID | None = None


class SenseUpdateRequest(BaseModel):
    """Report the client's own senses, so the agent has a sense of the moment.

    Sent on connect, whenever any field changes, and on a slow heartbeat — not
    per frame. Everything here is the browser describing itself, so it is
    *descriptive only*: it shapes what the agent knows, never what it is
    allowed to do.
    """

    type: Literal["sense"]
    # IANA zone, e.g. "Asia/Jakarta". The server cannot infer this, and without
    # it every time-relative statement the agent makes is wrong.
    timezone: str | None = Field(default=None, max_length=64)
    # Proprioception: where the user is in the app. Costs nothing and needs no
    # permission, unlike the other two senses.
    route: str | None = Field(default=None, max_length=120)
    activity: Literal["idle", "reading", "typing", "speaking"] = "idle"
    # Hearing.
    hearing_enabled: bool = False
    wake_armed: bool = False
    voice_state: (
        Literal["off", "idle", "listening", "thinking", "speaking"] | None
    ) = None
    # Eyes: whether the user is sharing their screen, plus the newest frame
    # they've uploaded (via POST /api/images?kind=screen). An id, not pixels —
    # the frame is already on disk, and this keeps the sense frame tiny enough
    # to send on every change. Re-validated server-side against the user's own
    # store, so a forged id degrades to "no frame".
    vision_enabled: bool = False
    screen_image_id: str | None = Field(default=None, max_length=64)


class NudgeFeedbackRequest(BaseModel):
    """The user's response to the agent having spoken first.

    Only "dismiss" for now, and it is deliberately blunt: it mutes unprompted
    messages for the rest of the session rather than counting toward a streak.
    Silence is ambiguous; being told to stop is not.
    """

    type: Literal["nudge_feedback"]
    action: Literal["dismiss"] = "dismiss"


class VoiceCancelRequest(BaseModel):
    """Discard the open utterance without running a turn.

    Sent when the listening window closes with no speech in it, or the user
    dismisses the mic.
    """

    type: Literal["voice_cancel"]


# A frame off the chat WebSocket is one of the command types above, chosen by
# its ``type`` field. `chat_ws_message_adapter` validates raw JSON into the
# right model (or raises ValidationError). Binary frames are not JSON and are
# handled before this adapter ever sees them.
ChatWSMessage = Annotated[
    ChatRunRequest
    | ChatStopRequest
    | NudgeFeedbackRequest
    | SenseUpdateRequest
    | VoiceStartRequest
    | VoiceEndRequest
    | VoiceCancelRequest,
    Field(discriminator="type"),
]
chat_ws_message_adapter: TypeAdapter[ChatWSMessage] = TypeAdapter(ChatWSMessage)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    turn_id: uuid.UUID | None = None
    role: Literal["user", "assistant"]
    content: str
    status: Literal["completed", "partial", "cancelled", "failed"] = "completed"
    created_at: datetime
    # Photos attached to this turn, for rendering thumbnails in the transcript.
    image_ids: list[str] | None = None
    asset_ids: list[str] | None = None


class ConversationOut(BaseModel):
    """A conversation without its messages (for the conversation list)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    """A conversation with its full ordered message history."""

    messages: list[ChatMessageOut]
