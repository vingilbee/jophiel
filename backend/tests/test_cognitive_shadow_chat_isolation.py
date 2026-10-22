"""Zero behavioral influence: shadow capture never changes the live turn."""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess
import sys
import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db.models import ChatJob, ChatMessage, CognitiveShadowJob, Conversation
from app.services import chat as chat_service
from app.services import cognitive_shadow as service
from app.workers import chat as chat_worker
from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: F401

REPLY = ("Two large eggs ", "have about ", "12 g of protein.")


@pytest.fixture(autouse=True)
def _legacy_preparation(monkeypatch):
    """These tests pin the old reply preparation without shared recall: a local .env with
    SHARED_STATE_PIPELINE_ENABLED or RECALL_ENABLED must not change what they compare (a recall result carries
    its own time and the test's fresh user ids, so two runs can never be equal). Both have their own tests.
    Module-local, so importers of ``live_turn`` aren't affected."""
    from app.services import recall_references, shared_state
    monkeypatch.setattr(shared_state, "enabled", lambda: False)
    monkeypatch.setattr(recall_references, "enabled", lambda: False)


@pytest.fixture
def live_turn(shadow_env, monkeypatch):
    """Run the real chat-worker `_generate` with a deterministic mocked agent."""

    order: list[str] = []
    published: list[dict] = []
    agent_inputs: list[tuple] = []

    async def fake_stream_chat(*, messages, context, **_):
        order.append("generation")
        agent_inputs.append((messages, dataclasses.replace(context, user_id=None)))
        for token in REPLY:
            yield {"type": "token", "content": token}

    async def no_token(db, *, user_id):
        return None

    real_capture = service.capture_turn

    def recording_capture(**kwargs):
        order.append("capture")
        return real_capture(**kwargs)

    monkeypatch.setattr(chat_worker, "SessionLocal", shadow_env.db)
    monkeypatch.setattr(chat_worker, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt", lambda user_id, content: None)
    consolidation = Mock()
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", consolidation)
    monkeypatch.setattr(service, "capture_turn", recording_capture)
    monkeypatch.setattr(service, "wake_worker", lambda job_id="wake": order.append("shadow-wake"))

    def run(content: str = "How much protein is in two eggs?"):
        order.clear()
        published.clear()
        with shadow_env.db() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            turn_id = uuid.uuid4()
            message = chat_service.add_message(db, conversation=conversation, role="user", content=content,
                                               turn_id=turn_id)
            chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                     user_message_id=message.id, payload={"content": content}, commit=False)
            db.commit()
            job = chat_service.claim_next_job(db, lease_seconds=60)
        asyncio.run(chat_worker._generate(job))
        with shadow_env.db() as db:
            job = db.get(ChatJob, job.id)
            assistant = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id,
                                                            ChatMessage.role == "assistant"))
        normalized = [
            {key: value for key, value in event.items() if key not in {"turn_id", "message_id"}}
            for event in published
        ]
        return {
            "events": normalized, "job": (job.status, job.last_error), "order": list(order),
            "assistant": (assistant.content, assistant.status), "agent_input": agent_inputs[-1],
            "turn_id": turn_id, "consolidations": consolidation.call_count,
        }

    return run


def test_enabled_and_disabled_shadow_emit_identical_user_visible_behavior(shadow_env, live_turn) -> None:
    shadow_env.configure(cognitive_live_shadow_enabled=False)
    disabled = live_turn()
    shadow_env.configure(cognitive_live_shadow_enabled=True)
    enabled = live_turn()
    assert enabled["events"] == disabled["events"]
    assert [event["type"] for event in enabled["events"]] == ["start", "token", "token", "token", "done"]
    assert enabled["assistant"] == disabled["assistant"] == ("".join(REPLY), "completed")
    assert enabled["job"] == disabled["job"] == ("completed", None)
    # The model input (history and runtime context) is byte-for-byte the same.
    assert enabled["agent_input"][0] == disabled["agent_input"][0]
    assert enabled["agent_input"][1] == disabled["agent_input"][1]
    # Memory consolidation is scheduled exactly once per live turn either way
    # (the mock is shared, so its count is cumulative across both runs).
    assert disabled["consolidations"] == 1 and enabled["consolidations"] == 2
    # Capture ran once, and before generation started.
    assert disabled["order"] == ["generation"]
    assert enabled["order"] == ["capture", "shadow-wake", "generation"]
    with shadow_env.db() as db:
        jobs = list(db.scalars(select(CognitiveShadowJob)))
    assert [job.turn_id for job in jobs] == [enabled["turn_id"]]


