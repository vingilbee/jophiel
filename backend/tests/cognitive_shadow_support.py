"""Shared inputs for the Durable Live-Shadow Trace V0 tests.

The personality baseline below is **test data only**. It exists so the
completed path can be exercised; it is not a reviewed production baseline and
is never shipped as configuration.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ai.Cognitive.system.v0.contracts.actions import ActionPersonalityProfile
from app.ai.Cognitive.system.v0.contracts.safety import (
    APPLICATION_SAFETY_POLICY_VERSION, SafetyDecisionProvenance, SafetyDisposition,
)
from app.ai.Cognitive.system.v0.dynamic_state.models import PersonalityProfile
from app.ai.Cognitive.system.v0.interpretation_v0.models import InterpretationBiasProfile
from app.ai.Cognitive.system.v0.live_shadow.models import (
    ALWAYS_OMITTED, CAPABILITY_MAP_VERSION, CAPTURE_PRODUCER_VERSION, MEMORY_RETRIEVAL_POLICY_VERSION,
    CapturedCapabilities, CapturedMessage, CapturedResponseObligation, CapturedSafetyDecision,
    CognitiveShadowCapture, ConfiguredLanguage, CoreMemoryLineage, GenerationPath, OmittedInput,
    PersonalityMissingReason, ShadowPersonalityProjection, ShadowSourceChannel, capture_id_for,
    core_memory_ref_for,
)
from app.core.config import get_settings
from app.db.base import Base
from app.db.models import ChatJob, ChatMessage, Conversation, CoreMemory, User

CORE_REF = core_memory_ref_for("columbina", 1)

# The Columbina CoreMemory groups exactly as seeded by migration d8f1a2b3c4e5
# (test_personality_runtime_v0_mapper pins this against the real migration).
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
    import copy
    values = {"name": "columbina", "version": 1, "schema_version": 1, "is_active": True,
              **copy.deepcopy(COLUMBINA_CORE_GROUPS)}
    values.update(overrides)
    return CoreMemory(**values)


def baseline_projection(core_ref: str = CORE_REF, *, state_mapper: str = "test-baseline-state-v1") -> ShadowPersonalityProjection:
    return ShadowPersonalityProjection(
        projection_kind="reviewed_baseline",
        projection_id="test-baseline",
        projection_version="test-baseline-v1",
        scope="application_baseline",
        review_ref="test-only-review",
        provenance="test fixture baseline; not a reviewed production baseline",
        core_memory_ref=core_ref,
        interpretation_biases=InterpretationBiasProfile(
            profile_id="test-interpretation", compiler_version="test-baseline-v1",
            source_core_memory_ref=core_ref, benign_prior=0.5, trust_prior=0.5, threat_sensitivity=0.5,
            confirmation_tendency=0.5, self_attribution_tendency=0.5, ambiguity_tolerance=0.5,
            social_affiliation_prior=0.5,
        ),
        state_personality=PersonalityProfile(profile_id="test-state", mapper_version=state_mapper),
        action_personality=ActionPersonalityProfile(
            profile_id="test-action", mapper_version="action-personality-unknown-v0",
            source_core_memory_ref=core_ref, status="unknown",
        ),
    )


def make_capture(
    *,
    contents: tuple[tuple[str, str], ...] = (("user", "How much protein is in two eggs?"),),
    turn_id: str | None = None,
    user_ref: str | None = None,
    conversation_ref: str | None = None,
    message_ids: tuple[str, ...] | None = None,
    previous_user_message_id: str | None = None,
    personality: ShadowPersonalityProjection | None | str = "baseline",
    language: str = "en",
    capability_keys: tuple[str, ...] = ("nutrition_image_estimation",),
    **overrides,
) -> CognitiveShadowCapture:
    """A pure, valid capture for compiler/contract tests (no database)."""

    turn_id = turn_id or str(uuid.uuid4())
    message_ids = message_ids or tuple(str(uuid.uuid4()) for _ in contents)
    projection = baseline_projection() if personality == "baseline" else personality
    omitted = set(ALWAYS_OMITTED) | {
        OmittedInput.MEMORY_PROJECTION, OmittedInput.RELATIONSHIP_PROJECTION,
        OmittedInput.APPLICATION_SAFETY_POLICY,
    }
    if projection is None:
        omitted.add(OmittedInput.PERSONALITY_PROJECTION)
    values = dict(
        capture_id=capture_id_for(turn_id),
        producer_version=CAPTURE_PRODUCER_VERSION,
        user_ref=user_ref or str(uuid.uuid4()),
        conversation_ref=conversation_ref or str(uuid.uuid4()),
        turn_id=turn_id,
        chat_job_ref=str(uuid.uuid4()),
        generation_path=GenerationPath.CHAT_WORKER,
        focus_message_id=message_ids[-1],
        previous_user_message_id=previous_user_message_id,
        messages=tuple(
            CapturedMessage(message_id=message_id, role=role, content=content)
            for message_id, (role, content) in zip(message_ids, contents)
        ),
        context_truncated=False,
        source_channel=ShadowSourceChannel.WEB,
        language=ConfiguredLanguage(tag=language),
        attachments_available=True,
        capabilities=CapturedCapabilities(mapping_version=CAPABILITY_MAP_VERSION, capability_keys=capability_keys),
        safety_decision=CapturedSafetyDecision(
            policy_version=APPLICATION_SAFETY_POLICY_VERSION, disposition=SafetyDisposition.UNAVAILABLE,
            provenance=SafetyDecisionProvenance.RUNTIME_UNAVAILABLE,
            producer_ref=f"{CAPTURE_PRODUCER_VERSION}:application-safety-policy-unavailable",
        ),
        response_obligation=CapturedResponseObligation(evidence_ref=f"chat-turn:{turn_id}:user-message:{message_ids[-1]}"),
        memory_retrieval_policy_version=MEMORY_RETRIEVAL_POLICY_VERSION,
        core_memory=CoreMemoryLineage(core_memory_ref=CORE_REF, name="columbina", version=1, schema_version=1),
        personality=projection,
        personality_missing_reason=None if projection is not None else PersonalityMissingReason.NO_ACCEPTED_SOURCE,
        omitted_inputs=tuple(sorted(omitted)),
        sampling_policy_version="shadow-sampling-v0.1",
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
    values.update(overrides)
    return CognitiveShadowCapture(**values)


# ---------------------------------------------------------------------------
# Real PostgreSQL (disposable schema), skipped without TEST_DATABASE_URL
# ---------------------------------------------------------------------------

@pytest.fixture
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL cognitive-shadow tests")
    schema = "shadow_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def shadow_env(database, monkeypatch, tmp_path):
    """Wire the shadow service and worker to the disposable schema and settings."""

    from app.services import cognitive_shadow as service
    from app.workers import cognitive_shadow as worker

    baseline = tmp_path / "baseline.json"
    baseline.write_text(baseline_projection().model_dump_json(), encoding="utf-8")
    state = {"settings": get_settings().model_copy(update={
        "cognitive_shadow_orchestration": "staged",
        "cognitive_live_shadow_enabled": True,
        "cognitive_live_shadow_sample_rate": 1.0,
        "cognitive_live_shadow_retention_days": 30,
        "cognitive_live_shadow_personality_source": "runtime_mapper",
        # Present but unused in runtime-mapper mode: proves no fallback.
        "cognitive_live_shadow_personality_baseline_path": str(baseline),
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
    for module in (service, worker, image_store, asset_store):
        monkeypatch.setattr(module, "get_settings", settings)
    monkeypatch.setattr(service, "SessionLocal", database)
    monkeypatch.setattr(worker, "SessionLocal", database)
    monkeypatch.setattr(worker, "_last_purge", float("inf"))  # purge only when a test asks
    service.metrics.reset()
    with database() as db:
        db.add(columbina_core_memory())
        db.commit()
    return type("ShadowEnv", (), {"db": database, "configure": staticmethod(configure), "baseline": baseline})


def create_user(db, *, user_id: uuid.UUID | None = None) -> User:
    user = User(id=user_id, name="Shadow test", email=f"{uuid.uuid4()}@example.com", hashed_password="unused")
    db.add(user)
    db.commit()
    return user


def create_conversation(db, user: User, *, conversation_id: uuid.UUID | None = None) -> Conversation:
    conversation = Conversation(id=conversation_id, user_id=user.id, title="shadow")
    db.add(conversation)
    db.commit()
    return conversation


_clock = {"now": datetime(2026, 9, 1, 12, 0, 0)}


def _tick() -> datetime:
    _clock["now"] += timedelta(seconds=5)
    return _clock["now"]


def add_turn(
    db, conversation: Conversation, content: str, *, with_job: bool = True, reply: str | None = None,
    image_ids: list[str] | None = None, payload: dict | None = None, turn_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None, job_id: uuid.UUID | None = None, reply_id: uuid.UUID | None = None,
) -> tuple[ChatMessage, ChatJob | None]:
    """Persist one user turn (and its ChatJob), optionally with a finished reply.

    Identities are random unless given; fixed identities make cognitive ids
    (which derive from turn and message ids) reproducible across runs.
    """

    turn_id = turn_id or uuid.uuid4()
    at = _tick()
    message = ChatMessage(
        id=message_id, conversation_id=conversation.id, turn_id=turn_id, role="user", content=content,
        image_ids=image_ids, created_at=at,
    )
    db.add(message)
    db.flush()
    job = None
    if with_job:
        job = ChatJob(
            id=job_id, turn_id=turn_id, user_id=conversation.user_id, conversation_id=conversation.id,
            user_message_id=message.id, payload=payload or {"content": content}, status="completed",
            created_at=at,
        )
        db.add(job)
    if reply is not None:
        db.add(ChatMessage(
            id=reply_id, conversation_id=conversation.id, turn_id=turn_id, role="assistant", content=reply,
            created_at=_tick(),
        ))
    db.commit()
    return message, job


@contextmanager
def no_wakeups(monkeypatch):
    from app.services import cognitive_shadow as service
    calls = []
    monkeypatch.setattr(service, "wake_worker", lambda job_id="wake": calls.append(job_id))
    yield calls
