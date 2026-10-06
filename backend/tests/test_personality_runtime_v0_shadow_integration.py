"""Personality Runtime Mapper V0 inside live-shadow capture, on real PostgreSQL."""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import select, text, update

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineRequest
from app.ai.Cognitive.system.v0.live_shadow.compiler import PredecessorRecord, resolve_lineage
from app.ai.Cognitive.system.v0.live_shadow.models import (
    CognitiveShadowCapture, GenerationPath, OmittedInput, PersonalityMissingReason, SegmentResetReason,
    ShadowSourceChannel, TerminalStatus,
)
from app.ai.Cognitive.system.v0.live_shadow.service import ReplayStatus
from app.ai.Cognitive.system.v0.personality_runtime_v0 import (
    PERSONALITY_MAPPER_VERSION, PersonalityRuntimeMapperV0, build_source,
)
from app.ai.Cognitive.system.v0.personality_runtime_v0 import mapper as mapper_module
from app.db.models import CognitiveShadowJob, CognitiveShadowTrace, Conversation, CoreMemory, User
from app.services import cognitive_shadow as service
from app.workers import cognitive_shadow as worker
from cognitive_shadow_support import (  # noqa: F401  (fixtures)
    COLUMBINA_CORE_GROUPS, add_turn, baseline_projection, create_conversation, create_user, database,
    make_capture, shadow_env,
)


def _columbina_projection():
    return PersonalityRuntimeMapperV0().compile(build_source(
        name="columbina", version=1, schema_version=1, groups=copy.deepcopy(COLUMBINA_CORE_GROUPS),
    ))


def _turn(env, content="How much protein is in two eggs?", conversation=None):
    with env.db() as db:
        if conversation is None:
            conversation = create_conversation(db, create_user(db))
        else:
            conversation = db.get(Conversation, conversation.id)
        focus, job = add_turn(db, conversation, content)
    captured = service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=focus.turn_id,
        user_message_id=focus.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert captured
    return conversation, focus


def _capture(env, turn_id) -> CognitiveShadowCapture:
    with env.db() as db:
        payload = db.scalar(select(CognitiveShadowJob.capture_payload).where(CognitiveShadowJob.turn_id == turn_id))
    return CognitiveShadowCapture.model_validate(payload)


def _terminal(env, turn_id) -> CognitiveShadowTrace:
    with env.db() as db:
        return db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.turn_id == turn_id))


def _edit_core(env, **values) -> None:
    with env.db() as db:
        db.execute(update(CoreMemory).where(CoreMemory.name == "columbina").values(**values))
        db.commit()


def test_active_core_memory_produces_a_runtime_mapper_projection_without_a_baseline(shadow_env) -> None:
    shadow_env.configure(cognitive_live_shadow_personality_baseline_path=None)
    _, focus = _turn(shadow_env)
    capture = _capture(shadow_env, focus.turn_id)
    projection = capture.personality
    assert projection.projection_kind == "runtime_mapper" and projection.scope == "core_identity"
    assert projection.projection_version == PERSONALITY_MAPPER_VERSION
    assert projection == _columbina_projection()
    assert capture.personality_missing_reason is None
    assert OmittedInput.PERSONALITY_PROJECTION not in capture.omitted_inputs
    # Other currently omitted inputs remain omitted.
    assert {OmittedInput.MEMORY_PROJECTION, OmittedInput.TASK_CONTRACT,
            OmittedInput.ACTION_GROUNDED_OBJECTS} <= set(capture.omitted_inputs)
    assert capture.core_memory.core_memory_ref == projection.core_memory_ref == "core_memory:columbina:v1"

    assert worker.run_once()
    row = _terminal(shadow_env, focus.turn_id)
    assert row.terminal_status == "completed"
    request = CognitivePipelineRequest.model_validate(row.compiled_request_payload)
    assert request.interpretation_biases == projection.interpretation_biases
    assert request.state_personality == projection.state_personality
    assert request.action_personality == projection.action_personality
    assert row.versions["personality_projection"] == (
        f"runtime_mapper:{projection.projection_id}:{PERSONALITY_MAPPER_VERSION}"
    )


def test_reviewed_baseline_mode_is_explicit_and_validated(shadow_env, tmp_path) -> None:
    shadow_env.configure(cognitive_live_shadow_personality_source="reviewed_baseline")
    _, focus = _turn(shadow_env)
    capture = _capture(shadow_env, focus.turn_id)
    assert capture.personality.projection_kind == "reviewed_baseline"
    assert capture.personality == baseline_projection()

    # A configuration file can never claim to be the runtime mapper.
    forged = tmp_path / "forged.json"
    forged.write_text(_columbina_projection().model_dump_json(), encoding="utf-8")
    shadow_env.configure(cognitive_live_shadow_personality_baseline_path=str(forged))
    _, forged_focus = _turn(shadow_env)
    rejected = _capture(shadow_env, forged_focus.turn_id)
    assert rejected.personality is None
    assert rejected.personality_missing_reason == PersonalityMissingReason.BASELINE_INVALID


