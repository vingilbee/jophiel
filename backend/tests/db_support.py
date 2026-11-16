"""Real PostgreSQL for tests: a disposable schema per test, skipped without TEST_DATABASE_URL.

``database`` is a session factory on that schema. ``db_env`` adds isolated settings (no Redis, temporary
image/asset stores) and her seeded CoreMemory, which hard thoughts read for her character.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Conversation, CoreMemory, User

# Tests provide their own text profile; production is seeded by the migration.
def columbina_core_memory(**overrides) -> CoreMemory:
    values = {"name": "columbina", "version": 1, "schema_version": 2, "is_active": True,
              "profile": "Jophiel is warm, curious and honest. She values privacy and the user's autonomy."}
    values.update(overrides)
    return CoreMemory(**values)


@pytest.fixture
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL tests")
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    # Code that opens the app's own factory lands in this schema too (tests/conftest.py points it nowhere).
    from app.db import session as app_session
    before = app_session.SessionLocal.kw.get("bind")
    app_session.SessionLocal.configure(bind=engine)
    try:
        yield factory
    finally:
        app_session.SessionLocal.configure(bind=before)
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def db_env(database, monkeypatch, tmp_path):
    """The disposable schema, isolated settings for the stores, and her CoreMemory."""

    state = {"settings": get_settings().model_copy(update={
        "redis_url": None,
        "image_store_dir": str(tmp_path / "images"),
        "asset_store_dir": str(tmp_path / "assets"),
    })}

    def settings():
        return state["settings"]

    def configure(**updates):
        state["settings"] = state["settings"].model_copy(update=updates)
        return state["settings"]

    from app.services import asset_store, image_store, personality
    monkeypatch.setattr(personality, "SessionLocal", database)
    monkeypatch.setattr(personality, "_snapshot", None)
    for module in (image_store, asset_store):
        monkeypatch.setattr(module, "get_settings", settings)
    with database() as db:
        db.add(columbina_core_memory())
        db.commit()
    return type("DbEnv", (), {"db": database, "configure": staticmethod(configure)})


def create_user(db, *, user_id: uuid.UUID | None = None) -> User:
    user = User(id=user_id, name="Test user", email=f"{uuid.uuid4()}@example.com", hashed_password="unused")
    db.add(user)
    db.commit()
    return user


def create_conversation(db, user: User, *, conversation_id: uuid.UUID | None = None) -> Conversation:
    conversation = Conversation(id=conversation_id, user_id=user.id, title="test")
    db.add(conversation)
    db.commit()
    return conversation

