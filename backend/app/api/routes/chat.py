"""Chatbot routes: conversation history (REST) + a streaming reply (WebSocket).

History is stored server-side. The client sends only the new user message plus
the conversation it belongs to; the backend appends it, reconstructs the full
history from the DB, streams the assistant's reply, and persists that too.

WebSocket protocol
------------------
Connect to ``/api/chat/ws``. Authentication uses the session cookie, sent by the
browser on the handshake; an unauthenticated connection is rejected before
``accept``. The connection is persistent and handles many turns. Sessions are
revalidated before incoming frames and during idle waits; revocation closes
the socket with code 1008 and cancels its active reply.

Text *and* voice ride this one socket, deliberately: a spoken turn, a typed
turn, and an attached image all reach the same agent in the same conversation,
with one auth handshake and one reconnect story.

Client -> server. JSON frames (discriminated by ``type``)::

  {"type": "run",  "conversation_id": str|null, "content": str,
   "image_ids": [str]}                                           → generate a reply
  {"type": "stop", "conversation_id": str|null}                  → interrupt / barge-in
  {"type": "sense", "timezone": str, "route": str, ...}          → report senses
  {"type": "nudge_feedback", "action": "dismiss"}                → stop initiating
  {"type": "voice_start", "conversation_id": str|null, "turn_id": str, "sample_rate": int}
  {"type": "voice_interrupt", "cancel_turn_id": str|null, "new_turn_id": str,
   "conversation_id": str|null, "sample_rate": int,
   "tentative": bool}                                            → barge-in: stop the
                                                                   reply (or just hold it),
                                                                   open an utterance
  {"type": "voice_interrupt_commit", "turn_id": str}            → the hold is a real turn
  {"type": "voice_heard", "turn_id": str, "heard_chars": int}   → how much of a cut-off
                                                                   reply was actually heard
  {"type": "voice_end", "turn_id": str}                         → transcribe + reply
  {"type": "voice_cancel"}                                       → discard utterance
  {"type": "voice_latency", "turn_id": str, "outcome": str, "marks": {...}}
                                                                 → browser timing marks

Binary frames are raw 16-bit mono PCM at the ``sample_rate`` declared by the
preceding ``voice_start``, and are ignored unless an utterance is open.

Server -> client. JSON frames::

  {"type": "meta", "conversation_id": str}      (once, first)
  {"type": "state", "awareness": {...}}         (on connect, on sense updates,
                                                 and on a slow heartbeat)
  {"type": "nudge_start", "conversation_id": str, "reason": str}
  {"type": "nudge_token", "content": str}       (repeated, streamed)
  {"type": "nudge_end"}

``nudge_*`` frames are the agent speaking *first* — unprompted, from a salience
rule (see `app.services.salience`), with no client request behind them. They
are kept distinct from ``token`` so the client's request/response bookkeeping
never mistakes one for a reply it asked for. Audio frames may accompany them
exactly as for a spoken turn.
  {"type": "transcript", "content": str}        (voice turns: what we heard)
  {"type": "token", "turn_id": str, "message_id": str, "content": str}
                                                   (repeated, streamed)
  {"type": "sticker", "turn_id": str, "message_id": str, "sticker": str}
                                                   (assistant sticker)
  {"type": "audio_start", "sample_rate": int}   (voice turns, before any audio)
  {"type": "audio_segment", "turn_id": str, "segment": int,
   "text_start": int, "text_end": int}          (the audio that follows speaks
                                                 this span of the reply text)
  {"type": "barge_resolved", "turn_id": str,
   "action": "resume"|"stop"|"turn"}            (a held interruption was decided)
  {"type": "audio_end"}
  {"type": "done"} | {"type": "stopped"} | {"type": "error", "detail": str}

plus binary frames between ``audio_start`` and ``audio_end``: 16-bit mono PCM
to be queued for playback.

``conversation_id`` is null (or omitted) to start a new conversation; the server
returns the resolved id in the ``meta`` frame. A ``stop`` frame interrupts the
in-flight reply; partial text generated so far is persisted so it stays part of
the history.

REST endpoints (auth'd via `get_current_user`) let the frontend load prior
conversations and their messages, and delete conversations.
"""
from pydantic_settings import SettingsConfigDict
import asyncio
import base64
import contextlib
import dataclasses
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import AgentRuntimeContext, prompts, stream_chat, vision
from app.ai.Cognitive.system.v0.live_shadow.models import GenerationPath, ShadowSourceChannel
from app.ai.integrations import google
from app.ai.integrations.google.scopes import DRIVE, GMAIL_MODIFY
from app.api.deps import get_current_user, validate_origin
from app.core.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal, get_db
from app.schemas.chat import (
    ChatRunRequest,
    ChatStopRequest,
    ConversationDetailOut,
    ConversationOut,
    NudgeFeedbackRequest,
    SenseUpdateRequest,
    VoiceCancelRequest,
    VoiceEndRequest,
    VoiceHeardReport,
    VoiceInterruptCommitRequest,
    VoiceInterruptRequest,
    VoiceLatencyReport,
    VoiceStartRequest,
    chat_ws_message_adapter,
)
from app.services import asset_store, google_account, image_store, salience
from app.services import auth as auth_service
from app.services import awareness as awareness_service
from app.services import chat as chat_service
from app.services import cognitive_shadow
from app.services import dialogue_memory
from app.services import long_term_memory as memory_service
from app.services import stickers
from app.services import stt as stt_service
from app.services import tts as tts_service
from app.services import voice_latency
from app.workers import chat as chat_worker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

def _nudge_frame(frame: dict) -> dict:
    if frame["type"] == "token":
        return {"type": "nudge_token", "content": frame["content"]}
    return {"type": "nudge_sticker", "sticker": frame["sticker"]}


@router.get("/stickers/{name}.png", include_in_schema=False)
def get_sticker(name: str) -> FileResponse:
    """Serve one approved, read-only Columbina sticker to the chat client."""
    if name not in stickers.STICKER_NAMES:
        raise HTTPException(status_code=404, detail="Sticker not found")
    sticker_path = Path(__file__).resolve().parents[2] / "assets" / "columbina_stickers" / f"{name}.png"
    if not sticker_path.is_file():
        raise HTTPException(status_code=404, detail="Sticker not found")
    return FileResponse(sticker_path, media_type="image/png")


@dataclasses.dataclass
class _ActiveTurn:
    """A generation task is user-scoped, not WebSocket-scoped."""

    task: asyncio.Task
    stop: asyncio.Event
    conversation_id: uuid.UUID | None
    turn_id: uuid.UUID


# Strong references let generation survive a transport disconnect and let a
# replacement socket interrupt the exact turn that is still running.
_active_turns: dict[uuid.UUID, _ActiveTurn] = {}


