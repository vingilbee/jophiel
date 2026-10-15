"""Mind tick recording for the worker started with python -m app.workers."""
from app.services.mind_ticks import record_ticks


def run_once() -> bool:
    """Record one tick for each live game pack session (about once a second)."""
    record_ticks()
    return False
