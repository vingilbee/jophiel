"""Shared builders for Conversational Workspace V0 tests."""

from __future__ import annotations

from app.ai.Cognitive.system.v0.contracts.workspace import (
    AddConstraint, AddNote, AddSideDish, ArtifactConstraint, ArtifactNote, ArtifactOperation, ArtifactPatch,
    ArtifactType, ContentClass, MealDish, SetMainDish, SetServings, SetTimeLimit, SourcedInteger, UpdateConstraint,
    UpdateDish,
)
from app.ai.Cognitive.system.v0.conversation_workspace_v0 import PatchAuthority, apply_patch

CONVERSATION = "conversation-1"
REQUEST = "frame-turn2-request"
CONSTRAINTS = "frame-turn2-request#constraints"
REVISE = "frame-turn4-request"
REVISE_CONSTRAINTS = "frame-turn4-request#constraints"
ANSWER = "frame-turn7-answer"
WITHDRAW = "frame-withdrawal"
ALL_REFS = frozenset({REQUEST, CONSTRAINTS, REVISE, REVISE_CONSTRAINTS, ANSWER, WITHDRAW, "model-proposal"})
USER_REFS = frozenset({REQUEST, CONSTRAINTS, REVISE, REVISE_CONSTRAINTS, ANSWER, WITHDRAW})
AUTHORITY = PatchAuthority(source_refs=ALL_REFS, user_refs=USER_REFS, withdrawal_refs=frozenset({WITHDRAW}))
ARTIFACT = "artifact-dinner"


def constraint(constraint_id: str, kind: str, value: str, *, refs=(CONSTRAINTS,), basis=ContentClass.USER_SUPPLIED_CONSTRAINT,
               reason: str = "unspecified") -> ArtifactConstraint:
    return ArtifactConstraint(constraint_id=constraint_id, kind=kind, value=value, basis=basis, reason=reason,
                              source_refs=tuple(sorted(refs)))


def dish(dish_id: str, name: str, *, contains=(), traits=(), minutes=None, refs=("model-proposal",),
         content_class=ContentClass.GENERATED_PROPOSAL) -> MealDish:
    return MealDish(dish_id=dish_id, name=name, content_class=content_class, contains=tuple(sorted(contains)),
                    traits=tuple(sorted(traits)), prep_time_minutes=minutes, source_refs=tuple(sorted(refs)))


def create_patch() -> ArtifactPatch:
    return ArtifactPatch(
        patch_id="patch-create", artifact_ref=ARTIFACT, artifact_type=ArtifactType.MEAL_PLAN,
        operation=ArtifactOperation.CREATE, title="Vegetarian dinner for six", source_refs=tuple(sorted((CONSTRAINTS, REQUEST))),
        operations=(
            SetServings(value=SourcedInteger(value=6, content_class=ContentClass.USER_SUPPLIED_CONSTRAINT,
                                             source_refs=(CONSTRAINTS,)), source_refs=(CONSTRAINTS,)),
            SetTimeLimit(value=SourcedInteger(value=30, content_class=ContentClass.USER_SUPPLIED_CONSTRAINT,
                                              source_refs=(CONSTRAINTS,)), source_refs=(CONSTRAINTS,)),
            AddConstraint(constraint=constraint("vegetarian", "dietary", "vegetarian"), source_refs=(CONSTRAINTS,)),
            SetMainDish(dish=dish("main", "chickpea and spinach stew", contains=("dairy",), traits=("one_pot", "quick"),
                                  minutes=25), source_refs=("model-proposal",)),
        ),
    )


def created(turn: str = "turn-2"):
    return apply_patch(create_patch(), base=None, conversation_ref=CONVERSATION, turn_ref=turn,
                       producer_ref="test-producer", authority=AUTHORITY).revision


def delivered(revision, outcome: str = "outcome-1"):
    from app.ai.Cognitive.system.v0.contracts.workspace import ArtifactRevisionStatus

    return revision.with_status(ArtifactRevisionStatus.DELIVERED, delivered_outcome_ref=outcome)


def revise_patch(base, *, operations=None, patch_id: str = "patch-revise") -> ArtifactPatch:
    return ArtifactPatch(
        patch_id=patch_id, artifact_ref=ARTIFACT, artifact_type=ArtifactType.MEAL_PLAN,
        base_revision_ref=base.revision_id, operation=ArtifactOperation.REVISE, source_refs=(REVISE,),
        operations=operations or (
            AddConstraint(constraint=constraint("dairy-free", "dietary", "dairy_free", refs=(REVISE_CONSTRAINTS,)),
                          source_refs=(REVISE_CONSTRAINTS,)),
            UpdateDish(dish=dish("main", "chickpea and spinach stew", traits=("one_pot", "quick"), minutes=25),
                       source_refs=tuple(sorted(("model-proposal", REVISE_CONSTRAINTS)))),
            AddSideDish(dish=dish("side-rice", "steamed rice", traits=("inexpensive",), minutes=20),
                        source_refs=tuple(sorted(("model-proposal", REVISE_CONSTRAINTS)))),
        ),
    )


def revised(base, turn: str = "turn-4"):
    return apply_patch(revise_patch(base), base=base, conversation_ref=CONVERSATION, turn_ref=turn,
                       producer_ref="test-producer", authority=AUTHORITY).revision


__all__ = [name for name in globals() if not name.startswith("_")] + [
    "AddNote", "ArtifactNote", "UpdateConstraint",
]
