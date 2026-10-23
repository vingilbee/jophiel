"""Source capture/recovery runs independently of mind. This is not learning preparation."""
from app.db.session import SessionLocal
from app.services import observation


def run_once() -> bool:
    if observation.enabled():
        with SessionLocal() as db:
            observation.tick(db, consume_memory=False)
    return False
