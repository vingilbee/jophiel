"""Phone pushes: settle one due row of the push outbox (services/push.py)."""
from app.services import phone_actions, push


def run_once() -> bool:
    phone_actions.retry_activities()
    return push.run_once()
