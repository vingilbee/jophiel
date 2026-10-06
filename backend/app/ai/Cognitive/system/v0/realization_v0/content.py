"""Authorized content-index construction and resolution only.

The content index maps upstream Action refs (move targets, content-bearing
evidence, and reference parameters) to the inventory items that represent
them.  It decides nothing about wording.

``project_dialogue_structure`` is the one V0 content producer connected to live
shadow.  It projects only content-free dialogue structure from the selected
plan itself -- "the user's contribution", "this conversation", "an unresolved
element of the user's message" -- as *generic* references with no wording.
It asserts nothing, reads no memory, tool, or history, and supplies no
propositions; grounded content remains an explicit omission until a trusted
producer is connected.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from ..contracts.actions import ActionSelectionResult, ActionTargetType, GroundedActionMove
from ..contracts.realization import (
    ContentAttribution, ContentEpistemicStatus, ContentSourceKind, DialogueReference,
    DialogueReferenceKind, RealizationContentInventory, ReferenceForm, realization_uuid,
)

DIALOGUE_STRUCTURE_PRODUCER = "realization-dialogue-structure-projection-v0.1"
# The content ids this producer derives are part of its own versioned lineage,
# so they stay stable across the realization-content-v0.2 contract change.
_STRUCTURE_ID_LINEAGE = "realization-content-v0.1"

# Evidence roles that record *why* a move is grounded (the observation of the
# user's input), not material the move words.  They never bind content.
NON_CONTENT_EVIDENCE_ROLES = frozenset({"grounding", "locator"})
# Action parameters whose values are upstream references.
REFERENCE_PARAMETERS = (
    "basis_refs", "candidate_answer_refs", "correction_content_refs", "option_refs", "reason_refs", "scope_ref",
    "step_refs",
)


def move_references(move: GroundedActionMove) -> Iterator[tuple[str, str]]:
    """Every ``(binding role, upstream ref)`` a move may bind content through, in canonical order."""

    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for target in move.target_refs:
        pairs.append((f"target:{target.role}", target.ref))
    for evidence in move.supporting_evidence_refs:
        if evidence.role not in NON_CONTENT_EVIDENCE_ROLES:
            pairs.append((f"evidence:{evidence.role}", evidence.ref))
    for key in REFERENCE_PARAMETERS:
        value = move.parameters.get(key)
        if isinstance(value, str):
            pairs.append((f"parameter:{key}", value))
        elif isinstance(value, (list, tuple)):
            pairs.extend((f"parameter:{key}", item) for item in value if isinstance(item, str))
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            yield pair


class ContentIndex:
    """Immutable lookup from upstream refs to the inventory items representing them."""

    def __init__(self, inventory: RealizationContentInventory) -> None:
        self.inventory = inventory
        self._by_id = {item.content_id: item for item in inventory.items}
        by_ref: dict[str, list] = defaultdict(list)
        for item in inventory.items:
            for ref in item.represents_refs:
                by_ref[ref].append(item)
        self._by_ref = {ref: tuple(sorted(items, key=lambda item: item.content_id)) for ref, items in by_ref.items()}

    def resolve(self, ref: str) -> tuple:
        """Items representing ``ref`` in canonical id order (empty when unresolvable)."""

        return self._by_ref.get(ref, ())

    def get(self, content_id: str):
        return self._by_id.get(content_id)


_DIALOGUE_TARGETS: dict[ActionTargetType, tuple[DialogueReferenceKind, ContentSourceKind, ContentAttribution,
                                                ContentEpistemicStatus]] = {
    ActionTargetType.MESSAGE: (DialogueReferenceKind.USER_CONTRIBUTION, ContentSourceKind.CURRENT_USER_STATEMENT,
                               ContentAttribution.USER, ContentEpistemicStatus.OBSERVED),
    ActionTargetType.USER_ACT: (DialogueReferenceKind.USER_CONTRIBUTION, ContentSourceKind.CURRENT_USER_STATEMENT,
                                ContentAttribution.USER, ContentEpistemicStatus.OBSERVED),
    ActionTargetType.DIALOGUE_SCOPE: (DialogueReferenceKind.DIALOGUE_SCOPE,
                                      ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION, ContentAttribution.RUNTIME,
                                      ContentEpistemicStatus.OBSERVED),
    ActionTargetType.SEMANTIC_ARGUMENT: (DialogueReferenceKind.UNRESOLVED_ELEMENT,
                                         ContentSourceKind.CURRENT_USER_STATEMENT, ContentAttribution.USER,
                                         ContentEpistemicStatus.UNRESOLVED),
    ActionTargetType.UNKNOWN: (DialogueReferenceKind.UNRESOLVED_ELEMENT, ContentSourceKind.CURRENT_USER_STATEMENT,
                               ContentAttribution.USER, ContentEpistemicStatus.UNRESOLVED),
}


def project_dialogue_structure(result: ActionSelectionResult) -> RealizationContentInventory:
    """Generic, content-free dialogue references for the selected plan's structural targets."""

    turn = result.cognitive_turn_id
    items: dict[str, DialogueReference] = {}
    for move in result.plan.moves:
        for target in move.target_refs:
            spec = _DIALOGUE_TARGETS.get(target.target_type)
            if spec is None:
                continue
            kind, source, attribution, status = spec
            content_id = "dialogue-ref:" + realization_uuid(
                _STRUCTURE_ID_LINEAGE, DIALOGUE_STRUCTURE_PRODUCER, turn, target.target_type.value, target.ref,
            )
            items.setdefault(content_id, DialogueReference(
                content_id=content_id, cognitive_turn_id=turn, source_kind=source,
                source_refs=(f"action-result:{result.result_id}", f"action-target:{target.ref}"),
                represents_refs=(target.ref,), authorized_for_realization=True,
                epistemic_status=status, attribution=attribution, reference_kind=kind,
                target_ref=target.ref, reference_form=ReferenceForm.GENERIC,
            ))
    return RealizationContentInventory(
        inventory_id=realization_uuid(_STRUCTURE_ID_LINEAGE, "inventory", DIALOGUE_STRUCTURE_PRODUCER,
                                      result.result_id),
        cognitive_turn_id=turn, producer_refs=(DIALOGUE_STRUCTURE_PRODUCER,),
        items=tuple(items[key] for key in sorted(items)),
    )


__all__ = [
    "ContentIndex", "DIALOGUE_STRUCTURE_PRODUCER", "NON_CONTENT_EVIDENCE_ROLES", "REFERENCE_PARAMETERS",
    "move_references", "project_dialogue_structure",
]
