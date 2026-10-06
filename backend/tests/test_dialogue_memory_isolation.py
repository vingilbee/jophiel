"""Live isolation and privacy for Dialogue Working Memory V0 (section 24.9)."""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.errors import DialogueMemoryError
from app.db.models import ChatJob, ChatMessage, CognitiveDialogueMemoryJob, CognitiveShadowJob, MemoryJob
from app.services import dialogue_memory as service
from app.workers import chat as chat_worker
from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: F401
from dialogue_memory_support import dialogue_env  # noqa: F401
from test_cognitive_shadow_chat_isolation import REPLY, _fallback_turn, live_turn  # noqa: F401
from test_auth_sessions import auth_client, socket_client  # noqa: F401
from test_dialogue_memory_persistence import _drain

APP = Path(__file__).resolve().parents[1] / "app"


def _dialogue_jobs(env) -> list[CognitiveDialogueMemoryJob]:
    with env.db() as db:
        return list(db.scalars(select(CognitiveDialogueMemoryJob)))


def test_enabling_dialogue_memory_leaves_the_live_turn_byte_identical(dialogue_env, live_turn) -> None:
    dialogue_env.configure(cognitive_dialogue_memory_enabled=False)
    disabled = live_turn()
    assert _dialogue_jobs(dialogue_env) == []
    dialogue_env.configure(cognitive_dialogue_memory_enabled=True)
    enabled = live_turn()
    assert enabled["events"] == disabled["events"]
    assert enabled["assistant"] == disabled["assistant"] == ("".join(REPLY), "completed")
    assert enabled["job"] == disabled["job"] == ("completed", None)
    assert enabled["agent_input"][0] == disabled["agent_input"][0]  # same model history
    assert enabled["agent_input"][1] == disabled["agent_input"][1]  # same tools, awareness, memory
    assert enabled["consolidations"] - disabled["consolidations"] == 1  # long-term memory: once per turn either way
    jobs = _dialogue_jobs(dialogue_env)
    with dialogue_env.db() as db:
        assistant = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == enabled["turn_id"],
                                                        ChatMessage.role == "assistant"))
        shadow = db.scalar(select(CognitiveShadowJob).where(CognitiveShadowJob.turn_id == enabled["turn_id"]))
    assert [(job.trigger_message_id, job.trigger_kind) for job in jobs] == [(assistant.id, "message_committed")]
    assert shadow.capture_schema_version == "cognitive-shadow-capture-v0.3"  # capture sees memory, live turn doesn't
    _drain()
    assert [job.status for job in _dialogue_jobs(dialogue_env)] == ["completed"]


def test_dialogue_memory_failures_cannot_fail_or_alter_a_live_turn(dialogue_env, live_turn, monkeypatch) -> None:
    baseline = live_turn()

    def broken_insert(*args, **kwargs):
        raise RuntimeError("dialogue queue unavailable")

    def broken_context(*args, **kwargs):
        raise DialogueMemoryError(DialogueMemoryFailurePhase.SOURCE_ORDERING,
                                  DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP, "gap")

    monkeypatch.setattr(service, "insert", broken_insert)
    monkeypatch.setattr(service, "turn_context", broken_context)
    failed = live_turn()
    assert failed["events"] == baseline["events"] and failed["job"] == ("completed", None)
    assert failed["assistant"] == baseline["assistant"]
    with dialogue_env.db() as db:
        shadow = db.scalar(select(CognitiveShadowJob).where(CognitiveShadowJob.turn_id == failed["turn_id"]))
    # The typed dialogue failure is recorded on the capture, never raised into the turn.
    assert shadow.capture_payload["dialogue"]["status"] == "unavailable"
    assert shadow.capture_payload["dialogue"]["failure"]["code"] == "source_sequence_gap"


def test_the_worker_failure_path_schedules_through_the_same_service(dialogue_env, monkeypatch) -> None:
    async def failing_stream(*, messages, context, **_):
        yield {"type": "token", "content": "Half an ans"}
        raise RuntimeError("model crashed")

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "SessionLocal", dialogue_env.db)
    monkeypatch.setattr(chat_worker, "stream_chat", failing_stream)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: None)
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt", lambda user_id, content: None)
    from app.services import chat as chat_service

    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        message = chat_service.add_message(db, conversation=conversation, role="user", content="hi", turn_id=turn_id)
        chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                 user_message_id=message.id, payload={"content": "hi"}, commit=False)
        db.commit()
        job = chat_service.claim_next_job(db, lease_seconds=60)
    asyncio.run(chat_worker._generate(job))
    with dialogue_env.db() as db:
        failed = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id, ChatMessage.role == "assistant"))
        assert db.get(ChatJob, job.id).status == "failed"
    assert (failed.status, failed.content) == ("failed", "Half an ans")
    assert [job.trigger_message_id for job in _dialogue_jobs(dialogue_env)] == [failed.id]