def test_modes_never_fall_back_to_each_other(shadow_env) -> None:
    # Runtime mapping fails: a valid baseline file is configured but never used.
    _edit_core(shadow_env, action_style={"warmth": 0.8})
    _, focus = _turn(shadow_env)
    capture = _capture(shadow_env, focus.turn_id)
    assert capture.personality is None
    assert capture.personality_missing_reason == PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID
    # Baseline mode without a baseline file: the runtime mapper is never used.
    _edit_core(shadow_env, action_style=copy.deepcopy(COLUMBINA_CORE_GROUPS["action_style"]), version=2)
    shadow_env.configure(
        cognitive_live_shadow_personality_source="reviewed_baseline",
        cognitive_live_shadow_personality_baseline_path=None,
    )
    _, focus = _turn(shadow_env)
    capture = _capture(shadow_env, focus.turn_id)
    assert capture.personality is None
    assert capture.personality_missing_reason == PersonalityMissingReason.NO_ACCEPTED_SOURCE


@pytest.mark.parametrize("edit, reason", (
    ({"drives": {"curiosity": 0.88}}, PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID),
    ({"action_style": {**COLUMBINA_CORE_GROUPS["action_style"], "warmth": True}},
     PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID),
    ({"action_style": {**COLUMBINA_CORE_GROUPS["action_style"], "warmth": "high"}},
     PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID),
    ({"drives": {**COLUMBINA_CORE_GROUPS["drives"], "affiliation": 1.5}},
     PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID),
    ({"schema_version": 2}, PersonalityMissingReason.RUNTIME_MAPPER_SCHEMA_UNSUPPORTED),
    ({"is_active": False}, PersonalityMissingReason.CORE_MEMORY_INACTIVE),
    ({"name": "someone-else"}, PersonalityMissingReason.CORE_MEMORY_UNAVAILABLE),
))
def test_malformed_core_memory_persists_a_typed_skip(shadow_env, caplog, edit, reason) -> None:
    _edit_core(shadow_env, **edit)
    _, focus = _turn(shadow_env)
    capture = _capture(shadow_env, focus.turn_id)
    assert capture.personality is None and capture.personality_missing_reason == reason
    assert OmittedInput.PERSONALITY_PROJECTION in capture.omitted_inputs
    assert worker.run_once()
    row = _terminal(shadow_env, focus.turn_id)
    assert (row.terminal_status, row.skip_reason) == ("skipped", "missing_personality_projection")
    # Logs carry codes and references only, never CoreMemory values.
    for message in caplog.messages:
        for leaked in ("0.88", "1.5", "high", "0.8"):
            assert leaked not in message


def test_turn_time_projection_survives_later_core_edits_and_worker_never_remaps(shadow_env, monkeypatch) -> None:
    _, focus = _turn(shadow_env)
    captured = _capture(shadow_env, focus.turn_id).personality
    _edit_core(shadow_env, action_style={**COLUMBINA_CORE_GROUPS["action_style"], "warmth": 0.1}, version=2)

    def forbidden(*args, **kwargs):
        raise AssertionError("the worker must not map or read CoreMemory")

    monkeypatch.setattr(service, "_core_personality", forbidden)
    monkeypatch.setattr(PersonalityRuntimeMapperV0, "compile", forbidden)
    assert worker.run_once()
    row = _terminal(shadow_env, focus.turn_id)
    assert row.terminal_status == "completed"
    request = CognitivePipelineRequest.model_validate(row.compiled_request_payload)
    assert request.action_personality == captured.action_personality
    assert request.action_personality.warmth == 0.8 and request.core_memory_ref == "core_memory:columbina:v1"
    # Replay uses the stored projection and matches semantically.
    with shadow_env.db() as db:
        user_id = db.scalar(select(CognitiveShadowTrace.user_id))
        assert service.replay_terminal(db, user_id=user_id, terminal_id=row.id).status == ReplayStatus.MATCH


