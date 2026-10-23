"""WhatsApp Cloud API verification, webhooks, and account linking."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, validate_origin
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.services import whatsapp as whatsapp_service

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
# Uvicorn owns the API process logging configuration. Using its error logger
# keeps these operational diagnostics visible in hosted deploy logs without
# logging message bodies, phone numbers, link codes, or credentials.
logger = logging.getLogger("uvicorn.error")


class LinkCodeOut(BaseModel):
    code: str
    expires_at: datetime
    instruction: str


class LinkStatusOut(BaseModel):
    configured: bool
    linked: bool
    display_name: str | None = None
    wa_id_suffix: str | None = None


@router.get("/webhook", response_class=Response)
def verify_webhook(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    settings = get_settings()
    if (
        not settings.whatsapp_enabled
        or mode != "subscribe"
        or not token
        or not settings.whatsapp_verify_token
        or token != settings.whatsapp_verify_token
        or challenge is None
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid verification")
    return Response(content=challenge, media_type="text/plain")


def _message_text(message: dict) -> str | None:
    kind = message.get("type")
    if kind == "text":
        return (message.get("text") or {}).get("body")
    if kind == "button":
        return (message.get("button") or {}).get("text")
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or reply.get("id")
    # Preserve that the user contacted us without pretending media was text.
    if kind in {"image", "audio", "video", "document", "sticker", "location", "contacts"}:
        return f"[Unsupported WhatsApp {kind} message. Ask the user to send text instead.]"
    return None


def _timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    settings = get_settings()
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp is disabled")
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            too_large = int(declared_length) > settings.whatsapp_webhook_max_bytes
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid content length")
        if too_large:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Webhook too large")
    raw = await request.body()
    if len(raw) > settings.whatsapp_webhook_max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Webhook too large")
    if not whatsapp_service.verify_signature(raw, x_hub_signature_256):
        logger.warning("WhatsApp webhook rejected: invalid signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")
    if payload.get("object") != "whatsapp_business_account":
        logger.info("WhatsApp webhook ignored: unexpected object")
        return {"status": "ignored"}

    message_events = 0
    status_events = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            account_id = (value.get("metadata") or {}).get("phone_number_id")
            if not account_id or account_id != settings.whatsapp_phone_number_id:
                logger.warning(
                    "WhatsApp webhook ignored: phone_number_id mismatch "
                    "received_suffix=%s configured_suffix=%s",
                    str(account_id)[-6:] if account_id else "missing",
                    str(settings.whatsapp_phone_number_id)[-6:]
                    if settings.whatsapp_phone_number_id else "missing",
                )
                continue
            contacts = {
                contact.get("wa_id"): (contact.get("profile") or {}).get("name")
                for contact in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                sender = message.get("from")
                provider_id = message.get("id")
                content = _message_text(message)
                if not sender or not provider_id or content is None or not content.strip():
                    logger.warning(
                        "WhatsApp inbound event ignored: incomplete or unsupported payload "
                        "type=%s message_id_suffix=%s",
                        message.get("type", "missing"),
                        str(provider_id)[-8:] if provider_id else "missing",
                    )
                    continue
                result = whatsapp_service.ingest_message(
                    db,
                    provider_account_id=account_id,
                    provider_message_id=provider_id,
                    external_subject=sender,
                    content=content,
                    provider_timestamp=_timestamp(message.get("timestamp")),
                    display_name=contacts.get(sender),
                )
                message_events += 1
                log = logger.warning if result == "unlinked" else logger.info
                log(
                    "WhatsApp inbound processed: result=%s message_id_suffix=%s",
                    result,
                    str(provider_id)[-8:],
                )
            for delivery_status in value.get("statuses") or []:
                provider_id = delivery_status.get("id")
                if not provider_id:
                    continue
                errors = delivery_status.get("errors") or []
                error = str(errors[0])[:1000] if errors else None
                whatsapp_service.apply_delivery_status(
                    db,
                    provider_message_id=provider_id,
                    status=delivery_status.get("status", ""),
                    error=error,
                )
                status_events += 1
                logger.info(
                    "WhatsApp delivery status processed: status=%s "
                    "message_id_suffix=%s has_error=%s",
                    delivery_status.get("status", "missing"),
                    str(provider_id)[-8:],
                    bool(error),
                )
    db.commit()
    logger.info(
        "WhatsApp webhook accepted: message_events=%d status_events=%d",
        message_events,
        status_events,
    )
    return {"status": "accepted"}


@router.post(
    "/link-code", response_model=LinkCodeOut,
    dependencies=[Depends(validate_origin)],
)
def link_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LinkCodeOut:
    settings = get_settings()
    if not (
        settings.whatsapp_enabled
        and settings.whatsapp_phone_number_id
        and settings.whatsapp_access_token
        and settings.whatsapp_app_secret
        and settings.whatsapp_verify_token
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WhatsApp is not fully configured",
        )
    code, expires_at = whatsapp_service.create_link_code(db, user_id=current_user.id)
    destination = settings.whatsapp_display_phone_number or "the configured WhatsApp number"
    return LinkCodeOut(
        code=code,
        expires_at=expires_at,
        instruction=f"Send link {code} to {destination}.",
    )


@router.get("/link", response_model=LinkStatusOut)
def link_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LinkStatusOut:
    identity = whatsapp_service.linked_identity(db, user_id=current_user.id)
    settings = get_settings()
    return LinkStatusOut(
        configured=bool(
            settings.whatsapp_enabled
            and settings.whatsapp_phone_number_id
            and settings.whatsapp_access_token
            and settings.whatsapp_app_secret
            and settings.whatsapp_verify_token
        ),
        linked=identity is not None,
        display_name=identity.display_name if identity else None,
        wa_id_suffix=(identity.external_subject[-4:] if identity else None),
    )


@router.delete(
    "/link", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(validate_origin)],
)
def unlink(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    whatsapp_service.unlink(db, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
