"""Third-party integrations the AI layer can act through.

Each subpackage owns the *connection* to an external provider — OAuth, token
lifecycle, and a way to hand a live credential to whoever needs it. The tools
that actually call those providers live in ``app.ai.tools``; keeping the two
apart means a single integration (e.g. Google) can back several tool sets
(Gmail today, Calendar/Drive later) without duplicating auth.

Providers are listed in a registry (``registry.py``) so the API and the UI can
treat integrations as data: adding one means writing its OAuth module and
registering a descriptor, not touching routes or the frontend.

Current integrations:
    - google : OAuth 2.0 authorization-code flow, per-user refresh tokens
"""
# Importing each provider module registers it. Keep these imports last and
# side-effecting on purpose — the registry must be populated before the API
# layer reads it.
from app.ai.integrations import registry
from app.ai.integrations.google import provider as _google_provider  # noqa: F401

__all__ = ["registry"]
