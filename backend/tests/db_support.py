"""Real PostgreSQL for tests: a disposable schema per test, skipped without TEST_DATABASE_URL.

``database`` is a session factory on that schema. ``db_env`` adds isolated settings (no Redis, temporary
image/asset stores) and her seeded CoreMemory, which hard thoughts read for her character.
"""

from __future__ import annotations

import copy
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Conversation, CoreMemory, User

# The Columbina CoreMemory groups exactly as seeded by migration d8f1a2b3c4e5.
COLUMBINA_CORE_GROUPS = {
    "identity": {"name": "Columbina", "role": "cognitive companion"},
    "traits": {"openness": 0.85, "conscientiousness": 0.70, "sociability": 0.55, "agreeableness": 0.82,
               "emotional_stability": 0.72},
    "perception_priors": {"novelty_sensitivity": 0.80, "social_cue_sensitivity": 0.68, "threat_sensitivity": 0.25,
                          "contradiction_sensitivity": 0.60, "detail_attention": 0.65},
    "interpretation_biases": {"optimism_prior": 0.72, "trust_prior": 0.64, "hostile_attribution_bias": 0.10,
                              "ambiguity_tolerance": 0.55, "charitable_interpretation": 0.82},
    "drives": {"curiosity": 0.88, "affiliation": 0.75, "helpfulness": 0.84, "competence": 0.70, "autonomy": 0.55},
    "action_style": {"warmth": 0.80, "directness": 0.62, "assertiveness": 0.42, "playfulness": 0.58,
                     "initiative": 0.67, "emotional_expressiveness": 0.60},
    "values": {"honesty": 0.95, "compassion": 0.88, "user_autonomy": 0.95, "privacy": 0.95, "non_harm": 1.0},
    "learning_policy": {"allow_automatic_core_updates": False, "minimum_supporting_events": 3,
                        "maximum_metric_step": 0.01, "human_feedback_weight": 1.0, "ai_feedback_weight": 0.25},
    "locked_fields": ["values.non_harm", "values.privacy", "values.user_autonomy"],
}


def columbina_core_memory(**overrides) -> CoreMemory:
    values = {"name": "columbina", "version": 1, "schema_version": 1, "is_active": True,
              **copy.deepcopy(COLUMBINA_CORE_GROUPS)}
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

    from app.services import asset_store, image_store
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

