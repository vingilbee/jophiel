"""Deterministic compiler for untrusted collaborative-content candidates (``collaborative-content-compiler-v0.1``).

The provider may propose; this compiler decides what is representable.  It
rejects (Section 9.4): unknown artifact types or operations, foreign source or
subject refs, malformed patches, unsupported verified claims, fabricated tool
or delivery outcomes, changes inconsistent with explicit user constraints,
silent removal of prior constraints (via the patch engine), unauthorized
sensitive content, and inquiries without a typed purpose and expected use.
Every rejection is a typed ``CollaborationCompileError``; nothing is partially
accepted and nothing falls back to another producer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..contracts.collaboration import (
    COLLABORATION_COMPILER_VERSION, RESTRICTED_CLAIM_KINDS, ROLES_BY_OPERATION, ClaimKind, CollaborationFailureCode as F,
    CollaborationProposal, CollaborativeContentItem, ContentItemRole, InquiryDisposition, InquiryOpportunity,
    WorkspaceTargetBinding, WorkspaceTargetStatus,
)
from ..contracts.workspace import (
    MODEL_ASSIGNABLE_CLASSES, AddConstraint, AddItem, AddNote, AddSection, AddSideDish, ArtifactConstraint,
    ArtifactItem, ArtifactNote, ArtifactOperation, ArtifactPatch, ArtifactRevision, ArtifactSection, ArtifactType,
    ConstraintKind, ContentClass, InquiryCriticality, InquiryExpectedUse, InquiryPurpose, InquirySensitivity,
    InquiryTargetKind, MealDish, MealPlanPayload, OpenInquiryRecord, RemoveConstraint, RemoveDish, RemoveItem,
    RemoveNote, SetMainDish, SetServings, SetTimeLimit, SourcedInteger, USER_SOURCED_CLASSES, UpdateConstraint,
    UpdateDish, UpdateItem, sha256_of,
)
from ..conversation_workspace_v0 import PatchAuthority, WorkspaceError, apply_patch
from . import inquiry_policy
from .model_protocol import CollaborationModelOutput
from .sources import SourceSet, element_labels

COMPILER_VERSION = COLLABORATION_COMPILER_VERSION
PRODUCER_REF = f"collaborative-content:{COMPILER_VERSION}"
_DIGITS = re.compile(r"(?<![\w.])\d+(?![\w])")
_OPTION = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
SAFETY_GUIDANCE_CLASSES = frozenset({ContentClass.REASONED_INFERENCE, ContentClass.UNCERTAIN_SUGGESTION})
CONSTRAINT_SAFETY_KINDS = frozenset({ConstraintKind.DIETARY, ConstraintKind.ALLERGY})
# Classes a presentation item may carry when it re-presents the target's own content.
PRESENTATION_EXTRA_CLASSES = frozenset({ContentClass.ARTIFACT_CONTENT})
FABRICATED_OUTCOME_KINDS = frozenset({ClaimKind.TOOL_OUTCOME, ClaimKind.EXTERNAL_STATE_CHANGE})


class CollaborationCompileError(ValueError):
    def __init__(self, code: F, subject: str = "") -> None:
        super().__init__(code.value)
        self.code = code
        self.subject = subject


def _digest(*parts: str, length: int = 24) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:length]


def new_artifact_ref(conversation_ref: str, turn_ref: str, goal_ref: str) -> str:
    return f"artifact-{_digest(conversation_ref, turn_ref, goal_ref, length=20)}"


def inquiry_id_for(conversation_ref: str, artifact_ref: str | None, target_ref: str, purpose: str) -> str:
    """Stable per (conversation, artifact, target, purpose): a repeat is the same inquiry."""

    return f"workspace-inquiry:{_digest(conversation_ref, artifact_ref or '-', target_ref, purpose)}"


@dataclass(frozen=True)
class CompileContext:
    """Everything the compiler may consult for one goal, and nothing else."""

    cognitive_turn_id: str
    conversation_ref: str
    goal_ref: str
    frame_ref: str
    operation: ArtifactOperation
    artifact_type: ArtifactType
    target: WorkspaceTargetBinding
    base: ArtifactRevision | None
    sources: SourceSet
    inquiry_history: tuple[OpenInquiryRecord, ...]
    curiosity: float | None
    max_inquiries: int


def _sorted_refs(values, sources: SourceSet, *, allow_empty: bool = False) -> tuple[str, ...]:
    refs = tuple(sorted(set(values)))
    if not refs and not allow_empty:
        raise CollaborationCompileError(F.FOREIGN_SOURCE_REF, "empty")
    if not set(refs) <= sources.refs:
        raise CollaborationCompileError(F.FOREIGN_SOURCE_REF, "source")
    return refs


PATCH_REQUIRED_FIELDS = {
    'set_servings': ('integer_value','integer_class'), 'set_time_limit': ('integer_value','integer_class'),
    'add_constraint': ('constraint',), 'update_constraint': ('constraint',),
    'set_main_dish': ('dish',), 'update_dish': ('dish',), 'add_side_dish': ('dish',),
    'remove_constraint': ('element_id',), 'remove_dish': ('element_id',), 'remove_note': ('element_id',),
    'add_note': ('element_id','text','text_class'), 'add_section': ('element_id','heading'),
    'add_item': ('element_id','section_id','text','text_class'),
    'update_item': ('element_id','section_id','text','text_class'),
    'remove_item': ('element_id','section_id'),
}

_PATCH_DIAGNOSTIC_FIELDS = frozenset({
    'value','content_class','source_refs','constraint_id','kind','basis','reason',
    'dish_id','name','contains','traits','prep_time_minutes','note_id','text',
    'section_id','heading','item_id','operations','title',
    *(field for fields in PATCH_REQUIRED_FIELDS.values() for field in fields),
})
_PATCH_DIAGNOSTIC_TYPES = frozenset({
    'missing','string_too_short','greater_than_equal','less_than_equal','int_type',
    'string_type','enum','literal_error','value_error','too_short','too_long',
})

def patch_rejection_detail(subject):
    """Only closed operation/field/error tokens may enter terminal diagnostics."""
    from ..conversation_workspace_v0.errors import WorkspaceErrorCode
    tokens=subject.split('.')
    roots={'patch',*PATCH_REQUIRED_FIELDS,*(e.value for e in WorkspaceErrorCode)}
    if len(tokens)==1 and tokens[0] in roots:return subject
    if len(tokens)==3 and tokens[0] in roots and tokens[1] in _PATCH_DIAGNOSTIC_FIELDS and tokens[2] in _PATCH_DIAGNOSTIC_TYPES:
        return subject
    return 'invalid_patch'

def _patch_validation_subject(root, exc):
    if isinstance(exc,ValidationError):
        for error in exc.errors(include_input=False,include_url=False):
            field=next((part for part in reversed(error['loc']) if part in _PATCH_DIAGNOSTIC_FIELDS),None)
            if field and error['type'] in _PATCH_DIAGNOSTIC_TYPES:
                return patch_rejection_detail(f"{root}.{field}.{error['type']}")
    return patch_rejection_detail(root)

def _typed_operation(candidate, sources: SourceSet):
    refs = _sorted_refs(candidate.source_refs, sources)
    op = candidate.op
    for field in PATCH_REQUIRED_FIELDS.get(op,()):
        if getattr(candidate,field) is None:
            raise CollaborationCompileError(F.PATCH_REJECTED, f'{op}.{field}.missing')
    try:
        if op in {"set_servings", "set_time_limit"}:
            value = SourcedInteger(value=candidate.integer_value, content_class=candidate.integer_class, source_refs=refs)
            return (SetServings if op == "set_servings" else SetTimeLimit)(value=value, source_refs=refs)
        if op in {"add_constraint", "update_constraint"}:
            item = candidate.constraint
            constraint = ArtifactConstraint(constraint_id=item.constraint_id, kind=item.kind, value=item.value,
                                            basis=item.basis, reason=item.reason, source_refs=refs)
            return (AddConstraint if op == "add_constraint" else UpdateConstraint)(constraint=constraint, source_refs=refs)
        if op == "remove_constraint":
            return RemoveConstraint(constraint_id=candidate.element_id, source_refs=refs)
        if op in {"set_main_dish", "update_dish", "add_side_dish"}:
            item = candidate.dish
            dish = MealDish(dish_id=item.dish_id, name=item.name.strip(), content_class=item.content_class,
                            contains=tuple(sorted(set(item.contains))), traits=tuple(sorted(set(item.traits))),
                            prep_time_minutes=item.prep_time_minutes, source_refs=refs)
            kind = {"set_main_dish": SetMainDish, "update_dish": UpdateDish, "add_side_dish": AddSideDish}[op]
            return kind(dish=dish, source_refs=refs)
        if op == "remove_dish":
            return RemoveDish(dish_id=candidate.element_id, source_refs=refs)
        if op == "add_note":
            note = ArtifactNote(note_id=candidate.element_id, text=(candidate.text or "").strip(),
                                content_class=candidate.text_class, source_refs=refs)
            return AddNote(note=note, source_refs=refs)
        if op == "remove_note":
            return RemoveNote(note_id=candidate.element_id, source_refs=refs)
        if op == "add_section":
            section = ArtifactSection(section_id=candidate.element_id, heading=(candidate.heading or "").strip(),
                                      source_refs=refs)
            return AddSection(section=section, source_refs=refs)
        if op in {"add_item", "update_item"}:
            item = ArtifactItem(item_id=candidate.element_id, text=(candidate.text or "").strip(),
                                content_class=candidate.text_class, source_refs=refs)
            return (AddItem if op == "add_item" else UpdateItem)(section_id=candidate.section_id, item=item,
                                                                  source_refs=refs)
        if op == "remove_item":
            return RemoveItem(section_id=candidate.section_id, item_id=candidate.element_id, source_refs=refs)
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        raise CollaborationCompileError(F.PATCH_REJECTED, _patch_validation_subject(op,exc)) from exc
    raise CollaborationCompileError(F.PATCH_REJECTED, op)


def _written_elements(operations) -> set[str]:
    """Element ids a patch actually writes (what a ``change`` item may claim)."""

    out: set[str] = set()
    for op in operations:
        if isinstance(op, SetServings):
            out.add("servings")
        elif isinstance(op, SetTimeLimit):
            out.add("time_limit")
        elif isinstance(op, (AddConstraint, UpdateConstraint)):
            out.add(op.constraint.constraint_id)
        elif isinstance(op, RemoveConstraint):
            out.add(op.constraint_id)
        elif isinstance(op, (SetMainDish, UpdateDish, AddSideDish)):
            out.add(op.dish.dish_id)
        elif isinstance(op, RemoveDish):
            out.add(op.dish_id)
        elif isinstance(op, AddNote):
            out.add(op.note.note_id)
        elif isinstance(op, RemoveNote):
            out.add(op.note_id)
        elif isinstance(op, AddSection):
            out.add(op.section.section_id)
        elif isinstance(op, (AddItem, UpdateItem)):
            out.add(op.item.item_id)
        elif isinstance(op, RemoveItem):
            out.add(op.item_id)
    return out


def _numbers(revision: ArtifactRevision | None) -> set[int]:
    if revision is None or not isinstance(revision.payload, MealPlanPayload):
        return set()
    payload = revision.payload
    out = {item.value for item in (payload.servings, payload.time_limit_minutes) if item is not None}
    out |= {dish.prep_time_minutes for dish in payload.dishes if dish.prep_time_minutes is not None}
    return out


def _constraint_ids(revision: ArtifactRevision | None, kinds=None) -> set[str]:
    if revision is None:
        return set()
    return {item.constraint_id for item in getattr(revision.payload, "constraints", ())
            if kinds is None or item.kind in kinds}


DEFAULT_SECTION_ID = "items"
DEFAULT_SECTION_HEADING = "Ideas"


def _sectioned(output: CollaborationModelOutput) -> CollaborationModelOutput:
    """Items added without a section go in the one section there is, or a default one.

    A list of ideas needs no heading; only placement is supplied, never content.
    """
    loose = [op for op in output.operations if op.op == "add_item" and op.section_id is None]
    if not loose:
        return output
    sections = [op.element_id for op in output.operations if op.op == "add_section" and op.element_id]
    if len(sections) > 1:
        return output  # ambiguous placement: left to the strict rule
    operations = list(output.operations)
    if sections:
        section = sections[0]
    else:
        section = DEFAULT_SECTION_ID
        operations.insert(0, loose[0].model_copy(update=dict(
            op="add_section", element_id=section, section_id=None, heading=DEFAULT_SECTION_HEADING, text=None,
            text_class=None, source_refs=loose[0].source_refs)))
    operations = [op.model_copy(update={"section_id": section}) if op.op == "add_item" and op.section_id is None
                  else op for op in operations]
    return output.model_copy(update={"operations": tuple(operations)})


_FALLBACK_ANCHOR_WORDS = 4


def _anchors_in_statement(anchors: tuple[str, ...], statement: str) -> tuple[str, ...]:
    """Anchors that occur in the statement; else its opening words (an exact substring)."""
    kept = tuple(anchor for anchor in anchors if anchor.casefold() in statement.casefold())
    if kept:
        return kept[:6]
    words = statement.split()
    return (" ".join(words[:_FALLBACK_ANCHOR_WORDS]).rstrip(".,;:!?"),) if words else ()


class CollaborationCompilerV0:
    version = COMPILER_VERSION

    def compile(self, context: CompileContext, output: CollaborationModelOutput,
                candidate: Mapping[str, Any], *, tolerant: bool = False) -> CollaborationProposal:
        """``tolerant`` (compiler v0.2, recorded in the candidate's provenance) relaxes two
        bookkeeping rules only: a user requirement honored and cited by a content item
        counts as cited, anchors that do not occur in their statement are dropped
        (falling back to the statement's opening words), and list items added without
        a section are placed in the single section (or a default one).  Every claim, number, role,
        source and safety rule is unchanged."""
        operation = context.operation
        revising = operation in {ArtifactOperation.CREATE, ArtifactOperation.REVISE}
        target = context.target
        if (target.operation, target.artifact_type) != (operation, context.artifact_type):
            raise CollaborationCompileError(F.OPERATION_MISMATCH)
        sources = context.sources
        proposed: ArtifactRevision | None = None
        patch: ArtifactPatch | None = None
        written: set[str] = set()
        if revising:
            if not output.operations:
                raise CollaborationCompileError(F.MISSING_PATCH)
            patch, proposed = self._patch(context, _sectioned(output) if tolerant else output)
            written = _written_elements(patch.operations)
            cited = {ref for op in patch.operations for ref in op.source_refs}
            if tolerant:
                cited |= {ref for item in output.content_items for ref in item.source_refs}
            ignored = sorted(context.sources.required_refs - cited)
            if ignored:
                raise CollaborationCompileError(F.USER_CONSTRAINT_IGNORED, ignored[0])
        elif output.operations:
            raise CollaborationCompileError(F.UNEXPECTED_PATCH)
        subject_revision = proposed if revising else context.base
        elements = set(element_labels(subject_revision))
        if revising and context.base is not None:
            # A change may also name what it removed from the base.
            elements |= set(element_labels(context.base)) & written
        items = self._items(context, output, elements=elements, written=written, revision=subject_revision,
                            tolerant=tolerant)
        inquiries = self._inquiries(context, output, revision=subject_revision)
        refs = {context.frame_ref, *(ref for item in items for ref in item.source_refs),
                *(ref for item in inquiries for ref in item.source_refs)}
        if patch is not None:
            refs |= set(patch.source_refs)
        candidate_digest = sha256_of(dict(candidate))
        artifact_ref = patch.artifact_ref if patch is not None else target.artifact_ref
        try:
            return CollaborationProposal(
                content_proposal_id=f"collaboration-proposal:{_digest(context.cognitive_turn_id, context.goal_ref, candidate_digest)}",
                cognitive_turn_id=context.cognitive_turn_id, goal_ref=context.goal_ref, frame_ref=context.frame_ref,
                operation=operation, artifact_type=context.artifact_type, artifact_ref=artifact_ref,
                target_revision_ref=None if revising else target.base_revision_ref,
                target_dialogue_refs=target.dialogue_refs, patch=patch, proposed_revision=proposed,
                content_items=items, inquiry_opportunities=inquiries, source_refs=tuple(sorted(refs)),
                epistemic_classes=tuple(sorted({item.content_class for item in items})),
                uncertainty_item_refs=tuple(sorted(
                    item.item_id for item in items
                    if item.content_class in {ContentClass.UNCERTAIN_SUGGESTION, ContentClass.REASONED_INFERENCE}
                )),
                candidate_digest=candidate_digest,
            )
        except (ValidationError, ValueError) as exc:
            raise CollaborationCompileError(F.CONTENT_INVALID, "proposal") from exc

    # -- patch -------------------------------------------------------------------
    def _patch(self, context: CompileContext, output: CollaborationModelOutput):
        target = context.target
        lifted = context.operation == ArtifactOperation.REVISE and target.status == WorkspaceTargetStatus.RESOLVED_DIALOGUE
        creating = context.operation == ArtifactOperation.CREATE or lifted
        if creating:
            artifact_ref = new_artifact_ref(context.conversation_ref, context.cognitive_turn_id, context.goal_ref)
            base = None
        else:
            if target.status != WorkspaceTargetStatus.RESOLVED_ARTIFACT or context.base is None:
                raise CollaborationCompileError(F.OPERATION_MISMATCH, "target")
            artifact_ref, base = target.artifact_ref, context.base
        operations = tuple(_typed_operation(item, context.sources) for item in output.operations)
        title = output.title.strip() if isinstance(output.title, str) and output.title.strip() else None
        try:
            patch = ArtifactPatch(
                patch_id=f"artifact-patch:{_digest(context.cognitive_turn_id, context.goal_ref, artifact_ref)}",
                artifact_ref=artifact_ref, artifact_type=context.artifact_type,
                base_revision_ref=None if creating else base.revision_id,
                operation=ArtifactOperation.CREATE if creating else ArtifactOperation.REVISE, title=title,
                operations=operations, source_refs=(context.frame_ref,),
            )
        except (ValidationError, ValueError) as exc:
            raise CollaborationCompileError(F.PATCH_REJECTED, _patch_validation_subject('patch',exc)) from exc
        authority = PatchAuthority(source_refs=context.sources.refs, user_refs=context.sources.user_refs,
                                   withdrawal_refs=context.sources.withdrawal_refs)
        try:
            result = apply_patch(patch, base=base, conversation_ref=context.conversation_ref,
                                 turn_ref=context.cognitive_turn_id, producer_ref=PRODUCER_REF, authority=authority)
        except WorkspaceError as exc:
            raise CollaborationCompileError(F.PATCH_REJECTED, _patch_validation_subject(exc.code.value,exc.__cause__)) from exc
        return patch, result.revision

    # -- content -----------------------------------------------------------------
    def _items(self, context: CompileContext, output, *, elements: set[str], written: set[str],
               revision: ArtifactRevision | None, tolerant: bool = False) -> tuple[CollaborativeContentItem, ...]:
        if not output.content_items:
            raise CollaborationCompileError(F.MISSING_CONTENT)
        allowed_roles = {item.value for item in ROLES_BY_OPERATION[context.operation]}
        presenting = context.operation not in {ArtifactOperation.CREATE, ArtifactOperation.REVISE}
        allowed_classes = {item.value for item in MODEL_ASSIGNABLE_CLASSES}
        if presenting and revision is not None:
            allowed_classes |= {item.value for item in PRESENTATION_EXTRA_CLASSES}
        numbers = _numbers(revision)
        safety_constraints = _constraint_ids(revision, CONSTRAINT_SAFETY_KINDS)
        out: list[CollaborativeContentItem] = []
        for index, item in enumerate(output.content_items):
            subject = f"item-{index}"
            try:
                claim = ClaimKind(item.claim_kind)
            except ValueError as exc:
                raise CollaborationCompileError(F.CONTENT_INVALID, subject) from exc
            if claim in FABRICATED_OUTCOME_KINDS:
                raise CollaborationCompileError(F.FABRICATED_OUTCOME, subject)
            if claim in RESTRICTED_CLAIM_KINDS:
                raise CollaborationCompileError(F.UNSUPPORTED_VERIFIED_CLAIM, subject)
            if item.role not in allowed_roles:
                raise CollaborationCompileError(F.ROLE_NOT_ALLOWED, subject)
            if item.content_class not in allowed_classes:
                raise CollaborationCompileError(F.UNSUPPORTED_CONTENT_CLASS, subject)
            refs = _sorted_refs(item.source_refs, context.sources)
            if ContentClass(item.content_class) in USER_SOURCED_CLASSES and not set(refs) & context.sources.user_refs:
                raise CollaborationCompileError(F.UNSUPPORTED_CONTENT_CLASS, subject)
            subjects = tuple(sorted(set(item.subject_ids)))
            if not set(subjects) <= elements:
                raise CollaborationCompileError(F.FOREIGN_SUBJECT_REF, subject)
            role = ContentItemRole(item.role)
            if role == ContentItemRole.CHANGE and (not subjects or not set(subjects) <= written):
                # A change item may only describe what the patch actually did.
                raise CollaborationCompileError(F.FABRICATED_OUTCOME, subject)
            if role == ContentItemRole.SAFETY_GUIDANCE and (
                ContentClass(item.content_class) not in SAFETY_GUIDANCE_CLASSES
                or claim != ClaimKind.GENERAL_PRACTICE or not set(subjects) & safety_constraints
            ):
                raise CollaborationCompileError(F.SENSITIVE_CONTENT_UNAUTHORIZED, subject)
            stated = {int(value) for value in _DIGITS.findall(item.statement)}
            declared = set(item.numbers)
            if stated - declared or declared - numbers:
                raise CollaborationCompileError(F.UNSUPPORTED_NUMBER, subject)
            anchors = tuple(dict.fromkeys(anchor.strip() for anchor in item.anchors if anchor.strip()))
            if tolerant:
                anchors = _anchors_in_statement(anchors, item.statement.strip())
            try:
                out.append(CollaborativeContentItem(
                    item_id=f"collaboration-item:{_digest(context.goal_ref, str(index), item.statement)}",
                    role=role, statement=item.statement.strip(),
                    anchors=anchors,
                    content_class=item.content_class, claim_kind=claim, subject_refs=subjects, source_refs=refs,
                    numbers=tuple(sorted(declared)),
                ))
            except (ValidationError, ValueError) as exc:
                raise CollaborationCompileError(F.CONTENT_INVALID, subject) from exc
        return tuple(out)

    # -- inquiries --------------------------------------------------------------------
    def _inquiries(self, context: CompileContext, output, *, revision: ArtifactRevision | None,
                   ) -> tuple[InquiryOpportunity, ...]:
        if len(output.inquiries) > max(context.max_inquiries, 0) + 2:
            raise CollaborationCompileError(F.INQUIRY_INVALID, "count")
        labels = element_labels(revision)
        constraints = {item.constraint_id: item for item in getattr(revision.payload, "constraints", ())} if revision else {}
        artifact_ref = revision.artifact_id if revision is not None else None
        drafts, raw = [], {}
        for index, item in enumerate(output.inquiries):
            subject = f"inquiry-{index}"
            try:
                target_kind = InquiryTargetKind(item.target_kind)
                purpose = InquiryPurpose(item.purpose)
                expected_use = InquiryExpectedUse(item.expected_use)
                criticality = InquiryCriticality(item.criticality)
                sensitivity = InquirySensitivity(item.sensitivity)
            except ValueError as exc:
                raise CollaborationCompileError(F.INQUIRY_INVALID, subject) from exc
            if criticality == InquiryCriticality.BLOCKING or target_kind == InquiryTargetKind.REQUEST_ARGUMENT or (
                expected_use == InquiryExpectedUse.COMPLETE_REQUEST
            ):
                # Blocking questions belong to requirement resolution, never a proposal.
                raise CollaborationCompileError(F.INQUIRY_INVALID, subject)
            options = tuple(dict.fromkeys(option.strip() for option in item.answer_options if option.strip()))
            if any(not _OPTION.fullmatch(option) for option in options):
                raise CollaborationCompileError(F.INQUIRY_INVALID, subject)
            if item.question_kind not in {"open_set", "yes_no", "choice"} or (
                (item.question_kind == "choice") != (len(options) >= 2)
            ):
                raise CollaborationCompileError(F.INQUIRY_INVALID, subject)
            target_known = (
                item.target_id in constraints if target_kind == InquiryTargetKind.ARTIFACT_CONSTRAINT
                else (item.target_id == artifact_ref if target_kind == InquiryTargetKind.ARTIFACT
                      else item.target_id in labels)
            )
            refs = _sorted_refs(item.source_refs, context.sources)
            inquiry_id = inquiry_id_for(context.conversation_ref, artifact_ref, item.target_id, purpose.value)
            if inquiry_id in raw:
                raise CollaborationCompileError(F.INQUIRY_INVALID, subject)
            constraint = constraints.get(item.target_id)
            drafts.append(inquiry_policy.InquiryDraft(
                inquiry_id=inquiry_id, target_kind=target_kind, target_ref=item.target_id, target_known=target_known,
                purpose=purpose, expected_use=expected_use, criticality=criticality, sensitivity=sensitivity,
                answer_options=options,
                reason_already_known=constraint is not None and constraint.reason.value != "unspecified",
            ))
            raw[inquiry_id] = (item, options, refs)
        decisions = inquiry_policy.decide(tuple(drafts), curiosity=context.curiosity, history=context.inquiry_history)
        out = []
        for draft in drafts:
            item, options, refs = raw[draft.inquiry_id]
            decision = decisions[draft.inquiry_id]
            try:
                out.append(InquiryOpportunity(
                    inquiry_id=draft.inquiry_id, parent_goal_ref=context.goal_ref, originating_frame_ref=context.frame_ref,
                    artifact_ref=artifact_ref, target_kind=draft.target_kind, target_ref=draft.target_ref,
                    question_kind=item.question_kind, purpose=draft.purpose, expected_use=draft.expected_use,
                    criticality=draft.criticality, timing=decision.timing, sensitivity=draft.sensitivity,
                    asked_about=item.asked_about.strip(), answer_options=options, source_refs=refs,
                    disposition=InquiryDisposition.SELECTED if decision.selected else InquiryDisposition.SUPPRESSED,
                    suppression_reason=decision.suppression, score=decision.score, policy_rule_ids=decision.rule_ids,
                ))
            except (ValidationError, ValueError) as exc:
                raise CollaborationCompileError(F.INQUIRY_INVALID, draft.inquiry_id) from exc
        return tuple(sorted(out, key=lambda value: value.inquiry_id))


__all__ = [
    "COMPILER_VERSION", "CollaborationCompileError", "CollaborationCompilerV0", "CompileContext", "PRODUCER_REF",
    "inquiry_id_for", "new_artifact_ref",
]
