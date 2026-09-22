"""Durable chat generation worker.

The WebSocket never owns model execution. Jobs are claimed from Postgres and
their replayable events are appended to a Redis Stream per turn.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid

import redis
from sqlalchemy import select

from app.ai import AgentRuntimeContext, stream_chat
from app.ai.integrations import google
from app.ai.integrations.google.scopes import DRIVE, GMAIL_MODIFY
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, User
from app.db.session import SessionLocal
from app.services import asset_store, chat as chat_service, google_account
from app.services import image_store, long_term_memory as memory_service
from app.services import tts as tts_service
from app.services import whatsapp as whatsapp_service

logger = logging.getLogger(__name__)


def stream_key(turn_id: uuid.UUID | str) -> str:
    return f"{get_settings().chat_event_stream_prefix}{turn_id}"


def cancel_key(turn_id: uuid.UUID | str) -> str:
    return f"{stream_key(turn_id)}:cancel"


def _redis():
    url = get_settings().redis_url
    return redis.from_url(url) if url else None


def publish(turn_id: uuid.UUID | str, event: dict) -> None:
    r = _redis()
    if r is None:
        return
    key = stream_key(turn_id)
    r.xadd(key, {"event": json.dumps(event)}, maxlen=2000, approximate=True)
    r.expire(key, 86400)


def request_cancel(turn_id: uuid.UUID | str) -> None:
    r = _redis()
    if r is not None:
        r.setex(cancel_key(turn_id), 3600, "1")


def _cancelled(turn_id: uuid.UUID | str) -> bool:
    r = _redis()
    return bool(r and r.exists(cancel_key(turn_id)))


def event_bus_available() -> bool:
    r = _redis()
    try:
        return bool(r and r.ping())
    except Exception:
        return False


async def _generate(job: ChatJob) -> None:
    payload = job.payload
    claim_token = job.claim_token
    if not claim_token:
        raise RuntimeError("claimed chat job has no ownership token")
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
            chat_service.finish_job(
                db, job.id, "completed", claim_token=claim_token
            )
            return
        history = chat_service.get_history(
            db, conversation=conversation, up_to_message_id=job.user_message_id,
            up_to_job_id=job.id,
        )
        user = db.get(User, job.user_id)
        gmail_token = await google.get_access_token(db, user_id=job.user_id)
        account = google_account.get_for_user(db, user_id=job.user_id)
        granted = account.granted_scopes() if account else set()
        drive_token = gmail_token if DRIVE in granted else None
        if GMAIL_MODIFY not in granted:
            gmail_token = None

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
        blender_enabled=settings.blender_tools_enabled,
        voice=bool(payload.get("voice", False)),
        awareness=payload.get("awareness", ""),
        long_term_memory=await asyncio.to_thread(
            memory_service.retrieve_for_prompt, job.user_id, payload["content"]
        ),
        vision_enabled=bool(payload.get("vision_enabled", False)),
        screen_image_id=payload.get("screen_image_id"),
    )
    turn_id = job.turn_id
    assistant_id = uuid.uuid5(job.turn_id, "assistant")
    reply = ""
    feed, flush = tts_service.clause_chunker()
    audio_started = False
    publish(turn_id, {"type": "start", "turn_id": str(turn_id),
                      "message_id": str(assistant_id)})
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
    try:
        async for event in stream_chat(messages=history, context=context):
            if _cancelled(turn_id) or lost_claim.is_set():
                break
            if event.get("type") == "token":
                reply += event["content"]
                if context.voice:
                    for clause in feed(event["content"]):
                        async for pcm in tts_service.synthesize(clause):
                            if not audio_started:
                                audio_started = True
                                publish(turn_id, {"type": "audio_start", "sample_rate": settings.tts_sample_rate})
                            publish(turn_id, {"type": "audio_chunk", "content": base64.b64encode(pcm).decode()})
            publish(turn_id, {**event, "turn_id": str(turn_id),
                              "message_id": str(assistant_id)})
        if lost_claim.is_set():
            raise RuntimeError("chat job lease ownership was lost")
        stopped = _cancelled(turn_id)
        if context.voice and not stopped:
            for clause in flush():
                async for pcm in tts_service.synthesize(clause):
                    if not audio_started:
                        audio_started = True
                        publish(turn_id, {"type": "audio_start", "sample_rate": settings.tts_sample_rate})
                    publish(turn_id, {"type": "audio_chunk", "content": base64.b64encode(pcm).decode()})
        if audio_started:
            publish(turn_id, {"type": "audio_end"})
        if context.channel == "whatsapp" and not stopped and not reply:
            raise RuntimeError("assistant completed without a text reply")
        outcome = ("partial" if stopped and reply else
                   "cancelled" if stopped else "completed")
        with SessionLocal() as db:
            conversation = chat_service.get_conversation(db, user_id=job.user_id,
                                                          conversation_id=job.conversation_id)
            if conversation is not None and (reply or outcome == "cancelled"):
                assistant = chat_service.add_message(
                    db, message_id=assistant_id, conversation=conversation,
                    role="assistant", content=reply, turn_id=turn_id, status=outcome,
                    source_channel=context.channel, commit=False,
                )
                if outcome == "completed":
                    memory_service.schedule_consolidation(
                        db, user_id=job.user_id, conversation_id=job.conversation_id,
                        assistant_message_id=assistant.id, commit=False,
                    )
                    if context.channel == "whatsapp":
                        identity_id = uuid.UUID(payload["channel_identity_id"])
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
            db.commit()
        publish(turn_id, {"type": "stopped" if stopped else "done",
                          "turn_id": str(turn_id), "message_id": str(assistant_id),
                          "status": outcome})
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
                if conversation is not None and existing is None:
                    assistant = chat_service.add_message(
                        db, message_id=assistant_id, conversation=conversation,
                        role="assistant", content=failure_text, turn_id=turn_id,
                        status="failed", source_channel=context.channel, commit=False,
                    )
                    if context.channel == "whatsapp":
                        whatsapp_service.schedule_delivery(
                            db, assistant_message_id=assistant.id,
                            channel_identity_id=uuid.UUID(payload["channel_identity_id"]),
                            content=failure_text,
                        )
                finished = chat_service.finish_job(
                    db, job.id, "failed", str(exc)[:1000],
                    claim_token=claim_token, commit=False,
                )
                if finished:
                    db.commit()
                    if context.channel == "whatsapp":
                        whatsapp_service.wake_worker()
                else:
                    db.rollback()
        except Exception:
            logger.exception("Could not persist failure outcome for chat job %s", job.id)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


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
