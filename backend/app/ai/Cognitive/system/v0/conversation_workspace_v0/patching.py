"""Deterministic artifact patch application (``artifact-patch-engine-v0.1``).

A patch is validated and applied against an exact base revision and produces
one *proposed* revision -- never a delivered one.  Every rejection is a typed
``WorkspaceError``; nothing is partially applied, and there is no
last-write-wins: a patch whose base is not the current head is stale.

Checks (Section 7.4): exact base revision, payload-type allow-list, closed
value types (enforced by the contracts), conflicting operations, provenance of
every changed value, no silent removal of a user constraint, preservation of
every unrelated constraint, deterministic application, internal constraint
consistency of the result, and a digest-sealed result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from ..contracts.workspace import (
    MEAL_PLAN_OTHER_KINDS, PAYLOAD_TYPE_FOR_ARTIFACT, USER_SOURCED_CLASSES, AddConstraint, AddItem, AddNote,
    AddSection, AddSideDish, ArtifactConstraint, ArtifactOperation, ArtifactPatch, ArtifactRevision,
    ArtifactRevisionStatus, ConstraintKind, MealDish, MealPlanPayload, RemoveConstraint, RemoveDish, RemoveItem,
    RemoveNote, SetMainDish, SetServings, SetTimeLimit, StructuredArtifactPayload, UpdateConstraint, UpdateDish,
    UpdateItem, empty_payload,
)
from .errors import WorkspaceError, WorkspaceErrorCode as E

PATCH_ENGINE_VERSION = "artifact-patch-engine-v0.1"


@dataclass(frozen=True)
class PatchAuthority:
    """The refs a patch may cite, by kind, for one turn.

    ``source_refs``: every ref a changed value may cite (authorized
    interpretation, dialogue, workspace, and content refs of this turn).
    ``user_refs``: the subset that is the user's own contribution; content
    classed as a user constraint or claim must cite one of them.
    ``withdrawal_refs``: the subset that explicitly withdraws a requirement;
    only they may remove a user constraint.
    """

    source_refs: frozenset[str]
    user_refs: frozenset[str] = frozenset()
    withdrawal_refs: frozenset[str] = frozenset()


def _targets(op) -> tuple[tuple, ...]:
    """The fields or elements one operation writes (for conflict detection)."""

    if isinstance(op, SetServings):
        return (("servings",),)
    if isinstance(op, SetTimeLimit):
        return (("time_limit",),)
    if isinstance(op, (AddConstraint, UpdateConstraint)):
        item = op.constraint
        return (("constraint", item.constraint_id), ("constraint_value", item.kind.value, item.value))
    if isinstance(op, RemoveConstraint):
        return (("constraint", op.constraint_id),)
    if isinstance(op, SetMainDish):
        return (("main_dish",), ("dish", op.dish.dish_id))
    if isinstance(op, (UpdateDish, AddSideDish)):
        return (("dish", op.dish.dish_id),)
    if isinstance(op, RemoveDish):
        return (("dish", op.dish_id),)
    if isinstance(op, AddNote):
        return (("note", op.note.note_id),)
    if isinstance(op, RemoveNote):
        return (("note", op.note_id),)
    if isinstance(op, AddSection):
        return (("section", op.section.section_id),)
    if isinstance(op, (AddItem, UpdateItem)):
        return (("item", op.section_id, op.item.item_id),)
    if isinstance(op, RemoveItem):
        return (("item", op.section_id, op.item_id),)
    raise WorkspaceError(E.OPERATION_NOT_ALLOWED)  # pragma: no cover - closed union


def _value_refs(op) -> tuple[tuple[str, ...], object | None]:
    """The source refs of the value an operation writes, and its content class."""

    for name in ("value", "constraint", "dish", "note", "section", "item"):
        value = getattr(op, name, None)
        if value is not None:
            content_class = getattr(value, "content_class", None) or getattr(value, "basis", None)
            return tuple(getattr(value, "source_refs", ())), content_class
    return (), None


def _check_provenance(patch: ArtifactPatch, authority: PatchAuthority) -> None:
    for op in patch.operations:
        refs, content_class = _value_refs(op)
        if not set(op.source_refs) <= authority.source_refs or not set(refs) <= authority.source_refs:
            raise WorkspaceError(E.UNAUTHORIZED_SOURCE, op.op)
        if content_class in USER_SOURCED_CLASSES and not set(refs) & authority.user_refs:
            # A user constraint or claim cites the user's own contribution.
            raise WorkspaceError(E.UNAUTHORIZED_SOURCE, op.op)
        if isinstance(op, RemoveConstraint) and not set(op.source_refs) <= authority.withdrawal_refs:
            raise WorkspaceError(E.CONSTRAINT_REMOVAL_UNAUTHORIZED, op.constraint_id)
    if not set(patch.source_refs) <= authority.source_refs:
        raise WorkspaceError(E.UNAUTHORIZED_SOURCE, "patch")


def _replace(items: Iterable, key: str, value, attribute: str):
    out, found = [], False
    for item in items:
        if getattr(item, attribute) == key:
            out.append(value)
            found = True
        else:
            out.append(item)
    if not found:
        raise WorkspaceError(E.UNKNOWN_ELEMENT, key)
    return tuple(out)


def _without(items: Iterable, key: str, attribute: str):
    items = tuple(items)
    kept = tuple(item for item in items if getattr(item, attribute) != key)
    if len(kept) == len(items):
        raise WorkspaceError(E.UNKNOWN_ELEMENT, key)
    return kept


def _constraint_bucket(payload: MealPlanPayload, kind: ConstraintKind) -> str:
    if kind == ConstraintKind.DIETARY:
        return "dietary_constraints"
    if kind == ConstraintKind.ALLERGY:
        return "allergy_constraints"
    if kind in MEAL_PLAN_OTHER_KINDS:
        return "other_constraints"
    # Time and quantity are the typed ``time_limit_minutes`` and ``servings`` fields.
    raise WorkspaceError(E.OPERATION_NOT_ALLOWED, kind.value)


def _apply_meal(payload: MealPlanPayload, op) -> MealPlanPayload:
    fields = {name: getattr(payload, name) for name in type(payload).model_fields}
    ids = {item.constraint_id: item for item in payload.constraints}
    if isinstance(op, SetServings):
        fields["servings"] = op.value
    elif isinstance(op, SetTimeLimit):
        fields["time_limit_minutes"] = op.value
    elif isinstance(op, AddConstraint):
        item = op.constraint
        if item.constraint_id in ids:
            raise WorkspaceError(E.DUPLICATE_ELEMENT, item.constraint_id)
        bucket = _constraint_bucket(payload, item.kind)
        if any(existing.value == item.value for existing in fields[bucket]):
            raise WorkspaceError(E.DUPLICATE_CONSTRAINT, item.constraint_id)
        fields[bucket] = (*fields[bucket], item)
    elif isinstance(op, UpdateConstraint):
        item = op.constraint
        existing = ids.get(item.constraint_id)
        if existing is None:
            raise WorkspaceError(E.UNKNOWN_ELEMENT, item.constraint_id)
        if (existing.kind, existing.value) != (item.kind, item.value):
            raise WorkspaceError(E.CONSTRAINT_IDENTITY_CHANGED, item.constraint_id)
        bucket = _constraint_bucket(payload, item.kind)
        fields[bucket] = _replace(fields[bucket], item.constraint_id, item, "constraint_id")
    elif isinstance(op, RemoveConstraint):
        existing = ids.get(op.constraint_id)
        if existing is None:
            raise WorkspaceError(E.UNKNOWN_ELEMENT, op.constraint_id)
        bucket = _constraint_bucket(payload, existing.kind)
        fields[bucket] = _without(fields[bucket], op.constraint_id, "constraint_id")
    elif isinstance(op, SetMainDish):
        if any(item.dish_id == op.dish.dish_id for item in payload.side_dishes):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, op.dish.dish_id)
        fields["main_dish"] = op.dish
    elif isinstance(op, UpdateDish):
        if payload.main_dish is not None and payload.main_dish.dish_id == op.dish.dish_id:
            fields["main_dish"] = op.dish
        else:
            fields["side_dishes"] = _replace(payload.side_dishes, op.dish.dish_id, op.dish, "dish_id")
    elif isinstance(op, AddSideDish):
        if any(item.dish_id == op.dish.dish_id for item in payload.dishes):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, op.dish.dish_id)
        fields["side_dishes"] = (*payload.side_dishes, op.dish)
    elif isinstance(op, RemoveDish):
        if payload.main_dish is not None and payload.main_dish.dish_id == op.dish_id:
            fields["main_dish"] = None
        else:
            fields["side_dishes"] = _without(payload.side_dishes, op.dish_id, "dish_id")
    elif isinstance(op, AddNote):
        if any(item.note_id == op.note.note_id for item in payload.notes):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, op.note.note_id)
        fields["notes"] = (*payload.notes, op.note)
    elif isinstance(op, RemoveNote):
        fields["notes"] = _without(payload.notes, op.note_id, "note_id")
    else:
        raise WorkspaceError(E.OPERATION_NOT_ALLOWED, op.op)
    return MealPlanPayload(**fields)


def _apply_structured(payload: StructuredArtifactPayload, op) -> StructuredArtifactPayload:
    fields = {name: getattr(payload, name) for name in type(payload).model_fields}
    if isinstance(op, AddConstraint):
        item = op.constraint
        if any(existing.constraint_id == item.constraint_id for existing in payload.constraints):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, item.constraint_id)
        if any((existing.kind, existing.value) == (item.kind, item.value) for existing in payload.constraints):
            raise WorkspaceError(E.DUPLICATE_CONSTRAINT, item.constraint_id)
        fields["constraints"] = (*payload.constraints, item)
    elif isinstance(op, UpdateConstraint):
        existing = next((item for item in payload.constraints if item.constraint_id == op.constraint.constraint_id), None)
        if existing is None:
            raise WorkspaceError(E.UNKNOWN_ELEMENT, op.constraint.constraint_id)
        if (existing.kind, existing.value) != (op.constraint.kind, op.constraint.value):
            raise WorkspaceError(E.CONSTRAINT_IDENTITY_CHANGED, op.constraint.constraint_id)
        fields["constraints"] = _replace(payload.constraints, existing.constraint_id, op.constraint, "constraint_id")
    elif isinstance(op, RemoveConstraint):
        fields["constraints"] = _without(payload.constraints, op.constraint_id, "constraint_id")
    elif isinstance(op, AddNote):
        if any(item.note_id == op.note.note_id for item in payload.notes):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, op.note.note_id)
        fields["notes"] = (*payload.notes, op.note)
    elif isinstance(op, RemoveNote):
        fields["notes"] = _without(payload.notes, op.note_id, "note_id")
    elif isinstance(op, AddSection):
        if any(item.section_id == op.section.section_id for item in payload.sections):
            raise WorkspaceError(E.DUPLICATE_ELEMENT, op.section.section_id)
        fields["sections"] = (*payload.sections, op.section)
    elif isinstance(op, (AddItem, UpdateItem, RemoveItem)):
        section = next((item for item in payload.sections if item.section_id == op.section_id), None)
        if section is None:
            raise WorkspaceError(E.UNKNOWN_ELEMENT, op.section_id)
        items = section.items
        if isinstance(op, AddItem):
            if any(item.item_id == op.item.item_id for item in items):
                raise WorkspaceError(E.DUPLICATE_ELEMENT, op.item.item_id)
            items = (*items, op.item)
        elif isinstance(op, UpdateItem):
            items = _replace(items, op.item.item_id, op.item, "item_id")
        else:
            items = _without(items, op.item_id, "item_id")
        fields["sections"] = _replace(payload.sections, section.section_id,
                                      section.model_copy(update={"items": items}), "section_id")
    else:
        raise WorkspaceError(E.OPERATION_NOT_ALLOWED, op.op)
    return StructuredArtifactPayload(**fields)


def constraint_violations(payload) -> tuple[str, ...]:
    """Dishes that declare a food class some constraint excludes (canonical order)."""

    if not isinstance(payload, MealPlanPayload):
        return ()
    excluded = set()
    for item in payload.constraints:
        excluded |= item.excluded_food_classes
    return tuple(sorted(dish.dish_id for dish in payload.dishes if set(dish.contains) & excluded))


@dataclass(frozen=True)
class PatchResult:
    revision: ArtifactRevision
    base: ArtifactRevision | None
    removed_constraint_refs: tuple[str, ...]


def apply_patch(
    patch: ArtifactPatch, *, base: ArtifactRevision | None, conversation_ref: str, turn_ref: str,
    producer_ref: str, authority: PatchAuthority,
) -> PatchResult:
    """Apply ``patch`` to ``base`` and return one sealed *proposed* revision."""

    if patch.operation == ArtifactOperation.CREATE:
        if base is not None:
            raise WorkspaceError(E.ARTIFACT_EXISTS, patch.artifact_ref)
        payload = empty_payload(patch.artifact_type)
        number, parent, title = 1, None, patch.title
    else:
        if base is None:
            raise WorkspaceError(E.UNKNOWN_ARTIFACT, patch.artifact_ref)
        if base.artifact_id != patch.artifact_ref:
            raise WorkspaceError(E.UNKNOWN_ARTIFACT, patch.artifact_ref)
        if base.revision_id != patch.base_revision_ref:
            raise WorkspaceError(E.STALE_BASE, patch.artifact_ref)
        if base.artifact_type != patch.artifact_type:
            raise WorkspaceError(E.ARTIFACT_TYPE_MISMATCH, patch.artifact_ref)
        if base.conversation_ref != conversation_ref:
            raise WorkspaceError(E.UNKNOWN_ARTIFACT, patch.artifact_ref)
        payload = base.payload
        number, parent, title = base.revision_number + 1, base.revision_id, patch.title or base.title
    _check_provenance(patch, authority)
    written: set[tuple] = set()
    for op in patch.operations:
        for target in _targets(op):
            if target in written:
                raise WorkspaceError(E.CONFLICTING_OPERATIONS, op.op)
            written.add(target)
    before = {item.constraint_id for item in getattr(payload, "constraints", ())}
    removed = tuple(sorted(op.constraint_id for op in patch.operations if isinstance(op, RemoveConstraint)))
    try:
        for op in patch.operations:
            payload = _apply_meal(payload, op) if isinstance(payload, MealPlanPayload) else _apply_structured(payload, op)
    except ValidationError as exc:
        raise WorkspaceError(E.PAYLOAD_INVALID, patch.artifact_ref) from exc
    after = {item.constraint_id for item in getattr(payload, "constraints", ())}
    if not before - set(removed) <= after:
        raise WorkspaceError(E.CONSTRAINT_LOST, patch.artifact_ref)
    violations = constraint_violations(payload)
    if violations:
        raise WorkspaceError(E.CONSTRAINT_CONFLICT, violations[0])
    if base is not None and payload == base.payload and title == base.title:
        raise WorkspaceError(E.NO_OP_PATCH, patch.artifact_ref)
    if payload.payload_type != PAYLOAD_TYPE_FOR_ARTIFACT[patch.artifact_type]:
        raise WorkspaceError(E.PAYLOAD_INVALID, patch.artifact_ref)
    refs = tuple(sorted({*patch.source_refs, *(ref for op in patch.operations for ref in op.source_refs)}))
    revision = ArtifactRevision.sealed(
        artifact_id=patch.artifact_ref, conversation_ref=conversation_ref, artifact_type=patch.artifact_type,
        revision_number=number, parent_revision_ref=parent, status=ArtifactRevisionStatus.PROPOSED, title=title,
        payload=payload, source_refs=refs, created_turn_ref=turn_ref, producer_ref=producer_ref,
    )
    return PatchResult(revision=revision, base=base, removed_constraint_refs=removed)


__all__ = ["PATCH_ENGINE_VERSION", "PatchAuthority", "PatchResult", "apply_patch", "constraint_violations"]