@pytest.mark.parametrize("stop", (False, True))
def test_local_fallback_schedules_once_and_is_otherwise_unchanged(socket_client, monkeypatch, stop) -> None:
    _, _, chat = socket_client
    monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: False)
    monkeypatch.setattr(chat.dialogue_memory, "enabled", lambda: False)
    monkeypatch.setattr(chat.dialogue_memory, "schedule_update", lambda *args, **kwargs: False)
    baseline = _fallback_turn(socket_client, monkeypatch, stop=stop)

    calls: list[dict] = []
    monkeypatch.setattr(chat.dialogue_memory, "enabled", lambda: True)
    monkeypatch.setattr(chat.dialogue_memory, "schedule_update",
                        lambda db, **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(chat.dialogue_memory, "wake_worker", lambda: None)
    assert _fallback_turn(socket_client, monkeypatch, stop=stop) == baseline
    assert len(calls) == 1 and calls[0].get("trigger_kind", "message_committed") == "message_committed"
    # schedule_update contains its own failures (see the persistence suite); a
    # scheduler that reports failure leaves the turn unchanged.
    monkeypatch.setattr(chat.dialogue_memory, "schedule_update", lambda db, **kwargs: False)
    assert _fallback_turn(socket_client, monkeypatch, stop=stop) == baseline


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------

def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _innermost_calls(function) -> list[ast.Call]:
    """Calls made by this function itself, not by functions nested inside it."""

    nested: set[int] = set()
    for node in ast.walk(function):
        if node is not function and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested.update(id(inner) for inner in ast.walk(node))
    return [node for node in ast.walk(function) if isinstance(node, ast.Call) and id(node) not in nested]


def _is_assistant_append(call: ast.Call) -> bool:
    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
    role = next((keyword.value for keyword in call.keywords if keyword.arg == "role"), None)
    return name in {"add_message", "ChatMessage"} and isinstance(role, ast.Constant) and role.value == "assistant"


def _schedules(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "schedule_update"


@pytest.mark.parametrize("module", ["api/routes/chat.py", "workers/chat.py", "services/whatsapp.py"])
def test_every_assistant_append_schedules_a_dialogue_update(module) -> None:
    tree = ast.parse((APP / module).read_text(encoding="utf-8"))
    checked = 0
    for function in _functions(tree):
        calls = _innermost_calls(function)
        if any(_is_assistant_append(call) for call in calls):
            checked += 1
            assert any(_schedules(call) for call in calls), f"{module}:{function.name}"
    assert checked


def test_live_modules_only_schedule_and_never_read_dialogue_state() -> None:
    allowed = {"schedule_update", "wake_worker", "enabled", "delete_cached_conversation", "TRIGGER_DELIVERY_SETTLED"}
    for module in ("api/routes/chat.py", "workers/chat.py", "services/whatsapp.py", "workers/whatsapp.py",
                   "api/routes/whatsapp.py", "services/chat.py", "services/auth.py"):
        uses = set(re.findall(r"dialogue_memory\.(\w+)", (APP / module).read_text(encoding="utf-8")))
        assert uses <= allowed, (module, uses - allowed)
    # Nothing in the live agent layer reads dialogue memory at all.
    for path in (APP / "ai").rglob("*.py"):
        if "Cognitive" in path.parts:
            continue
        assert "dialogue_memory" not in path.read_text(encoding="utf-8"), path


def test_v0_writes_no_long_term_relationship_episodic_core_or_learning_memory() -> None:
    forbidden = ("SemanticMemory", "EpisodicMemory", "LearningMemory", "CoreMemory", "WorkingMemory",
                 "long_term_memory", "schedule_consolidation")
    for path in [APP / "services/dialogue_memory.py", APP / "workers/dialogue_memory.py",
                 *(APP / "ai/Cognitive/system/v0/dialogue_memory_v0").glob("*.py")]:
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, (path.name, name)


def test_logs_and_metrics_carry_no_message_or_snapshot_content(dialogue_env, caplog) -> None:
    from app.services import chat as chat_service

    marker = "PRIVATE-MARKER-7f3a"
    caplog.set_level(logging.DEBUG)
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        chat_service.add_message(db, conversation=conversation, role="user", content=f"My code is {marker}.",
                                 turn_id=turn_id, commit=False)
        reply = chat_service.add_message(db, conversation=conversation, role="assistant",
                                         content=f"Noted {marker}.", turn_id=turn_id, commit=False)
        service.schedule_update(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=reply.id)
        db.commit()
    _drain()
    with dialogue_env.db() as db:  # a changed consumed prefix exercises the reset path's logs and metrics
        db.execute(ChatMessage.__table__.delete().where(ChatMessage.id == reply.id))
        db.commit()
        followup = chat_service.add_message(db, conversation=db.get(type(conversation), conversation.id),
                                            role="user", content=f"Again {marker}.", turn_id=uuid.uuid4(),
                                            commit=False)
        service.schedule_update(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=followup.id)
        db.commit()
    _drain()
    assert service.metrics.snapshot().get("prefix_mismatches") == 1
    assert marker not in caplog.text
    assert all(marker not in key for key in service.metrics.snapshot())
    with dialogue_env.db() as db:
        assert db.scalar(select(MemoryJob.id)) is None  # nothing reached long-term memory
