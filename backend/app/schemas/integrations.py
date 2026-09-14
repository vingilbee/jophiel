"""Pydantic schemas for third-party integration endpoints."""
from datetime import datetime

from pydantic import BaseModel


class IntegrationOut(BaseModel):
    """One connectable provider, plus this user's connection to it.

    Everything the UI needs to render an integration card comes from here, so
    a new provider shows up in the app without any frontend change.
    """

    id: str
    name: str
    description: str
    category: str
    capabilities: list[str] = []

    # Server-side setup present (client id/secret). False ⇒ the UI should show
    # "unavailable" rather than a Connect button that can't work.
    configured: bool
    connected: bool
    # Which account is connected, e.g. an email address. Null when not connected.
    account_label: str | None = None
    connected_at: datetime | None = None
    scopes: list[str] = []


class AuthorizeOut(BaseModel):
    """Where to send the browser to start a provider's consent flow."""

    authorization_url: str
