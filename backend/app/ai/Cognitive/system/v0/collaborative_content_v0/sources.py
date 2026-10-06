"""The authorized, attributed sources one collaborative proposal may cite.

Built only from typed records of this turn: the goal's own semantic frame and
its closed arguments, co-turn requirement frames, a bound inquiry answer, the
delivered dialogue items of the authorized dialogue context, and the elements
of the exact target revision.  Raw messages are bounded context, never a
source of authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.collaboration import InquiryAnswerBinding, WorkspaceTargetBinding
from ..contracts.interpretation import InterpretationEnvelope, SemanticFrame
from ..contracts.workspace import ArtifactRevision, MealPlanPayload, OpenInquiryRecord, StructuredArtifactPayload
from ..interpretation_v0.models import InterpretationDialogueContext
from ..perception_v0.models import PerceptionMessage
from .model_protocol import CollaborationMessage, CollaborationSource, SourceKind

MAX_CONTEXT_MESSAGES = 12
MAX_MESSAGE_CHARACTERS = 1200


def element_labels(revision: ArtifactRevision | None) -> dict[str, str]:
    """Element id -> label of a revision (the ids content may name as subjects)."""

    if revision is None:
        return {}
    payload = revision.payload
    labels: dict[str, str] = {}
    if isinstance(payload, MealPlanPayload):
        if payload.servings is not None:
            labels["servings"] = f"serves {payload.servings.value}"
        if payload.time_limit_minutes is not None:
            labels["time_limit"] = f"{payload.time_limit_minutes.value} minutes"
        for item in payload.constraints:
            labels[item.constraint_id] = f"{item.kind.value}: {item.value}"
        for dish in payload.dishes:
            labels[dish.dish_id] = dish.name
        for note in payload.notes:
            labels[note.note_id] = note.text
    elif isinstance(payload, StructuredArtifactPayload):
        for section in payload.sections:
            labels[section.section_id] = section.heading
            for item in section.items:
                labels[item.item_id] = item.text
        for item in payload.constraints:
            labels[item.constraint_id] = f"{item.kind.value}: {item.value}"
        for note in payload.notes:
            labels[note.note_id] = note.text
    return labels


def element_ref(revision: ArtifactRevision, element_id: str) -> str:
    return f"{revision.revision_id}#{element_id}"


def constraint_ref(frame: SemanticFrame, index: int) -> str:
    return f"{frame.frame_id}#constraints.{index}"


@dataclass(frozen=True)
class SourceSet:
    sources: tuple[CollaborationSource, ...]
    # Current-turn requirements and answers the proposal must incorporate.
    required_refs: frozenset[str]
    user_refs: frozenset[str]
    withdrawal_refs: frozenset[str]

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(item.ref for item in self.sources)


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def build_sources(
    *, frame: SemanticFrame, interpretation: InterpretationEnvelope, target: WorkspaceTargetBinding,
    base: ArtifactRevision | None, dialogue_context: InterpretationDialogueContext | None,
    answer: InquiryAnswerBinding | None, inquiry: OpenInquiryRecord | None,
) -> SourceSet:
    sources: list[CollaborationSource] = []
    required: set[str] = set()
    withdrawals: set[str] = set()

    def add(ref: str, kind: SourceKind, speaker: str, text: str, current: bool) -> None:
        sources.append(CollaborationSource(ref=ref, kind=kind, speaker=speaker, text=text[:300], current_turn=current))

    if frame.predicate == "inquiry_answer" and answer is not None and inquiry is not None:
        value = answer.answer_option or _text(frame.arguments.get("answer_text")) or answer.answer_kind
        add(frame.frame_id, SourceKind.USER_ANSWER, "user", f"{inquiry.asked_about}: {value}", True)
        required.add(frame.frame_id)
    else:
        summary = " ".join(part for part in (
            _text(frame.arguments.get("operation")), _text(frame.arguments.get("artifact_type")),
            _text(frame.arguments.get("target_ref")), _text(frame.arguments.get("requested_form")),
        ) if part)
        add(frame.frame_id, SourceKind.USER_REQUEST, "user", summary or "request", True)
        constraints = frame.arguments.get("constraints")
        for index, value in enumerate(constraints if isinstance(constraints, (list, tuple)) else ()):
            if _text(value):
                ref = constraint_ref(frame, index)
                add(ref, SourceKind.USER_REQUIREMENT, "user", value.strip(), True)
                required.add(ref)
    for other in sorted(interpretation.semantic_frames, key=lambda item: item.frame_id):
        if other.frame_id == frame.frame_id or other.arguments.get("embedding", "direct") != "direct":
            continue
        if other.predicate == "constraint_statement":
            text = _text(other.arguments.get("constraint")) or "requirement"
            kind = SourceKind.USER_WITHDRAWAL if other.polarity == "negative" else SourceKind.USER_REQUIREMENT
            add(other.frame_id, kind, "user", text, True)
            (withdrawals if kind == SourceKind.USER_WITHDRAWAL else required).add(other.frame_id)
        elif other.predicate == "correction":
            text = " ".join(part for part in (
                _text(other.arguments.get("corrected_attribute")), _text(other.arguments.get("corrected_value")),
            ) if part) or "correction"
            add(other.frame_id, SourceKind.USER_REQUIREMENT, "user", text, True)
            required.add(other.frame_id)
            withdrawals.add(other.frame_id)
    if dialogue_context is not None and target.dialogue_refs:
        items = {item.item_ref: item for item in dialogue_context.items}
        for ref in target.dialogue_refs:
            item = items.get(ref)
            if item is not None:
                add(ref, SourceKind.DELIVERED_DIALOGUE, "columbina" if item.speaker == "columbina" else "user",
                    item.surface_anchor or f"{item.speaker} utterance", False)
    if base is not None:
        for element_id, label in sorted(element_labels(base).items()):
            add(element_ref(base, element_id), SourceKind.ARTIFACT_ELEMENT, "workspace", label, False)
    user = {item.ref for item in sources if item.speaker == "user" and item.kind != SourceKind.DELIVERED_DIALOGUE}
    return SourceSet(tuple(sorted(sources, key=lambda item: item.ref)), frozenset(required), frozenset(user),
                     frozenset(withdrawals))


def context_messages(messages: tuple[PerceptionMessage, ...]) -> tuple[CollaborationMessage, ...]:
    """The bounded conversation window as attributed context (instructions are data)."""

    out = []
    for message in messages[-MAX_CONTEXT_MESSAGES:]:
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        if role not in {"user", "assistant"}:
            continue
        out.append(CollaborationMessage(role=role, text=message.content[:MAX_MESSAGE_CHARACTERS]))
    return tuple(out)


__all__ = ["SourceSet", "build_sources", "constraint_ref", "context_messages", "element_labels", "element_ref"]
