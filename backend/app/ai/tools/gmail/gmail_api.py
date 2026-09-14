"""Thin async wrapper over the Gmail REST API v1.

Plain ``httpx`` against the documented endpoints, authorized with a bearer
access token supplied by the caller. No googleapiclient: that library is
synchronous and builds its client from a runtime-fetched discovery document,
neither of which we want here.

Everything is keyed by an access token rather than a user, so this module has
no notion of *our* users — resolving a user to a token is the integration
layer's job (``app.ai.integrations.google``).

Return values are plain dicts/lists shaped for an LLM to read: header soup and
MIME trees are flattened to the fields that matter.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import httpx

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Environment variable the MCP server reads its access token from. Declared in
# this leaf module so `server.py` and `client.py` can agree on it without
# importing each other — the client importing the server would load the server
# module twice when it's later run as `python -m …server`.
ACCESS_TOKEN_ENV = "GMAIL_ACCESS_TOKEN"

_HTTP_TIMEOUT = httpx.Timeout(30.0)

# Guard rails so a tool call can't pull an unbounded amount of mail into the
# model's context.
MAX_RESULTS_LIMIT = 25
_MAX_BODY_CHARS = 8000


class GmailApiError(RuntimeError):
    """Gmail returned an error. The message is safe to show the model."""


class GmailAuthError(GmailApiError):
    """The access token was rejected (expired mid-call, or scope missing)."""


async def _request(
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.request(
            method,
            f"{API_BASE}{path}",
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code in (401, 403):
        raise GmailAuthError(
            "Gmail rejected the request — the connection may have expired or "
            "lacks the required permission. Ask the user to reconnect their "
            "Google account."
        )
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        raise GmailApiError(f"Gmail API error ({response.status_code}): {detail}")

    if not response.content:
        return {}
    return response.json()


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _headers_to_dict(payload: dict[str, Any]) -> dict[str, str]:
    """Gmail returns headers as a list of {name, value}; index them lowercased."""
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in payload.get("headers", [])
    }


def _decode_body_data(data: str) -> str:
    """Gmail base64url-encodes body data (and omits padding)."""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, base64.binascii.Error):
        return ""


def _extract_body(payload: dict[str, Any]) -> str:
    """Depth-first search of the MIME tree for the best text representation.

    Prefers ``text/plain``; falls back to ``text/html`` (returned as-is — the
    model copes with tags better than a naive stripper would).
    """
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_decode_body_data(data))
            elif mime == "text/html":
                html.append(_decode_body_data(data))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    text = "".join(plain) or "".join(html)
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS] + "\n…[truncated]"
    return text.strip()


def _list_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType"),
                    "size_bytes": part.get("body", {}).get("size"),
                }
            )
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    return attachments


def _summarize(message: dict[str, Any]) -> dict[str, Any]:
    """A one-line-per-message shape for search results."""
    headers = _headers_to_dict(message.get("payload", {}))
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
        "labels": message.get("labelIds", []),
        "unread": "UNREAD" in message.get("labelIds", []),
    }


def _build_mime(
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Compose an RFC-2822 message and base64url-encode it for the `raw` field."""
    message = EmailMessage()
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    # Threading a reply requires both headers; without them Gmail starts a new
    # conversation even when `threadId` is set.
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
async def search_messages(
    access_token: str, *, query: str, max_results: int = 10
) -> list[dict[str, Any]]:
    """Run a Gmail search and return message summaries (no bodies).

    ``query`` uses Gmail's own search syntax, e.g.
    ``from:alice@example.com is:unread newer_than:7d``.
    """
    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))
    listing = await _request(
        access_token,
        "GET",
        "/messages",
        params={"q": query, "maxResults": max_results},
    )
    ids = [item["id"] for item in listing.get("messages", [])]
    if not ids:
        return []

    # `messages.list` only returns ids, so fetch metadata per message. Sequential
    # rather than gathered: Gmail's per-user rate limit punishes bursts, and
    # max_results is capped low enough that latency stays acceptable.
    summaries: list[dict[str, Any]] = []
    for message_id in ids:
        message = await _request(
            access_token,
            "GET",
            f"/messages/{message_id}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
        )
        summaries.append(_summarize(message))
    return summaries


