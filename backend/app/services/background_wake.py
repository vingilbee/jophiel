"""Post-commit hints only. Durable jobs, reports and due dates remain the source of truth."""
from sqlalchemy import event
from sqlalchemy.orm import Session


def after_commit(db):
    if db.info.pop("background_wake", False):
        from app.services import coordinator
        coordinator._wake()
        from app.services.coordinator_runtime import capture_wake
        capture_wake.set()
        from app.core.config import get_settings
        s = get_settings()
        if s.redis_url:
            try:
                import redis
                with redis.from_url(s.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2) as client:
                    with client.pipeline() as pipe:
                        pipe.rpush("macrovision:observation:wakeups", "wake")
                        pipe.ltrim("macrovision:observation:wakeups", -1, -1)
                        pipe.execute()
            except Exception:
                pass


def after_rollback(db):
    db.info.pop("background_wake", None)


def mark(db):
    db.info["background_wake"] = True


event.listen(Session, "after_commit", after_commit)
event.listen(Session, "after_rollback", after_rollback)