def _fence_active_turn(user_id: uuid.UUID) -> _ActiveTurn | None:
    """Signal the active turn to stop, without waiting for it to finish.

    Synchronous on purpose: from here on its audio is dropped by the relay and
    the worker stops synthesizing, so a barge-in can open the new utterance
    straight away and leave the teardown to `_await_teardown`.
    """
    active = _active_turns.get(user_id)
    if active is None or active.task.done():
        return None
    if not active.stop.is_set():  # already fenced by a barge-in
        chat_worker.request_cancel(active.turn_id)
        active.stop.set()
    return active


async def _stop_active_turn(user_id: uuid.UUID) -> None:
    active = _fence_active_turn(user_id)
    if active is not None:
        await _await_teardown(active)


async def _await_teardown(active: _ActiveTurn) -> None:
    # Cooperative stopping lets run_turn persist its partial assistant reply
    # before a follow-up reconstructs conversation history.
    try:
        await asyncio.wait_for(asyncio.shield(active.task), timeout=2.0)
    except TimeoutError:
        # A provider/tool may be between yields and unable to observe `stop`.
        # Bound barge-in latency, while run_turn's cancellation handler still
        # takes the normal partial-reply persistence path.
        active.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await active.task


def _latest_conversation_id(user_id: uuid.UUID) -> uuid.UUID | None:
    """This user's most recently active thread, or None if they have none.

    Used to place an unprompted message when the socket hasn't seen a turn yet
    — e.g. the user reloaded the page and hasn't typed since.
    """
    db = SessionLocal()
    try:
        conversations = chat_service.list_conversations(db, user_id=user_id)
        return conversations[0].id if conversations else None
    finally:
        db.close()


def _active_jobs_for_user(user_id: uuid.UUID):
    db = SessionLocal()
    try:
        try:
            return chat_service.active_jobs(db, user_id=user_id)
        except Exception:
            # Compatibility with lightweight route test doubles and, more
            # importantly, keep reconnect bootstrap non-fatal if the job table
            # is temporarily unavailable. The submitted job is still durable.
            logger.exception("Could not load active chat jobs")
            return []
    finally:
        db.close()

# A client that opens an utterance and never closes it would otherwise buffer
# without bound. 60s at 16kHz/16-bit is ~1.9MB; well past any real utterance.
MAX_UTTERANCE_BYTES = 16000 * 2 * 60

# An interruption that says only this was a request to stop talking, not a
# question. Matched on the whole transcript after normalizing case/punctuation.
# Deliberately word lists, not a model: instant, readable, and a miss is mild —
# an unlisted phrase is treated as a real turn, which is the pre-hold behavior.
_STOP_PHRASES = frozenset({
    "stop", "wait", "hold on", "hang on", "pause", "shh", "shush", "quiet",
    "be quiet", "enough", "okay stop", "ok stop", "stop stop", "wait wait",
    "no stop", "stop please", "please stop", "one second", "one sec",
    "just a second", "just a sec", "hold on a second", "hold on a sec",
    "stop it", "stop talking", "hold up", "wait a second", "wait a sec", "wait a minute",
})


# Listening noises said over the assistant: she should carry on, not answer.
# "thank you" is here too because it is also STT's favorite hallucination on
# near-silent audio, and resuming is the right outcome either way.
_BACKCHANNEL_PHRASES = frozenset({
    "i see", "oh i see", "ah i see", "mm", "mmm", "mm hm", "mm hmm", "mhm", "mhmm",
    "hmm", "uh huh", "uh hum", "okay", "ok", "oh okay", "oh ok", "ah okay", "okay okay",
    "right", "right right", "yeah", "yeah yeah", "yes", "yep", "yup", "yea", "ya",
    "got it", "gotcha", "sure", "cool", "nice", "alright", "all right", "oh", "ah",
    "makes sense", "that makes sense", "interesting", "true", "exactly", "i know",
    "wow", "great", "oh nice", "oh cool", "thank you", "thanks", "uh", "um",
    "i get it", "i understand", "understood", "fair", "fair enough",
    "nice nice", "oh wow", "of course", "totally", "definitely", "for sure",
})


# Stretched filler sounds STT spells however it likes: "hmmm", "mmmm",
# "ohhh", "ahh", "umm", "mhmm". Matched per word, so "hmmm i see" is fillers.
_FILLER_WORD = re.compile(r"^(h?m+|h+m+|m+h+m+|o+h+|a+h+|u+m+|u+h+|e+r+m*)$")


def _normalize_phrase(transcript: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", transcript.lower()).split())


def _segments(words: list[str], phrases: frozenset[str]) -> list[set[str]] | None:
    """Split ``words`` into known phrases (or filler words), if possible.

    Returns, for one valid split, which list each piece came from; ``None``
    when some part of the utterance is not a known phrase at all. Phrases are
    at most four words, so this is a tiny dynamic program per utterance.
    """
    n = len(words)
    best: list[list[str] | None] = [None] * (n + 1)
    best[0] = []
    for end in range(1, n + 1):
        for start in range(max(0, end - 4), end):
            if best[start] is None:
                continue
            piece = " ".join(words[start:end])
            if piece in phrases or (end - start == 1 and _FILLER_WORD.match(piece)):
                best[end] = [*best[start], piece]
                break
    if best[n] is None:
        return None
    return [{"stop"} if piece in _STOP_PHRASES else {"backchannel"} for piece in best[n]]


def _is_stop_only(transcript: str) -> bool:
    return _normalize_phrase(transcript) in _STOP_PHRASES


def _classify_hold(transcript: str) -> str:
    """What a held interruption meant: ``resume``, ``stop``, or ``turn``.

    Made only of listening noises in any combination ("hmm, I see", "oh okay
    got it") resumes; any stop phrase among them ("hmm, wait") stops; a word
    outside both lists makes it a real turn. Nothing intelligible (her own
    echo, a cough) resumes too: holding is what removed the echo, so a false
    barge-in costs a short pause, not the reply.
    """
    words = _normalize_phrase(transcript).split()
    if not words:
        return "resume"
    pieces = _segments(words, _BACKCHANNEL_PHRASES | _STOP_PHRASES)
    if pieces is None:
        return "turn"
    return "stop" if any("stop" in kinds for kinds in pieces) else "resume"


# Worker events that carry reply audio; dropped once their turn is stopped.
_AUDIO_EVENT_TYPES = frozenset({"audio_start", "audio_segment", "audio_chunk", "audio_end"})

# A heard report usually arrives before the interrupted reply is saved (the
# worker persists it on cancellation). Retry this long for the row to appear.
_HEARD_RETRY_SECONDS = 15.0
_HEARD_RETRY_INTERVAL = 0.5

# How often the server re-derives and pushes awareness on its own. The clock
# and "how long since you last spoke" drift without anyone touching anything,
# so the UI needs a pulse rather than only change-driven updates. Slow on
# purpose: it costs three small queries, and nothing here changes per second.
AWARENESS_HEARTBEAT_SECONDS = 30

# Check idle sockets too, including while a detached reply is streaming.
SESSION_CHECK_SECONDS = 1.0


# --------------------------------------------------------------------------- #
# Conversation history (REST)
# --------------------------------------------------------------------------- #
@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return chat_service.list_conversations(db, user_id=current_user.id)


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationDetailOut
)
def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = chat_service.get_conversation(
        db, user_id=current_user.id, conversation_id=conversation_id
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_origin)]
)
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = chat_service.delete_conversation(
        db, user_id=current_user.id, conversation_id=conversation_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )


