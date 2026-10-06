"""Shared builders for the Dialogue Working Memory V0 tests.

Pure builders need no database.  Identities are UUID5s of readable names so a
failing test can be reproduced exactly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueResetReason,
    turn_context_digest,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import (
    DialogueMemoryUpdaterV0,
    bootstrap,
    compile_turn_context,
    source_event,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.models import DeliveryFact, DialogueMessageRecord
from app.ai.Cognitive.system.v0.live_shadow.models import (
    ALWAYS_OMITTED_V03,
    CAPTURE_SCHEMA_VERSION_V03,
    CapturedDialogueMemory,
    CapturedMessage,
    OmittedInput,
)
from app.core.config import get_settings

TEST_NAMESPACE = uuid.UUID("0c1f9a8e-5d2b-5e4f-a7c3-6b9d2e1f4a80")
CREATED_AT = "2026-09-01T12:00:00+00:00"


def uid(*names: str) -> str:
    return str(uuid.uuid5(TEST_NAMESPACE, "/".join(names)))


@dataclass
class Script:
    """An ordered, persisted-looking conversation for the pure updater."""

    name: str = "conversation"
    records: list[DialogueMessageRecord] = field(default_factory=list)

    @property
    def conversation(self) -> str:
        return uid(self.name, "conversation")

    @property
    def user(self) -> str:
        return uid(self.name, "user")

    def turn(self, index: int) -> str:
        return uid(self.name, "turn", str(index))

    def add(self, role: str, content: str, *, turn: int | None = None, status: str = "completed",
            channel: str = "web", deliveries: tuple[tuple[int, str], ...] = (), unprompted: bool = False,
            ) -> DialogueMessageRecord:
        sequence = len(self.records) + 1
        message = uid(self.name, "message", str(sequence))
        if unprompted:
            turn_ref = None
        else:
            turn_ref = self.turn(turn if turn is not None else sequence)
        record = DialogueMessageRecord(
            message_ref=message, conversation_ref=self.conversation, conversation_sequence=sequence,
            turn_ref=turn_ref, role=role, status=status, source_channel=channel, content=content,
            created_at=CREATED_AT,
            deliveries=tuple(DeliveryFact(delivery_ref=uid(self.name, "delivery", str(sequence), str(index)),
                                          channel="whatsapp", part_index=index, status=state)
                             for index, state in deliveries),
        )
        self.records.append(record)
        return record

    def exchange(self, turn: int, user: str, assistant: str | None = None, **assistant_options):
        self.add("user", user, turn=turn)
        if assistant is not None:
            self.add("assistant", assistant, turn=turn, **assistant_options)

    def events(self, start: int = 0, end: int | None = None):
        return [source_event(record, user_ref=self.user) for record in self.records[start:end]]

    def snapshot(self, *, limits=None):
        return bootstrap(user_ref=self.user, conversation_ref=self.conversation, events=self.events(),
                         window_start=1, revision=1, reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP,
                         limits=limits)

    def incremental(self, batches: list[int], *, limits=None):
        """Genesis then one transition per batch size; returns every snapshot."""

        updater = DialogueMemoryUpdaterV0(limits)
        snapshot = updater.genesis(user_ref=self.user, conversation_ref=self.conversation, revision=1,
                                   segment_start_sequence=1, reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP)
        produced = []
        start = 0
        for index, size in enumerate(batches):
            snapshot = updater.apply(snapshot, self.events(start, start + size), continue_lineage=index > 0)
            produced.append(snapshot)
            start += size
        return produced

    def focus(self, content: str, *, turn: int = 999):
        """A focus user message positioned right after the script (not appended)."""

        sequence = len(self.records) + 1
        record = DialogueMessageRecord(
            message_ref=uid(self.name, "message", str(sequence)), conversation_ref=self.conversation,
            conversation_sequence=sequence, turn_ref=self.turn(turn), role="user", status="completed",
            source_channel="web", content=content, created_at=CREATED_AT,
        )
        return record, source_event(record, user_ref=self.user)

    def context(self, content: str, *, turn: int = 999):
        snapshot = self.snapshot()
        record, event = self.focus(content, turn=turn)
        return compile_turn_context(snapshot, event, cognitive_turn_id=self.turn(turn)), record

    def captured_messages(self, focus: DialogueMessageRecord) -> tuple[CapturedMessage, ...]:
        eligible = [record for record in self.records
                    if not (record.role == "assistant" and record.status == "cancelled" and not record.content)]
        return tuple(
            CapturedMessage(message_id=record.message_ref, role=record.role, content=record.content,
                            conversation_sequence=record.conversation_sequence)
            for record in [*eligible[-23:], focus]
        )

    def v03_capture(self, content: str, *, turn: int = 999, **overrides):
        """A valid capture v0.3 for this script's next user message (no database)."""

        from cognitive_shadow_support import make_capture

        context, focus = self.context(content, turn=turn)
        omitted = set(ALWAYS_OMITTED_V03) | {
            OmittedInput.MEMORY_PROJECTION, OmittedInput.RELATIONSHIP_PROJECTION,
            OmittedInput.APPLICATION_SAFETY_POLICY,
        }
        previous_users = [record.message_ref for record in self.records if record.role == "user"]
        values = dict(
            contents=(("user", content),), turn_id=self.turn(turn), user_ref=self.user,
            conversation_ref=self.conversation, message_ids=(focus.message_ref,),
            previous_user_message_id=previous_users[-1] if previous_users else None,
            messages=self.captured_messages(focus), schema_version=CAPTURE_SCHEMA_VERSION_V03,
            omitted_inputs=tuple(sorted(omitted)),
            dialogue=CapturedDialogueMemory(status="available", turn_context=context,
                                            turn_context_digest=turn_context_digest(context)),
        )
        values.update(overrides)
        return make_capture(**values)


# ---------------------------------------------------------------------------
# Real PostgreSQL wiring (skipped without TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.fixture
def dialogue_env(shadow_env, monkeypatch):
    """Dialogue memory enabled on the disposable schema, alongside the shadow environment."""

    from app.services import cognitive_shadow as shadow_service
    from app.services import dialogue_memory as service
    from app.workers import dialogue_memory as worker

    shadow_env.configure(cognitive_dialogue_memory_enabled=True, cognitive_dialogue_memory_cache_enabled=False)
    settings = lambda: shadow_env.configure()  # noqa: E731 - current settings
    for module in (service, worker):
        monkeypatch.setattr(module, "get_settings", settings)
    monkeypatch.setattr(worker, "SessionLocal", shadow_env.db)
    monkeypatch.setattr(service, "wake_worker", lambda: None)
    monkeypatch.setattr(service, "_cache_override", None)
    service.metrics.reset()
    shadow_service.metrics.reset()
    return shadow_env


__all__ = ["CREATED_AT", "Script", "dialogue_env", "get_settings", "uid"]
