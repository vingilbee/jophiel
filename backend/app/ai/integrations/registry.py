"""Registry of connectable third-party providers.

Integrations are a *list*, not a special case per provider: the frontend renders
whatever this registry contains, so adding Slack/Notion/Outlook later is a new
``IntegrationProvider`` entry plus its own OAuth module — no route or UI changes.

A provider is described by metadata (what the user sees) plus four callables
that say how to check, start, and tear down a connection. Providers register
themselves at import time; ``app.ai.integrations`` imports each one so the
registry is populated as soon as the package is loaded.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Connection:
    """An established link between one of our users and a provider account."""

    # Human-readable identifier of the connected account, e.g. an email
    # address. None when the provider didn't tell us (or we failed to ask).
    account_label: str | None
    connected_at: datetime
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationProvider:
    """Everything the API needs to expose one connectable provider."""

    id: str
    name: str
    # One line, user-facing: what connecting this gets them.
    description: str
    # Grouping label for the UI, e.g. "Email".
    category: str
    # Bullet points describing what the assistant can do once connected.
    capabilities: tuple[str, ...] = ()

    # Is the *server* set up for this provider (client id/secret present)? A
    # provider can be registered but unavailable, which the UI shows as
    # "unavailable" rather than offering a flow that would fail.
    is_configured: Callable[[], bool] = lambda: False
    # Current connection for a user, or None.
    get_connection: Callable[[Session, uuid.UUID], Connection | None] = (
        lambda db, user_id: None
    )
    # Where to send the browser to begin the consent flow.
    build_authorize_url: Callable[[uuid.UUID], str] = field(
        default=lambda user_id: (_ for _ in ()).throw(NotImplementedError)
    )
    # Revoke upstream (best-effort) and delete our copy. Async because
    # revocation is an HTTP call.
    disconnect: Callable[[Session, uuid.UUID], Awaitable[None]] = field(
        default=lambda db, user_id: (_ for _ in ()).throw(NotImplementedError)
    )


_PROVIDERS: dict[str, IntegrationProvider] = {}


def register(provider: IntegrationProvider) -> IntegrationProvider:
    """Add a provider to the registry (idempotent — re-import replaces)."""
    _PROVIDERS[provider.id] = provider
    return provider


def all_providers() -> list[IntegrationProvider]:
    """Every registered provider, ordered by name for a stable UI."""
    return sorted(_PROVIDERS.values(), key=lambda p: p.name)


def get_provider(provider_id: str) -> IntegrationProvider | None:
    return _PROVIDERS.get(provider_id)