def test_shadow_failure_cannot_alter_the_chat_job_or_reply(shadow_env, live_turn, monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("capture exploded")

    monkeypatch.setattr(service, "build_capture", explode)
    failed_capture = live_turn()
    assert failed_capture["job"] == ("completed", None)
    assert failed_capture["assistant"] == ("".join(REPLY), "completed")
    assert all(event["type"] != "error" for event in failed_capture["events"])
    assert service.metrics.snapshot()["counts"]["capture_failures|code=RuntimeError"] == 1

    # Even a capture owner that raises past its own guard is contained.
    monkeypatch.setattr(service, "capture_turn", explode)
    raised = live_turn()
    assert raised["job"] == ("completed", None) and raised["events"] == failed_capture["events"]


def test_retry_never_recaptures(shadow_env, live_turn, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(service, "capture_turn", lambda **kwargs: calls.append(kwargs))
    with shadow_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        message = chat_service.add_message(db, conversation=conversation, role="user", content="hi", turn_id=turn_id)
        chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                 user_message_id=message.id, payload={"content": "hi"})
        job = chat_service.claim_next_job(db, lease_seconds=60)
    job.attempts = 2  # a reclaimed job after an expired lease
    asyncio.run(chat_worker._generate(job))
    assert calls == []


def test_shadow_output_never_reaches_events_prompts_or_tools(shadow_env, live_turn) -> None:
    from app.workers import cognitive_shadow as worker

    first = live_turn("hi")
    assert worker.run_once()
    second = live_turn("hi")
    # A completed trace exists for the first turn; the next turn is unchanged.
    assert second["events"] == first["events"]
    assert second["agent_input"][1] == first["agent_input"][1]
    rendered = str(second["agent_input"]) + str(second["events"])
    for marker in ("shadow", "action_key", "give_feedback", "pipeline_trace", "cognitive"):
        assert marker not in rendered


SHADOW_WORKER_FORBIDDEN = (
    "app.ai.chat", "app.ai.agent", "app.ai.prompts", "app.ai.subagents", "app.ai.tools",
    "app.ai.integrations", "app.services.long_term_memory", "app.services.chat", "app.services.tts",
    "app.services.whatsapp", "app.workers.chat", "app.ai.Cognitive.system.v0.action_selection_v0.fixture_adapter",
    "app.ai.Cognitive.system.v0.goal_v0.fixture_adapter", "langchain_openai", "langgraph",
)


def test_shadow_worker_imports_no_agent_prompt_tool_memory_or_fixture_code() -> None:
    probe = (
        "import sys, app.workers.cognitive_shadow as w, app.services.cognitive_shadow as s; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    loaded = set(subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True,
    ).stdout.split())
    offenders = sorted(name for name in loaded for forbidden in SHADOW_WORKER_FORBIDDEN
                       if name == forbidden or name.startswith(forbidden + "."))
    assert offenders == []


def test_duplicated_scope_constants_match_the_integration() -> None:
    from app.ai.integrations.google.scopes import DRIVE, GMAIL_MODIFY

    assert service._GMAIL_MODIFY_SCOPE == GMAIL_MODIFY
    assert service._DRIVE_SCOPE == DRIVE


