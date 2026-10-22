"""The coordinator's background work (services/coordinator.py): recall and thinking for user requests, one item
at a time, leased. Runs as its own runner thread when SHARED_STATE_PIPELINE_ENABLED."""
from __future__ import annotations

from app.services import coordinator


def run_once() -> bool:
    return coordinator.run_once()
