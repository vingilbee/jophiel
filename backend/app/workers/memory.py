"""Memory job execution for the worker started with python -m app.workers."""
from app.db.session import SessionLocal
from app.services.long_term_memory import next_pending_job_id, process_job


def run_once() -> bool:
    """Process at most one memory job using thread-local database sessions."""
    with SessionLocal() as db:
        job_id = next_pending_job_id(db)
    if job_id is None:
        return False
    with SessionLocal() as db:
        process_job(db, job_id)
    return True


if __name__ == "__main__":
    # Keep the original command compatible; the package command is preferred.
    from app.workers.runner import run
    run()
