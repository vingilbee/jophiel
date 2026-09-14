"""Gmail MCP server.

A standalone MCP server exposing one user's Gmail over stdio. Run directly:

    GMAIL_ACCESS_TOKEN=ya29.… uv run python -m app.ai.tools.gmail.server

The backend spawns it per chat turn (see ``client.py``), but nothing here is
app-specific — point Claude Desktop or any other MCP client at the same command
and the tools work identically.

Auth model
----------
The server is deliberately *dumb about identity*: it reads a single Google
access token from the ``GMAIL_ACCESS_TOKEN`` environment variable and acts as
that mailbox for its whole lifetime. All the OAuth machinery — per-user grants,
refresh, revocation — stays in the parent process
(``app.ai.integrations.google``), which means this process never touches the
database and a leaked subprocess environment exposes at most one short-lived
token.

Tools return JSON strings so the model can quote ids (message_id, draft_id,
label ids) back into follow-up calls.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.ai.tools.gmail import gmail_api
from app.ai.tools.gmail.gmail_api import ACCESS_TOKEN_ENV

mcp = FastMCP(
    "gmail",
    instructions=(
        "Read, search, send, draft, and organize the user's Gmail. "
        "Message and draft ids returned by these tools are stable — pass them "
        "back verbatim in follow-up calls."
    ),
    # Quiet by default: this runs as a child of the API process, so its
    # per-request INFO logs would interleave with the backend's own.
    log_level="WARNING",
)


def _token() -> str:
    token = os.environ.get(ACCESS_TOKEN_ENV)
    if not token:
        raise RuntimeError(
            f"{ACCESS_TOKEN_ENV} is not set — this server must be launched with "
            "a Google access token in its environment."
        )
    return token


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


async def _resolve_label_ids(token: str, names: list[str]) -> list[str]:
    """Map human label names to Gmail label ids, case-insensitively.

    The model will happily say "Important" or "starred"; Gmail wants
    ``IMPORTANT`` / ``STARRED`` for system labels and an opaque
    ``Label_17``-style id for user labels. Anything we can't match is passed
    through unchanged, so callers that already have an id still work.
    """
    if not names:
        return []
    labels = await gmail_api.list_labels(token)
    by_name = {label["name"].lower(): label["id"] for label in labels}
    by_id = {label["id"].lower(): label["id"] for label in labels}
    return [by_name.get(n.lower()) or by_id.get(n.lower()) or n for n in names]


_READ_ONLY = ToolAnnotations(readOnlyHint=True)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=_READ_ONLY)
async def gmail_search(query: str, max_results: int = 10) -> str:
    """Search the user's mailbox and return matching message summaries.

    Uses Gmail's native search syntax. Examples:
      "is:unread"                          — unread mail
      "from:alice@example.com newer_than:7d" — recent mail from Alice
      "subject:invoice has:attachment"     — invoices with attachments
      "label:receipts after:2026/01/01"    — labelled mail since a date

    Returns summaries only (no bodies) — call gmail_read_message with an id to
    read one. Capped at 25 results.
    """
    results = await gmail_api.search_messages(
        _token(), query=query, max_results=max_results
    )
    if not results:
        return f"No messages matched the query: {query}"
    return _json(results)


@mcp.tool(annotations=_READ_ONLY)
async def gmail_read_message(message_id: str) -> str:
    """Read one message in full, including its decoded text body and attachment list.

    `message_id` comes from gmail_search. Long bodies are truncated.
    """
    return _json(await gmail_api.get_message(_token(), message_id=message_id))


@mcp.tool(annotations=_READ_ONLY)
async def gmail_list_labels() -> str:
    """List every label on the account — system labels (INBOX, UNREAD, STARRED,
    IMPORTANT, SPAM, TRASH) and the user's own. Use this to discover the exact
    label names available before applying one.
    """
    return _json(await gmail_api.list_labels(_token()))


@mcp.tool(annotations=_READ_ONLY)
async def gmail_get_profile() -> str:
    """Get the connected mailbox's email address and total message/thread counts."""
    return _json(await gmail_api.get_profile(_token()))


