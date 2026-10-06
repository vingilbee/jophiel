"""Turn-local context and stage projections (dialogue-working-memory-projection-v0.1).

The committed snapshot describes the conversation immediately before the
focus user message.  ``compile_turn_context`` adds only that message and the
deterministic reading of its dialogue cues; any source event after the focus
message is future evidence and fails compilation.

Each stage then receives a typed, bounded projection -- never the snapshot
itself, the transcript, or a database object:

- Perception: refs and state for cue interpretation (``PerceptionWorkingContext``);
- Interpretation: attributed items, referents, and exact provenance
  (``InterpretationDialogueContext``);
- Action: evidence-linked grounded objects and target descriptors, only for
  items whose source messages are inside the turn's bounded message window
  (the Action index must resolve every source reference).

Goal receives nothing in V0: no dialogue item is by itself Goal evidence, and
topic continuity never becomes an active goal outside the Goal policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..contracts.actions import ActionGroundedObject, ActionTargetDescriptor, ActionTargetType, ObjectStatus
from ..contracts.dialogue_memory import (
    DIALOGUE_MEMORY_PROJECTION_VERSION,
    DialogueCue,
    DialogueEntity,
    DialogueEventRef,
    DialogueFocusAnalysis,
    DialogueFocusEntity,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueMemorySnapshot,
    DialogueMemoryVersions,
    DialogueOptionSet,
    DialoguePlanReference,
    DialogueProposition,
    DialogueQuestion,
    DialogueRequest,
    DialogueResolution,
    DialogueRole,
    DialogueSourceEvent,
    DialogueSpeaker,
    DialogueTopic,
    DialogueTurnContext,
    DialogueUtteranceReference,
    DialogueCommitmentReference,
    sha256_text,
    turn_context_id_for,
)
from ..interpretation_v0.models import (
    InterpretationDialogueContext,
    InterpretationDialogueItem,
    InterpretationDialogueReferent,
)
from ..perception_v0.models import PerceptionWorkingContext
from .errors import fail
from .updater import (
    CLAIMING_CUES,
    detect_cues,
    is_request_sentence,
    local_entity_refs,
    resolve_cue,
    sentence_spans,
)

PROJECTION_VERSION = DIALOGUE_MEMORY_PROJECTION_VERSION
GROUNDED_PREFIX = "dialogue:"
# Bounds on what one projection may carry into a stage.
MAX_INTERPRETATION_ITEMS = 128
MAX_PROJECTED_PROPOSITIONS = 16
MAX_PROJECTED_ENTITIES = 32


# ---------------------------------------------------------------------------
# Focus analysis and the turn-local view
# ---------------------------------------------------------------------------

def analyze_focus(snapshot: DialogueMemorySnapshot, focus: DialogueSourceEvent) -> DialogueFocusAnalysis:
    return analyze_focus_text(snapshot, focus.content, focus_sequence=focus.conversation_sequence,
                              focus_message_ref=focus.message_ref)


def analyze_focus_text(
    snapshot: DialogueMemorySnapshot, content: str, *, focus_sequence: int, focus_message_ref: str,
) -> DialogueFocusAnalysis:
    """The reviewed cue rules over the focus text, resolved against the committed items only.

    Uses exactly the updater's resolution (including names earlier in the
    focus itself), so what a capture predicts is what the later committed
    transition records.
    """

    raws = detect_cues(content)
    claimed = [(raw.span.start, raw.span.end) for raw in raws if raw.kind in CLAIMING_CUES]
    local = local_entity_refs(snapshot.items, content, conversation_ref=snapshot.conversation_ref,
                              message_ref=focus_message_ref, excluded=claimed)
    mentions = tuple((piece.start, ref) for piece, _, ref, _ in local)
    cues = tuple(resolve_cue(raw, snapshot.items, before_sequence=focus_sequence, local_entities=mentions)
                 for raw in raws)
    sentences = sentence_spans(content)
    return DialogueFocusAnalysis(
        is_question=any(item.text.endswith("?") for item in sentences),
        is_request=any(is_request_sentence(item.text) for item in sentences),
        focus_entities=tuple(
            DialogueFocusEntity(entity_ref=ref, entity_key=key, surface_anchor=piece.text, start=piece.start,
                                end=piece.end, committed=existing)
            for piece, key, ref, existing in local
        ),
        cues=tuple(sorted(cues, key=lambda item: item.sort_key)),
    )


def compile_turn_context(
    snapshot: DialogueMemorySnapshot, focus: DialogueSourceEvent, *, cognitive_turn_id: str,
) -> DialogueTurnContext:
    """Committed snapshot + focus message, or a typed failure.  Never reads anything else."""

    if snapshot.versions != DialogueMemoryVersions():
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.PROJECTION_VERSION_UNSUPPORTED,
                   "The committed snapshot was produced under different versions.")
    if (focus.conversation_ref, focus.user_ref) != (snapshot.conversation_ref, snapshot.user_ref):
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                   "The focus message belongs to a different user or conversation.")
    if focus.role != DialogueRole.USER or focus.turn_ref != cognitive_turn_id:
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "The focus of a cognitive turn is its own user message.")
    if snapshot.through_conversation_sequence >= focus.conversation_sequence:
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.FUTURE_EVIDENCE_DETECTED,
                   "The committed snapshot contains evidence at or after the focus message.")
    if snapshot.through_conversation_sequence != focus.conversation_sequence - 1:
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
                   "The committed snapshot does not end immediately before the focus message.")
    try:
        return DialogueTurnContext(
            context_id=turn_context_id_for(cognitive_turn_id, snapshot.semantic_digest, focus.message_ref),
            cognitive_turn_id=cognitive_turn_id, user_ref=snapshot.user_ref,
            conversation_ref=snapshot.conversation_ref, committed_snapshot_ref=snapshot.snapshot_id,
            committed_snapshot_digest=snapshot.semantic_digest, committed_snapshot_revision=snapshot.revision,
            focus_message_ref=focus.message_ref, focus_conversation_sequence=focus.conversation_sequence,
            prior_context_projection=snapshot,
            current_user_event=DialogueEventRef(
                message_ref=focus.message_ref, conversation_sequence=focus.conversation_sequence,
                turn_ref=focus.turn_ref, role=focus.role, status=focus.status, source_channel=focus.source_channel,
                exposure_state=focus.exposure_state, content_digest=focus.content_digest,
                attachment_count=len(focus.attachment_refs),
            ),
            current_interpretation_projection=analyze_focus(snapshot, focus),
            future_boundary_sequence=focus.conversation_sequence,
        )
    except ValueError as exc:
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                   "The turn-local dialogue context failed contract validation.") from exc


def cue_ref(context: DialogueTurnContext, cue: DialogueCue) -> str:
    return f"dialogue-cue:{context.context_id}:{cue.start}-{cue.end}:{cue.cue_kind.value}"


def verify_turn_context(context: DialogueTurnContext, *, focus_content: str) -> None:
    """Replay check: the stored focus analysis is exactly what the rules produce from the focus text."""

    if sha256_text(focus_content) != context.current_user_event.content_digest:
        raise fail(DialogueMemoryFailurePhase.REPLAY, DialogueMemoryFailureCode.SOURCE_PREFIX_DIGEST_MISMATCH,
                   "The focus text differs from the text the dialogue context was compiled from.")
    rebuilt = analyze_focus_text(context.prior_context_projection, focus_content,
                                 focus_sequence=context.focus_conversation_sequence,
                                 focus_message_ref=context.focus_message_ref)
    if rebuilt != context.current_interpretation_projection:
        raise fail(DialogueMemoryFailurePhase.REPLAY, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "The stored focus analysis differs from the reviewed cue rules.")


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------

def perception_working_context(context: DialogueTurnContext) -> PerceptionWorkingContext:
    snapshot = context.prior_context_projection
    unresolved = [cue_ref(context, cue) for cue in context.current_interpretation_projection.cues
                  if cue.resolution != DialogueResolution.RESOLVED]
    return PerceptionWorkingContext(
        active_topic_ref=snapshot.active_topic_refs[0] if snapshot.active_topic_refs else None,
        unresolved_refs=tuple(dict.fromkeys((*snapshot.unresolved_reference_refs, *unresolved))),
        active_topic_refs=snapshot.active_topic_refs, focus_entity_refs=snapshot.focus_entity_refs,
        open_question_refs=snapshot.open_question_refs,
        source_dialogue_snapshot_ref=snapshot.snapshot_id,
        source_dialogue_snapshot_digest=snapshot.semantic_digest,
    )


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------

def _speaker(item) -> str:
    return item.speaker.value


def _anchor(item) -> str | None:
    return item.surface_anchors[0] if item.surface_anchors else None


def _recent(items: Iterable, limit: int) -> list:
    return sorted(items, key=lambda item: (-item.last_supported_sequence, item.item_id))[:limit]


def interpretation_dialogue_context(context: DialogueTurnContext) -> InterpretationDialogueContext:
    snapshot = context.prior_context_projection
    by_id = {item.item_id: item for item in snapshot.items}
    entries: dict[str, InterpretationDialogueItem] = {}

    def add(item) -> None:
        if item.item_id in entries:
            return
        if isinstance(item, DialogueProposition):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="proposition", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, surface_anchor=_anchor(item),
                source_message_refs=item.source_message_refs,
                related_refs=tuple(ref for ref in item.entity_refs if ref in by_id),
            )
        elif isinstance(item, DialogueEntity):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="entity", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, surface_anchor=_anchor(item),
                source_message_refs=item.source_message_refs,
            )
        elif isinstance(item, DialogueQuestion):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="question", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, surface_anchor=_anchor(item),
                source_message_refs=item.source_message_refs,
            )
        elif isinstance(item, DialogueRequest):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="request", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, surface_anchor=_anchor(item),
                source_message_refs=item.source_message_refs,
            )
        elif isinstance(item, DialogueTopic):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="topic", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, surface_anchor=_anchor(item),
                source_message_refs=item.source_message_refs,
            )
        elif isinstance(item, DialoguePlanReference):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="plan", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, source_message_refs=item.source_message_refs,
                related_refs=tuple(step.step_ref for step in item.steps),
            )
        elif isinstance(item, DialogueUtteranceReference):
            entry = InterpretationDialogueItem(
                item_ref=item.item_id, item_kind="utterance", speaker=_speaker(item),
                epistemic_status=item.epistemic_status.value, source_message_refs=item.source_message_refs,
                completeness=item.completeness,
            )
        elif isinstance(item, DialogueOptionSet):
            for option in item.options:
                entries.setdefault(option.option_ref, InterpretationDialogueItem(
                    item_ref=option.option_ref, item_kind="option", speaker=_speaker(item),
                    epistemic_status=item.epistemic_status.value, surface_anchor=option.surface_anchor,
                    source_message_refs=(option.span.message_ref,), related_refs=(item.item_id,),
                ))
            return
        else:
            return
        entries[item.item_id] = entry

    analysis = context.current_interpretation_projection
    focus = context.focus_message_ref
    for mention in analysis.focus_entities:
        if mention.committed:
            add(by_id[mention.entity_ref])
        else:
            entries[mention.entity_ref] = InterpretationDialogueItem(
                item_ref=mention.entity_ref, item_kind="entity", speaker="user", epistemic_status="attributed",
                surface_anchor=mention.surface_anchor, source_message_refs=(focus,),
            )
    # Everything a focus cue resolved to or competes over comes first, so the
    # bound can never drop a referent the current turn needs.
    for cue in analysis.cues:
        for ref in (*cue.resolved_refs, *cue.candidate_refs):
            parent = by_id.get(ref.partition("#")[0])
            if parent is not None:
                add(parent)
    for ref in snapshot.active_option_set_refs:
        add(by_id[ref])
    for ref in (*snapshot.open_question_refs, *snapshot.active_request_refs, *snapshot.active_topic_refs,
                *snapshot.active_plan_refs):
        add(by_id[ref])
    for item in _recent((item for item in snapshot.items if isinstance(item, DialogueProposition) and item.is_current),
                        MAX_PROJECTED_PROPOSITIONS):
        add(item)
    for item in _recent((item for item in snapshot.items if isinstance(item, DialogueEntity) and item.is_current),
                        MAX_PROJECTED_ENTITIES):
        add(item)
    if len(entries) > MAX_INTERPRETATION_ITEMS:
        raise fail(DialogueMemoryFailurePhase.PROJECTION, DialogueMemoryFailureCode.REQUIRED_DIALOGUE_CONTENT_MISSING,
                   "The interpretation dialogue projection exceeds its bound.")
    referents = tuple(
        InterpretationDialogueReferent(
            cue_ref=cue_ref(context, cue), cue_kind=cue.cue_kind.value, surface_anchor=cue.surface_anchor,
            start=cue.start, end=cue.end, resolution=cue.resolution.value,
            resolved_refs=tuple(ref for ref in cue.resolved_refs if ref in entries),
            candidate_refs=tuple(ref for ref in cue.candidate_refs if ref in entries),
        )
        for cue in analysis.cues
    )
    return InterpretationDialogueContext(
        context_ref=context.context_id, projection_version=PROJECTION_VERSION,
        conversation_ref=context.conversation_ref, focus_message_ref=context.focus_message_ref,
        source_snapshot_ref=snapshot.snapshot_id, source_snapshot_digest=snapshot.semantic_digest,
        items=tuple(entries[key] for key in sorted(entries)), referents=referents,
    )


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

def grounded_ref(ref: str) -> str:
    return f"{GROUNDED_PREFIX}{ref}"


@dataclass(frozen=True)
class ActionDialogueProjection:
    grounded_objects: tuple[ActionGroundedObject, ...]
    target_descriptors: tuple[ActionTargetDescriptor, ...]
    # Dialogue refs whose grounded object exists / was left out because a
    # source message is outside the bounded window.
    projected_refs: tuple[str, ...]
    outside_window_refs: tuple[str, ...]

    def resolves(self, ref: str) -> str | None:
        target = grounded_ref(ref)
        return target if any(item.object_id == target for item in self.grounded_objects) else None


def action_projection(
    context: DialogueTurnContext, *, cognitive_turn_id: str, window_message_ids: Iterable[str],
) -> ActionDialogueProjection:
    """Evidence-linked Action objects for the active dialogue state, bounded to the message window."""

    if cognitive_turn_id != context.cognitive_turn_id:
        raise fail(DialogueMemoryFailurePhase.PIPELINE_HANDOFF, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "The dialogue context belongs to a different cognitive turn.")
    window = frozenset(window_message_ids)
    if context.focus_message_ref not in window:
        raise fail(DialogueMemoryFailurePhase.PIPELINE_HANDOFF, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "The focus message is outside the turn's message window.")
    snapshot = context.prior_context_projection
    by_id = {item.item_id: item for item in snapshot.items}
    analysis = context.current_interpretation_projection
    focus_targets = {ref for cue in analysis.cues if cue.resolution == DialogueResolution.RESOLVED
                     for ref in cue.resolved_refs}
    objects: dict[str, ActionGroundedObject] = {}
    projected: list[str] = []
    outside: list[str] = []

    def sources(item) -> tuple[str, ...] | None:
        # The introducing message must be citable; later supporting messages
        # are cited only while they are inside the window too.
        if item.source_message_refs[0] not in window:
            return None
        return tuple(ref for ref in item.source_message_refs if ref in window)

    def put(obj: ActionGroundedObject) -> None:
        objects[obj.object_id] = obj

    def common(item, object_type: ActionTargetType, cited: tuple[str, ...], ref: str | None = None, **extra):
        target = ref or item.item_id
        return ActionGroundedObject(
            object_id=grounded_ref(target), object_type=object_type, cognitive_turn_id=cognitive_turn_id,
            status=ObjectStatus.CURRENT, source_refs=cited, focus_contribution=False,
            requested_by_focus=target in focus_targets, **extra,
        )

    def asserted(item) -> str:
        return {DialogueSpeaker.USER: "user", DialogueSpeaker.COLUMBINA: "columbina"}.get(item.speaker, "runtime")

    def project(item) -> None:
        cited = sources(item)
        if cited is None:
            outside.append(item.item_id)
            return
        if isinstance(item, DialogueOptionSet):
            option_ids = tuple(grounded_ref(option.option_ref) for option in item.options)
            put(common(item, ActionTargetType.OPTION_SET, cited, asserted_by="columbina", option_refs=option_ids))
            for option in item.options:
                put(common(item, ActionTargetType.OPTION, cited, ref=option.option_ref, asserted_by="columbina",
                           about_refs=(grounded_ref(item.item_id),)))
        elif isinstance(item, DialoguePlanReference):
            step_ids = tuple(grounded_ref(step.step_ref) for step in item.steps)
            put(common(item, ActionTargetType.PLAN, cited, owner="columbina", step_refs=step_ids))
            for step in item.steps:
                put(common(item, ActionTargetType.PLAN_STEP, cited, ref=step.step_ref, owner="columbina",
                           about_refs=(grounded_ref(item.item_id),)))
        elif isinstance(item, DialogueQuestion):
            put(common(item, ActionTargetType.QUESTION, cited, asserted_by=asserted(item),
                       question_kind="polar" if item.question_shape == "yes_no" else None))
        elif isinstance(item, DialogueRequest):
            put(common(item, ActionTargetType.REQUEST, cited))
        elif isinstance(item, DialogueTopic):
            status = "active" if snapshot.active_topic_refs[:1] == (item.item_id,) else "prior_active"
            put(common(item, ActionTargetType.TOPIC, cited, topic_status=status))
        elif isinstance(item, DialogueProposition):
            # An attributed user statement: never ``supported``.
            put(common(item, ActionTargetType.PROPOSITION, cited, asserted_by="user", epistemic_status="unknown"))
        elif isinstance(item, DialogueCommitmentReference):
            put(common(item, ActionTargetType.COMMITMENT, cited))
        else:
            return
        projected.append(item.item_id)

    for ref in (*snapshot.active_option_set_refs, *snapshot.active_plan_refs, *snapshot.open_question_refs,
                *snapshot.active_request_refs, *snapshot.active_topic_refs, *snapshot.active_commitment_refs):
        project(by_id[ref])
    for item in _recent((item for item in snapshot.items if isinstance(item, DialogueProposition) and item.is_current),
                        MAX_PROJECTED_PROPOSITIONS):
        project(item)

    descriptors: list[ActionTargetDescriptor] = []
    focus = context.focus_message_ref
    for cue in analysis.cues:
        if cue.resolution == DialogueResolution.RESOLVED:
            continue
        unknown_id = grounded_ref(cue_ref(context, cue))
        put(ActionGroundedObject(
            object_id=unknown_id, object_type=ActionTargetType.UNKNOWN, cognitive_turn_id=cognitive_turn_id,
            source_refs=(focus,), focus_contribution=True,
        ))
        competing = tuple(dict.fromkeys(grounded_ref(ref) for ref in cue.candidate_refs
                                        if grounded_ref(ref) in objects))
        descriptors.append(ActionTargetDescriptor(
            ref=unknown_id, target_type=ActionTargetType.UNKNOWN, owner="user", unknown_kind="referent",
            competing_candidate_refs=competing if len(competing) >= 2 else (),
        ))
    ordered = tuple(objects[key] for key in sorted(objects))
    return ActionDialogueProjection(
        grounded_objects=ordered, target_descriptors=tuple(sorted(descriptors, key=lambda item: item.ref)),
        projected_refs=tuple(sorted(set(projected))), outside_window_refs=tuple(sorted(set(outside))),
    )


__all__ = [
    "ActionDialogueProjection", "GROUNDED_PREFIX", "PROJECTION_VERSION", "action_projection", "analyze_focus",
    "analyze_focus_text",
    "compile_turn_context", "cue_ref", "grounded_ref", "interpretation_dialogue_context",
    "perception_working_context", "verify_turn_context",
]