# --------------------------------------------------------------------------- #
# Streaming reply (WebSocket)
# --------------------------------------------------------------------------- #
@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    settings = get_settings()
    token = websocket.cookies.get(settings.session_cookie_name)
    origin = websocket.headers.get("origin")

    if origin not in settings.cors_origins:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return


    # Resolve the session cookie to a user before accepting the socket.
    user_id: uuid.UUID | None = None
    user_name: str | None = None
    db = SessionLocal()
    try:
        user = auth_service.get_user_for_token(db, token) if token else None
        if user is not None:
            user_id, user_name = user.id, user.name
    finally:
        db.close()

    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    connected = True
    session_revoked = False

    def session_is_valid() -> bool:
        db = SessionLocal()
        try:
            user = auth_service.get_user_for_token(db, token)
            return user is not None and user.id == user_id
        finally:
            db.close()

    async def check_session() -> bool:
        nonlocal session_revoked, connected
        try:
            valid = await asyncio.to_thread(session_is_valid)
        except Exception:
            # Fail closed if the session store cannot confirm authentication.
            logger.exception("WebSocket session validation failed")
            valid = False
        if not valid:
            session_revoked = True
            connected = False
        return valid

    # Text frames and audio frames are produced by different tasks; Starlette
    # makes no concurrency guarantee about sends, so serialize them.
    send_lock = asyncio.Lock()

    async def send_json(payload: dict) -> None:
        """Best-effort delivery; generation is allowed to outlive this socket."""
        nonlocal connected
        if not connected:
            return
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            connected = False

    async def send_bytes(payload: bytes) -> None:
        nonlocal connected
        if not connected:
            return
        try:
            async with send_lock:
                await websocket.send_bytes(payload)
        except (WebSocketDisconnect, RuntimeError):
            connected = False

    def read_history() -> dict:
        db = SessionLocal()
        try:
            conversations = chat_service.list_conversations(db, user_id=user_id)
            conversation = conversations[0] if conversations else None
            detail = (
                chat_service.get_conversation(
                    db, user_id=user_id, conversation_id=conversation.id
                )
                if conversation is not None
                else None
            )
            return {
                "type": "history",
                "conversation_id": str(conversation.id) if conversation else None,
                "messages": [
                    {
                        "id": str(message.id),
                        "turn_id": str(message.turn_id) if message.turn_id else None,
                        "role": message.role,
                        "content": message.content,
                        "status": message.status,
                        "source_channel": message.source_channel,
                        "created_at": message.created_at.isoformat(),
                        "image_ids": message.image_ids,
                        "asset_ids": message.asset_ids,
                    }
                    for message in (detail.messages if detail else [])
                ],
            }
        finally:
            db.close()

    # This connection's sense of itself: when it opened, and whatever the
    # client has told us about its time zone, route, and microphone. Lives for
    # exactly as long as the socket — see services/awareness.py.
    session = awareness_service.AwarenessSession(user_id)

    # Send an authoritative snapshot on every new/reconnected socket. REST
    # remains available as a fallback if the snapshot query fails.
    try:
        await send_json(await asyncio.to_thread(read_history))
    except Exception:
        logger.exception("WebSocket history bootstrap failed; using REST fallback")

    def read_awareness() -> awareness_service.AwarenessState:
        """Derive the current state. Blocking (three small queries)."""
        db = SessionLocal()
        try:
            return awareness_service.build(db, user_id=user_id, session=session)
        finally:
            db.close()

    async def push_state() -> None:
        """Send the client the state the agent would see right now.

        The UI animates from this, and it shows the user the exact narration
        the model is given — being able to see what it perceives is the point.
        """
        state = await asyncio.to_thread(read_awareness)
        await send_json({"type": "state", "awareness": state.to_dict()})

    async def _caption_screen(
        state: awareness_service.AwarenessState,
    ) -> awareness_service.AwarenessState:
        """Describe the current frame and fold the caption into ``state``.

        Returns the state unchanged on any failure: a missing caption costs the
        agent some ambient context, and `look_at_screen` still works, so this
        must never be able to fail a turn.
        """
        image_id = state.senses.screen_image_id
        if image_id is None:
            return state
        try:
            data, media_type = await asyncio.to_thread(
                image_store.read_bytes, user_id, image_id
            )
            caption = await vision.describe_screen(data, media_type)
        except Exception:
            return state
        session.remember_caption(image_id, caption)
        return dataclasses.replace(state, screen_caption=caption)

    async def heartbeat() -> None:
        """Keep awareness current, and decide whether to speak first.

        Salience is evaluated here rather than on its own timer because it
        needs exactly what was just derived, and because "check the clock" and
        "is anything worth saying?" are the same question asked twice.
        """
        while True:
            await asyncio.sleep(AWARENESS_HEARTBEAT_SECONDS)
            try:
                if not await check_session():
                    return
                state = await asyncio.to_thread(read_awareness)
                await send_json({"type": "state", "awareness": state.to_dict()})
                await consider_nudging(state)
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception:
                # A failed heartbeat must never take the conversation with it.
                logger.exception("Awareness heartbeat failed; continuing.")
                continue

    async def consider_nudging(state: awareness_service.AwarenessState) -> None:
        """Speak first, if a rule says it's worth it and nothing is in flight."""
        if current_task is not None and not current_task.done():
            return
        memory = salience.memory_for(user_id)
        nudge = salience.evaluate(state, memory)
        if nudge is None:
            return
        # Only into a thread that already exists: an unprompted message has to
        # land somewhere the user will see it in context, and opening a brand
        # new conversation to say "you haven't logged lunch" is worse than
        # saying nothing.
        conversation_id = last_conversation_id or await asyncio.to_thread(
            _latest_conversation_id, user_id
        )
        if conversation_id is None:
            return
        memory.note_sent(nudge, datetime.now(timezone.utc))
        await run_nudge(conversation_id, nudge, state)

    async def speak(
        clauses: asyncio.Queue[str | None],
        stop_event: asyncio.Event,
        audio_already_started: bool = False,
    ) -> None:
        """Synthesize queued clauses and stream the PCM out.

        Runs alongside generation so synthesis of clause N overlaps generation
        of clause N+1. A filler goes out first to cover the ~1.9s fixed onset
        of the first real chunk; because RTF is ~0.63, real audio is ready
        before the filler finishes and stays ahead from there.
        """
        started = audio_already_started
        speech_started_at = time.perf_counter()
        first_tts_chunk = True
        try:
            # Acknowledge immediately while the model is still producing its
            # first clause. Previously this lived below clauses.get(), which
            # meant it could not hide model/endpointing latency at all.
            filler = None if started else tts_service.pick_filler()
            if filler is not None and filler[1] == settings.tts_sample_rate:
                await send_json(
                    {"type": "audio_start", "sample_rate": settings.tts_sample_rate}
                )
                started = True
                await send_bytes(filler[0])
                logger.info(
                    "voice latency: filler_audio=%.3fs",
                    time.perf_counter() - speech_started_at,
                )
            while True:
                clause = await clauses.get()
                if clause is None or stop_event.is_set():
                    break
                if not started:
                    await send_json(
                        {"type": "audio_start", "sample_rate": settings.tts_sample_rate}
                    )
                    started = True
                async for pcm in tts_service.synthesize(clause):
                    if stop_event.is_set():
                        break
                    await send_bytes(pcm)
                    if first_tts_chunk:
                        first_tts_chunk = False
                        logger.info(
                            "voice latency: first_tts_audio=%.3fs first_clause_chars=%d",
                            time.perf_counter() - speech_started_at,
                            len(clause),
                        )
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            if started:
                with contextlib.suppress(Exception):
                    await send_json({"type": "audio_end"})

    async def run_nudge(
        conversation_id: uuid.UUID,
        nudge: salience.Nudge,
        state: awareness_service.AwarenessState,
    ) -> None:
        """Generate and deliver one unprompted message.

        Deliberately *not* `run_turn`: there is no user message to persist, no
        `meta` frame (the conversation already exists), and the frames are
        distinct so the client can't mistake this for a reply it asked for.
        Spoken aloud only when a voice session is actually live — a silent app
        must never suddenly start talking.
        """
        speak_aloud = (
            settings.voice_enabled
            and state.senses.hearing_enabled
            and state.senses.voice_state == "idle"
        )

        db = SessionLocal()
        try:
            conversation = chat_service.get_conversation(
                db, user_id=user_id, conversation_id=conversation_id
            )
            if conversation is None:
                return
            history = chat_service.get_history(db, conversation=conversation)
        finally:
            db.close()
        if not history:
            return
        # Close history with a synthetic user turn so the model starts a new
        # proactive message. Never persisted — only the reply is.
        history = [*history, {"role": "user", "content": prompts.NUDGE_TRIGGER}]

        context = AgentRuntimeContext(
            user_id=user_id,
            user_name=user_name,
            macrovision_enabled=settings.macrovision_tools_enabled,
            voice=speak_aloud,
            awareness=state.describe(),
            vision_enabled=state.senses.vision_enabled,
            screen_image_id=state.senses.screen_image_id,
            nudge=nudge.directive,
            model_override=settings.nudge_model,
        )

        await send_json(
            {
                "type": "nudge_start",
                "conversation_id": str(conversation_id),
                "reason": nudge.reason,
            }
        )

        stop_event = asyncio.Event()
        clauses: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = (
            asyncio.create_task(speak(clauses, stop_event)) if speak_aloud else None
        )
        feed, flush = tts_service.clause_chunker()

        reply = ""
        sticker_buffer = ""
        try:
            async for event in stream_chat(messages=history, context=context):
                if event["type"] == "token":
                    text = event["content"]
                    reply += text
                    frames, sticker_buffer = stickers.consume_text(sticker_buffer, text)
                    for frame in frames:
                        await send_json(_nudge_frame(frame))
                    if speaker is not None:
                        visible = stickers.spoken_text(frames)
                        for clause in feed(visible):
                            clauses.put_nowait(clause)
                elif event["type"] == "subagent_start":
                    await send_json({"type": "nudge_subagent_start", "agent": event["agent"]})
                elif event["type"] == "subagent_token":
                    await send_json(
                        {"type": "nudge_subagent_token", "agent": event["agent"], "content": event["content"]}
                    )
                elif event["type"] == "subagent_end":
                    await send_json({"type": "nudge_subagent_end", "agent": event["agent"]})
            frames, sticker_buffer = stickers.consume_text(sticker_buffer, "", final=True)
            for frame in frames:
                await send_json(_nudge_frame(frame))
            if speaker is not None:
                for clause in flush():
                    clauses.put_nowait(clause)
        except Exception:
            logger.exception("Nudge generation failed.")

        if reply:
            db = SessionLocal()
            scheduled = False
            try:
                conversation = chat_service.get_conversation(
                    db, user_id=user_id, conversation_id=conversation_id
                )
                if conversation is not None:
                    nudge_message = chat_service.add_message(
                        db, conversation=conversation, role="assistant", content=reply, commit=False,
                    )
                    # An unprompted message is still a delivered dialogue event.
                    scheduled = dialogue_memory.schedule_update(
                        db, user_id=user_id, conversation_id=conversation_id,
                        trigger_message_id=nudge_message.id,
                    )
                    db.commit()
            finally:
                db.close()
            if scheduled:
                dialogue_memory.wake_worker()

        await send_json({"type": "nudge_end"})

    async def run_turn(
        conversation_id: uuid.UUID | None,
        content: str,
        stop_event: asyncio.Event,
        voice: bool,
        turn_id: uuid.UUID | None = None,
        emit: object | None = None,
        image_ids: list[str] | None = None,
        asset_ids: list[str] | None = None,
        audio_already_started: bool = False,
    ) -> None:
        """Stream one reply. Persist the (possibly partial, if stopped) result."""
        turn_started_at = time.perf_counter()
        nonlocal last_conversation_id
        turn_id = turn_id or uuid.uuid4()
        emit_event = emit or send_json
        # The user is talking, so anything we said unprompted has landed —
        # reset the ignored streak before it counts against us.
        salience.memory_for(user_id).note_user_spoke()

        # Drop ids that aren't really this user's before they reach the agent,
        # so a forged id becomes "no attachment" rather than a tool error the
        # model has to reason about mid-turn.
        attached = [
            image_id
            for image_id in (image_ids or [])
            if image_store.exists(user_id, image_id)
        ]
        attached_assets = [
            asset_id
            for asset_id in (asset_ids or [])
            if asset_store.exists(user_id, asset_id)
        ]

        # Persist the incoming user turn and load history to prompt with.
        db = SessionLocal()
        try:
            conversation = chat_service.get_or_create_conversation(
                db, user_id=user_id, conversation_id=conversation_id
            )
            user_message = chat_service.add_message(
                db,
                conversation=conversation,
                role="user",
                content=content,
                turn_id=turn_id,
                image_ids=attached,
                asset_ids=attached_assets,
            )
            await emit_event({"type": "meta", "conversation_id": str(conversation.id)})
            await emit_event({
                "type": "ack",
                "turn_id": str(turn_id),
                "user_message_id": str(user_message.id),
                "conversation_id": str(conversation.id),
            })
            history = chat_service.get_history(db, conversation=conversation)
            resolved_id = last_conversation_id = conversation.id
            # Derived after the user's message is persisted, so "you last spoke
            # moments ago" is true of *this* turn rather than the previous one.
            state = awareness_service.build(
                db, user_id=user_id, session=session
            )
            # Resolved per turn, not per connection: Google access tokens last
            # about an hour, and a chat socket can outlive that. None when the
            # user hasn't connected Google — the agent then runs without the
            # Gmail tools.
            gmail_access_token = await google.get_access_token(db, user_id=user_id)
            account = google_account.get_for_user(db, user_id=user_id)
            granted = account.granted_scopes() if account else set()
            drive_access_token = gmail_access_token if DRIVE in granted else None
            if GMAIL_MODIFY not in granted:
                gmail_access_token = None
        finally:
            db.close()

        # Local fallback has no ChatJob, so it calls the same shared capture
        # owner as the chat worker, marked as the local-fallback path.
        # Observational only: nothing below reads the result.
        if cognitive_shadow.capture_enabled():
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    cognitive_shadow.capture_turn,
                    user_id=user_id, conversation_id=resolved_id, turn_id=turn_id,
                    user_message_id=user_message.id, chat_job_id=None,
                    generation_path=GenerationPath.LOCAL_FALLBACK,
                    source_channel=ShadowSourceChannel.VOICE if voice else ShadowSourceChannel.WEB,
                )

        # Caption the screen lazily, here, rather than on a timer while the
        # user is idle — this is what makes an always-on eye free when nobody
        # is talking to it. Cached per frame id, so only the first turn after
        # the screen actually changed pays for it.
        senses = session.report
        if senses.vision_enabled and not session.captioned(senses.screen_image_id):
            state = await _caption_screen(state)

        # Retrieval may make one embedding API call. Keep that blocking client
        # off the WebSocket event loop, and fail open inside the service.
        long_term_memory = await asyncio.to_thread(
            memory_service.retrieve_for_prompt, user_id, content
        )

        context = AgentRuntimeContext(
            user_id=user_id,
            user_name=user_name,
            gmail_access_token=gmail_access_token,
            drive_access_token=drive_access_token,
            macrovision_enabled=settings.macrovision_tools_enabled,
            image_ids=tuple(attached),
            asset_ids=tuple(attached_assets),
            blender_enabled=settings.blender_tools_enabled,
            voice=voice,
            awareness=state.describe(),
            long_term_memory=long_term_memory,
            vision_enabled=senses.vision_enabled,
            screen_image_id=senses.screen_image_id,
        )
        clauses: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = (
            asyncio.create_task(
                speak(clauses, stop_event, audio_already_started=audio_already_started)
            )
            if voice
            else None
        )
        feed, flush = tts_service.clause_chunker()

        reply = ""
        stopped = False
        failed = False
        assistant_message_id = uuid.uuid4()
        first_model_token = True
        sticker_buffer = ""
        try:
            async for event in stream_chat(messages=history, context=context):
                if stop_event.is_set():
                    stopped = True
                    break

                if event["type"] == "token":
                    if first_model_token:
                        first_model_token = False
                        logger.info(
                            "voice latency: first_model_token=%.3fs voice=%s",
                            time.perf_counter() - turn_started_at,
                            voice,
                        )
                    text = event["content"]
                    reply += text
                    frames, sticker_buffer = stickers.consume_text(sticker_buffer, text)
                    for frame in frames:
                        await emit_event({
                            **frame,
                            "turn_id": str(turn_id),
                            "message_id": str(assistant_message_id),
                        })
                    if speaker is not None:
                        visible = stickers.spoken_text(frames)
                        for clause in feed(visible):
                            clauses.put_nowait(clause)
                else:
                    await emit_event({
                        **event,
                        "turn_id": str(turn_id),
                        "message_id": str(assistant_message_id),
                    })
            if not stopped:
                frames, sticker_buffer = stickers.consume_text(sticker_buffer, "", final=True)
                for frame in frames:
                    await emit_event({
                        **frame,
                        "turn_id": str(turn_id),
                        "message_id": str(assistant_message_id),
                    })
                    if speaker is not None and frame["type"] == "token":
                        for clause in feed(frame["content"]):
                            clauses.put_nowait(clause)
            if speaker is not None and not stopped:
                for clause in flush():
                    clauses.put_nowait(clause)
        except asyncio.CancelledError:
            # Forced interruption while a model/tool is between yields. Treat
            # it like a cooperative stop so accumulated text is still durable.
            stopped = True
        except Exception:
            failed = True
            await emit_event(
                {"type": "error", "detail": "The assistant failed to respond."}
            )
        finally:
            if speaker is not None:
                clauses.put_nowait(None)  # sentinel: no more clauses
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await speaker

        # Persist whatever was generated (full reply, or an explicit outcome
        # marker if stopped before the first token) so every turn is durable.
        outcome_status = (
            "failed" if failed else
            "cancelled" if stopped and not reply else
            "partial" if stopped else
            "completed"
        )
        dialogue_scheduled = False
        if reply or outcome_status in {"cancelled", "failed"}:
            db = SessionLocal()
            try:
                conversation = chat_service.get_conversation(
                    db, user_id=user_id, conversation_id=resolved_id
                )
                if conversation is not None:
                    assistant_message = chat_service.add_message(
                        db,
                        message_id=assistant_message_id,
                        conversation=conversation,
                        role="assistant",
                        content=reply,
                        turn_id=turn_id,
                        status=outcome_status,
                        commit=False,
                    )
                    # The same scheduling service as the chat worker, in the
                    # transaction that commits the terminal outcome.
                    dialogue_scheduled = dialogue_memory.schedule_update(
                        db, user_id=user_id, conversation_id=resolved_id,
                        trigger_message_id=assistant_message_id,
                    )
                    db.commit()
                    # A stopped reply is partial model output, not a completed
                    # interaction, so only consolidate finished turns.
                    if not stopped:
                        memory_service.schedule_consolidation(
                            db,
                            user_id=user_id,
                            conversation_id=resolved_id,
                            assistant_message_id=assistant_message_id,
                        )
            finally:
                db.close()
        elif dialogue_memory.enabled():
            # A user-only terminal turn still advances dialogue memory over
            # the persisted user message.
            db = SessionLocal()
            try:
                dialogue_scheduled = dialogue_memory.schedule_update(
                    db, user_id=user_id, conversation_id=resolved_id, trigger_message_id=user_message.id,
                )
                db.commit()
            finally:
                db.close()
        if dialogue_scheduled:
            dialogue_memory.wake_worker()

        await emit_event({
            "type": "stopped" if stopped else "done",
            "turn_id": str(turn_id),
            "message_id": str(assistant_message_id),
            "status": outcome_status,
        })

    async def run_voice_turn(
        conversation_id: uuid.UUID | None,
        pcm: bytes,
        sample_rate: int,
        stop_event: asyncio.Event,
        turn_id: uuid.UUID,
        interruption: bool = False,
        transcript: str | None = None,
    ) -> None:
        """Transcribe a captured utterance, then run it as a normal turn.

        ``interruption`` marks an utterance that began by talking over a
        reply. If all it says is "stop" or "wait", cutting the reply off was
        the whole request: there is nothing to answer, and persisting it
        would make the next turn respond to the word "stop".
        """
        if transcript is None:
            try:
                transcript = await transcribe_utterance(pcm, sample_rate, turn_id)
            except stt_service.STTError:
                await send_json({"type": "error", "detail": "Couldn't transcribe that."})
                return
        if not transcript:
            # Silence or a stray VAD trigger: end the turn quietly rather than
            # making the user dismiss an error.
            await send_json({"type": "done"})
            return
        if interruption and _is_stop_only(transcript):
            voice_latency.mark("barge_stop_only", turn_id, source="api")
            await send_json({"type": "done", "turn_id": str(turn_id), "status": "cancelled"})
            return
        await send_json({"type": "transcript", "turn_id": str(turn_id), "content": transcript})
        db = SessionLocal()
        try:
            conversation = chat_service.get_or_create_conversation(
                db, user_id=user_id, conversation_id=conversation_id
            )
            user_message = chat_service.add_message(
                db, conversation=conversation, role="user", content=transcript,
                turn_id=turn_id,
            )
            state = awareness_service.build(db, user_id=user_id, session=session)
            chat_service.enqueue_job(
                db, user_id=user_id, conversation=conversation, turn_id=turn_id,
                user_message_id=user_message.id,
                payload={"content": transcript, "image_ids": [], "asset_ids": [],
                         "voice": True, "awareness": state.describe(),
                         "vision_enabled": state.senses.vision_enabled,
                         "screen_image_id": state.senses.screen_image_id},
            )
            voice_latency.mark("job_enqueued", turn_id, source="api")
            await send_json({"type": "meta", "conversation_id": str(conversation.id)})
            await send_json({"type": "ack", "turn_id": str(turn_id),
                             "user_message_id": str(user_message.id),
                             "conversation_id": str(conversation.id)})
        finally:
            db.close()
        await subscribe_turn(turn_id, voice=True, stop_event=stop_event)

    async def transcribe_utterance(
        pcm: bytes, sample_rate: int, turn_id: uuid.UUID,
    ) -> str:
        stt_started_at = time.perf_counter()
        try:
            transcript = await stt_service.transcribe(pcm, sample_rate)
        except stt_service.STTError:
            voice_latency.mark("stt_failed", turn_id, source="api")
            raise
        voice_latency.mark(
            "stt_final", turn_id, source="api",
            stt_model=settings.stt_model, empty=not transcript,
        )
        logger.info(
            "voice latency: transcription=%.3fs audio_seconds=%.2f",
            time.perf_counter() - stt_started_at,
            len(pcm) / 2 / max(sample_rate, 1),
        )
        return transcript

    async def resolve_hold(
        conversation_id: uuid.UUID | None,
        pcm: bytes,
        sample_rate: int,
        turn_id: uuid.UUID,
    ) -> None:
        """Decide a held interruption once its utterance is complete.

        The held reply is still the active turn throughout; it is only
        stopped once the decision is stop or turn. `barge_resolved` goes out
        before any teardown, so the client never waits on a slow provider to
        resume or go quiet.
        """
        try:
            transcript = await transcribe_utterance(pcm, sample_rate, turn_id)
        except stt_service.STTError:
            transcript = ""  # couldn't tell: carrying on beats losing the reply
        action = _classify_hold(transcript)
        voice_latency.mark("barge_hold_resolved", turn_id, source="api", action=action)
        await send_json({"type": "barge_resolved", "turn_id": str(turn_id), "action": action})
        if action == "resume":
            return
        await _stop_active_turn(user_id)
        if action == "stop":
            return
        next_stop = asyncio.Event()
        task = asyncio.current_task()
        assert task is not None
        register_turn(task, next_stop, conversation_id, turn_id)
        await run_voice_turn(
            conversation_id, pcm, sample_rate, next_stop, turn_id,
            interruption=True, transcript=transcript,
        )

    async def record_heard(turn_id: uuid.UUID, heard_chars: int) -> None:
        """Save a heard report once the interrupted reply has been persisted."""

        def attempt() -> bool:
            db = SessionLocal()
            try:
                return chat_service.record_heard(
                    db, user_id=user_id, turn_id=turn_id, heard_chars=heard_chars,
                )
            finally:
                db.close()

        deadline = time.monotonic() + _HEARD_RETRY_SECONDS
        while True:
            try:
                if await asyncio.to_thread(attempt):
                    voice_latency.mark("barge_heard_recorded", turn_id, source="api",
                                       heard_chars=heard_chars)
                    return
            except Exception:
                logger.exception("Could not record heard progress for %s", turn_id)
                return
            if time.monotonic() >= deadline:
                logger.warning("Reply %s never persisted; heard report dropped", turn_id)
                return
            await asyncio.sleep(_HEARD_RETRY_INTERVAL)

    def fence_in_background() -> None:
        """Stop the active turn now; finish its teardown off the receive loop."""
        active = _fence_active_turn(user_id)
        if active is not None:
            teardown = asyncio.create_task(_await_teardown(active))
            background_tasks.add(teardown)
            teardown.add_done_callback(background_tasks.discard)

    current_task: asyncio.Task | None = None
    stop_event: asyncio.Event | None = None
    # The thread this socket is actually in, so an unprompted message lands in
    # the conversation the user is looking at rather than an arbitrary one.
    last_conversation_id: uuid.UUID | None = None

    # Open-utterance state, reset on voice_start / voice_end / voice_cancel.
    utterance: bytearray | None = None
    utterance_rate = 16000
    utterance_conversation: uuid.UUID | None = None
    utterance_turn_id: uuid.UUID | None = None
    utterance_interruption = False
    # A held interruption: the reply is paused client-side, not cancelled.
    utterance_tentative = False
    # Barge-in teardowns and hold decisions run detached from the receive
    # loop; strong refs so they aren't garbage-collected mid-flight.
    background_tasks: set[asyncio.Task] = set()

    def register_turn(
        task: asyncio.Task,
        event: asyncio.Event,
        conversation_id: uuid.UUID | None,
        turn_id: uuid.UUID,
    ) -> None:
        """Publish a detached turn and remove only that exact task when done."""
        nonlocal current_task, stop_event
        current_task, stop_event = task, event
        active = _ActiveTurn(task, event, conversation_id, turn_id)
        _active_turns[user_id] = active

        def discard(_finished: asyncio.Task) -> None:
            if _active_turns.get(user_id) is active:
                _active_turns.pop(user_id, None)

        task.add_done_callback(discard)

    async def subscribe_turn(
        turn_id: uuid.UUID,
        *,
        refresh_history: bool = False,
        voice: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Replay and follow the worker's Redis Stream for this turn.

        A reconnect has no client-side pending promise for the recovered turn.
        Refresh its authoritative history after the terminal event so the
        newly mounted page receives the assistant message persisted by the
        worker, even when the initial history snapshot raced generation.
        """
        settings = get_settings()
        if not settings.redis_url:
            await send_json({"type": "error", "detail": "Chat worker event bus is unavailable."})
            return

        def read_events(last_id: str):
            client = chat_worker._redis()
            if client is None:
                return []
            return client.xread({chat_worker.stream_key(turn_id): last_id}, block=2000, count=100)

        last_id = "0-0"
        first_audio = voice
        try:
            while connected:
                batches = await asyncio.to_thread(read_events, last_id)
                for _, entries in batches:
                    for entry_id, fields in entries:
                        last_id = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                        raw = fields.get(b"event", fields.get("event"))
                        if raw is None:
                            continue
                        event = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                        # Once this turn is told to stop, its audio is stale:
                        # the worker may still be finishing a chunk, and a
                        # barge-in must not hear the old reply resume. Text
                        # and terminal events still flow so the turn settles.
                        if (stop_event is not None and stop_event.is_set()
                                and event.get("type") in _AUDIO_EVENT_TYPES):
                            continue
                        if event.get("type") == "audio_chunk":
                            await send_bytes(base64.b64decode(event["content"]))
                            if first_audio:
                                first_audio = False
                                voice_latency.mark("ws_first_audio", turn_id, source="api")
                        else:
                            await send_json(event)
                        if event.get("type") in {"done", "stopped", "error"}:
                            if voice:
                                voice_latency.mark(
                                    "ws_terminal", turn_id, source="api",
                                    outcome=event.get("type"),
                                )
                            if refresh_history and event.get("type") != "error":
                                with contextlib.suppress(Exception):
                                    await send_json(await asyncio.to_thread(read_history))
                            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Turn event subscription failed for %s", turn_id)

    # Push an initial reading immediately (the client has told us nothing yet,
    # so this is UTC and senses-off) and keep it ticking for the clock.
    heartbeat_task = asyncio.create_task(heartbeat())
    with contextlib.suppress(Exception):
        await push_state()

    # A reconnect immediately resumes every unfinished turn belonging to this
    # user. Redis Streams replay events already produced while the old socket
    # was down; the database remains the source of truth for the final reply.
    initial_active = await asyncio.to_thread(
        lambda: _active_jobs_for_user(user_id)
    )
    subscription_tasks: list[asyncio.Task] = []
    for active_job in initial_active:
        resumed_stop = asyncio.Event()
        task = asyncio.create_task(
            subscribe_turn(active_job.turn_id, refresh_history=True, stop_event=resumed_stop),
            name=f"chat-turn:{active_job.turn_id}",
        )
        subscription_tasks.append(task)
        register_turn(task, resumed_stop, active_job.conversation_id, active_job.turn_id)

    # The handshake just authenticated this session.
    last_session_check = time.monotonic()
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    websocket.receive(), timeout=SESSION_CHECK_SECONDS
                )
            except TimeoutError:
                if not await check_session():
                    break
                last_session_check = time.monotonic()
                continue
            if event["type"] == "websocket.disconnect":
                break
            # Revalidate before every text frame (they start turns and change
            # state), but at most once per SESSION_CHECK_SECONDS for audio. The
            # mic sends a frame every 8ms and a check costs ~5ms (Redis + DB),
            # so checking each one let frames queue up behind the checks and
            # delayed `voice_end` by 1.5-3s. Revocation is still caught within
            # a second, the same bound as the idle check above.
            is_audio = event.get("bytes") is not None
            if not is_audio or time.monotonic() - last_session_check >= SESSION_CHECK_SECONDS:
                if not await check_session():
                    break
                last_session_check = time.monotonic()

            # --- binary: audio for the open utterance ---
            if (data := event.get("bytes")) is not None:
                if utterance is None:
                    continue  # no utterance open; ignore stray audio
                if len(utterance) + len(data) > MAX_UTTERANCE_BYTES:
                    utterance = None
                    await send_json(
                        {"type": "error", "detail": "Utterance too long."}
                    )
                    continue
                utterance.extend(data)
                continue

            raw_text = event.get("text")
            if raw_text is None:
                continue

            try:
                message = chat_ws_message_adapter.validate_json(raw_text)
            except (ValueError, ValidationError):
                await send_json({"type": "error", "detail": "Invalid message payload."})
                continue

            # --- stop / barge-in ---
            if isinstance(message, ChatStopRequest):
                active = _active_turns.get(user_id)
                stopped_turn_id = active.turn_id if active is not None else message.turn_id
                # Use the same bounded path as barge-in/follow-up. Merely
                # setting the flag can hang forever while a provider or tool
                # is between streamed events and cannot observe it.
                await _stop_active_turn(user_id)
                # If the worker's terminal event arrived, this is harmless:
                # the client has already cleared its pending turn. If the
                # subscription was bounded/cancelled, this guarantees the
                # client's stopping state cannot remain stuck forever.
                # Tagged with the stopped turn: teardown can take up to 2s,
                # and by then a barge-in may already have a new turn pending
                # that an untagged `stopped` would settle as cancelled.
                await send_json({
                    "type": "stopped", "status": "cancelled",
                    "turn_id": str(stopped_turn_id) if stopped_turn_id else None,
                })
                continue

            # --- "stop doing that" ---
            if isinstance(message, NudgeFeedbackRequest):
                salience.memory_for(user_id).note_dismissed()
                continue

            # --- senses: the client describing itself ---
            if isinstance(message, SenseUpdateRequest):
                fields = message.model_dump(exclude={"type"})
                fields["vision_enabled"] = (
                    fields["vision_enabled"] and settings.vision_enabled
                )
                # Trust the flag, verify the frame. An id that isn't really
                # this user's becomes "no frame", which the agent handles, and
                # not a tool error it has to reason about mid-turn.
                frame = fields.pop("screen_image_id", None)
                if not fields["vision_enabled"] or not frame:
                    frame = None
                elif not await asyncio.to_thread(
                    image_store.exists, user_id, frame
                ):
                    frame = None
                fields["screen_image_id"] = frame
                # Stamp only when the frame is genuinely new — a heartbeat
                # re-reporting the same id must not make a stale frame look
                # freshly captured.
                fields["screen_captured_at"] = (
                    session.report.screen_captured_at
                    if frame and frame == session.report.screen_image_id
                    else (datetime.now(timezone.utc) if frame else None)
                )
                session.update(**fields)
                # Echo the derived state straight back rather than making the
                # client wait up to a heartbeat to see its own change land.
                with contextlib.suppress(Exception):
                    await push_state()
                continue

            # --- voice: open an utterance ---
            if isinstance(message, VoiceStartRequest):
                if not settings.voice_enabled:
                    await send_json({"type": "error", "detail": "Voice is disabled."})
                    continue
                # Barge-in arrives as stop-then-voice_start; make sure the old
                # turn is fully torn down before buffering the new one.
                await _stop_active_turn(user_id)
                utterance = bytearray()
                utterance_rate = message.sample_rate
                utterance_conversation = message.conversation_id
                utterance_turn_id = message.turn_id or uuid.uuid4()
                utterance_interruption = False
                utterance_tentative = False
                continue

            if isinstance(message, VoiceInterruptRequest):
                if not settings.voice_enabled:
                    await send_json({"type": "error", "detail": "Voice is disabled."})
                    continue
                # Fence now, tear down in the background. Awaiting teardown
                # here (up to 2s) would hold the pre-roll and live audio that
                # follow this frame in the transport queue. `voice_end` still
                # awaits it, so the old partial reply is persisted before the
                # new turn reads history. A hold fences nothing yet.
                if not message.tentative:
                    fence_in_background()
                utterance = bytearray()
                utterance_rate = message.sample_rate
                utterance_conversation = message.conversation_id
                utterance_turn_id = message.new_turn_id
                utterance_interruption = True
                utterance_tentative = message.tentative
                voice_latency.mark(
                    "barge_control_received", message.new_turn_id, source="api",
                    cancelled_turn=str(message.cancel_turn_id) if message.cancel_turn_id else None,
                    tentative=message.tentative,
                )
                continue

            if isinstance(message, VoiceInterruptCommitRequest):
                # Only the open hold it names; a late commit is a no-op.
                if (utterance is not None and utterance_tentative
                        and utterance_turn_id == message.turn_id):
                    fence_in_background()
                    utterance_tentative = False
                    voice_latency.mark("barge_committed", message.turn_id, source="api")
                continue

            if isinstance(message, VoiceHeardReport):
                task = asyncio.create_task(
                    record_heard(message.turn_id, message.heard_chars),
                    name=f"chat-heard:{message.turn_id}",
                )
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)
                continue

            if isinstance(message, VoiceLatencyReport):
                voice_latency.record_client_report(
                    message.turn_id, outcome=message.outcome,
                    endpoint=message.endpoint, marks=message.marks,
                )
                continue

            if isinstance(message, VoiceCancelRequest):
                utterance = None
                utterance_turn_id = None
                utterance_interruption = False
                utterance_tentative = False
                continue

            if isinstance(message, VoiceEndRequest):
                if utterance is None:
                    continue
                pcm, rate = bytes(utterance), utterance_rate
                conversation_id = utterance_conversation
                interruption = utterance_interruption
                tentative = utterance_tentative
                turn_id = message.turn_id or utterance_turn_id or uuid.uuid4()
                voice_latency.mark(
                    "voice_end_received", turn_id, source="api",
                    audio_ms=round(len(pcm) / 2 / max(rate, 1) * 1000),
                    sample_rate=rate,
                )
                utterance = None
                utterance_turn_id = None
                utterance_interruption = False
                utterance_tentative = False
                if tentative:
                    # The held reply stays the active turn while we decide.
                    hold = asyncio.create_task(
                        resolve_hold(conversation_id, pcm, rate, turn_id),
                        name=f"chat-hold:{turn_id}",
                    )
                    background_tasks.add(hold)
                    hold.add_done_callback(background_tasks.discard)
                    continue
                await _stop_active_turn(user_id)
                voice_latency.mark("prior_turn_stopped", turn_id, source="api")
                next_stop = asyncio.Event()
                task = asyncio.create_task(
                    run_voice_turn(conversation_id, pcm, rate, next_stop, turn_id, interruption)
                )
                register_turn(task, next_stop, conversation_id, turn_id)
                continue

            # --- text turn ---
            if isinstance(message, ChatRunRequest):
                # A message sent while thinking is an interruption followed by
                # a new turn, never a competing generation against stale history.
                await _stop_active_turn(user_id)
                # Local development/test fallback. Production deployments must
                # configure Redis and run the external chat worker; without an
                # event bus there is nowhere for a detached worker to report.
                if not settings.redis_url or not await asyncio.to_thread(
                    chat_worker.event_bus_available
                ):
                    turn_id = message.turn_id or uuid.uuid4()
                    next_stop = asyncio.Event()
                    task = asyncio.create_task(run_turn(
                        message.conversation_id, message.content, next_stop,
                        voice=False, turn_id=turn_id,
                        image_ids=message.image_ids, asset_ids=message.asset_ids,
                    ))
                    register_turn(task, next_stop, message.conversation_id, turn_id)
                    continue
                turn_id = message.turn_id or uuid.uuid4()
                attached = [image_id for image_id in message.image_ids
                             if image_store.exists(user_id, image_id)]
                attached_assets = [asset_id for asset_id in message.asset_ids
                                   if asset_store.exists(user_id, asset_id)]
                db = SessionLocal()
                try:
                    conversation = chat_service.get_or_create_conversation(
                        db, user_id=user_id, conversation_id=message.conversation_id
                    )
                    user_message = chat_service.add_message(
                        db, conversation=conversation, role="user", content=message.content,
                        turn_id=turn_id, image_ids=attached, asset_ids=attached_assets,
                    )
                    state = awareness_service.build(db, user_id=user_id, session=session)
                    chat_service.enqueue_job(
                        db, user_id=user_id, conversation=conversation, turn_id=turn_id,
                        user_message_id=user_message.id,
                        payload={
                            "content": message.content, "image_ids": attached,
                            "asset_ids": attached_assets, "voice": False,
                            "awareness": state.describe(),
                            "vision_enabled": state.senses.vision_enabled,
                            "screen_image_id": state.senses.screen_image_id,
                        },
                    )
                    await send_json({"type": "meta", "conversation_id": str(conversation.id)})
                    await send_json({"type": "ack", "turn_id": str(turn_id),
                                     "user_message_id": str(user_message.id),
                                     "conversation_id": str(conversation.id)})
                finally:
                    db.close()
                task = asyncio.create_task(subscribe_turn(turn_id), name=f"chat-turn:{turn_id}")
                subscription_tasks.append(task)
                register_turn(task, asyncio.Event(), conversation.id, turn_id)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Unexpected error in the receive loop — don't let it leak past without
        # cleaning up the in-flight task and the socket.
        pass
    finally:
        connected = False
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat_task
        for task in subscription_tasks:
            if not task.done():
                task.cancel()
        for task in subscription_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Transport loss may preserve a turn, but revoked credentials must not.
        # Cancel only this socket's task, never another session's active turn.
        if session_revoked and current_task is not None:
            if stop_event is not None:
                stop_event.set()
            current_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await current_task
        with contextlib.suppress(Exception):
            async with send_lock:
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION if session_revoked else 1000
                )
