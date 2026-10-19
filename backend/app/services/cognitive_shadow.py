"""Durable queue and turn-time capture for Cognitive Live-Shadow Trace V0.

Observation only. Nothing here is read by the live chat path: the chat worker
and the local WebSocket fallback call ``capture_turn`` once, before generation,
and ignore its result. Capture never raises, never changes a ChatJob, and never
publishes a chat event. PostgreSQL is the queue's source of truth; Redis only
wakes the shadow worker.

Logs carry ids and stable codes only — never message text or payloads.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from pydantic import ValidationError
from sqlalchemy import and_, case, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineTrace, CognitivePipelineV0
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import (
    ModelSemanticGrounder, UnavailableSemanticGrounder,
)
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticParserError, SemanticParserErrorCode
from app.ai.Cognitive.system.v0.contracts.safety import (
    APPLICATION_SAFETY_POLICY_VERSION,
    SafetyDecisionProvenance,
    SafetyDisposition,
)
from app.ai.Cognitive.system.v0.live_shadow.compiler import PredecessorRecord
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import (
    ALWAYS_OMITTED,
    ALWAYS_OMITTED_V03,
    CAPABILITY_MAP_VERSION,
    CAPTURE_MESSAGE_LIMIT,
    CAPTURE_PRODUCER_VERSION,
    CAPTURE_SCHEMA_VERSION,
    CAPTURE_SCHEMA_VERSION_V03,
    CAPTURE_SCHEMA_VERSION_V04,
    DIALOGUE_CAPTURE_SCHEMAS,
    DIALOGUE_SUPPLIED_INPUTS,
    CapturedDialogueMemory,
    CapturedInterpretedDialogue,
    MEMORY_RETRIEVAL_POLICY_VERSION,
    CapturedAttachment,
    CapturedCapabilities,
    CapturedMessage,
    CapturedResponseObligation,
    CapturedSafetyDecision,
    CognitiveShadowCapture,
    CognitiveShadowTerminalRecord,
    ConfiguredLanguage,
    CoreMemoryLineage,
    GenerationPath,
    OmittedInput,
    PersonalityMissingReason,
    ShadowPersonalityProjection,
    ShadowSourceChannel,
    TerminalStatus,
    capture_id_for,
    core_memory_ref_for,
)
from app.ai.Cognitive.system.v0.contracts.character import Character, character_from_identity
from app.ai.Cognitive.system.v0.personality_runtime_v0 import (
    PersonalityMappingError,
    PersonalityMappingErrorCode,
    PersonalityRuntimeMapperV0,
    build_source,
    validate_core_identity,
)
from app.ai.Cognitive.system.v0.live_shadow.service import (
    ReplayReport,
    ReplayStatus,
    build_pipeline,
    replay,
)
from app.ai.Cognitive.system.v0.live_shadow.realization import replay_realization
from app.ai.Cognitive.system.v0.contracts.realization import ProviderUnavailableReason, RealizationModelProvenance
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderConfigurationError
from app.core.config import get_settings
from app.db.models import (
    ChatJob,
    ChatMessage,
    CognitiveDialogueInterpretation,
    CognitiveShadowJob,
    CognitiveShadowTrace,
    Conversation,
    CoreMemory,
    GoogleAccount,
)
from app.db.session import SessionLocal
from app.services import asset_store, image_store

logger = logging.getLogger(__name__)

# Scope strings are duplicated from app.ai.integrations.google.scopes on
# purpose: importing that package loads the chat agent, which the shadow
# worker must never import.  test_cognitive_shadow_chat_isolation pins them.
_GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


# ---------------------------------------------------------------------------
# Content-free, in-process metrics
# ---------------------------------------------------------------------------

class ShadowMetrics:
    """Aggregate counters and latency summaries with no conversation content.

    Kept in process (not exported): action-key aggregates stay access-
    controlled until reviewed for privacy risk.  No metric treats continued
    conversation or silence as success.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._summaries: dict[str, list[float]] = {}

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> str:
        return name + "".join(f"|{key}={labels[key]}" for key in sorted(labels))

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        with self._lock:
            self._counts[self._key(name, labels)] += amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            summary = self._summaries.setdefault(key, [0.0, 0.0, 0.0])  # count, sum, max
            summary[0] += 1
            summary[1] += value
            summary[2] = max(summary[2], value)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "summaries": {
                    key: {"count": int(count), "sum": total, "max": peak}
                    for key, (count, total, peak) in self._summaries.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._summaries.clear()


metrics = ShadowMetrics()


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def is_sampled(turn_id: uuid.UUID | str, *, policy_version: str, rate: float) -> bool:
    """Deterministic: the same turn gets the same answer in every process.

    Only the policy version and the durable turn id are hashed — never content,
    so sampling cannot target sensitive conversations.
    """

    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    digest = hashlib.sha256(f"{policy_version}:{turn_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def capture_enabled() -> bool:
    return bool(get_settings().cognitive_live_shadow_enabled)


# ---------------------------------------------------------------------------
# Turn-time capture
# ---------------------------------------------------------------------------

class CaptureRejected(RuntimeError):
    """A capture could not be built from trusted server facts; carries a code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_EFFECTIVE_TIME = func.coalesce(ChatJob.created_at, ChatMessage.created_at)
_ROLE_ORDER = case((ChatMessage.role == "assistant", 1), else_=0)
# A cancelled assistant row with no text is a durable outcome marker, not an
# utterance (the live path drops it from model history for the same reason).
_ELIGIBLE = and_(
    ChatMessage.role.in_(("user", "assistant")),
    ~and_(ChatMessage.role == "assistant", ChatMessage.status == "cancelled", ChatMessage.content == ""),
)


def _ordered_messages():
    """Chat records with their source-turn ordering key.

    A message's effective time is its turn's ChatJob creation time when one
    exists, otherwise its own creation time — the same boundary the worker's
    history uses — so an interrupted reply persisted late still sorts inside
    its own turn.
    """

    return (
        select(ChatMessage, _EFFECTIVE_TIME.label("effective_at"))
        .outerjoin(ChatJob, ChatJob.turn_id == ChatMessage.turn_id)
    )


def _bounded_messages(
    db: Session, *, conversation_id: uuid.UUID, focus: ChatMessage, focus_at: datetime
) -> tuple[tuple[CapturedMessage, ...], bool, str | None]:
    before_focus = tuple_(_EFFECTIVE_TIME, _ROLE_ORDER, ChatMessage.created_at, ChatMessage.id) < tuple_(
        focus_at, 0, focus.created_at, focus.id
    )
    prior = list(db.execute(
        _ordered_messages()
        .where(ChatMessage.conversation_id == conversation_id, _ELIGIBLE, before_focus)
        .order_by(
            _EFFECTIVE_TIME.desc(), _ROLE_ORDER.desc(), ChatMessage.created_at.desc(), ChatMessage.id.desc()
        )
        .limit(CAPTURE_MESSAGE_LIMIT)  # one beyond what fits reveals truncation
    ).scalars())
    truncated = len(prior) > CAPTURE_MESSAGE_LIMIT - 1
    kept = list(reversed(prior[: CAPTURE_MESSAGE_LIMIT - 1]))
    messages = tuple(
        CapturedMessage(message_id=str(item.id), role=item.role, content=item.content)
        for item in (*kept, focus)
    )
    previous_user = db.scalar(
        _ordered_messages()
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user", before_focus)
        .order_by(
            _EFFECTIVE_TIME.desc(), _ROLE_ORDER.desc(), ChatMessage.created_at.desc(), ChatMessage.id.desc()
        )
        .limit(1)
    )
    return messages, truncated, (str(previous_user.id) if previous_user is not None else None)


def _bounded_messages_by_position(
    db: Session, *, conversation_id: uuid.UUID, focus: ChatMessage
) -> tuple[tuple[CapturedMessage, ...], bool, str | None]:
    """Capture v0.3: the window is bounded by durable conversation position.

    Nothing positioned after the focus message can enter -- not even an
    earlier turn's reply that was persisted late.
    """

    before_focus = ChatMessage.conversation_sequence < focus.conversation_sequence
    prior = list(db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, _ELIGIBLE, before_focus)
        .order_by(ChatMessage.conversation_sequence.desc())
        .limit(CAPTURE_MESSAGE_LIMIT)  # one beyond what fits reveals truncation
    ))
    truncated = len(prior) > CAPTURE_MESSAGE_LIMIT - 1
    kept = list(reversed(prior[: CAPTURE_MESSAGE_LIMIT - 1]))
    messages = tuple(
        CapturedMessage(message_id=str(item.id), role=item.role, content=item.content,
                        conversation_sequence=item.conversation_sequence)
        for item in (*kept, focus)
    )
    previous_user = db.scalar(
        select(ChatMessage.id)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user", before_focus)
        .order_by(ChatMessage.conversation_sequence.desc())
        .limit(1)
    )
    return messages, truncated, (str(previous_user) if previous_user is not None else None)


def _dialogue_capture(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, turn_id: uuid.UUID, focus_message_id: uuid.UUID,
) -> CapturedDialogueMemory:
    """The dialogue turn context through the message before the focus, or its typed failure.

    A typed dialogue failure never blocks the capture: it is recorded, and
    the observation of this turn then fails with that code.
    """

    from app.ai.Cognitive.system.v0.contracts.dialogue_memory import turn_context_digest
    from app.ai.Cognitive.system.v0.dialogue_memory_v0.errors import DialogueMemoryError
    from app.services import dialogue_memory

    try:
        context = dialogue_memory.turn_context(
            db, user_id=user_id, conversation_id=conversation_id, turn_id=turn_id,
            focus_message_id=focus_message_id,
        )
    except DialogueMemoryError as exc:
        db.rollback()
        metrics.increment("dialogue_context_failures", code=exc.code.value)
        return CapturedDialogueMemory(status="unavailable", failure=exc.failure)
    return CapturedDialogueMemory(status="available", turn_context=context,
                                  turn_context_digest=turn_context_digest(context))


def _call_one_reasoning(settings) -> str | None:
    effort = getattr(settings, "cognitive_shadow_call_one_reasoning_effort", "none")
    return None if effort == "none" else effort


def _interpreted_capture(
    db: Session, *, conversation_id: uuid.UUID, focus: ChatMessage, messages: tuple[CapturedMessage, ...],
) -> CapturedInterpretedDialogue:
    """Stored interpretation records before the focus, plus earlier window messages still being read."""

    from app.ai.Cognitive.system.v0.contracts.dialogue_interpretation import (
        MAX_FOLDED_RECORDS, DialogueInterpretationRecord,
    )

    rows = db.scalars(
        select(CognitiveDialogueInterpretation)
        .where(CognitiveDialogueInterpretation.conversation_id == conversation_id,
               CognitiveDialogueInterpretation.source_conversation_sequence < focus.conversation_sequence)
        .order_by(CognitiveDialogueInterpretation.source_conversation_sequence.desc())
        .limit(MAX_FOLDED_RECORDS)
    ).all()
    records = []
    for row in rows:
        record = DialogueInterpretationRecord.model_validate(row.payload)
        if record.digest != row.record_digest:
            metrics.increment("dialogue_interpretation_digest_mismatch")
            continue
        records.append(record)
    records.sort(key=lambda record: record.source_conversation_sequence)
    read = {record.source_message_ref for record in records}
    unread = [message.message_id for message in messages[:-1] if message.role == "user" and message.message_id not in read]
    pending = set()
    if unread:
        pending = {str(ref) for ref in db.scalars(
            select(CognitiveShadowJob.user_message_id).where(
                CognitiveShadowJob.conversation_id == conversation_id,
                CognitiveShadowJob.user_message_id.in_([uuid.UUID(ref) for ref in unread]),
                CognitiveShadowJob.status.in_(("pending", "processing")),
            )
        )}
    return CapturedInterpretedDialogue(records=tuple(records),
                                       pending_message_refs=tuple(ref for ref in unread if ref in pending))


def _store_interpretation(db: Session, *, record: CognitiveShadowTerminalRecord, job: CognitiveShadowJob) -> None:
    """Keep the dialogue updates a completed v1.7 turn read from its own message.

    Runs in a savepoint: a failure here is counted and never costs the trace.
    """

    from app.ai.Cognitive.system.v0.contracts.dialogue_interpretation import (
        DialogueInterpretationRecord, InterpretedUpdate,
    )

    trace = record.pipeline_trace
    orchestration = trace.orchestration if trace is not None else None
    if record.status != TerminalStatus.COMPLETED or orchestration is None:
        return
    call_one = next((call for call in orchestration.calls if call.stage == "call_one"), None)
    compiled = call_one.compiled_proposal if call_one is not None else None
    if not compiled or "dialogue_updates" not in compiled:
        return
    sequence = db.scalar(select(ChatMessage.conversation_sequence).where(ChatMessage.id == job.user_message_id))
    if sequence is None:
        return
    try:
        with db.begin_nested():
            interpretation = DialogueInterpretationRecord(
                source_message_ref=str(job.user_message_id), source_conversation_sequence=sequence,
                updates=tuple(InterpretedUpdate.model_validate(item) for item in compiled["dialogue_updates"]),
            )
            db.add(CognitiveDialogueInterpretation(
                user_id=job.user_id, conversation_id=job.conversation_id, source_message_id=job.user_message_id,
                source_conversation_sequence=sequence, shadow_trace_id=uuid.UUID(record.terminal_id),
                schema_version=interpretation.schema_version, payload=interpretation.model_dump(mode="json"),
                record_digest=interpretation.digest,
            ))
            db.flush()
        metrics.increment("dialogue_interpretations_stored", updates=str(len(interpretation.updates)))
    except Exception as exc:  # noqa: BLE001 - never fail the terminal write
        metrics.increment("dialogue_interpretation_store_failures", error=type(exc).__name__)


def _verified_attachments(
    user_id: uuid.UUID, focus: ChatMessage
) -> tuple[bool, tuple[CapturedAttachment, ...]]:
    """Attachments of the captured turn that exist in this user's own store.

    A client-supplied id alone is never evidence: each persisted id is re-read
    from the per-user store (which cannot reach another user's files). An id
    that no longer resolves is absent; a store that cannot be read makes the
    attachment subsystem unavailable, which is different from "no attachment".
    """

    facts: dict[CapturedAttachment, None] = {}
    try:
        for image_id in focus.image_ids or ():
            try:
                image_store.get_record(user_id, image_id)
            except (image_store.ImageNotFoundError, ValueError, TypeError):
                continue
            facts[CapturedAttachment(attachment_id=image_id, store="image_store")] = None
        for asset_id in focus.asset_ids or ():
            try:
                asset_store.get_path(user_id, asset_id)
            except (asset_store.AssetNotFoundError, ValueError, TypeError):
                continue
            facts[CapturedAttachment(attachment_id=asset_id, store="asset_store")] = None
    except OSError:
        return False, ()
    return True, tuple(facts)


def _capabilities(db: Session, *, user_id: uuid.UUID) -> CapturedCapabilities:
    """Map server-owned facts through the registered, versioned capability map.

    Scopes support the projection; tokens never enter the capture.
    """

    settings = get_settings()
    granted = set((db.scalar(select(GoogleAccount.scopes).where(GoogleAccount.user_id == user_id)) or "").split())
    keys = set()
    if settings.macrovision_tools_enabled:
        keys.add("nutrition_image_estimation")
    if settings.gmail_tools_enabled and _GMAIL_MODIFY_SCOPE in granted:
        keys.add("email_mailbox_access")
    if settings.drive_tools_enabled and _DRIVE_SCOPE in granted:
        keys.add("drive_document_access")
    if settings.blender_tools_enabled:
        keys.add("blender_asset_tools")
    return CapturedCapabilities(mapping_version=CAPABILITY_MAP_VERSION, capability_keys=tuple(sorted(keys)))


_MAPPER_FAILURE_REASONS = {
    PersonalityMappingErrorCode.CORE_MEMORY_UNAVAILABLE: PersonalityMissingReason.CORE_MEMORY_UNAVAILABLE,
    PersonalityMappingErrorCode.CORE_MEMORY_INACTIVE: PersonalityMissingReason.CORE_MEMORY_INACTIVE,
    PersonalityMappingErrorCode.CORE_MEMORY_SCHEMA_UNSUPPORTED: PersonalityMissingReason.RUNTIME_MAPPER_SCHEMA_UNSUPPORTED,
    PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID: PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID,
    PersonalityMappingErrorCode.SOURCE_FIELD_MISSING: PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID,
    PersonalityMappingErrorCode.SOURCE_TYPE_INVALID: PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID,
    PersonalityMappingErrorCode.SOURCE_NONFINITE: PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID,
    PersonalityMappingErrorCode.SOURCE_OUT_OF_RANGE: PersonalityMissingReason.RUNTIME_MAPPER_INPUT_INVALID,
    PersonalityMappingErrorCode.PROJECTION_INVALID: PersonalityMissingReason.RUNTIME_MAPPER_PROJECTION_INVALID,
    PersonalityMappingErrorCode.INTERNAL_CONTRACT: PersonalityMissingReason.RUNTIME_MAPPER_PROJECTION_INVALID,
}
# The CoreMemory JSON groups the mapper's source builder may read from.
_CORE_GROUPS = ("interpretation_biases", "perception_priors", "drives", "action_style", "traits")
_mapper = PersonalityRuntimeMapperV0()


def _core_personality(
    db: Session, *, turn_id: uuid.UUID,
) -> tuple[CoreMemoryLineage | None, ShadowPersonalityProjection | None, PersonalityMissingReason | None,
           Character | None]:
    """One turn-time CoreMemory read producing lineage, personality and (core v2+) character.

    The configured source is used exclusively; there is no fallback between
    the runtime mapper and the reviewed baseline. Any failure yields no
    projection and a typed missing reason, so the terminal record is
    ``missing_personality_projection``. Logs carry only the turn, the core
    reference, and a stable code — never CoreMemory content or mapped values.
    """

    settings = get_settings()
    row = db.scalar(select(CoreMemory).where(CoreMemory.name == settings.cognitive_live_shadow_core_memory_name))
    if row is None:
        return None, None, PersonalityMissingReason.CORE_MEMORY_UNAVAILABLE, None
    if not row.is_active:
        return None, None, PersonalityMissingReason.CORE_MEMORY_INACTIVE, None

    if settings.cognitive_live_shadow_personality_source == "reviewed_baseline":
        try:
            core = CoreMemoryLineage(
                core_memory_ref=core_memory_ref_for(row.name, row.version),
                name=row.name, version=row.version, schema_version=row.schema_version,
            )
        except ValueError:
            return None, None, PersonalityMissingReason.CORE_MEMORY_UNAVAILABLE, None
        projection, reason = _reviewed_baseline(core)
        return core, projection, reason, _character_of(row, core, turn_id=turn_id) if projection else None

    # Runtime mapper: supported schema and positive version before lineage.
    try:
        core_ref = validate_core_identity(name=row.name, version=row.version, schema_version=row.schema_version)
    except PersonalityMappingError as exc:
        metrics.increment("personality_mapping_failures", code=exc.code.value)
        logger.warning("Cognitive shadow personality mapping failed for turn %s: %s", turn_id, exc.code.value)
        return None, None, _MAPPER_FAILURE_REASONS[exc.code], None
    core = CoreMemoryLineage(
        core_memory_ref=core_ref, name=row.name, version=row.version, schema_version=row.schema_version,
    )
    try:
        # Copy exactly the consumed groups out of the ORM row as plain JSON;
        # the pure mapper never sees a database object.
        groups = {name: copy.deepcopy(getattr(row, name)) for name in _CORE_GROUPS}
        source = build_source(
            name=row.name, version=row.version, schema_version=row.schema_version, groups=groups,
        )
        if source.core_memory_ref != core.core_memory_ref:
            raise PersonalityMappingError(PersonalityMappingErrorCode.CORE_MEMORY_LINEAGE_INVALID)
        projection = _mapper.compile(source)
    except PersonalityMappingError as exc:
        metrics.increment("personality_mapping_failures", code=exc.code.value)
        logger.warning(
            "Cognitive shadow personality mapping failed for turn %s (%s): %s",
            turn_id, core.core_memory_ref, exc.code.value,
        )
        return core, None, _MAPPER_FAILURE_REASONS[exc.code], None
    return core, projection, None, _character_of(row, core, turn_id=turn_id)


def _character_of(row: CoreMemory, core: CoreMemoryLineage, *, turn_id: uuid.UUID) -> Character | None:
    """Her values, principles and beliefs from the same core-memory row, or ``None``.

    A core without a character (version 1) or with an invalid one captures
    none; the turn still runs, with opinions reasoned without it.
    """
    try:
        return character_from_identity(copy.deepcopy(row.identity), core_memory_ref=core.core_memory_ref)
    except ValueError:
        metrics.increment("personality_mapping_failures", code="character_invalid")
        logger.warning("Cognitive shadow character invalid for turn %s (%s)", turn_id, core.core_memory_ref)
        return None


def _reviewed_baseline(
    core: CoreMemoryLineage,
) -> tuple[ShadowPersonalityProjection | None, PersonalityMissingReason | None]:
    """Explicit compatibility mode: only the configured reviewed baseline file."""

    path = get_settings().cognitive_live_shadow_personality_baseline_path
    if not path:
        return None, PersonalityMissingReason.NO_ACCEPTED_SOURCE
    try:
        projection = ShadowPersonalityProjection.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None, PersonalityMissingReason.BASELINE_INVALID
    if projection.projection_kind != "reviewed_baseline":
        # Only the accepted Personality Runtime Mapper may produce a
        # ``runtime_mapper`` projection; a configuration file cannot claim it.
        return None, PersonalityMissingReason.BASELINE_INVALID
    if projection.core_memory_ref != core.core_memory_ref:
        return None, PersonalityMissingReason.CORE_MEMORY_MISMATCH
    return projection, None


def build_capture(
    db: Session,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    user_message_id: uuid.UUID,
    chat_job_id: uuid.UUID | None,
    generation_path: GenerationPath,
    source_channel: ShadowSourceChannel,
) -> tuple[CognitiveShadowCapture, datetime]:
    """Build the immutable capture from server-owned facts at this instant.

    Returns the capture and its source-turn ordering time.  Ownership of every
    record is verified against the user; a mismatch is rejected, not repaired.
    """

    settings = get_settings()
    owned = db.scalar(select(Conversation.id).where(
        Conversation.id == conversation_id, Conversation.user_id == user_id,
    ))
    if owned is None:
        raise CaptureRejected("conversation_not_owned")
    row = db.execute(_ordered_messages().where(ChatMessage.id == user_message_id)).one_or_none()
    if row is None:
        raise CaptureRejected("focus_message_missing")
    focus, focus_at = row
    if focus.conversation_id != conversation_id or focus.role != "user" or focus.turn_id != turn_id:
        raise CaptureRejected("focus_message_mismatch")
    if chat_job_id is not None:
        job = db.get(ChatJob, chat_job_id)
        if job is None or (job.turn_id, job.user_id, job.conversation_id, job.user_message_id) != (
            turn_id, user_id, conversation_id, user_message_id,
        ):
            raise CaptureRejected("chat_job_mismatch")

    from app.services import dialogue_memory

    dialogue = interpreted = None
    if dialogue_memory.enabled():
        # Capture v0.3: position-bounded window plus the committed dialogue
        # context immediately before the focus (never the reply to come).
        messages, truncated, previous_user = _bounded_messages_by_position(
            db, conversation_id=conversation_id, focus=focus,
        )
        dialogue = _dialogue_capture(db, user_id=user_id, conversation_id=conversation_id, turn_id=turn_id,
                                     focus_message_id=focus.id)
        if settings.cognitive_dialogue_interpretation_enabled:
            # Capture v0.4: what earlier turns interpreted between messages.
            interpreted = _interpreted_capture(db, conversation_id=conversation_id, focus=focus, messages=messages)
    else:
        messages, truncated, previous_user = _bounded_messages(
            db, conversation_id=conversation_id, focus=focus, focus_at=focus_at,
        )
    attachments_available, attachments = _verified_attachments(user_id, focus)
    core, personality, missing_reason, character = _core_personality(db, turn_id=turn_id)
    omitted = set(ALWAYS_OMITTED if dialogue is None else ALWAYS_OMITTED_V03) | {
        OmittedInput.MEMORY_PROJECTION,
        OmittedInput.RELATIONSHIP_PROJECTION,
        OmittedInput.APPLICATION_SAFETY_POLICY,
    }
    if dialogue is not None and dialogue.status == "unavailable":
        omitted |= DIALOGUE_SUPPLIED_INPUTS | {OmittedInput.DIALOGUE_WORKING_MEMORY}
    if personality is None:
        omitted.add(OmittedInput.PERSONALITY_PROJECTION)

    capture = CognitiveShadowCapture(
        capture_id=capture_id_for(str(turn_id)),
        schema_version=(CAPTURE_SCHEMA_VERSION if dialogue is None
                        else CAPTURE_SCHEMA_VERSION_V04 if interpreted is not None else CAPTURE_SCHEMA_VERSION_V03),
        dialogue=dialogue,
        interpreted_dialogue=interpreted,
        character=character,
        producer_version=CAPTURE_PRODUCER_VERSION,
        user_ref=str(user_id),
        conversation_ref=str(conversation_id),
        turn_id=str(turn_id),
        chat_job_ref=str(chat_job_id) if chat_job_id is not None else None,
        generation_path=generation_path,
        focus_message_id=str(focus.id),
        previous_user_message_id=previous_user,
        messages=messages,
        context_truncated=truncated,
        source_channel=source_channel,
        language=ConfiguredLanguage(tag=settings.cognitive_live_shadow_language_tag),
        attachments_available=attachments_available,
        attachments=attachments,
        capabilities=_capabilities(db, user_id=user_id),
        # No application safety policy producer exists yet: record that it was
        # unavailable. Never a synthetic ``allow``.
        safety_decision=CapturedSafetyDecision(
            policy_version=APPLICATION_SAFETY_POLICY_VERSION,
            disposition=SafetyDisposition.UNAVAILABLE,
            provenance=SafetyDecisionProvenance.RUNTIME_UNAVAILABLE,
            producer_ref=f"{CAPTURE_PRODUCER_VERSION}:application-safety-policy-unavailable",
        ),
        response_obligation=CapturedResponseObligation(
            evidence_ref=f"chat-turn:{turn_id}:user-message:{focus.id}",
        ),
        memory_retrieval_policy_version=MEMORY_RETRIEVAL_POLICY_VERSION,
        core_memory=core,
        personality=personality,
        personality_missing_reason=missing_reason,
        omitted_inputs=tuple(sorted(omitted)),
        sampling_policy_version=settings.cognitive_live_shadow_sampling_policy_version,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
    return capture, focus_at


def enqueue_capture(
    db: Session,
    capture: CognitiveShadowCapture,
    *,
    source_turn_at: datetime,
) -> bool:
    """Insert one capture/job idempotently. True if this call created it."""

    payload = capture.model_dump(mode="json")
    statement = insert(CognitiveShadowJob).values(
        id=uuid.UUID(capture.capture_id),
        user_id=uuid.UUID(capture.user_ref),
        conversation_id=uuid.UUID(capture.conversation_ref),
        turn_id=uuid.UUID(capture.turn_id),
        chat_job_id=uuid.UUID(capture.chat_job_ref) if capture.chat_job_ref else None,
        user_message_id=uuid.UUID(capture.focus_message_id),
        generation_path=capture.generation_path.value,
        capture_schema_version=capture.schema_version,
        capture_payload=payload,
        capture_digest=payload_digest(payload),
        sampling_policy_version=capture.sampling_policy_version,
        source_channel=capture.source_channel.value,
        source_turn_at=source_turn_at,
        status="pending",
        attempts=0,
        captured_at=datetime.fromisoformat(capture.captured_at),
    ).on_conflict_do_nothing()
    inserted = db.execute(statement.returning(CognitiveShadowJob.id)).scalar_one_or_none()
    db.commit()
    return inserted is not None


def wake_worker(job_id: uuid.UUID | str = "wake") -> None:
    settings = get_settings()
    if settings.redis_url:
        try:
            redis.from_url(settings.redis_url).rpush(settings.cognitive_live_shadow_queue_name, str(job_id))
        except Exception:
            pass  # PostgreSQL polling is the source of truth.


def capture_turn(
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    user_message_id: uuid.UUID,
    chat_job_id: uuid.UUID | None,
    generation_path: GenerationPath,
    source_channel: ShadowSourceChannel,
    session_factory: Callable[[], Session] | None = None,
) -> bool:
    """Capture one durable turn for shadow observation, if enabled and sampled.

    The single shared capture owner for web, voice, WhatsApp, and the local
    fallback. Never raises and returns nothing the caller may act on beyond
    "captured or not"; the live turn proceeds identically either way.
    """

    started = time.perf_counter()
    try:
        settings = get_settings()
        if not settings.cognitive_live_shadow_enabled:
            return False
        metrics.increment("eligible_turns", channel=source_channel.value, path=generation_path.value)
        if not is_sampled(
            turn_id,
            policy_version=settings.cognitive_live_shadow_sampling_policy_version,
            rate=settings.cognitive_live_shadow_sample_rate,
        ):
            metrics.increment("unsampled_turns")
            return False
        metrics.increment("sampled_turns")
        if settings.cognitive_live_shadow_retention_days is None:
            metrics.increment("capture_failures", code="retention_unreviewed")
            logger.warning("Cognitive shadow capture refused for turn %s: no reviewed retention", turn_id)
            return False
        factory = session_factory or SessionLocal
        with factory() as db:
            capture, source_turn_at = build_capture(
                db, user_id=user_id, conversation_id=conversation_id, turn_id=turn_id,
                user_message_id=user_message_id, chat_job_id=chat_job_id,
                generation_path=generation_path, source_channel=source_channel,
            )
            created = enqueue_capture(db, capture, source_turn_at=source_turn_at)
        metrics.increment("capture_successes" if created else "capture_duplicates")
        if created:
            wake_worker(capture.capture_id)
        return created
    except CaptureRejected as exc:
        metrics.increment("capture_failures", code=exc.code)
        logger.warning("Cognitive shadow capture rejected for turn %s: %s", turn_id, exc.code)
        return False
    except Exception as exc:
        metrics.increment("capture_failures", code=type(exc).__name__)
        # Content-free: ids and the exception class only, never its message.
        logger.error("Cognitive shadow capture failed for turn %s (%s)", turn_id, type(exc).__name__)
        return False
    finally:
        metrics.observe("capture_ms", (time.perf_counter() - started) * 1000)


def source_channel_for(payload: dict) -> ShadowSourceChannel:
    if payload.get("channel") == "whatsapp":
        return ShadowSourceChannel.WHATSAPP
    if payload.get("voice"):
        return ShadowSourceChannel.VOICE
    return ShadowSourceChannel.WEB


# ---------------------------------------------------------------------------
# Queue: claiming, predecessor lineage, terminal persistence
# ---------------------------------------------------------------------------

_TERMINAL = ("completed", "failed", "skipped")


def _conversation_lock_key(conversation_id: uuid.UUID) -> int:
    """A shadow-only advisory lock key, never the chat conversation row lock."""

    digest = hashlib.sha256(f"cognitive-shadow:{conversation_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_conversation(db: Session, conversation_id: uuid.UUID) -> None:
    db.execute(select(func.pg_advisory_xact_lock(_conversation_lock_key(conversation_id))))


def _blocked(job_table, now: datetime):
    """An earlier unfinished job, or any live lease, in the same conversation."""

    other = aliased(CognitiveShadowJob)
    return exists(select(other.id).where(
        other.conversation_id == job_table.conversation_id,
        other.id != job_table.id,
        or_(
            and_(
                other.status.in_(("pending", "processing")),
                tuple_(other.source_turn_at, other.turn_id) < tuple_(job_table.source_turn_at, job_table.turn_id),
            ),
            and_(other.status == "processing", other.lease_expires_at > now),
        ),
    ))


def claim_next_job(db: Session, *, lease_seconds: int) -> CognitiveShadowJob | None:
    """Claim the earliest eligible job, preserving per-conversation source order."""

    now = datetime.now(timezone.utc)
    job = db.scalar(
        select(CognitiveShadowJob)
        .where(
            or_(
                CognitiveShadowJob.status == "pending",
                and_(CognitiveShadowJob.status == "processing", CognitiveShadowJob.lease_expires_at <= now),
            ),
            ~_blocked(CognitiveShadowJob, now),
        )
        .order_by(CognitiveShadowJob.source_turn_at, CognitiveShadowJob.turn_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.rollback()
        return None
    # Row locks alone cannot stop two workers locking two different turns of
    # one conversation; serialize the final claim per conversation and recheck.
    lock_conversation(db, job.conversation_id)
    still_blocked = db.scalar(
        select(_blocked(CognitiveShadowJob, now)).where(CognitiveShadowJob.id == job.id)
    )
    if still_blocked:
        db.rollback()
        return None
    job.status = "processing"
    job.attempts += 1
    job.claim_token = secrets.token_urlsafe(32)
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()
    db.refresh(job)
    return job


class PredecessorPending(RuntimeError):
    """An earlier job of the conversation has no terminal record yet (retry)."""


def load_predecessor(db: Session, job: CognitiveShadowJob) -> PredecessorRecord | None:
    """The previous captured turn of this conversation, in source-turn order.

    Never selected by completion time. A stored trace that no longer validates
    under the installed contracts, or whose digest does not match, is handed
    over without a trace so lineage records a gap instead of continuing it.
    """

    previous = db.scalar(
        select(CognitiveShadowJob)
        .where(
            CognitiveShadowJob.conversation_id == job.conversation_id,
            CognitiveShadowJob.id != job.id,
            tuple_(CognitiveShadowJob.source_turn_at, CognitiveShadowJob.turn_id)
            < tuple_(job.source_turn_at, job.turn_id),
        )
        .order_by(CognitiveShadowJob.source_turn_at.desc(), CognitiveShadowJob.turn_id.desc())
        .limit(1)
    )
    if previous is None:
        return None
    terminal = db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.job_id == previous.id))
    if terminal is None:
        raise PredecessorPending(str(previous.id))
    status = TerminalStatus(terminal.terminal_status)
    trace = None
    if status == TerminalStatus.COMPLETED and terminal.pipeline_trace_payload is not None:
        if payload_digest(terminal.pipeline_trace_payload) == terminal.trace_payload_digest:
            try:
                trace = CognitivePipelineTrace.model_validate(terminal.pipeline_trace_payload)
            except ValidationError:
                trace = None
    return PredecessorRecord(
        terminal_id=str(terminal.id),
        turn_id=str(terminal.turn_id),
        user_ref=str(terminal.user_id),
        conversation_ref=str(terminal.conversation_id),
        focus_message_id=str(previous.user_message_id),
        segment_id=str(terminal.segment_id),
        status=status,
        pipeline_trace=trace,
    )


def terminal_row(record: CognitiveShadowTerminalRecord, *, job: CognitiveShadowJob) -> CognitiveShadowTrace:
    return CognitiveShadowTrace(
        id=uuid.UUID(record.terminal_id),
        job_id=job.id,
        user_id=job.user_id,
        conversation_id=job.conversation_id,
        turn_id=job.turn_id,
        segment_id=uuid.UUID(record.segment_id),
        predecessor_trace_id=uuid.UUID(record.predecessor_trace_id) if record.predecessor_trace_id else None,
        segment_reset_reason=record.segment_reset_reason.value if record.segment_reset_reason else None,
        missing_predecessor_refs=list(record.missing_predecessor_refs),
        terminal_schema_version=record.terminal_schema_version,
        terminal_status=record.status.value,
        skip_reason=record.skip_reason.value if record.skip_reason else None,
        compiled_request_payload=(
            record.compiled_request.model_dump(mode="json") if record.compiled_request is not None else None
        ),
        pipeline_trace_payload=(
            record.pipeline_trace.model_dump(mode="json") if record.pipeline_trace is not None else None
        ),
        failure_payload=record.failure.model_dump(mode="json") if record.failure is not None else None,
        capture_digest=record.capture_digest,
        compiled_request_digest=record.compiled_request_digest,
        trace_payload_digest=record.trace_payload_digest,
        semantic_digest=record.semantic_digest,
        versions=record.versions.model_dump(mode="json"),
        timings=record.worker_timings.model_dump(mode="json") if record.worker_timings is not None else None,
        realization_status=record.realization_status.value,
        realization_request_payload=(
            record.realization_request.model_dump(mode="json") if record.realization_request is not None else None
        ),
        realization_request_digest=record.realization_request_digest,
        realization_trace_payload=(
            record.realization_trace.model_dump(mode="json") if record.realization_trace is not None else None
        ),
        realization_trace_digest=record.realization_trace_digest,
        realization_semantic_digest=record.realization_semantic_digest,
        realization_failure_payload=(
            record.realization_failure.model_dump(mode="json") if record.realization_failure is not None else None
        ),
        realization_timings=(
            record.realization_timings.model_dump(mode="json") if record.realization_timings is not None else None
        ),
        started_at=datetime.fromisoformat(record.started_at),
        completed_at=datetime.fromisoformat(record.completed_at),
    )


def persist_terminal(
    db: Session, *, job: CognitiveShadowJob, claim_token: str, record: CognitiveShadowTerminalRecord
) -> bool:
    """Insert the one terminal row and complete the owned job atomically.

    A stale worker (reclaimed job, changed token) writes nothing.
    """

    if (
        record.job_id != str(job.id) or record.turn_id != str(job.turn_id)
        or record.user_ref != str(job.user_id) or record.conversation_ref != str(job.conversation_id)
        or record.capture_digest != job.capture_digest
    ):
        raise ValueError("terminal record does not belong to this job")
    lock_conversation(db, job.conversation_id)
    finished = db.execute(
        update(CognitiveShadowJob)
        .where(
            CognitiveShadowJob.id == job.id,
            CognitiveShadowJob.status == "processing",
            CognitiveShadowJob.claim_token == claim_token,
        )
        .values(
            status=record.status.value,
            processed_at=datetime.now(timezone.utc),
            lease_expires_at=None,
            last_error_code=record.failure.code if record.failure is not None else (
                record.skip_reason.value if record.skip_reason is not None else None
            ),
            last_error_safe=record.failure.safe_message if record.failure is not None else None,
        )
    )
    if not finished.rowcount:
        db.rollback()
        return False
    db.add(terminal_row(record, job=job))
    if get_settings().cognitive_dialogue_interpretation_enabled:
        db.flush()
        _store_interpretation(db, record=record, job=job)
    db.commit()
    return True


def release_for_retry(
    db: Session, *, job_id: uuid.UUID, claim_token: str, code: str, safe_message: str
) -> bool:
    """Return an owned job to the queue after a transient failure."""

    result = db.execute(
        update(CognitiveShadowJob)
        .where(
            CognitiveShadowJob.id == job_id,
            CognitiveShadowJob.status == "processing",
            CognitiveShadowJob.claim_token == claim_token,
        )
        .values(
            status="pending", claim_token=None, lease_expires_at=None,
            last_error_code=code, last_error_safe=safe_message,
        )
    )
    db.commit()
    return bool(result.rowcount)


def realization_engine(settings=None) -> RealizationEngineV0:
    """The shadow realization engine for the configured, explicit model.

    No model id (or no API key) means no model: the outcome is
    ``provider_unavailable``. There is no fallback to chat_model,
    cognitive_model, a sub-agent model, or any other configured model.
    """

    settings = settings or get_settings()
    model = None
    reason = ProviderUnavailableReason.MODEL_NOT_CONFIGURED
    model_id = settings.cognitive_shadow_realization_model
    if model_id and model_id.strip() and settings.openai_api_key:
        from app.ai.Cognitive.system.v0.realization_provider import OpenAIRealizationModel

        try:
            model = OpenAIRealizationModel(
                model_id=model_id, api_key=settings.openai_api_key,
                timeout_seconds=settings.cognitive_shadow_realization_timeout_seconds,
            )
        except RealizationProviderConfigurationError as exc:
            # Detected locally, before any API call; never a fallback model.
            metrics.increment("realization_configuration_incompatible", code=exc.code)
            reason = ProviderUnavailableReason.CONFIGURATION_INCOMPATIBLE
    return RealizationEngineV0(
        model, max_attempts=settings.cognitive_shadow_realization_max_attempts,
        unavailable_reason=None if model is not None else reason,
    )


def cognitive_pipeline(turn_id: str, settings=None) -> CognitivePipelineV0:
    """Build the model-grounded shadow pipeline without a rule fallback."""

    settings = settings or get_settings()
    pipeline = build_pipeline(turn_id)
    if getattr(settings, "cognitive_shadow_orchestration", "staged") == "two_call":
        # Protocol v1 runs the configured content subsystem before Call 2;
        # earlier protocols replace this engine with their Call 2 content.
        pipeline.collaboration_engine = collaboration_engine(settings)
        from app.ai.Cognitive.system.v0.two_call.orchestrator import install
        from app.ai.Cognitive.system.v0.appraisal.model_contracts import AppraisalModelError
        from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticParserErrorCode
        from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
        # The combined calls use one explicit model. No stage or chat fallback.
        try:
            model = OpenAITwoCallModel(
                model_id=settings.cognitive_shadow_semantic_parser_model,
                api_key=settings.openai_api_key,
                timeout_seconds=settings.cognitive_shadow_semantic_parser_timeout_seconds,
                max_output_tokens=settings.cognitive_shadow_orchestration_max_output_tokens,
                call_one_reasoning_effort=_call_one_reasoning(settings),
            )
        except AppraisalModelError as exc:
            code = (SemanticParserErrorCode.MODEL_NOT_CONFIGURED if exc.code == "configuration_unavailable"
                    else SemanticParserErrorCode.CONFIGURATION_INCOMPATIBLE)
            pipeline.interpretation_engine = InterpretationEngine(grounder=UnavailableSemanticGrounder(code))
            return pipeline
        return install(pipeline, model, protocol=getattr(settings,"cognitive_shadow_two_call_protocol","legacy-v0.1"))
    from app.ai.Cognitive.system.v0.semantic_parser_provider import OpenAISemanticCandidateModel

    try:
        grounder = ModelSemanticGrounder(OpenAISemanticCandidateModel(
            model_id=settings.cognitive_shadow_semantic_parser_model,
            api_key=settings.openai_api_key,
            timeout_seconds=settings.cognitive_shadow_semantic_parser_timeout_seconds,
        ))
    except SemanticParserError as exc:
        # A configuration failure is a typed, non-retryable shadow failure on
        # every turn -- never the rule grounder and never a worker retry loop.
        grounder = UnavailableSemanticGrounder(exc.code)
    pipeline.interpretation_engine = InterpretationEngine(grounder=grounder)
    if getattr(settings, "cognitive_shadow_appraisal_implementation", "deterministic") == "model":
        from app.ai.Cognitive.system.v0.appraisal.model_engine import ModelAppraisalEngine
        from app.ai.Cognitive.system.v0.appraisal.model_contracts import AppraisalModelError
        from app.ai.Cognitive.system.v0.appraisal_provider import OpenAIAppraisalModel

        try:
            appraisal_model = OpenAIAppraisalModel(
                model_id=settings.cognitive_shadow_appraisal_model,
                api_key=settings.openai_api_key,
                timeout_seconds=settings.cognitive_shadow_appraisal_timeout_seconds,
            )
            appraisal_engine = ModelAppraisalEngine(appraisal_model)
        except AppraisalModelError as exc:
            appraisal_engine = ModelAppraisalEngine(unavailable_code=exc.code)
        pipeline.cognitive_turn_runner.appraisal_engine = appraisal_engine
    pipeline.collaboration_engine = collaboration_engine(settings)
    return pipeline


def collaboration_engine(settings=None):
    """The shadow collaborative-content engine: the configured provider or a typed failure.

    Never a fallback model and never a rule-based substitute: a disabled or
    misconfigured provider makes every ready deliverable a typed failure.
    """

    settings = settings or get_settings()
    from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
    from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import CollaborationProviderError
    from app.ai.Cognitive.system.v0.contracts.collaboration import CollaborationFailureCode

    # Absent (a partial settings object) means disabled, never enabled.
    if not getattr(settings, "cognitive_shadow_collaboration_enabled", False):
        return CollaborativeContentEngine()
    from app.ai.Cognitive.system.v0.collaboration_provider import OpenAICollaborationModel

    try:
        model = OpenAICollaborationModel(
            model_id=settings.cognitive_shadow_collaboration_model or "", api_key=settings.openai_api_key,
            timeout_seconds=settings.cognitive_shadow_collaboration_timeout_seconds,
        )
    except CollaborationProviderError as exc:
        return CollaborativeContentEngine(unavailable_code=exc.code)
    except Exception:  # pragma: no cover - SDK import/construction failure
        return CollaborativeContentEngine(unavailable_code=CollaborationFailureCode.PROVIDER_CONFIGURATION_INCOMPATIBLE)
    return CollaborativeContentEngine(model)


def semantic_parser_configuration_code(settings=None) -> str | None:
    """The stable code if the live-shadow semantic parser cannot run.

    Pure configuration check for worker startup: no client, key use, or call.
    """

    settings = settings or get_settings()
    from app.ai.Cognitive.system.v0.semantic_parser_provider import resolve_provenance

    try:
        resolve_provenance(
            model_id=settings.cognitive_shadow_semantic_parser_model,
            timeout_seconds=settings.cognitive_shadow_semantic_parser_timeout_seconds,
        )
    except SemanticParserError as exc:
        return exc.code.value
    if not settings.openai_api_key:
        return SemanticParserErrorCode.MODEL_NOT_CONFIGURED.value
    return None


def realization_configuration_code(settings=None) -> str | None:
    """The stable configuration code if realization is enabled but cannot run.

    Pure configuration check for worker startup: no client, key use, or call.
    """

    settings = settings or get_settings()
    if not settings.cognitive_shadow_realization_enabled:
        return None
    model_id = settings.cognitive_shadow_realization_model
    if not (model_id and model_id.strip()) or not settings.openai_api_key:
        return ProviderUnavailableReason.MODEL_NOT_CONFIGURED.value
    from app.ai.Cognitive.system.v0.realization_provider import resolve_provenance

    try:
        resolve_provenance(model_id=model_id, timeout_seconds=settings.cognitive_shadow_realization_timeout_seconds)
    except RealizationProviderConfigurationError as exc:
        return exc.code
    return None


def expected_realization_provenance(settings=None, *, instruction_variant: str | None = None) -> RealizationModelProvenance | None:
    """The provenance the installed adapter would record for the configured model."""

    settings = settings or get_settings()
    model_id = settings.cognitive_shadow_realization_model
    if not (model_id and model_id.strip()):
        return None
    from app.ai.Cognitive.system.v0.realization_provider import resolve_provenance

    try:
        return resolve_provenance(
            model_id=model_id, timeout_seconds=settings.cognitive_shadow_realization_timeout_seconds,
            instruction_variant=instruction_variant,
        )
    except RealizationProviderConfigurationError:
        return None


def purge_expired(db: Session, *, retention_days: int) -> int:
    """Delete captures (and, by cascade, their traces) past the retention period."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = db.execute(delete(CognitiveShadowJob).where(CognitiveShadowJob.captured_at < cutoff))
    db.commit()
    return int(result.rowcount or 0)


def queue_stats(db: Session) -> dict[str, float]:
    now = datetime.now(timezone.utc)
    pending, oldest = db.execute(
        select(func.count(CognitiveShadowJob.id), func.min(CognitiveShadowJob.captured_at))
        .where(CognitiveShadowJob.status.in_(("pending", "processing")))
    ).one()
    return {
        "pending": float(pending or 0),
        "oldest_pending_age_seconds": (now - oldest).total_seconds() if oldest is not None else 0.0,
    }


def replay_terminal(db: Session, *, user_id: uuid.UUID, terminal_id: uuid.UUID) -> ReplayReport:
    """Replay one of this user's completed traces. Read-only; never rewrites."""

    row = db.scalar(select(CognitiveShadowTrace).where(
        CognitiveShadowTrace.id == terminal_id, CognitiveShadowTrace.user_id == user_id,
    ))
    if row is None or row.terminal_status != TerminalStatus.COMPLETED.value:
        return ReplayReport(ReplayStatus.INVALID_RECORD, "not_a_completed_record", None)
    semantic_grounder = (
        ((row.versions or {}).get("pipeline") or {}).get("interpretation") or {}
    ).get("semantic_grounder")
    interpretation_versions = ((row.versions or {}).get("pipeline") or {}).get("interpretation") or {}
    if interpretation_versions.get("semantic_parser") is not None or (
        isinstance(semantic_grounder, str) and semantic_grounder.startswith("interpretation-semantic-model-compiler-")
    ):
        # A sampled model candidate is not a deterministic replay oracle.
        return ReplayReport(
            ReplayStatus.VERSION_UNAVAILABLE, "model_semantic_grounder_not_replayable", row.semantic_digest,
        )
    return replay(
        compiled_request_payload=row.compiled_request_payload,
        pipeline_trace_payload=row.pipeline_trace_payload,
        compiled_request_digest=row.compiled_request_digest,
        stored_semantic_digest=row.semantic_digest,
        versions_payload=row.versions,
    )


def replay_realization_terminal(db: Session, *, user_id: uuid.UUID, terminal_id: uuid.UUID) -> ReplayReport:
    """Replay one of this user's realization outcomes without any model call. Read-only."""

    row = db.scalar(select(CognitiveShadowTrace).where(
        CognitiveShadowTrace.id == terminal_id, CognitiveShadowTrace.user_id == user_id,
    ))
    if row is None or row.terminal_status != TerminalStatus.COMPLETED.value:
        return ReplayReport(ReplayStatus.INVALID_RECORD, "not_a_completed_record", None)
    settings = get_settings()
    dialogue_context = None
    job = db.get(CognitiveShadowJob, row.job_id)
    if job is not None and job.capture_schema_version in DIALOGUE_CAPTURE_SCHEMAS:
        # The dialogue inventory is rebuilt from the immutable capture, never
        # from the current head or a cache.
        if payload_digest(job.capture_payload) != job.capture_digest:
            return ReplayReport(ReplayStatus.MISMATCH, "stored_capture_digest_mismatch", row.realization_semantic_digest)
        try:
            capture = CognitiveShadowCapture.model_validate(job.capture_payload)
        except ValidationError:
            return ReplayReport(ReplayStatus.INVALID_RECORD, "stored_capture_invalid", row.realization_semantic_digest)
        dialogue_context = capture.dialogue.turn_context if capture.dialogue is not None else None
    orchestration = (row.pipeline_trace_payload or {}).get("orchestration")
    # Protocol v1 words through the configured Realization provider, not Call 2.
    if orchestration is not None and orchestration.get("realization_policy") not in (
            "separate_realization-v1", "separate_realization-v1.1", "separate_realization-v1.2",
            "separate_realization-v1.3", "separate_realization-v1.4", "separate_realization-v1.5", "separate_realization-v1.6"):
        from app.ai.Cognitive.system.v0.two_call.contracts import TwoCallTrace
        from app.ai.Cognitive.system.v0.two_call.realization import CapturedRealizationModel
        try:
            expected = CapturedRealizationModel(TwoCallTrace.model_validate(orchestration)).provenance
        except (ValueError, KeyError):
            return ReplayReport(ReplayStatus.INVALID_RECORD, "two_call_capture_invalid", row.realization_semantic_digest)
        expected_model = settings.cognitive_shadow_semantic_parser_model
    else:
        # Protocol v1 words in the conversational variant of the configured provider.
        from app.ai.Cognitive.system.v0.two_call.v1_contracts import INSTRUCTION_VARIANT_BY_REALIZATION_POLICY
        variant = (INSTRUCTION_VARIANT_BY_REALIZATION_POLICY.get(orchestration.get("realization_policy"),
                                                                 "conversational-v1")
                   if orchestration is not None else None)
        expected = expected_realization_provenance(settings, instruction_variant=variant)
        expected_model = settings.cognitive_shadow_realization_model
    return replay_realization(
        realization_status=row.realization_status,
        realization_trace_payload=row.realization_trace_payload,
        realization_trace_digest_value=row.realization_trace_digest,
        realization_semantic_digest_value=row.realization_semantic_digest,
        pipeline_trace_payload=row.pipeline_trace_payload,
        compiled_request_payload=row.compiled_request_payload,
        compiled_request_digest_value=row.compiled_request_digest,
        language_tag=(row.compiled_request_payload or {}).get("language_tag"),
        fallback_maximum_characters=settings.cognitive_shadow_realization_max_characters,
        expected_model_id=expected_model,
        expected_provenance=expected,
        dialogue_context=dialogue_context,
    )


__all__ = [
    "CaptureRejected", "PredecessorPending", "ShadowMetrics", "build_capture", "capture_enabled", "capture_turn",
    "claim_next_job", "enqueue_capture", "is_sampled", "load_predecessor", "lock_conversation", "metrics",
    "persist_terminal", "purge_expired", "queue_stats", "realization_configuration_code", "realization_engine",
    "expected_realization_provenance", "release_for_retry",
    "replay_realization_terminal", "replay_terminal", "source_channel_for", "terminal_row", "wake_worker",
]
