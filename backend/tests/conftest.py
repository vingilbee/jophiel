"""Tests never reach the development database, or a model provider.

Every part of her runs (no feature flags), so code under test opens ``app.db.session.SessionLocal`` in places a
test doesn't patch (the memory view in a reply, for one). That factory is bound to ``DATABASE_URL``, the dev
database. Here it is rebound, for every test, to an address nothing listens on, so a stray session fails at once
instead of reading or writing real data; ``db_support.database`` rebinds it to the test's own schema. A test that
runs her mind scripts the model (``tests/chat_turn_support.py``): nothing here calls OpenAI.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.db import session as app_session

NOWHERE = "postgresql+psycopg://nobody@127.0.0.1:1/no_database"


@pytest.fixture(autouse=True)
def _no_dev_database():
    engine = create_engine(NOWHERE, connect_args={"connect_timeout": 1})
    before = app_session.SessionLocal.kw.get("bind")
    app_session.SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        app_session.SessionLocal.configure(bind=before)
        engine.dispose()


@pytest.fixture(autouse=True)
def _one_personality(monkeypatch):
    """Every test reads one fixed text personality, as a process does after loading its snapshot once
    (``services/personality.py``); tests/test_personality.py resets the cache to test the loading itself."""
    from app.services import personality

    monkeypatch.setattr(personality, "_snapshot", personality.Personality(
        "columbina", 1, "Jophiel is warm, curious and honest. She values privacy and the user's autonomy."))


@pytest.fixture(autouse=True)
def _no_learning_capture():
    """Captures go nowhere unless a test installs its own writer (tests/test_learning_capture.py)."""
    from app.services import learning_capture

    before = learning_capture.install(learning_capture.disabled())
    try:
        yield
    finally:
        learning_capture.install(before)


@pytest.fixture(autouse=True)
def _no_system_signals(monkeypatch):
    """Heartbeats and signals for the System page never reach the dev Redis (a test that checks them installs
    its own client)."""
    from app.services import system_signals, system_status

    monkeypatch.setattr(system_signals, "_client", lambda: None)
    monkeypatch.setattr(system_status, "_client", lambda: None)


@pytest.fixture(autouse=True)
def _no_presence(monkeypatch):
    """Presence (services/presence.py) never reaches the dev Redis; tests that check it install their own."""
    from app.services import presence

    monkeypatch.setattr(presence, "_client", lambda: None)


@pytest.fixture(autouse=True)
def _no_chat_warmup(monkeypatch):
    """The chat worker's warm-up and keep-warm pings (workers/chat.py ``start``) would reach OpenAI: off."""
    from app.workers import chat as chat_worker

    monkeypatch.setattr(chat_worker, "start", lambda: None)