# --------------------------------------------------------------------------- #
# Send
# --------------------------------------------------------------------------- #
@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
async def gmail_send_email(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> str:
    """Send an email immediately. This is irreversible — it leaves the mailbox at once.

    Only call this after the user has seen the exact recipients, subject, and
    body and explicitly approved sending. If they haven't, use
    gmail_create_draft instead and show them the draft.
    """
    result = await gmail_api.send_message(
        _token(), to=to, subject=subject, body=body, cc=cc, bcc=bcc
    )
    return f"Sent. {_json(result)}"


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
async def gmail_reply_to_message(message_id: str, body: str) -> str:
    """Reply to an existing message, keeping it in the same thread.

    Recipients, subject ("Re: …") and threading headers are taken from the
    original message. Same rule as gmail_send_email: get explicit user approval
    of the body first.
    """
    token = _token()
    original = await gmail_api.get_message(token, message_id=message_id)

    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    reply_to = original["from"]
    if not reply_to:
        raise RuntimeError(
            f"Message {message_id} has no From header to reply to."
        )

    result = await gmail_api.send_message(
        token,
        to=[reply_to],
        subject=subject,
        body=body,
        thread_id=original["thread_id"],
        in_reply_to=original["message_id_header"] or None,
        references=(
            f"{original['references']} {original['message_id_header']}".strip()
            or None
        ),
    )
    return f"Reply sent to {reply_to}. {_json(result)}"


# --------------------------------------------------------------------------- #
# Drafts
# --------------------------------------------------------------------------- #
@mcp.tool()
async def gmail_create_draft(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> str:
    """Save an email as a draft without sending it.

    The safe default when composing on the user's behalf: show them the draft,
    then send it with gmail_send_draft once they approve.
    """
    result = await gmail_api.create_draft(
        _token(), to=to, subject=subject, body=body, cc=cc, bcc=bcc
    )
    return f"Draft saved. {_json(result)}"


@mcp.tool()
async def gmail_update_draft(
    draft_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> str:
    """Rewrite an existing draft. Replaces it entirely — pass every field, not just
    the ones that changed.
    """
    result = await gmail_api.update_draft(
        _token(),
        draft_id=draft_id,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
    )
    return f"Draft updated. {_json(result)}"


@mcp.tool(annotations=_READ_ONLY)
async def gmail_list_drafts(max_results: int = 10) -> str:
    """List saved drafts with their ids, recipients, and subjects."""
    drafts = await gmail_api.list_drafts(_token(), max_results=max_results)
    if not drafts:
        return "No drafts saved."
    return _json(drafts)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
async def gmail_send_draft(draft_id: str) -> str:
    """Send an existing draft. Irreversible — confirm with the user first."""
    result = await gmail_api.send_draft(_token(), draft_id=draft_id)
    return f"Draft sent. {_json(result)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def gmail_delete_draft(draft_id: str) -> str:
    """Delete a draft permanently. Only affects unsent drafts, never sent mail."""
    await gmail_api.delete_draft(_token(), draft_id=draft_id)
    return f"Draft {draft_id} deleted."


# --------------------------------------------------------------------------- #
# Organize
# --------------------------------------------------------------------------- #
@mcp.tool()
async def gmail_modify_labels(
    message_id: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> str:
    """Add and/or remove labels on a message.

    Labels may be given by name ("Receipts", "STARRED") or by id — names are
    resolved automatically. Call gmail_list_labels first if unsure what exists;
    this tool does not create missing labels.
    """
    token = _token()
    add_ids = await _resolve_label_ids(token, add_labels or [])
    remove_ids = await _resolve_label_ids(token, remove_labels or [])
    result = await gmail_api.modify_labels(
        token,
        message_id=message_id,
        add_label_ids=add_ids,
        remove_label_ids=remove_ids,
    )
    return _json(result)


@mcp.tool()
async def gmail_archive_message(message_id: str) -> str:
    """Archive a message — removes it from the inbox but keeps it searchable.

    Reversible: pass add_labels=["INBOX"] to gmail_modify_labels to un-archive.
    """
    result = await gmail_api.modify_labels(
        _token(), message_id=message_id, remove_label_ids=["INBOX"]
    )
    return f"Archived. {_json(result)}"


@mcp.tool()
async def gmail_mark_read(message_id: str, read: bool = True) -> str:
    """Mark a message read (read=True) or unread (read=False)."""
    result = await gmail_api.modify_labels(
        _token(),
        message_id=message_id,
        add_label_ids=[] if read else ["UNREAD"],
        remove_label_ids=["UNREAD"] if read else [],
    )
    return f"Marked as {'read' if read else 'unread'}. {_json(result)}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def gmail_trash_message(message_id: str) -> str:
    """Move a message to Trash. Recoverable for 30 days; never deletes permanently.

    Confirm with the user before trashing anything they didn't explicitly point at.
    """
    result = await gmail_api.trash_message(_token(), message_id=message_id)
    return f"Moved to Trash. {_json(result)}"


if __name__ == "__main__":
    # stdio transport: the parent process talks JSON-RPC over this process's
    # stdin/stdout, so nothing else may ever be printed to stdout here.
    mcp.run()
