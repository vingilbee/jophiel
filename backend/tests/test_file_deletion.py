"""Cleanup tests. Set TEST_DATABASE_URL to a disposable PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from PIL import Image
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import FileDeletionJob, User
from app.services import asset_store, auth, file_deletion, image_store


@pytest.fixture
def stores(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        image_store_dir=str(tmp_path / "images"),
        asset_store_dir=str(tmp_path / "assets"), asset_max_upload_mb=1,
    )
    monkeypatch.setattr(image_store, "get_settings", lambda: settings)
    monkeypatch.setattr(asset_store, "get_settings", lambda: settings)
    return tmp_path


def write_files(user_id):
    image_store.save_pil(user_id, Image.new("RGB", (8, 8)))
    asset_store.save_bytes(user_id, b"private document", filename="private.txt")


def test_cleanup_removes_only_owner_files_and_blocks_late_writes(stores):
    owner, other = uuid.uuid4(), uuid.uuid4()
    write_files(owner)
    write_files(other)
    for store in (image_store, asset_store):
        store.delete_user_files(owner)
        store.delete_user_files(owner)  # Safe retry after a partial job/crash.
        assert not (store._root() / str(owner)).exists()
        assert list((store._root() / str(other)).iterdir())
        with pytest.raises(ValueError):
            store.delete_user_files("../../outside")
    with pytest.raises(PermissionError):
        image_store.save_pil(owner, Image.new("RGB", (8, 8)))
    with pytest.raises(PermissionError):
        asset_store.save_bytes(owner, b"late", filename="late.txt")


def test_cleanup_waits_for_inflight_writer(stores):
    from app.services.storage_cleanup import allow_user_write

    user_id = uuid.uuid4()
    root = image_store._root()
    started = Event()

    def cleanup():
        started.set()
        image_store.delete_user_files(user_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with allow_user_write(root, user_id):
            future = pool.submit(cleanup)
            assert started.wait(2)
            directory = root / str(user_id)
            directory.mkdir()
            (directory / "inflight.png").write_bytes(b"private")
            assert not future.done()
        future.result(timeout=5)
    assert not directory.exists()


def test_session_cleanup_keeps_other_users(monkeypatch):
    import json

    owner, other = uuid.uuid4(), uuid.uuid4()
    values = {
        "session:owner": json.dumps({"user_id": str(owner)}),
        "session:other": json.dumps({"user_id": str(other)}),
        "session:expired": None,
    }
    redis = Mock()
    redis.scan_iter.return_value = list(values)
    redis.get.side_effect = values.get
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    file_deletion.delete_user_sessions(owner)
    redis.delete.assert_called_once_with("session:owner")


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL cleanup tests")
    schema = "cleanup_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(file_deletion, "wake_worker", Mock())
    try:
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def create_account(db):
    user = User(name="Cleanup test", email=f"{uuid.uuid4()}@example.com", hashed_password="unused")
    db.add(user)
    db.commit()
    return user


def test_account_deletion_durably_enqueues_cleanup(database, stores, monkeypatch):
    sessions = Mock()
    monkeypatch.setattr(file_deletion, "delete_user_sessions", sessions)
    with database() as db:
        user = create_account(db)
        user_id = user.id
        write_files(user_id)
        auth.delete_user(db, user)
    # New DB session models worker/API restart: job survived account deletion.
    with database() as db:
        assert db.get(User, user_id) is None
        job = db.scalar(select(FileDeletionJob))
        assert job.user_id == user_id and job.status == "pending"
        assert (image_store._root() / str(user_id)).exists()
        assert file_deletion.process_next_job(db)
        db.refresh(job)
        assert job.status == "completed" and job.attempts == 1
        assert not (image_store._root() / str(user_id)).exists()
        assert not (asset_store._root() / str(user_id)).exists()
        assert not file_deletion.process_next_job(db)
    sessions.assert_called_once_with(user_id)


def test_failed_transaction_preserves_account_without_job(database, monkeypatch):
    with database() as db:
        user = create_account(db)
        user_id = user.id
        monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(RuntimeError):
            auth.delete_user(db, user)
        db.rollback()
    with database() as db:
        assert db.get(User, user_id) is not None
        assert db.scalar(select(FileDeletionJob)) is None
    file_deletion.wake_worker.assert_not_called()


def test_delete_api_accepts_cleanup_and_immediately_denies_old_cookie(database, stores, monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.auth import router
    from app.core.config import get_settings
    from app.db.session import get_db

    with database() as db:
        user = create_account(db)
        user_id = user.id
        write_files(user_id)
    redis = Mock()
    redis.get.return_value = json.dumps({"user_id": str(user_id)})
    monkeypatch.setattr(auth, "get_redis", lambda: redis)

    def request_db():
        with database() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = request_db
    settings = get_settings()
    headers = {"Origin": settings.cors_origins[0], "Cookie": f"{settings.session_cookie_name}=test-token"}
    with TestClient(app) as client:
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        response = client.delete("/api/auth/me", headers=headers)
        assert response.status_code == 202
        assert response.json() == {"status": "deletion_pending"}
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert client.get("/api/auth/me", headers=headers).status_code == 401
    # The response acknowledges durable work, not completed physical cleanup.
    assert (image_store._root() / str(user_id)).exists()
    with database() as db:
        assert db.scalar(select(FileDeletionJob)).status == "pending"


def test_partial_cleanup_retries_and_eventually_completes(database, stores, monkeypatch):
    monkeypatch.setattr(file_deletion, "delete_user_sessions", Mock())
    user_id = uuid.uuid4()
    write_files(user_id)
    original_delete = asset_store.delete_user_files
    with database() as db:
        job = file_deletion.schedule_deletion(db, user_id)
        db.commit()
        monkeypatch.setattr(asset_store, "delete_user_files", Mock(side_effect=PermissionError("denied")))
        assert file_deletion.process_next_job(db)
        db.refresh(job)
        assert job.status == "pending" and job.attempts == 1
        assert job.last_error == "PermissionError"
        assert not (image_store._root() / str(user_id)).exists()
        assert (asset_store._root() / str(user_id)).exists()
        assert not file_deletion.process_next_job(db)  # Backoff prevents hot looping.
        job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(asset_store, "delete_user_files", original_delete)
    with database() as db:
        assert file_deletion.process_next_job(db)
        job = db.scalar(select(FileDeletionJob))
        assert job.status == "completed" and job.attempts == 2
        assert job.last_error is None


def test_concurrent_claim_skips_locked_job_and_recovers_after_rollback(database, monkeypatch):
    monkeypatch.setattr(image_store, "delete_user_files", Mock())
    monkeypatch.setattr(asset_store, "delete_user_files", Mock())
    cleanup = Mock()
    monkeypatch.setattr(file_deletion, "delete_user_sessions", cleanup)
    with database() as db:
        file_deletion.schedule_deletion(db, uuid.uuid4())
        db.commit()
    with database() as claimant, database() as competitor:
        claimant.scalar(select(FileDeletionJob).with_for_update())
        assert not file_deletion.process_next_job(competitor)
        cleanup.assert_not_called()
        claimant.rollback()  # Same lock release/rollback as losing a worker connection.
        assert file_deletion.process_next_job(competitor)
    cleanup.assert_called_once()


def test_memory_worker_processes_one_job(monkeypatch):
    from app.workers import memory

    db = Mock()
    context = Mock()
    context.__enter__ = Mock(return_value=db)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(memory, "SessionLocal", lambda: context)
    job_id = uuid.uuid4()
    monkeypatch.setattr(memory, "next_pending_job_id", Mock(return_value=job_id))
    process = Mock()
    monkeypatch.setattr(memory, "process_job", process)
    assert memory.run_once()
    process.assert_called_once_with(db, job_id)
    monkeypatch.setattr(memory, "next_pending_job_id", Mock(return_value=None))
    assert not memory.run_once()
    assert process.call_count == 1
