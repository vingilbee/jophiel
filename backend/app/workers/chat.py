"""Durable chat generation worker.

The WebSocket never owns model execution. Jobs are claimed from Postgres and
their replayable events are appended to a Redis Stream per turn.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import redis
from sqlalchemy import select

from app.ai import AgentRuntimeContext, stream_chat
from app.ai.integrations import google
from app.ai.integrations.google.scopes import DRIVE, GMAIL_MODIFY
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, User
from app.db.session import SessionLocal
from app.services import desktop_control, desktop_game, game_correction, security_activity
from app.services import asset_store, chat as chat_service, google_account
from app.services import memory_manager
from app.services import heard
from app.services import image_store, long_term_memory as memory_service
from app.services import recall_references
from app.services import coordinator, mind_turn, personality
from app.services import agent_say, push
from app.ai import prompts
from app.services import stickers
from app.services import tts as tts_service
from app.services import voice_latency
from app.services import whatsapp as whatsapp_service

logger = logging.getLogger(__name__)


def stream_key(turn_id: uuid.UUID | str) -> str:
    return f"{get_settings().chat_event_stream_prefix}{turn_id}"


def cancel_key(turn_id: uuid.UUID | str) -> str:
    return f"{stream_key(turn_id)}:cancel"


@lru_cache(maxsize=4)
def _client_for(url: str) -> redis.Redis:
    # One client (and connection pool) per process, shared by every job and
    # worker thread; redis-py clients are thread-safe. Building a client per
    # call opened a new TCP connection for every token, audio chunk, and
    # cancellation check.
    return redis.from_url(url)


def _redis():
    url = get_settings().redis_url
    return _client_for(url) if url else None


def publish(turn_id: uuid.UUID | str, event: dict) -> None:
    r = _redis()
    if r is None:
        return
    key = stream_key(turn_id)
    # One round trip for both commands.
    with r.pipeline(transaction=False) as pipe:
        pipe.xadd(key, {"event": json.dumps(event)}, maxlen=2000, approximate=True)
        pipe.expire(key, 86400)
        pipe.execute()


def request_cancel(turn_id: uuid.UUID | str) -> None:
    r = _redis()
    if r is not None:
        r.setex(cancel_key(turn_id), 3600, "1")


def _cancelled(turn_id: uuid.UUID | str) -> bool:
    r = _redis()
    return bool(r and r.exists(cancel_key(turn_id)))


# How often a wait on the model or the speech sidecar looks for a stop, so a
# barge-in ends the turn at once instead of when the next token or chunk lands.
CANCEL_POLL_SECONDS = 0.2
# The sidecar's first chunk normally comes in ~1-2 s, and later chunks faster.
# Silence beyond these means it is wedged: finish the turn as text instead of
# hanging on the HTTP timeout (60 s) with the user stuck on "thinking".
TTS_FIRST_CHUNK_SECONDS = 12.0
TTS_STALL_SECONDS = 8.0


class _Stopped(Exception):
    """The turn was stopped while waiting for the next item."""


async def _next_unless(iterator, should_stop, limit: float | None = None):
    """The iterator's next item, unless ``should_stop()`` turns true first
    (raises ``_Stopped``) or ``limit`` seconds pass (raises ``TimeoutError``).
    End of iteration raises ``StopAsyncIteration`` as usual. The pending read
    is cancelled on the way out, so the iterator can be closed right after."""
    task = asyncio.ensure_future(iterator.__anext__())
    waited = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            waited += CANCEL_POLL_SECONDS
            if should_stop():
                raise _Stopped
            if limit is not None and waited >= limit:
                raise TimeoutError
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


def event_bus_available() -> bool:
    r = _redis()
    try:
        return bool(r and r.ping())
    except Exception:
        return False


def _whatsapp_identity(payload: dict, job_id: uuid.UUID) -> uuid.UUID | None:
    """Who a WhatsApp reply goes to. A job without one (a wake scheduled before wakes kept it) keeps its reply in
    the conversation and logs why it wasn't sent, instead of failing the turn."""
    raw = payload.get("channel_identity_id")
    try:
        if raw:
            return uuid.UUID(str(raw))
    except ValueError:
        pass
    logger.warning("WhatsApp job %s has no valid channel identity: reply kept, not sent", job_id)
    return None


