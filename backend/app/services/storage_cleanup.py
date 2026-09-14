"""Coordinate local writes and deletion across API/worker processes (Unix)."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import shutil
import uuid


@contextmanager
def user_storage_lock(root: Path, user_id: uuid.UUID | str):
    user_id = str(uuid.UUID(str(user_id)))
    control = root / ".cleanup"
    control.mkdir(parents=True, exist_ok=True)
    # Never unlink the lock: waiters must all lock the same inode.
    with (control / f"{user_id}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield control / f"{user_id}.deleted"
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def allow_user_write(root: Path, user_id: uuid.UUID | str):
    with user_storage_lock(root, user_id) as marker:
        if marker.exists():
            raise PermissionError("Account storage has been deleted")
        yield


def delete_user_files(root: Path, user_id: uuid.UUID | str) -> None:
    user_id = str(uuid.UUID(str(user_id)))
    with user_storage_lock(root, user_id) as marker:
        # Persist the fence before deletion, including on partial failure.
        marker.touch(exist_ok=True)
        try:
            shutil.rmtree(root / user_id)
        except FileNotFoundError:
            pass
