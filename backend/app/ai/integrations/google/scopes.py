"""OAuth scopes requested from Google.

Scopes are grouped by capability so it's obvious what a change to the consent
screen actually buys. Narrowing the integration later = drop a group from
``REQUESTED_SCOPES`` and have users reconnect.
"""
from __future__ import annotations

# Identity: who the grant belongs to. `openid` + `userinfo.email` let us show
# "connected as alice@gmail.com" in the UI and detect account switches.
OPENID = "openid"
USERINFO_EMAIL = "https://www.googleapis.com/auth/userinfo.email"

# Read and search mail. Subsumed by MODIFY below, but named here so a
# read-only deployment can request just this one.
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
# Read + write everything except permanent deletion: labels, archive,
# read/unread, trash.
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
# Create and manage drafts (and send them).
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
# Send mail directly.
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
DRIVE = "https://www.googleapis.com/auth/drive"

# What we actually ask for. GMAIL_READONLY is deliberately omitted: GMAIL_MODIFY
# already covers reading, and every extra scope adds a line to the consent screen.
REQUESTED_SCOPES: tuple[str, ...] = (
    OPENID,
    USERINFO_EMAIL,
    GMAIL_MODIFY,
    GMAIL_COMPOSE,
    GMAIL_SEND,
    DRIVE,
)

# At least one usable capability is enough to retain a connection. Each
# specialist is separately gated by its scope when the chat context is built.
CAPABILITY_SCOPES: frozenset[str] = frozenset({GMAIL_MODIFY, DRIVE})


def scope_string(scopes: tuple[str, ...] = REQUESTED_SCOPES) -> str:
    """Google wants scopes space-delimited in the authorization URL."""
    return " ".join(scopes)


def has_required_scopes(granted: set[str]) -> bool:
    return bool(granted & CAPABILITY_SCOPES)
