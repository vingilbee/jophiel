"""Her attempts at teaching herself a move (stage H1), for the worker started with python -m app.workers."""
from app.services.game_practice import run_once as _run_once


def run_once() -> bool:
    """Run at most one queued attempt to its end."""
    return _run_once()
