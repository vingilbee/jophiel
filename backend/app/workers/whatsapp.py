"""Dispatch WhatsApp input batches and deliver durable outbox records."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import exists, select, update
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.db.models import ChannelIdentity, MessageDelivery
from app.db.session import SessionLocal
from app.services import whatsapp

logger = logging.getLogger(__name__)


def _claim_delivery() -> tuple[str, str, str, str, int] | None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        earlier = aliased(MessageDelivery)
        delivery = db.scalar(
            select(MessageDelivery)
            .where(
                MessageDelivery.status.in_(("pending", "retry", "sending")),
                MessageDelivery.next_attempt_at <= now,
                ~exists(select(earlier.id).where(
                    earlier.assistant_message_id == MessageDelivery.assistant_message_id,
                    earlier.channel == MessageDelivery.channel,
                    earlier.part_index < MessageDelivery.part_index,
                    ~earlier.status.in_(("accepted", "sent", "delivered", "read")),
                )),
            )
            .order_by(
                MessageDelivery.next_attempt_at,
                MessageDelivery.created_at,
                MessageDelivery.part_index,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if delivery is None:
            db.rollback()
            return None
        identity = db.get(ChannelIdentity, delivery.channel_identity_id)
        if identity is None:
            delivery.status = "failed"
            delivery.last_error = "channel identity no longer exists"
            db.commit()
            return None
        token = secrets.token_urlsafe(32)
        delivery.status = "sending"
        delivery.claim_token = token
        delivery.attempts += 1
        delivery.next_attempt_at = now + timedelta(
            seconds=max(60, get_settings().whatsapp_delivery_timeout_seconds * 2)
        )
        db.commit()
        return (
            str(delivery.id), token, identity.external_subject,
            delivery.content, delivery.attempts,
        )


def _finish_delivery(
    delivery_id: str, token: str, *, status: str,
    provider_message_id: str | None = None, error: str | None = None,
    retry_after: float | None = None,
) -> None:
    values: dict = {
        "status": status,
        "provider_message_id": provider_message_id,
        "last_error": error,
        "claim_token": None,
    }
    if retry_after is not None:
        values["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
    with SessionLocal() as db:
        delivery = db.get(MessageDelivery, delivery_id)
        result = db.execute(
            update(MessageDelivery)
            .where(
                MessageDelivery.id == delivery_id,
                MessageDelivery.status == "sending",
                MessageDelivery.claim_token == token,
            )
            .values(**values)
        )
        if status in ("accepted", "failed") and result.rowcount and delivery is not None:
            from app.services.whatsapp import _settled
            _settled(db, delivery, status, error)
        if status == "failed" and result.rowcount and delivery is not None:
            db.execute(
                update(MessageDelivery)
                .where(
                    MessageDelivery.assistant_message_id == delivery.assistant_message_id,
                    MessageDelivery.channel == delivery.channel,
                    MessageDelivery.part_index > delivery.part_index,
                    MessageDelivery.status.in_(("pending", "retry")),
                )
                .values(
                    status="failed",
                    last_error="An earlier message part could not be delivered",
                    claim_token=None,
                )
            )
        db.commit()


def _send_one(
    delivery_id: str, token: str, to: str, content: str, attempts: int
) -> None:
    settings = get_settings()
    backoff = min(3600, 5 * 2 ** min(max(attempts - 1, 0), 10))

    def retry(error: str, delay: float) -> None:
        if attempts >= settings.whatsapp_delivery_max_attempts:
            _finish_delivery(
                delivery_id, token, status="failed",
                error=f"Retry limit reached: {error}"[:1000],
            )
        else:
            _finish_delivery(
                delivery_id, token, status="retry", error=error[:1000],
                retry_after=delay,
            )

    if not (
        settings.whatsapp_enabled
        and settings.whatsapp_access_token
        and settings.whatsapp_phone_number_id
    ):
        retry("WhatsApp delivery is not configured", max(60, backoff))
        return
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.whatsapp_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": content},
            },
            timeout=settings.whatsapp_delivery_timeout_seconds,
        )
        data = response.json() if response.content else {}
        if response.is_success:
            messages = data.get("messages") or []
            provider_id = messages[0].get("id") if messages else None
            if not provider_id:
                raise RuntimeError("Meta accepted the request without a message id")
            _finish_delivery(
                delivery_id, token, status="accepted",
                provider_message_id=provider_id,
            )
            return
        detail = str(data)[:1000]
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = backoff
            if response.headers.get("retry-after"):
                try:
                    retry_after = max(retry_after, float(response.headers["retry-after"]))
                except ValueError:
                    pass
            retry(detail, retry_after)
        else:
            _finish_delivery(delivery_id, token, status="failed", error=detail)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # A timeout is delivery-ambiguous. Retrying is at-least-once; the
        # provider does not offer a client idempotency key for this endpoint.
        retry(str(exc), backoff)


def run_once() -> bool:
    with SessionLocal() as db:
        job = whatsapp.dispatch_due_batch(db)
    if job is not None:
        return True
    claimed = _claim_delivery()
    if claimed is None:
        return False
    _send_one(*claimed)
    return True


if __name__ == "__main__":
    from app.workers.runner import run
    run()
