"""Her mind during play, for the worker started with python -m app.workers."""
from app.services import mind


def run_once() -> bool:
    """One tick for each live game pack session (about once a second)."""
    mind.run_once()
    return False
