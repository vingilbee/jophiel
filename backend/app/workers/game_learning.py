"""Game reflection jobs for the worker started with python -m app.workers."""
from app.db.session import SessionLocal
from app.services.game_learning import next_pending_job_id, process_job


def run_once() -> bool:
    """Reflect on at most one ended game session."""
    with SessionLocal() as db:
        job_id = next_pending_job_id(db)
    if job_id is None:
        return False
    with SessionLocal() as db:
        process_job(db, job_id)
    return True
