"""Tests never reach the development database, or the decision model served on this machine.

Every part of her runs (no feature flags), so code under test opens ``app.db.session.SessionLocal`` in places a
test doesn't patch (the memory view in a reply, for one). That factory is bound to ``DATABASE_URL``, the dev
database. Here it is rebound, for every test, to an address nothing listens on, so a stray session fails at once
instead of reading or writing real data; ``db_support.database`` rebinds it to the test's own schema. Student 2's
HTTP call is refused the same way: a test that wants a decision scripts one (``student_two.decide(live, ask=...)``).
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
def _no_served_student(monkeypatch):
    from app.services import student_two

    def refuse(model, text):
        raise ConnectionRefusedError("no decision model in tests")

    monkeypatch.setattr(student_two, "_ask", refuse)
    monkeypatch.setattr(student_two, "_ask_teacher", refuse)
    monkeypatch.setattr(student_two.get_settings(), "cognition_decision_provider", "student")


@pytest.fixture(autouse=True)
def _no_learning_capture():
    """Captures go nowhere unless a test installs its own writer (tests/test_learning_capture.py)."""
    from app.services import learning_capture

    before = learning_capture.install(learning_capture.disabled())
    try:
        yield
    finally:
        learning_capture.install(before)