def test_no_live_module_reads_shadow_state() -> None:
    """Only the capture owners may reference the shadow service, and only to capture."""

    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {
        "services/cognitive_shadow.py", "workers/cognitive_shadow.py", "workers/runner.py",
        "workers/chat.py", "api/routes/chat.py", "db/models.py", "core/config.py",
    }
    pattern = re.compile(r"cognitive_shadow|CognitiveShadow|live_shadow")
    readers = sorted(
        str(path.relative_to(root)) for path in root.rglob("*.py")
        # The pure personality mapper produces the live-shadow projection contract.
        if "Cognitive/system/v0/live_shadow" not in str(path)
        and "Cognitive/system/v0/personality_runtime_v0" not in str(path)
        # The offline, local-only review CLI reads terminal rows by design; it is
        # never imported by a live module (asserted below).
        and str(path.relative_to(root)) != "ai/Cognitive/system/v0/trace_summary.py"
        and pattern.search(path.read_text(encoding="utf-8"))
        and str(path.relative_to(root)) not in allowed
    )
    assert readers == []
    summary_importers = sorted(
        str(path.relative_to(root)) for path in root.rglob("*.py")
        if path.name != "trace_summary.py" and "trace_summary" in path.read_text(encoding="utf-8")
    )
    assert summary_importers == []
    for owner in ("workers/chat.py", "api/routes/chat.py"):
        uses = set(re.findall(r"cognitive_shadow\.(\w+)", (root / owner).read_text(encoding="utf-8")))
        assert uses <= {"capture_turn", "capture_enabled", "source_channel_for"}, owner


# ---------------------------------------------------------------------------
# Local WebSocket fallback (no ChatJob): same capture owner, same isolation
# ---------------------------------------------------------------------------

from test_auth_sessions import auth_client, socket_client  # noqa: E402,F401  (fixtures)


def _fallback_turn(socket_client, monkeypatch, *, stop: bool = False) -> list[dict]:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.config import get_settings
    from app.services import auth

    client, user, chat = socket_client
    conversation = SimpleNamespace(id=uuid.uuid4())

    async def stream(**kwargs):
        for token in REPLY:
            yield {"type": "token", "content": token}
            if stop:
                await asyncio.sleep(30)

    monkeypatch.setattr(chat, "stream_chat", stream)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "add_message", Mock(return_value=SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
    monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
    monkeypatch.setattr(chat.memory_service, "retrieve_for_prompt", Mock(return_value=""))
    monkeypatch.setattr(chat.memory_service, "schedule_consolidation", Mock())
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    frames: list[dict] = []
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "run", "content": "How much protein is in two eggs?"})
        for _ in range(20):
            frame = ws.receive_json()
            if frame["type"] == "state":
                continue
            frames.append({k: v for k, v in frame.items()
                           if k not in {"turn_id", "message_id", "conversation_id", "user_message_id"}})
            if stop and frame["type"] == "token":
                ws.send_json({"type": "stop"})
            if frame["type"] in {"done", "stopped"}:
                break
    return frames


@pytest.mark.parametrize("stop", (False, True))
def test_local_fallback_is_unchanged_by_capture_success_or_failure(socket_client, monkeypatch, stop) -> None:
    _, _, chat = socket_client
    monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: False)
    baseline = _fallback_turn(socket_client, monkeypatch, stop=stop)

    captured: list[dict] = []
    monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: True)
    monkeypatch.setattr(chat.cognitive_shadow, "capture_turn", lambda **kwargs: captured.append(kwargs) or True)
    assert _fallback_turn(socket_client, monkeypatch, stop=stop) == baseline
    (call,) = captured
    assert call["chat_job_id"] is None and call["generation_path"].value == "local_fallback"
    assert call["source_channel"].value == "web"

    def explode(**kwargs):
        raise RuntimeError("capture exploded")

    monkeypatch.setattr(chat.cognitive_shadow, "capture_turn", explode)
    assert _fallback_turn(socket_client, monkeypatch, stop=stop) == baseline
    assert baseline[-1]["type"] == ("stopped" if stop else "done")