def test_capture_retry_does_not_remap(shadow_env, monkeypatch) -> None:
    conversation, focus = _turn(shadow_env)
    original = _capture(shadow_env, focus.turn_id)
    _edit_core(shadow_env, drives={**COLUMBINA_CORE_GROUPS["drives"], "curiosity": 0.2}, version=3)
    with shadow_env.db() as db:
        job_id = db.scalar(select(CognitiveShadowJob.chat_job_id).where(CognitiveShadowJob.turn_id == focus.turn_id))
    assert not service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=focus.turn_id,
        user_message_id=focus.id, chat_job_id=job_id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert _capture(shadow_env, focus.turn_id) == original


def test_one_core_memory_read_per_capture(shadow_env) -> None:
    from sqlalchemy import event

    reads = []
    engine = shadow_env.db.kw["bind"]

    def count(conn, cursor, statement, *args):
        if "FROM core_memory" in statement:
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        _turn(shadow_env)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(reads) == 1


def test_mapper_version_change_resets_lineage(monkeypatch) -> None:
    from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
    from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, observe
    source = build_source(name="columbina", version=1, schema_version=1, groups=copy.deepcopy(COLUMBINA_CORE_GROUPS))
    current = PersonalityRuntimeMapperV0().compile(source)
    monkeypatch.setattr(mapper_module, "PERSONALITY_MAPPER_VERSION", "personality-runtime-mapper-v0.2-test")
    successor = PersonalityRuntimeMapperV0().compile(source)
    assert successor.state_personality.mapper_version != current.state_personality.mapper_version
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    first = make_capture(user_ref=user, conversation_ref=conversation, personality=current)
    record = observe(first, None, ObservationContext(
        job_id=first.capture_id, attempt=1, capture_digest=payload_digest(first), started_at="2026-09-26T00:00:00+00:00",
    ))
    assert record.status == TerminalStatus.COMPLETED
    predecessor = PredecessorRecord(
        terminal_id=record.terminal_id, turn_id=record.turn_id, user_ref=user, conversation_ref=conversation,
        focus_message_id=first.focus_message_id, segment_id=record.segment_id, status=record.status,
        pipeline_trace=record.pipeline_trace,
    )
    same = make_capture(user_ref=user, conversation_ref=conversation, personality=current,
                        previous_user_message_id=first.focus_message_id)
    assert resolve_lineage(same, predecessor).continuous
    changed = make_capture(user_ref=user, conversation_ref=conversation, personality=successor,
                           previous_user_message_id=first.focus_message_id)
    assert resolve_lineage(changed, predecessor).reset_reason == SegmentResetReason.LINEAGE_VERSION_CHANGE


def test_mapper_failure_cannot_alter_the_live_turn(shadow_env, monkeypatch) -> None:
    from test_cognitive_shadow_chat_isolation import REPLY
    from app.services import chat as chat_service
    from app.workers import chat as chat_worker
    import asyncio

    published: list[dict] = []

    async def fake_stream_chat(*, messages, context, **_):
        for token in REPLY:
            yield {"type": "token", "content": token}

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "SessionLocal", shadow_env.db)
    monkeypatch.setattr(chat_worker, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt", lambda user_id, content: None)
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", lambda *a, **k: None)

    def explode(self, source):
        raise RuntimeError("mapper exploded unexpectedly")

    def run_turn():
        published.clear()
        with shadow_env.db() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            turn_id = uuid.uuid4()
            message = chat_service.add_message(db, conversation=conversation, role="user", content="hi",
                                               turn_id=turn_id)
            chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                     user_message_id=message.id, payload={"content": "hi"}, commit=False)
            db.commit()
            job = chat_service.claim_next_job(db, lease_seconds=60)
        asyncio.run(chat_worker._generate(job))
        with shadow_env.db() as db:
            job = db.get(type(job), job.id)
            reply = db.scalar(text("SELECT content FROM chat_messages WHERE turn_id = :t AND role = 'assistant'"),
                              {"t": turn_id})
        return [{k: v for k, v in e.items() if k not in {"turn_id", "message_id"}} for e in published], job.status, reply

    healthy = run_turn()
    monkeypatch.setattr(PersonalityRuntimeMapperV0, "compile", explode)
    broken = run_turn()
    assert broken == healthy and healthy[1] == "completed" and healthy[2] == "".join(REPLY)


def test_deletion_still_removes_mapped_trace_data(shadow_env) -> None:
    conversation, _ = _turn(shadow_env)
    assert worker.run_once()
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowTrace)) == 1
        db.delete(db.get(User, conversation.user_id))
        db.commit()
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 0
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowTrace)) == 0
        # The mapper never writes CoreMemory.
        core = db.scalar(select(CoreMemory))
        assert (core.version, core.drives) == (1, COLUMBINA_CORE_GROUPS["drives"])