async def _generate(job: ChatJob) -> None:
    payload = job.payload
    claim_token = job.claim_token
    if not claim_token:
        raise RuntimeError("claimed chat job has no ownership token")
    if payload.get("phase") == "deliver":
        # Words she already chose in a game turn (autonomous_speech.deliver_worded): no model run, only delivery.
        await asyncio.to_thread(_deliver_worded, job)
        return

    def mark(stage: str, **dims) -> None:
        # Timing marks are for spoken turns only; typed turns aren't waiting
        # on a voice.
        if payload.get("voice"):
            voice_latency.mark(stage, job.turn_id, source="worker", **dims)

    mark("job_claimed", attempt=job.attempts)
    with SessionLocal() as db:
        conversation = chat_service.get_conversation(db, user_id=job.user_id,
                                                      conversation_id=job.conversation_id)
        if conversation is None:
            raise RuntimeError("conversation was deleted")
        existing = db.scalar(select(ChatMessage).where(
            ChatMessage.turn_id == job.turn_id,
            ChatMessage.role == "assistant",
        ))
        if existing is not None:
            chat_service.finish_job(db, job.id, "completed", claim_token=claim_token, commit=False)
            if payload.get("phase") == "follow_up":
                from app.db.models import CoordinatorOutput
                output = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.chat_job_id == job.id))
                if output is not None and output.status == "dispatched":
                    coordinator.save_follow_up_receipt(db, job, existing,
                        {"completed": "delivered", "partial": "partial", "cancelled": "cancelled"}.get(existing.status, "failed"))
            db.commit()
            if payload.get("phase") == "follow_up":
                coordinator.announce_follow_up(uuid.UUID(payload["request_id"]))
            return
        history = chat_service.get_history(
            db, conversation=conversation, up_to_message_id=job.user_message_id,
            up_to_job_id=job.id, records=True, zone=payload.get("zone"),
            current_message_id=None if payload.get("phase") == "follow_up" else job.user_message_id,
        )
        user = db.get(User, job.user_id)
        gmail_token = await google.get_access_token(db, user_id=job.user_id)
        account = google_account.get_for_user(db, user_id=job.user_id)
        granted = account.granted_scopes() if account else set()
        drive_token = gmail_token if DRIVE in granted else None
        if GMAIL_MODIFY not in granted:
            gmail_token = None

    assistant_id = uuid.uuid5(job.turn_id, "assistant")
    # One evaluation of her mind per job (services/mind_turn.py): a user's message, or a wake without one
    # (thinking that finished, a thought or proposal of hers, something she chose to come back to).
    follow_up = payload.get("phase") == "follow_up"
    wake_text = None
    if follow_up:
        # Checked again right before she is woken: a newer user message or a superseded item means the
        # thought stays private. Execution facts only; what to do with it is hers.
        request_id = uuid.UUID(payload["request_id"])
        valid, info = await asyncio.to_thread(coordinator.follow_up_still_valid, uuid.UUID(payload["work_id"]))
        if not valid:
            with SessionLocal() as db:
                chat_service.finish_job(db, job.id, "cancelled", claim_token=claim_token)
            coordinator.follow_up_outcome(request_id, status="cancelled", assistant_message_id=None)
            await asyncio.to_thread(_wake_ended, None, info, job, decided=False, why="it went stale before she was woken")
            return
        wake_text = coordinator.render_wake(info)
        mind = await asyncio.to_thread(mind_turn.prepare_wake, job=job, info=info,
                                       user_name=user.name if user else None)
    else:
        mind = await asyncio.to_thread(
            mind_turn.prepare_message, user_id=job.user_id, conversation_id=job.conversation_id,
            user_message_id=job.user_message_id, message=payload["content"],
            zone=payload.get("zone"), turn_id=job.turn_id,
            channel=payload.get("channel", "web"), voice=bool(payload.get("voice", False)), job=job,
            user_name=user.name if user else None, channel_identity_id=payload.get("channel_identity_id"))
    recall_turn = mind.recall_turn
    game = await asyncio.to_thread(lambda: desktop_game.summary_for(job.user_id, for_reply=True))
    desktop = await asyncio.to_thread(desktop_control.line_for, job.user_id)
    security = await asyncio.to_thread(security_activity.line_for, job.user_id, zone=payload.get("zone"))
    files = await asyncio.to_thread(_readable_files, job.user_id, payload.get("asset_ids") or [])
    working_memory = await asyncio.to_thread(memory_manager.view_for, job.user_id,
                                             user_name=user.name if user else None)
    spoke_at = payload.get("spoke_at")
    picture = (await asyncio.to_thread(desktop_game.turn_picture, job.user_id, spoke_at)
               if game is not None and spoke_at else None)

    settings = get_settings()
    context = AgentRuntimeContext(
        user_id=job.user_id,
        channel=payload.get("channel", "web"),
        user_name=user.name if user else None,
        gmail_access_token=gmail_token,
        drive_access_token=drive_token,
        macrovision_enabled=settings.macrovision_tools_enabled,
        image_ids=tuple(payload.get("image_ids", [])),
        asset_ids=tuple(payload.get("asset_ids", [])),
        files=files,
        blender_enabled=settings.blender_tools_enabled,
        voice=bool(payload.get("voice", False)),
        # Their app's senses without its clock (a queued job's snapshot is never the clock): "Now" is read here.
        awareness=mind_turn.fresh_awareness(payload.get("awareness", ""), wake=follow_up,
                                            reported_at=payload.get("awareness_at")),
        now=await asyncio.to_thread(mind.situation),
        long_term_memory=mind.long_term_memory,
        personality=await asyncio.to_thread(_personality),
        working_memory=working_memory,
        recall=mind.recall_rendered,
        recall_turn=recall_turn,
        cached_memory=mind.cached_memory,
        turn_boundary=mind.boundary(),
        mind_state=await asyncio.to_thread(mind.state),
        lessons=mind_turn.lessons_text(mind),
        mind=mind,
        wake=wake_text,
        memory_tools=True,
        source_message_id=job.user_message_id,
        vision_enabled=bool(payload.get("vision_enabled", False)),
        screen_image_id=payload.get("screen_image_id"),
        game=game.summary if game else None,
        game_activity=game.activity if game else None,
        game_vm=game.vm if game else False,
        game_planner=game.planner if game else False,
        game_moment=game.moment if game else None,
        spoke_at=spoke_at,
        game_picture=picture[0] if picture else None,
        game_picture_note=picture[1] if picture else None,
        desktop=desktop,
        security=security,
        zone=payload.get("zone"),
        turn_id=job.turn_id,
        conversation_id=job.conversation_id,
    )
    mark("context_ready", model=settings.voice_chat_model or settings.chat_model)
    turn_id = job.turn_id
    reply = ""
    feed, flush = tts_service.clause_chunker()
    audio_started = False
    clauses_spoken = 0
    # End of the reply text spoken so far, so each clause's audio can be
    # labelled with the span of `reply` it speaks (services/heard.py).
    spoken_end = 0

    speech_cancelled = False
    speech_failed = False  # the sidecar went silent: the rest of the reply is text only
    # Some clause wasn't fully said (the sidecar errored or sent nothing): the reply wasn't spoken aloud.
    speech_incomplete = False

    async def speak(clause: str) -> None:
        nonlocal audio_started, clauses_spoken, speech_cancelled, speech_failed, spoken_end, speech_incomplete
        # A barge-in must silence the reply now, not after the clause being
        # synthesized finishes: wait for each chunk only while not stopped, and
        # close the sidecar stream so its next request isn't queued behind this one.
        if speech_failed:
            return
        if speech_cancelled or _cancelled(turn_id):
            speech_cancelled = True
            return
        first = clauses_spoken == 0
        segment = clauses_spoken
        clauses_spoken += 1
        text_start = spoken_end
        spoken_end = heard.raw_end_of_spoken(reply, spoken_end, clause)
        if first:
            mark("first_clause_emitted", clause_chars=len(clause))
            mark("tts_request_started")
        announced = False
        report = tts_service.SpeechReport()
        sent_bytes = 0
        async with contextlib.aclosing(tts_service.synthesize(clause, report)) as stream:
            limit = TTS_FIRST_CHUNK_SECONDS
            while True:
                try:
                    pcm = await _next_unless(stream, lambda: _cancelled(turn_id), limit)
                except StopAsyncIteration:
                    if report.failed or (clause.strip() and sent_bytes == 0):
                        speech_incomplete = True
                    break
                except _Stopped:
                    speech_cancelled = True
                    return
                except TimeoutError:
                    speech_failed = True
                    mark("tts_stalled", seconds=limit, clause_index=segment)
                    logger.warning("TTS sidecar sent nothing for %.0f s on turn %s; finishing as text",
                                   limit, turn_id)
                    return
                limit = TTS_STALL_SECONDS
                if _cancelled(turn_id):  # chunks that arrive back to back never wait
                    speech_cancelled = True
                    return
                first_audio = not audio_started
                if first_audio:
                    audio_started = True
                    mark("tts_first_byte", first_clause=first)
                    publish(turn_id, {"type": "audio_start", "turn_id": str(turn_id),
                                      "sample_rate": settings.tts_sample_rate})
                if not announced:
                    # Binary frames carry no ids: everything until the next
                    # segment speaks this span, which is how the browser can
                    # tell how much of the text was actually heard.
                    announced = True
                    publish(turn_id, {"type": "audio_segment", "turn_id": str(turn_id),
                                      "segment": segment, "text_start": text_start,
                                      "text_end": spoken_end})
                publish(turn_id, {"type": "audio_chunk", "content": base64.b64encode(pcm).decode()})
                sent_bytes += len(pcm)
                if first_audio:
                    mark("redis_first_audio")
    publish(turn_id, {"type": "start", "turn_id": str(turn_id),
                      "message_id": str(assistant_id)})
    if not follow_up:
        # What memory gave this evaluation, failed or not: visible on the turn's stream, never silent.
        publish(turn_id, {**mind.event(), "turn_id": str(turn_id), "message_id": str(assistant_id)})
    lost_claim = asyncio.Event()

    async def keep_lease() -> None:
        interval = max(
            1,
            min(
                settings.chat_worker_heartbeat_seconds,
                max(1, settings.chat_worker_lease_seconds // 3),
            ),
        )

        def renew() -> bool:
            with SessionLocal() as heartbeat_db:
                return chat_service.renew_job_lease(
                    heartbeat_db, job.id, claim_token,
                    lease_seconds=settings.chat_worker_lease_seconds,
                )

        while True:
            await asyncio.sleep(interval)
            owned = await asyncio.to_thread(renew)
            if not owned:
                lost_claim.set()
                return

    heartbeat = asyncio.create_task(keep_lease())
    # The published tokens keep their sticker markers (the client renders
    # them); only what gets *spoken* has them stripped.
    sticker_buffer = ""
    first_token = True
    first_subagent = True
    # A wake has no new user message: a closing internal trigger (never persisted) stands in for one.
    messages = [*history, {"role": "user", "content": prompts.WAKE_TRIGGER}] if follow_up else history
    events = stream_chat(messages=messages, context=context, on_stage=lambda stage: mark(stage))
    try:
        while True:
            # Wait for the model only while the turn isn't stopped: a tool call
            # or a slow stream must not hold a barge-in for its whole duration.
            try:
                event = await _next_unless(events, lambda: _cancelled(turn_id) or lost_claim.is_set())
            except (StopAsyncIteration, _Stopped):
                break
            if _cancelled(turn_id) or lost_claim.is_set():
                break
            if event.get("type") == "token":
                if first_token:
                    first_token = False
                    mark("model_first_token")
                reply += event["content"]
                if follow_up:
                    continue  # a wake's words go out only once it's known she chose to say them (stored below)
                if context.voice:
                    frames, sticker_buffer = stickers.consume_text(sticker_buffer, event["content"])
                    for clause in feed(stickers.spoken_text(frames)):
                        await speak(clause)
            elif event.get("type") == "subagent_start" and first_subagent:
                # Delegation adds a whole extra model run; flag the turns
                # that paid for one so they aren't mistaken for slow plain turns.
                first_subagent = False
                mark("subagent_start", agent=event.get("agent"))
            publish(turn_id, {**event, "turn_id": str(turn_id),
                              "message_id": str(assistant_id)})
        if lost_claim.is_set():
            raise RuntimeError("chat job lease ownership was lost")
        stopped = _cancelled(turn_id)
        if context.voice and not stopped:
            frames, sticker_buffer = stickers.consume_text(sticker_buffer, "", final=True)
            for clause in [*feed(stickers.spoken_text(frames)), *flush()]:
                await speak(clause)
        if audio_started:
            publish(turn_id, {"type": "audio_end", "turn_id": str(turn_id)})
        mark("generation_done", stopped=stopped, clauses=clauses_spoken,
             audio=audio_started, reply_chars=len(reply))
        # Silence is her choice (stay_silent, or a wake she had nothing to say about): no message at all.
        # Words she already streamed to a waiting user stand, whatever she called afterwards.
        silent = not stopped and ((follow_up and (mind.silent is not None or not reply.strip()))
                                  or (not follow_up and mind.silent is not None and not reply.strip()))
        if follow_up and silent:
            reply = ""
        if context.channel == "whatsapp" and not silent and not stopped and not reply:
            raise RuntimeError("assistant completed without a text reply")
        outcome = ("silent" if silent else "partial" if stopped and reply else
                   "cancelled" if stopped else "completed")
        receipt_id = None
        with SessionLocal() as db:
            conversation = chat_service.get_conversation(db, user_id=job.user_id,
                                                          conversation_id=job.conversation_id)
            assistant = None
            if follow_up and not silent:
                # Fence what a wake produced against changes while she worded it. Lock the same
                # conversation row used by message sequencing until the delivery commit.
                from app.db.models import CoordinatorWork, Conversation
                from app.services import autonomous_speech
                work = db.get(CoordinatorWork, uuid.UUID(payload["work_id"]))
                db.scalar(select(Conversation.id).where(Conversation.id == job.conversation_id).with_for_update())
                if (work is None or work.status not in ("completed", "failed", "expired")
                        or not coordinator.evidence_current(db, work, lock=True) or mind_turn.moved_on(db, mind)):
                    reply, outcome, stopped = "", "cancelled", True
                    conversation = None
                elif work.input.get("autonomous") and (
                        not autonomous_speech.valid(db, work) or not autonomous_speech.reserve(db, work)):
                    reply, outcome, stopped = "", "cancelled", True
                    conversation = None
            if conversation is not None and not silent and (reply or outcome == "cancelled"):
                assistant = chat_service.add_message(
                    db, message_id=assistant_id, conversation=conversation,
                    role="assistant", content=reply, turn_id=turn_id, status=outcome,
                    source_channel=context.channel, commit=False,
                )
                # What this reply was given from memory, with it (a savepoint: never fails the turn).
                receipt_id = recall_references.save_receipt(
                    db, recall_turn, assistant_message_id=assistant.id, conversation_id=job.conversation_id,
                    reply=reply)
                if context.voice and audio_started and not speech_failed and not speech_incomplete:
                    # Said aloud on the live voice session that asked: not pushed to the phone.
                    assistant.spoken_at = datetime.now(timezone.utc)
                if outcome == "completed" and context.channel != "whatsapp":
                    # Reaches the phone if, a few seconds from now, the user isn't at any app (services/push.py).
                    push.schedule_for_message(db, user_id=job.user_id, conversation_id=job.conversation_id,
                                              message_id=assistant.id)
                if outcome == "completed":
                    memory_service.schedule_consolidation(
                        db, user_id=job.user_id, conversation_id=job.conversation_id,
                        assistant_message_id=assistant.id, commit=False,
                    )
                    if context.channel == "whatsapp":
                        identity_id = _whatsapp_identity(payload, job.id)
                        if identity_id is not None:
                            whatsapp_service.schedule_delivery(
                                db, assistant_message_id=assistant.id,
                                channel_identity_id=identity_id, content=reply,
                            )
            finished = chat_service.finish_job(
                db, job.id, "cancelled" if stopped else "completed",
                claim_token=claim_token, commit=False,
            )
            if not finished:
                db.rollback()
                raise RuntimeError("chat job lease ownership was lost before commit")
            if follow_up:
                coordinator.save_follow_up_receipt(
                    db, job, assistant, _DELIVERED[outcome],
                    decision=("silent" if mind.silent is not None else "no_words") if silent else "said")
            db.commit()
        # Which memories her words were about: its own thread, after the reply is safely stored.
        recall_references.start_association(receipt_id)
        publish(turn_id, {"type": "stopped" if stopped else "done",
                          "turn_id": str(turn_id), "message_id": str(assistant_id),
                          "status": outcome})
        delivered = _DELIVERED[outcome]
        stored = assistant.id if assistant is not None else None
        mind_turn.finish(mind, outcome=outcome, reply=reply, assistant_message_id=stored)
        if silent or reply:
            from app.services import system_signals
            system_signals.emit(job.user_id, "mind", "mouth", "silent" if silent else "reply", turn_id)
        if follow_up:
            coordinator.follow_up_outcome(request_id, status=delivered, assistant_message_id=stored)
            await asyncio.to_thread(_wake_ended, mind, info, job, decided=mind.proposal_decision is not None,
                                    why="she was woken with it and made no decision about it")
            if reply and outcome == "completed":
                # Shown (and spoken on an idle live voice session) by the sockets: already stored above.
                coordinator.announce_follow_up(request_id)
        if outcome == "completed":
            # The game may have moved on while she said it: she corrects herself if so.
            game_correction.start(job.user_id, reply, context.game_moment)
        if context.channel == "whatsapp":
            whatsapp_service.wake_worker()
    except Exception as exc:
        logger.exception("Chat job %s failed", job.id)
        publish(turn_id, {"type": "error", "turn_id": str(turn_id),
                          "detail": "The assistant failed to respond."})
        try:
            with SessionLocal() as db:
                conversation = chat_service.get_conversation(
                    db, user_id=job.user_id, conversation_id=job.conversation_id
                )
                existing = db.scalar(select(ChatMessage).where(
                    ChatMessage.turn_id == turn_id,
                    ChatMessage.role == "assistant",
                ))
                failure_text = reply or "I couldn't complete that response. Please try again."
                # A follow-up nobody asked for that failed says nothing (its output row records the failure).
                if conversation is not None and existing is None and not (follow_up and not reply):
                    assistant = chat_service.add_message(
                        db, message_id=assistant_id, conversation=conversation,
                        role="assistant", content=failure_text, turn_id=turn_id,
                        status="failed", source_channel=context.channel, commit=False,
                    )
                    identity_id = _whatsapp_identity(payload, job.id) if context.channel == "whatsapp" else None
                    if identity_id is not None:
                        whatsapp_service.schedule_delivery(
                            db, assistant_message_id=assistant.id,
                            channel_identity_id=identity_id, content=failure_text,
                        )
                finished = chat_service.finish_job(
                    db, job.id, "failed", str(exc)[:1000],
                    claim_token=claim_token, commit=False,
                )
                if finished:
                    db.commit()
                    mind_turn.finish(mind, outcome="failed", reply=reply, error=f"{type(exc).__name__}: {exc}"[:300],
                                     assistant_message_id=assistant_id if existing is None and not (
                                         follow_up and not reply) else None)
                    if follow_up:
                        coordinator.follow_up_outcome(request_id, status="failed", assistant_message_id=None)
                        await asyncio.to_thread(_wake_ended, mind, info, job,
                                                decided=mind.proposal_decision is not None,
                                                why="the evaluation it woke her for failed")
                    if context.channel == "whatsapp":
                        whatsapp_service.wake_worker()
                else:
                    db.rollback()
        except Exception:
            logger.exception("Could not persist failure outcome for chat job %s", job.id)
    finally:
        # Stopped mid-stream: close the model run now (its pending read was
        # already cancelled), not whenever the generator is collected.
        with contextlib.suppress(Exception):
            await events.aclose()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


_DELIVERED = {"completed": "delivered", "partial": "partial", "cancelled": "cancelled", "silent": "not_sent"}


def _readable_files(user_id, attached: list[str]) -> tuple[tuple[str, str], ...]:
    """The user's readable files for ``read_attachment`` (this message's first), as ``(asset_id, filename)``."""
    from app.services import attachment_text

    try:
        found = [(record.asset_id, record.filename) for record in attachment_text.readable_files(user_id)]
        for asset_id in attached:
            if asset_id not in dict(found):
                try:
                    record = asset_store.get_record(user_id, asset_id)
                except asset_store.AssetNotFoundError:
                    continue
                if attachment_text.readable(record.filename):
                    found.insert(0, (asset_id, record.filename))
        return tuple(found)
    except Exception:  # noqa: BLE001 - a turn never fails because the file list did
        logger.exception("chat worker: listing readable files failed")
        return ()


def _personality() -> str | None:
    """Her text personality (one cached snapshot per process). None when it can't be read: the turn goes on
    without the section rather than failing."""
    try:
        return personality.current().text
    except Exception:  # noqa: BLE001
        logger.exception("chat worker: personality unavailable for this evaluation")
        return None


def _wake_ended(mind, info: dict | None, job: ChatJob, *, decided: bool, why: str) -> None:
    """After a wake ended (or never ran): a proposal that got no decision in it goes back to its producer as
    undecided, never as a choice made for her. Never raises."""
    try:
        extra = ((info or {}).get("input") or {}).get("extra") or {}
        if (info or {}).get("reason") == "proposal" and extra.get("trial"):
            from app.services import learning_trials
            learning_trials.settle_presented(user_id=job.user_id, trial_id=extra["trial"], decided=decided, why=why)
    except Exception:  # noqa: BLE001
        logger.exception("chat worker: settling the wake's producer failed")


def _deliver_worded(job: ChatJob) -> None:
    """Store queued words in their conversation, or drop them: under the conversation's sequencing lock,
    re-checked against a newer user message, the activity and the restraint rules. The output row is the
    receipt (``delivered`` or ``cancelled``); the announcement goes through the follow-up outbox."""
    from app.db.models import Conversation, CoordinatorOutput
    from app.services import autonomous_speech

    request_id = uuid.UUID(job.payload["request_id"])
    message = None
    with SessionLocal() as db:
        db.scalar(select(Conversation.id).where(Conversation.id == job.conversation_id).with_for_update())
        conversation = chat_service.get_conversation(db, user_id=job.user_id, conversation_id=job.conversation_id)
        output = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.chat_job_id == job.id))
        message = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == job.turn_id,
                                                      ChatMessage.role == "assistant"))
        if message is None and conversation is not None and autonomous_speech.worded_still_valid(db, job):
            message = chat_service.add_message(
                db, message_id=uuid.uuid5(job.turn_id, "assistant"), conversation=conversation,
                role="assistant", content=job.payload["text"], turn_id=job.turn_id, status="completed",
                source_channel="web", commit=False)
            push.schedule_for_message(db, user_id=job.user_id, conversation_id=job.conversation_id,
                                      message_id=message.id)
        if output is not None and output.status == "dispatched":
            coordinator.save_follow_up_receipt(db, job, message, "delivered" if message else "cancelled")
        if not chat_service.finish_job(db, job.id, "completed" if message else "cancelled",
                                       claim_token=job.claim_token, commit=False):
            db.rollback()
            raise RuntimeError("chat job lease ownership was lost before commit")
        db.commit()
    coordinator._log({"op": "worded_delivered" if message else "worded_dropped", "request": str(request_id)})
    if message is not None:
        coordinator.announce_follow_up(request_id)