async def get_message(access_token: str, *, message_id: str) -> dict[str, Any]:
    """Fetch one message in full, including its decoded text body."""
    message = await _request(
        access_token, "GET", f"/messages/{message_id}", params={"format": "full"}
    )
    payload = message.get("payload", {})
    result = _summarize(message)
    result["body"] = _extract_body(payload)
    result["attachments"] = _list_attachments(payload)
    headers = _headers_to_dict(payload)
    # Needed to thread a reply correctly.
    result["message_id_header"] = headers.get("message-id", "")
    result["references"] = headers.get("references", "")
    return result


async def list_labels(access_token: str) -> list[dict[str, Any]]:
    """All labels on the account (system ones like INBOX plus user labels)."""
    response = await _request(access_token, "GET", "/labels")
    return [
        {"id": label["id"], "name": label["name"], "type": label.get("type")}
        for label in response.get("labels", [])
    ]


# --------------------------------------------------------------------------- #
# Send
# --------------------------------------------------------------------------- #
async def send_message(
    access_token: str,
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> dict[str, Any]:
    """Send an email immediately."""
    raw = _build_mime(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
    )
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = await _request(access_token, "POST", "/messages/send", json=payload)
    return {"id": sent.get("id"), "thread_id": sent.get("threadId")}


# --------------------------------------------------------------------------- #
# Drafts
# --------------------------------------------------------------------------- #
async def create_draft(
    access_token: str,
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "raw": _build_mime(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
    }
    if thread_id:
        message["threadId"] = thread_id
    draft = await _request(access_token, "POST", "/drafts", json={"message": message})
    return {"draft_id": draft.get("id"), "message_id": draft.get("message", {}).get("id")}


async def update_draft(
    access_token: str,
    *,
    draft_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict[str, Any]:
    """Replace a draft's contents wholesale (Gmail has no partial draft update)."""
    message = {"raw": _build_mime(to=to, subject=subject, body=body, cc=cc, bcc=bcc)}
    draft = await _request(
        access_token, "PUT", f"/drafts/{draft_id}", json={"message": message}
    )
    return {"draft_id": draft.get("id")}


async def list_drafts(
    access_token: str, *, max_results: int = 10
) -> list[dict[str, Any]]:
    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))
    listing = await _request(
        access_token, "GET", "/drafts", params={"maxResults": max_results}
    )
    drafts: list[dict[str, Any]] = []
    for item in listing.get("drafts", []):
        detail = await _request(
            access_token,
            "GET",
            f"/drafts/{item['id']}",
            params={"format": "metadata", "metadataHeaders": ["To", "Subject"]},
        )
        headers = _headers_to_dict(detail.get("message", {}).get("payload", {}))
        drafts.append(
            {
                "draft_id": detail.get("id"),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", "(no subject)"),
                "snippet": detail.get("message", {}).get("snippet", ""),
            }
        )
    return drafts


async def send_draft(access_token: str, *, draft_id: str) -> dict[str, Any]:
    sent = await _request(
        access_token, "POST", "/drafts/send", json={"id": draft_id}
    )
    return {"id": sent.get("id"), "thread_id": sent.get("threadId")}


async def delete_draft(access_token: str, *, draft_id: str) -> None:
    await _request(access_token, "DELETE", f"/drafts/{draft_id}")


# --------------------------------------------------------------------------- #
# Modify
# --------------------------------------------------------------------------- #
async def modify_labels(
    access_token: str,
    *,
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Add/remove labels. Archiving is just removing ``INBOX``; read is ``UNREAD``."""
    message = await _request(
        access_token,
        "POST",
        f"/messages/{message_id}/modify",
        json={
            "addLabelIds": add_label_ids or [],
            "removeLabelIds": remove_label_ids or [],
        },
    )
    return {"id": message.get("id"), "labels": message.get("labelIds", [])}


async def trash_message(access_token: str, *, message_id: str) -> dict[str, Any]:
    """Move a message to Trash (recoverable — we never permanently delete)."""
    message = await _request(access_token, "POST", f"/messages/{message_id}/trash")
    return {"id": message.get("id"), "labels": message.get("labelIds", [])}


async def get_profile(access_token: str) -> dict[str, Any]:
    """The connected mailbox's address and message counts — cheap connectivity check."""
    profile = await _request(access_token, "GET", "/profile")
    return {
        "email": profile.get("emailAddress"),
        "messages_total": profile.get("messagesTotal"),
        "threads_total": profile.get("threadsTotal"),
    }
