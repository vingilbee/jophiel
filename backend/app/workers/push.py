"""Phone pushes: settle one due row of the push outbox (services/push.py)."""
from app.services import push


def run_once() -> bool:
    return push.run_once()