def _announce(job: ChatJob, reply: str, message_id: uuid.UUID) -> bool:
    """Tell this user's sockets about something she said on her own, stored by this job: they show it like any
    unprompted message, and speak it only on an idle live voice session. Transport only: she already chose it."""
    r = _redis()
    if r is None:
        return False
    try:
        from app.db.models import CoordinatorWork
        interrupt = False
        voice_socket_id = None
        with SessionLocal() as db:
            work_id = (job.payload or {}).get("work_id")
            work = db.get(CoordinatorWork, uuid.UUID(work_id)) if work_id else None
            if work is not None and work.input.get("autonomous"):
                interrupt = agent_say.interrupts(work.input["reasons"])
                voice_socket_id = work.input.get("voice_socket_id")
            elif (job.payload or {}).get("phase") == "deliver":
                interrupt = agent_say.interrupts(job.payload["reasons"])
        r.publish(agent_say.channel(job.user_id), json.dumps({
            "conversation_id": str(job.conversation_id), "text": reply, "reason": "follow_up",
            "message_id": str(message_id), "interrupt": interrupt, "voice_socket_id": voice_socket_id}))
        return True
    except Exception:
        logger.warning("Follow-up announcement will be retried: %s", message_id)
        return False


def run_once() -> bool:
    settings = get_settings()
    with SessionLocal() as db:
        job = chat_service.claim_next_job(db, lease_seconds=settings.chat_worker_lease_seconds)
    if job is None:
        return False
    asyncio.run(_generate(job))
    return True


if __name__ == "__main__":
    from app.workers.runner import run
    run()
