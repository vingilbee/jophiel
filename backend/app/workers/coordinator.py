"""Cheap scheduling passes; slow work runs in bounded, separately leased executors."""
from app.services import coordinator


def run_once() -> bool:
    return coordinator.run_once()
