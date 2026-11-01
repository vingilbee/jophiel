"""Memory job execution for the worker started with python -m app.workers."""
from app.db.session import SessionLocal
from app.services import memory_corrections, memory_forget, memory_reconcile, memory_upkeep, thought_memory
from app.services.long_term_memory import next_pending_job_id, process_job


def run_once() -> bool:
    """Process at most one memory job using thread-local database sessions; with none waiting, carry out at
    most one forget request (services/memory_forget.py), else reconcile at most one revised fact with working
    memory (services/memory_reconcile.py), else apply at most one correction a job handed to the memory manager
    (services/memory_corrections.py), else take in at most one of her finished thoughts
    (services/thought_memory.py), else, every few minutes, look at one user's facts for duplicates
    (services/memory_upkeep.py). Held items are settled before a thought can restate them."""
    with SessionLocal() as db:
        job_id = next_pending_job_id(db)
    if job_id is None:
        return (memory_forget.run_once() or memory_reconcile.run_once() or memory_corrections.run_once()
                or thought_memory.run_once() or memory_upkeep.run_once())
    with SessionLocal() as db:
        process_job(db, job_id)
    return True


if __name__ == "__main__":
    # Keep the original command compatible; the package command is preferred.
    from app.workers.runner import run
    run()
