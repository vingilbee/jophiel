"""Deletion work for the shared worker; no separate process is required."""
from app.db.session import SessionLocal
from app.services.file_deletion import process_next_job


def run_once() -> bool:
    """Process at most one due deletion job."""
    with SessionLocal() as db:
        return process_next_job(db)
