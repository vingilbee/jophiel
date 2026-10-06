"""Conversational Workspace V0 contracts (``conversation-workspace-contract-v0.1``).

Dialogue Working Memory records what was *said*.  The conversational
workspace records the versioned *work product* being developed together: a
meal plan, a draft, an outline.  This module holds contracts only -- the
closed content-class policy, the typed artifact payloads, the immutable
revision envelope, typed patches, open inquiries, delivered-response outcomes,
and the conversation-scoped snapshot -- and imports no engine.

Authority is explicit everywhere:

- a *proposed* revision is not a *delivered* revision, and only a validated
  delivered-response outcome may mark one delivered;
- generated proposals are representable but can never claim verified-fact,
  runtime, or tool authority;
- user statements stay attributed user content;
- every value-bearing field carries its content class and exact source refs.

Payloads are a closed, versioned union: there is no unrestricted JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator

from .interpretation import FrozenModel

WORKSPACE_CONTRACT_VERSION = "conversation-workspace-contract-v0.1"
MEAL_PLAN_PAYLOAD_VERSION = "meal-plan-payload-v0.1"
STRUCTURED_PAYLOAD_VERSION = "structured-artifact-payload-v0.1"
CONTENT_CLASS_POLICY_VERSION = "content-class-policy-v0.1"
WORKSPACE_DIGEST_VERSION = "conversation-workspace-digest-v0.1"

MAX_LABEL_CHARACTERS = 120
MAX_TEXT_CHARACTERS = 400
MAX_ITEMS = 24
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.:\-#]*$")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _sorted_unique(values: tuple[str, ...], label: str) -> None:
    for value in values:
        _nonblank(value, label)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be unique and in canonical order")


def bounded_text(value: str, label: str, limit: int = MAX_TEXT_CHARACTERS) -> None:
    """Single-line, printable, bounded text (generated wording stays inert data)."""

    import unicodedata

    _nonblank(value, label)
    if len(value) > limit or value != value.strip():
        raise ValueError(f"{label} must be trimmed and at most {limit} characters")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"} for char in value):
        raise ValueError(f"{label} must not contain control or format characters")


def _identifier(value: str, label: str) -> None:
    _nonblank(value, label)
    if not _SLUG.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase identifier")


# ---------------------------------------------------------------------------
# Content classes and their epistemic policy (Section 8)
# ---------------------------------------------------------------------------

class ContentClass(StrEnum):
    VERIFIED_FACT = "verified_fact"
    TRUSTED_RUNTIME_OBSERVATION = "trusted_runtime_observation"
    USER_SUPPLIED_CONSTRAINT = "user_supplied_constraint"
    ATTRIBUTED_USER_CLAIM = "attributed_user_claim"
    DELIVERED_DIALOGUE_CONTENT = "delivered_dialogue_content"
    ARTIFACT_CONTENT = "artifact_content"
    GENERATED_PROPOSAL = "generated_proposal"
    REASONED_INFERENCE = "reasoned_inference"
    UNCERTAIN_SUGGESTION = "uncertain_suggestion"
    TOOL_RESULT = "tool_result"
    KNOWLEDGE_GAP = "knowledge_gap"


class ContentProducerKind(StrEnum):
    TRUSTED_PRODUCER = "trusted_producer"
    RUNTIME = "runtime"
    USER = "user"
    DIALOGUE_MEMORY = "dialogue_memory"
    WORKSPACE = "workspace"
    COLLABORATION_MODEL = "collaboration_model"
    TOOL_EXECUTOR = "tool_executor"
    REQUIREMENT_RESOLUTION = "requirement_resolution"


class RealizationModality(StrEnum):
    ASSERT = "assert"            # may be worded as a plain statement
    PRESENT = "present"          # worded as the content of the shared artifact
    SUGGEST = "suggest"          # worded as a suggestion or proposal
    HEDGE = "hedge"              # worded with a visible uncertainty marker
    ATTRIBUTE = "attribute"      # worded with explicit attribution to its speaker
    QUOTE = "quote"              # quoted or referenced, never asserted
    DISCLOSE_GAP = "disclose_gap"


class ContentClassPolicy(FrozenModel):
    """What one content class is, and what it may and may not support."""

    content_class: ContentClass
    producer: ContentProducerKind
    supports_factual_assertion: bool
    attribution_required: bool
    may_update_artifact: bool
    may_present_as_suggestion: bool
    requires_external_verification: bool
    # Only these classes may be assigned by the collaborative-content model;
    # the user classes additionally require user-sourced refs (checked by the
    # compiler), the rest come only from their trusted producers.
    model_assignable: bool
    requires_user_source: bool = False
    sensitivity_scope: Literal["ordinary", "personal"] = "ordinary"
    realization_modalities: tuple[RealizationModality, ...] = Field(min_length=1)


M = RealizationModality
C = ContentClass
P = ContentProducerKind
CONTENT_CLASS_POLICIES: Mapping[ContentClass, ContentClassPolicy] = {
    item.content_class: item for item in (
        ContentClassPolicy(content_class=C.VERIFIED_FACT, producer=P.TRUSTED_PRODUCER,
                           supports_factual_assertion=True, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=False, requires_external_verification=True,
                           model_assignable=False, realization_modalities=(M.ASSERT,)),
        ContentClassPolicy(content_class=C.TRUSTED_RUNTIME_OBSERVATION, producer=P.RUNTIME,
                           supports_factual_assertion=True, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=False, realization_modalities=(M.ASSERT,)),
        ContentClassPolicy(content_class=C.USER_SUPPLIED_CONSTRAINT, producer=P.USER,
                           supports_factual_assertion=False, attribution_required=True, may_update_artifact=True,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=True, requires_user_source=True, sensitivity_scope="personal",
                           realization_modalities=(M.ATTRIBUTE, M.PRESENT)),
        ContentClassPolicy(content_class=C.ATTRIBUTED_USER_CLAIM, producer=P.USER,
                           supports_factual_assertion=False, attribution_required=True, may_update_artifact=True,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=True, requires_user_source=True, sensitivity_scope="personal",
                           realization_modalities=(M.ATTRIBUTE,)),
        ContentClassPolicy(content_class=C.DELIVERED_DIALOGUE_CONTENT, producer=P.DIALOGUE_MEMORY,
                           supports_factual_assertion=False, attribution_required=True, may_update_artifact=False,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=False, realization_modalities=(M.QUOTE, M.ATTRIBUTE)),
        ContentClassPolicy(content_class=C.ARTIFACT_CONTENT, producer=P.WORKSPACE,
                           supports_factual_assertion=False, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=True, requires_external_verification=False,
                           model_assignable=False, realization_modalities=(M.PRESENT, M.SUGGEST)),
        ContentClassPolicy(content_class=C.GENERATED_PROPOSAL, producer=P.COLLABORATION_MODEL,
                           supports_factual_assertion=False, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=True, requires_external_verification=False,
                           model_assignable=True, realization_modalities=(M.PRESENT, M.SUGGEST)),
        ContentClassPolicy(content_class=C.REASONED_INFERENCE, producer=P.COLLABORATION_MODEL,
                           supports_factual_assertion=False, attribution_required=False, may_update_artifact=False,
                           may_present_as_suggestion=True, requires_external_verification=False,
                           model_assignable=True, realization_modalities=(M.SUGGEST, M.HEDGE)),
        ContentClassPolicy(content_class=C.UNCERTAIN_SUGGESTION, producer=P.COLLABORATION_MODEL,
                           supports_factual_assertion=False, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=True, requires_external_verification=False,
                           model_assignable=True, realization_modalities=(M.HEDGE, M.SUGGEST)),
        ContentClassPolicy(content_class=C.TOOL_RESULT, producer=P.TOOL_EXECUTOR,
                           supports_factual_assertion=True, attribution_required=False, may_update_artifact=True,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=False, realization_modalities=(M.ASSERT,)),
        ContentClassPolicy(content_class=C.KNOWLEDGE_GAP, producer=P.REQUIREMENT_RESOLUTION,
                           supports_factual_assertion=False, attribution_required=False, may_update_artifact=False,
                           may_present_as_suggestion=False, requires_external_verification=False,
                           model_assignable=False, realization_modalities=(M.DISCLOSE_GAP,)),
    )
}
if set(CONTENT_CLASS_POLICIES) != set(ContentClass):
    raise RuntimeError("every content class declares its epistemic policy")
MODEL_ASSIGNABLE_CLASSES = frozenset(key for key, item in CONTENT_CLASS_POLICIES.items() if item.model_assignable)
USER_SOURCED_CLASSES = frozenset(key for key, item in CONTENT_CLASS_POLICIES.items() if item.requires_user_source)
ARTIFACT_UPDATING_CLASSES = frozenset(key for key, item in CONTENT_CLASS_POLICIES.items() if item.may_update_artifact)
FACTUAL_CLASSES = frozenset(key for key, item in CONTENT_CLASS_POLICIES.items() if item.supports_factual_assertion)


# ---------------------------------------------------------------------------
# Closed artifact vocabularies
# ---------------------------------------------------------------------------

class ArtifactType(StrEnum):
    MEAL_PLAN = "meal_plan"
    PLAN = "plan"
    DRAFT_MESSAGE = "draft_message"
    OUTLINE = "outline"
    CHECKLIST = "checklist"
    COMPARISON = "comparison"
    SUMMARY = "summary"
    LIST = "list"
    OTHER = "other"


class ArtifactOperation(StrEnum):
    CREATE = "create"
    REVISE = "revise"
    EXPLAIN = "explain"
    EVALUATE = "evaluate"
    DESCRIBE = "describe"
    COMPARE = "compare"
    SUMMARIZE = "summarize"


# Operations that produce a new revision; the rest present an existing one.
REVISION_OPERATIONS = frozenset({ArtifactOperation.CREATE, ArtifactOperation.REVISE})
PRESENTATION_OPERATIONS = frozenset(set(ArtifactOperation) - REVISION_OPERATIONS)


class ConstraintKind(StrEnum):
    DIETARY = "dietary"
    ALLERGY = "allergy"
    TIME = "time"
    QUANTITY = "quantity"
    BUDGET = "budget"
    PREFERENCE = "preference"
    OTHER = "other"


class FoodClass(StrEnum):
    """Food classes a dish declares it contains (a declaration, never a verified analysis)."""

    MEAT = "meat"
    POULTRY = "poultry"
    FISH = "fish"
    SHELLFISH = "shellfish"
    DAIRY = "dairy"
    EGGS = "eggs"
    GLUTEN = "gluten"
    TREE_NUTS = "tree_nuts"
    PEANUTS = "peanuts"
    SOY = "soy"
    SESAME = "sesame"


class DietaryValue(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"
    DAIRY_FREE = "dairy_free"
    GLUTEN_FREE = "gluten_free"
    NUT_FREE = "nut_free"
    PEANUT_FREE = "peanut_free"
    EGG_FREE = "egg_free"
    SOY_FREE = "soy_free"
    SESAME_FREE = "sesame_free"


F = FoodClass
# The food classes each dietary constraint excludes (deterministic conflict detection).
DIETARY_EXCLUSIONS: Mapping[DietaryValue, frozenset[FoodClass]] = {
    DietaryValue.VEGETARIAN: frozenset({F.MEAT, F.POULTRY, F.FISH, F.SHELLFISH}),
    DietaryValue.VEGAN: frozenset({F.MEAT, F.POULTRY, F.FISH, F.SHELLFISH, F.DAIRY, F.EGGS}),
    DietaryValue.PESCATARIAN: frozenset({F.MEAT, F.POULTRY}),
    DietaryValue.DAIRY_FREE: frozenset({F.DAIRY}),
    DietaryValue.GLUTEN_FREE: frozenset({F.GLUTEN}),
    DietaryValue.NUT_FREE: frozenset({F.TREE_NUTS, F.PEANUTS}),
    DietaryValue.PEANUT_FREE: frozenset({F.PEANUTS}),
    DietaryValue.EGG_FREE: frozenset({F.EGGS}),
    DietaryValue.SOY_FREE: frozenset({F.SOY}),
    DietaryValue.SESAME_FREE: frozenset({F.SESAME}),
}
if set(DIETARY_EXCLUSIONS) != set(DietaryValue):
    raise RuntimeError("every dietary value declares its exclusions")


class DishTrait(StrEnum):
    INEXPENSIVE = "inexpensive"
    QUICK = "quick"
    ONE_POT = "one_pot"
    MAKE_AHEAD = "make_ahead"
    CROWD_FRIENDLY = "crowd_friendly"
    HEARTY = "hearty"
    LIGHT = "light"


class ConstraintReason(StrEnum):
    UNSPECIFIED = "unspecified"
    PREFERENCE = "preference"
    ALLERGY = "allergy"
    INTOLERANCE = "intolerance"
    ETHICAL = "ethical"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Typed payload elements
# ---------------------------------------------------------------------------

class SourcedInteger(FrozenModel):
    value: int = Field(ge=1, le=10_000)
    content_class: ContentClass
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> SourcedInteger:
        _sorted_unique(self.source_refs, "integer source refs")
        if self.content_class not in ARTIFACT_UPDATING_CLASSES:
            raise ValueError("this content class may not set an artifact value")
        return self


class ArtifactConstraint(FrozenModel):
    """A requirement the artifact must respect.  Always the user's, always attributed."""

    constraint_id: str = Field(min_length=1)
    kind: ConstraintKind
    value: str = Field(min_length=1)
    basis: Literal[ContentClass.USER_SUPPLIED_CONSTRAINT, ContentClass.ATTRIBUTED_USER_CLAIM]
    reason: ConstraintReason = ConstraintReason.UNSPECIFIED
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ArtifactConstraint:
        _identifier(self.constraint_id, "constraint id")
        _sorted_unique(self.source_refs, "constraint source refs")
        if self.kind == ConstraintKind.DIETARY:
            DietaryValue(self.value)
        elif self.kind == ConstraintKind.ALLERGY:
            FoodClass(self.value)
            if self.basis != ContentClass.ATTRIBUTED_USER_CLAIM:
                # The user reported an allergy; its diagnosis or severity is never inferred.
                raise ValueError("an allergy constraint is an attributed user claim")
        else:
            bounded_text(self.value, "constraint value", MAX_LABEL_CHARACTERS)
        return self

    @property
    def excluded_food_classes(self) -> frozenset[FoodClass]:
        if self.kind == ConstraintKind.DIETARY:
            return DIETARY_EXCLUSIONS[DietaryValue(self.value)]
        if self.kind == ConstraintKind.ALLERGY:
            return frozenset({FoodClass(self.value)})
        return frozenset()


class ArtifactNote(FrozenModel):
    note_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    content_class: ContentClass
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ArtifactNote:
        _identifier(self.note_id, "note id")
        bounded_text(self.text, "note text")
        _sorted_unique(self.source_refs, "note source refs")
        if self.content_class in FACTUAL_CLASSES:
            raise ValueError("an artifact note never claims verified, runtime, or tool authority")
        return self


class MealDish(FrozenModel):
    dish_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    content_class: ContentClass
    # Declared composition (what the proposal says the dish contains).
    contains: tuple[FoodClass, ...] = ()
    traits: tuple[DishTrait, ...] = ()
    prep_time_minutes: int | None = Field(default=None, ge=1, le=1440)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> MealDish:
        _identifier(self.dish_id, "dish id")
        bounded_text(self.name, "dish name", MAX_LABEL_CHARACTERS)
        _sorted_unique(self.source_refs, "dish source refs")
        if tuple(sorted(set(self.contains))) != self.contains or tuple(sorted(set(self.traits))) != self.traits:
            raise ValueError("dish composition and traits must be unique and in canonical order")
        if self.content_class not in {
            ContentClass.GENERATED_PROPOSAL, ContentClass.UNCERTAIN_SUGGESTION, ContentClass.USER_SUPPLIED_CONSTRAINT,
            ContentClass.ARTIFACT_CONTENT,
        }:
            raise ValueError("a dish is a proposal, a suggestion, carried artifact content, or the user's choice")
        return self


MEAL_PLAN_OTHER_KINDS = frozenset({ConstraintKind.BUDGET, ConstraintKind.PREFERENCE, ConstraintKind.OTHER})


class MealPlanPayload(FrozenModel):
    payload_type: Literal["meal_plan"] = "meal_plan"
    payload_version: Literal["meal-plan-payload-v0.1"] = MEAL_PLAN_PAYLOAD_VERSION
    servings: SourcedInteger | None = None
    time_limit_minutes: SourcedInteger | None = None
    dietary_constraints: tuple[ArtifactConstraint, ...] = ()
    allergy_constraints: tuple[ArtifactConstraint, ...] = ()
    # Budget, preference, and other stated requirements (servings and time
    # limits are the typed fields above).
    other_constraints: tuple[ArtifactConstraint, ...] = ()
    main_dish: MealDish | None = None
    side_dishes: tuple[MealDish, ...] = Field(default=(), max_length=MAX_ITEMS)
    notes: tuple[ArtifactNote, ...] = Field(default=(), max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def valid(self) -> MealPlanPayload:
        if any(item.kind != ConstraintKind.DIETARY for item in self.dietary_constraints):
            raise ValueError("dietary constraints carry only dietary values")
        if any(item.kind != ConstraintKind.ALLERGY for item in self.allergy_constraints):
            raise ValueError("allergy constraints carry only allergens")
        if any(item.kind not in MEAL_PLAN_OTHER_KINDS for item in self.other_constraints):
            raise ValueError("other meal-plan constraints are budget, preference, or other requirements")
        for label, items in (("dietary constraints", self.dietary_constraints),
                             ("allergy constraints", self.allergy_constraints),
                             ("other constraints", self.other_constraints)):
            values = tuple(item.value for item in items)
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not repeat a value")
        ids = [item.constraint_id for item in self.constraints]
        ids += [item.dish_id for item in ((self.main_dish,) if self.main_dish else ())]
        ids += [item.dish_id for item in self.side_dishes] + [item.note_id for item in self.notes]
        if len(ids) != len(set(ids)):
            raise ValueError("element ids are unique within a payload")
        return self

    @property
    def constraints(self) -> tuple[ArtifactConstraint, ...]:
        return (*self.dietary_constraints, *self.allergy_constraints, *self.other_constraints)

    @property
    def dishes(self) -> tuple[MealDish, ...]:
        return ((self.main_dish,) if self.main_dish is not None else ()) + self.side_dishes


class ArtifactItem(FrozenModel):
    item_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    content_class: ContentClass
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ArtifactItem:
        _identifier(self.item_id, "item id")
        bounded_text(self.text, "item text")
        _sorted_unique(self.source_refs, "item source refs")
        if self.content_class not in ARTIFACT_UPDATING_CLASSES or self.content_class in FACTUAL_CLASSES:
            raise ValueError("a generic artifact item is attributed or proposed content, never a verified claim")
        return self


class ArtifactSection(FrozenModel):
    section_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    items: tuple[ArtifactItem, ...] = Field(default=(), max_length=MAX_ITEMS)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ArtifactSection:
        _identifier(self.section_id, "section id")
        bounded_text(self.heading, "section heading", MAX_LABEL_CHARACTERS)
        _sorted_unique(self.source_refs, "section source refs")
        ids = tuple(item.item_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("section item ids must be unique")
        return self


class StructuredArtifactPayload(FrozenModel):
    """The generic structured conversational artifact: sections, items, constraints, notes."""

    payload_type: Literal["structured"] = "structured"
    payload_version: Literal["structured-artifact-payload-v0.1"] = STRUCTURED_PAYLOAD_VERSION
    sections: tuple[ArtifactSection, ...] = Field(default=(), max_length=MAX_ITEMS)
    constraints: tuple[ArtifactConstraint, ...] = ()
    notes: tuple[ArtifactNote, ...] = Field(default=(), max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def valid(self) -> StructuredArtifactPayload:
        ids = [item.section_id for item in self.sections]
        ids += [item.item_id for section in self.sections for item in section.items]
        ids += [item.constraint_id for item in self.constraints] + [item.note_id for item in self.notes]
        if len(ids) != len(set(ids)):
            raise ValueError("element ids are unique within a payload")
        return self


ArtifactPayload = Annotated[Union[MealPlanPayload, StructuredArtifactPayload], Field(discriminator="payload_type")]
PAYLOAD_TYPE_FOR_ARTIFACT: Mapping[ArtifactType, str] = {
    item: ("meal_plan" if item == ArtifactType.MEAL_PLAN else "structured") for item in ArtifactType
}


def empty_payload(artifact_type: ArtifactType) -> MealPlanPayload | StructuredArtifactPayload:
    return MealPlanPayload() if PAYLOAD_TYPE_FOR_ARTIFACT[artifact_type] == "meal_plan" else StructuredArtifactPayload()


def payload_class_counts(payload) -> tuple[tuple[str, int], ...]:
    """Value-free content-class counts of a payload (the epistemic summary)."""

    counts: dict[str, int] = {}

    def add(content_class) -> None:
        counts[ContentClass(content_class).value] = counts.get(ContentClass(content_class).value, 0) + 1

    if isinstance(payload, MealPlanPayload):
        for value in (payload.servings, payload.time_limit_minutes):
            if value is not None:
                add(value.content_class)
        for item in payload.constraints:
            add(item.basis)
        for dish in payload.dishes:
            add(dish.content_class)
        for note in payload.notes:
            add(note.content_class)
    else:
        for section in payload.sections:
            for item in section.items:
                add(item.content_class)
        for item in payload.constraints:
            add(item.basis)
        for note in payload.notes:
            add(note.content_class)
    return tuple(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Immutable revision envelope (Section 7.2)
# ---------------------------------------------------------------------------

class ArtifactRevisionStatus(StrEnum):
    PROPOSED = "proposed"
    OBSERVED = "observed"
    DELIVERED = "delivered"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"


def revision_identity(*, artifact_id: str, artifact_type: str, revision_number: int, parent_revision_ref: str | None,
                      title: str | None, payload: Mapping[str, Any]) -> str:
    """Content-addressed revision identity (status and delivery never change it)."""

    digest = sha256_of({
        "artifact_id": artifact_id, "artifact_type": artifact_type, "revision_number": revision_number,
        "parent_revision_ref": parent_revision_ref, "title": title, "payload": payload,
        "version": WORKSPACE_DIGEST_VERSION,
    })
    return f"artifact-revision:{digest[:32]}"


class ArtifactRevision(FrozenModel):
    """One immutable revision of one conversational artifact.

    ``revision_id`` addresses the content; ``digest`` seals every field
    (including status).  A status change produces a new envelope with the same
    revision id, never an in-place edit.
    """

    contract_version: Literal["conversation-workspace-contract-v0.1"] = WORKSPACE_CONTRACT_VERSION
    artifact_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    artifact_type: ArtifactType
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    parent_revision_ref: str | None = None
    status: ArtifactRevisionStatus
    title: str | None = None
    payload: ArtifactPayload
    source_refs: tuple[str, ...] = Field(min_length=1)
    created_turn_ref: str = Field(min_length=1)
    delivered_outcome_ref: str | None = None
    producer_ref: str = Field(min_length=1)
    epistemic_summary: tuple[tuple[str, int], ...] = ()
    digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> ArtifactRevision:
        _identifier(self.artifact_id, "artifact id")
        _sorted_unique(self.source_refs, "revision source refs")
        if self.title is not None:
            bounded_text(self.title, "artifact title", MAX_LABEL_CHARACTERS)
        if self.payload.payload_type != PAYLOAD_TYPE_FOR_ARTIFACT[self.artifact_type]:
            raise ValueError("the payload type is not the registered payload of this artifact type")
        if (self.revision_number == 1) != (self.parent_revision_ref is None):
            raise ValueError("only the first revision has no parent")
        expected = revision_identity(
            artifact_id=self.artifact_id, artifact_type=self.artifact_type.value,
            revision_number=self.revision_number, parent_revision_ref=self.parent_revision_ref, title=self.title,
            payload=self.payload.model_dump(mode="json"),
        )
        if self.revision_id != expected:
            raise ValueError("the revision id does not address its content")
        delivered = self.status == ArtifactRevisionStatus.DELIVERED
        if delivered != (self.delivered_outcome_ref is not None) and self.status != ArtifactRevisionStatus.SUPERSEDED:
            raise ValueError("a delivered revision, and only it, cites its delivered outcome")
        if self.epistemic_summary != payload_class_counts(self.payload):
            raise ValueError("the epistemic summary differs from the payload")
        if not _DIGEST.fullmatch(self.digest) or self.digest != envelope_digest(self.model_dump(mode="json")):
            raise ValueError("the revision digest does not match its contents")
        return self

    @classmethod
    def sealed(cls, **fields: Any) -> ArtifactRevision:
        payload = fields["payload"]
        payload_json = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        artifact_type = ArtifactType(fields["artifact_type"])
        fields = {
            **fields,
            "revision_id": revision_identity(
                artifact_id=fields["artifact_id"], artifact_type=artifact_type.value,
                revision_number=fields["revision_number"], parent_revision_ref=fields.get("parent_revision_ref"),
                title=fields.get("title"), payload=payload_json,
            ),
            "epistemic_summary": payload_class_counts(payload),
        }
        draft = cls.model_construct(**{**fields, "digest": "0" * 64})
        return cls(**{**fields, "digest": envelope_digest(draft.model_dump(mode="json"))})

    def with_status(self, status: ArtifactRevisionStatus, *, delivered_outcome_ref: str | None = None) -> ArtifactRevision:
        fields = {name: getattr(self, name) for name in type(self).model_fields if name != "digest"}
        fields.update(status=status, delivered_outcome_ref=delivered_outcome_ref)
        return type(self).sealed(**fields)


def envelope_digest(payload: Mapping[str, Any]) -> str:
    return sha256_of({key: value for key, value in payload.items() if key != "digest"})


# ---------------------------------------------------------------------------
# Typed patches (Section 7.4)
# ---------------------------------------------------------------------------

class _PatchOp(FrozenModel):
    source_refs: tuple[str, ...] = Field(min_length=1)

    def _refs(self) -> None:
        _sorted_unique(self.source_refs, "patch operation source refs")


class SetServings(_PatchOp):
    op: Literal["set_servings"] = "set_servings"
    value: SourcedInteger

    @model_validator(mode="after")
    def valid(self) -> SetServings:
        self._refs()
        return self


class SetTimeLimit(_PatchOp):
    op: Literal["set_time_limit"] = "set_time_limit"
    value: SourcedInteger

    @model_validator(mode="after")
    def valid(self) -> SetTimeLimit:
        self._refs()
        return self


class AddConstraint(_PatchOp):
    op: Literal["add_constraint"] = "add_constraint"
    constraint: ArtifactConstraint

    @model_validator(mode="after")
    def valid(self) -> AddConstraint:
        self._refs()
        return self


class UpdateConstraint(_PatchOp):
    """Replace an existing constraint of the same id (e.g. record its reason)."""

    op: Literal["update_constraint"] = "update_constraint"
    constraint: ArtifactConstraint

    @model_validator(mode="after")
    def valid(self) -> UpdateConstraint:
        self._refs()
        return self


class RemoveConstraint(_PatchOp):
    """Withdraw a constraint.  Valid only when its refs are an explicit user withdrawal."""

    op: Literal["remove_constraint"] = "remove_constraint"
    constraint_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RemoveConstraint:
        self._refs()
        return self


class SetMainDish(_PatchOp):
    op: Literal["set_main_dish"] = "set_main_dish"
    dish: MealDish

    @model_validator(mode="after")
    def valid(self) -> SetMainDish:
        self._refs()
        return self


class UpdateDish(_PatchOp):
    """Replace the main or a side dish of the same id."""

    op: Literal["update_dish"] = "update_dish"
    dish: MealDish

    @model_validator(mode="after")
    def valid(self) -> UpdateDish:
        self._refs()
        return self


class AddSideDish(_PatchOp):
    op: Literal["add_side_dish"] = "add_side_dish"
    dish: MealDish

    @model_validator(mode="after")
    def valid(self) -> AddSideDish:
        self._refs()
        return self


class RemoveDish(_PatchOp):
    op: Literal["remove_dish"] = "remove_dish"
    dish_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RemoveDish:
        self._refs()
        return self


class AddNote(_PatchOp):
    op: Literal["add_note"] = "add_note"
    note: ArtifactNote

    @model_validator(mode="after")
    def valid(self) -> AddNote:
        self._refs()
        return self


class RemoveNote(_PatchOp):
    op: Literal["remove_note"] = "remove_note"
    note_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RemoveNote:
        self._refs()
        return self


class AddSection(_PatchOp):
    op: Literal["add_section"] = "add_section"
    section: ArtifactSection

    @model_validator(mode="after")
    def valid(self) -> AddSection:
        self._refs()
        return self


class AddItem(_PatchOp):
    op: Literal["add_item"] = "add_item"
    section_id: str = Field(min_length=1)
    item: ArtifactItem

    @model_validator(mode="after")
    def valid(self) -> AddItem:
        self._refs()
        return self


class UpdateItem(_PatchOp):
    op: Literal["update_item"] = "update_item"
    section_id: str = Field(min_length=1)
    item: ArtifactItem

    @model_validator(mode="after")
    def valid(self) -> UpdateItem:
        self._refs()
        return self


class RemoveItem(_PatchOp):
    op: Literal["remove_item"] = "remove_item"
    section_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RemoveItem:
        self._refs()
        return self


PatchOperation = Annotated[
    Union[SetServings, SetTimeLimit, AddConstraint, UpdateConstraint, RemoveConstraint, SetMainDish, UpdateDish,
          AddSideDish, RemoveDish, AddNote, RemoveNote, AddSection, AddItem, UpdateItem, RemoveItem],
    Field(discriminator="op"),
]
# The operations each payload type admits (the closed field allow-list).
ALLOWED_OPERATIONS: Mapping[str, frozenset[str]] = {
    "meal_plan": frozenset({
        "set_servings", "set_time_limit", "add_constraint", "update_constraint", "remove_constraint",
        "set_main_dish", "update_dish", "add_side_dish", "remove_dish", "add_note", "remove_note",
    }),
    "structured": frozenset({
        "add_constraint", "update_constraint", "remove_constraint", "add_note", "remove_note", "add_section",
        "add_item", "update_item", "remove_item",
    }),
}
# Operations that remove a user constraint: never silent.
CONSTRAINT_REMOVING_OPERATIONS = frozenset({"remove_constraint"})


class ArtifactPatch(FrozenModel):
    """A typed change proposed against an exact base revision (``None`` creates the artifact)."""

    patch_id: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)
    artifact_type: ArtifactType
    base_revision_ref: str | None = None
    operation: Literal[ArtifactOperation.CREATE, ArtifactOperation.REVISE]
    title: str | None = None
    operations: tuple[PatchOperation, ...] = Field(min_length=1, max_length=MAX_ITEMS * 2)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ArtifactPatch:
        _identifier(self.artifact_ref, "artifact ref")
        _sorted_unique(self.source_refs, "patch source refs")
        if (self.operation == ArtifactOperation.CREATE) != (self.base_revision_ref is None):
            raise ValueError("a create patch, and only it, has no base revision")
        if self.title is not None:
            bounded_text(self.title, "artifact title", MAX_LABEL_CHARACTERS)
        allowed = ALLOWED_OPERATIONS[PAYLOAD_TYPE_FOR_ARTIFACT[self.artifact_type]]
        if any(item.op not in allowed for item in self.operations):
            raise ValueError("a patch operation is not allowed for this payload type")
        return self


# ---------------------------------------------------------------------------
# Inquiries and pending requirements (Sections 6.5, 10.3, 11)
# ---------------------------------------------------------------------------

class InquiryPurpose(StrEnum):
    RESOLVE_REQUIRED_MEANING = "resolve_required_meaning"
    RESOLVE_SAFETY_RELEVANT_CONTEXT = "resolve_safety_relevant_context"
    DISCOVER_PREFERENCE = "discover_preference"
    DISCOVER_SUCCESS_CRITERION = "discover_success_criterion"
    CHOOSE_BETWEEN_TRADEOFFS = "choose_between_tradeoffs"
    IMPROVE_PERSONALIZATION = "improve_personalization"
    UNDERSTAND_RATIONALE = "understand_rationale"
    CONFIRM_CONSEQUENTIAL_EFFECT = "confirm_consequential_effect"


class InquiryCriticality(StrEnum):
    BLOCKING = "blocking"
    QUALITY_IMPROVING = "quality_improving"
    OPTIONAL_CURIOSITY = "optional_curiosity"


class InquiryTiming(StrEnum):
    BEFORE_PRIMARY = "before_primary"
    AFTER_PRIMARY = "after_primary"
    CHECKPOINT = "checkpoint"


class InquiryTargetKind(StrEnum):
    ARTIFACT_CONSTRAINT = "artifact_constraint"
    ARTIFACT_FIELD = "artifact_field"
    ARTIFACT = "artifact"
    REQUEST_ARGUMENT = "request_argument"


class InquiryExpectedUse(StrEnum):
    """What the answer will be used for; it fixes what binding the answer may do."""

    SET_CONSTRAINT_REASON = "set_constraint_reason"
    ADD_CONSTRAINT = "add_constraint"
    SELECT_OPTION = "select_option"
    REFINE_ARTIFACT = "refine_artifact"
    COMPLETE_REQUEST = "complete_request"


class InquirySensitivity(StrEnum):
    ORDINARY = "ordinary"
    PERSONAL = "personal"
    HEALTH = "health"


class OpenInquiryStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DECLINED = "declined"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class OpenInquiryRecord(FrozenModel):
    """A question actually delivered whose answer is awaited (or a carried pending requirement).

    Never a goal: ``focus_eligibility`` is ``subordinate``, so it can never
    capture focus from a new explicit request.  It exists only after a
    validated delivered-response outcome named it.
    """

    inquiry_id: str = Field(min_length=1)
    record_kind: Literal["optional_inquiry", "pending_requirement"]
    conversation_ref: str = Field(min_length=1)
    parent_goal_ref: str = Field(min_length=1)
    originating_frame_ref: str = Field(min_length=1)
    artifact_ref: str | None = None
    target_kind: InquiryTargetKind
    target_ref: str = Field(min_length=1)
    purpose: InquiryPurpose
    expected_use: InquiryExpectedUse
    criticality: InquiryCriticality
    blocking: bool
    focus_eligibility: Literal["subordinate"] = "subordinate"
    asked_about: str = Field(min_length=1)
    answer_options: tuple[str, ...] = ()
    sensitivity: InquirySensitivity = InquirySensitivity.ORDINARY
    elicitation_move_ref: str = Field(min_length=1)
    elicitation_delivery_ref: str = Field(min_length=1)
    created_turn_ref: str = Field(min_length=1)
    expires_after_user_turns: int = Field(ge=1, le=6)
    user_turns_elapsed: int = Field(default=0, ge=0)
    status: OpenInquiryStatus = OpenInquiryStatus.OPEN
    resolution_ref: str | None = None
    # The deliverable an answer resumes (a pending requirement records the
    # operation its parent request was waiting on).
    parent_operation: ArtifactOperation | None = None
    parent_artifact_type: ArtifactType | None = None

    @model_validator(mode="after")
    def valid(self) -> OpenInquiryRecord:
        if (self.parent_operation is None) != (self.parent_artifact_type is None):
            raise ValueError("a parent operation and artifact type are recorded together")
        bounded_text(self.asked_about, "inquiry anchor", MAX_LABEL_CHARACTERS)
        if len(self.answer_options) != len(set(self.answer_options)):
            raise ValueError("inquiry answer options must be unique")
        for option in self.answer_options:
            _identifier(option, "inquiry answer option")
        if self.blocking != (self.criticality == InquiryCriticality.BLOCKING):
            raise ValueError("only a blocking requirement blocks")
        if (self.record_kind == "pending_requirement") != self.blocking:
            raise ValueError("a pending requirement, and only it, is blocking")
        if (self.status == OpenInquiryStatus.OPEN) == (self.resolution_ref is not None):
            raise ValueError("a closed inquiry, and only it, cites its resolution")
        if self.user_turns_elapsed > self.expires_after_user_turns:
            raise ValueError("an inquiry cannot outlive its carry-forward policy")
        return self


# ---------------------------------------------------------------------------
# Delivered-response outcomes (Section 14.1)
# ---------------------------------------------------------------------------

class DeliveryChannel(StrEnum):
    WEB = "web"
    VOICE = "voice"
    WHATSAPP = "whatsapp"
    # Offline evaluation only: an injected delivery of a reviewed shadow
    # candidate (``simulated_authority``).  Never accepted in another mode.
    SIMULATED = "simulated"


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"


class DeliveredResponseOutcome(FrozenModel):
    """Immutable evidence that a specific response was (or failed to be) delivered.

    The only record that can satisfy a deliverable goal, activate an open
    inquiry, or advance a workspace head (in a mode that permits it).
    """

    outcome_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    turn_ref: str = Field(min_length=1)
    selected_action_ref: str = Field(min_length=1)
    realization_ref: str = Field(min_length=1)
    delivery_channel: DeliveryChannel
    delivered_content_refs: tuple[str, ...] = ()
    artifact_revision_refs: tuple[str, ...] = ()
    inquiry_refs: tuple[str, ...] = ()
    delivery_status: DeliveryStatus
    observed_at: str = Field(min_length=1)
    # Set exactly for simulated deliveries: the evaluation harness that injected it.
    simulation_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> DeliveredResponseOutcome:
        for label, values in (("delivered content refs", self.delivered_content_refs),
                              ("artifact revision refs", self.artifact_revision_refs),
                              ("inquiry refs", self.inquiry_refs)):
            _sorted_unique(values, label)
        if (self.delivery_channel == DeliveryChannel.SIMULATED) != (self.simulation_ref is not None):
            raise ValueError("a simulated delivery, and only it, names its simulation harness")
        if self.delivery_status == DeliveryStatus.FAILED and (self.artifact_revision_refs or self.inquiry_refs):
            raise ValueError("a failed delivery delivered no revision and asked no question")
        from datetime import datetime

        if datetime.fromisoformat(self.observed_at).tzinfo is None:
            raise ValueError("an outcome observation time carries a timezone")
        return self



class ReconciliationState(StrEnum):
    DELIVERED_AND_SATISFIED = "delivered_and_satisfied"
    DELIVERED_WAITING_FOR_ANSWER = "delivered_waiting_for_answer"
    DELIVERY_FAILED = "delivery_failed"
    CONTENT_REJECTED = "content_rejected"
    ARTIFACT_CONFLICT = "artifact_conflict"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"
    # Observational shadow: the proposal was never delivered and never will be.
    SHADOW_ONLY_NOT_DELIVERED = "shadow_only_not_delivered"


# ---------------------------------------------------------------------------
# Conversation-scoped snapshot (Sections 7.5, 7.6)
# ---------------------------------------------------------------------------

class WorkspaceAuthorityMode(StrEnum):
    OBSERVATIONAL_SHADOW = "observational_shadow"
    SIMULATED_AUTHORITY = "simulated_authority"
    LIVE_AUTHORITY = "live_authority"


class ArtifactHead(FrozenModel):
    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    head_revision_ref: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    status: Literal["active", "abandoned"] = "active"


class WorkspaceSnapshot(FrozenModel):
    """The canonical conversational workspace of one conversation at one turn boundary.

    ``revisions`` holds every revision any head has reached (immutable
    envelopes); ``heads`` is the canonical pointer per artifact.  In
    ``observational_shadow`` nothing ever advances a head: only a real
    delivered outcome may, and live authority is not approved.
    """

    contract_version: Literal["conversation-workspace-contract-v0.1"] = WORKSPACE_CONTRACT_VERSION
    snapshot_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    authority_mode: WorkspaceAuthorityMode
    sequence: int = Field(ge=0)
    previous_snapshot_ref: str | None = None
    turn_ref: str | None = None
    heads: tuple[ArtifactHead, ...] = ()
    revisions: tuple[ArtifactRevision, ...] = ()
    inquiries: tuple[OpenInquiryRecord, ...] = ()
    digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> WorkspaceSnapshot:
        if (self.sequence == 0) != (self.previous_snapshot_ref is None):
            raise ValueError("only the empty initial snapshot has no predecessor")
        artifact_ids = tuple(item.artifact_id for item in self.heads)
        if artifact_ids != tuple(sorted(set(artifact_ids))):
            raise ValueError("heads are unique per artifact and in canonical order")
        keys = tuple((item.artifact_id, item.revision_number) for item in self.revisions)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("revisions are unique per (artifact, number) and in canonical order")
        by_id = {item.revision_id: item for item in self.revisions}
        if len(by_id) != len(self.revisions):
            raise ValueError("revision ids must be unique")
        for revision in self.revisions:
            if revision.conversation_ref != self.conversation_ref:
                raise ValueError("a revision belongs to a different conversation")
            if revision.status == ArtifactRevisionStatus.PROPOSED:
                raise ValueError("a proposed revision is never canonical workspace state")
            if revision.parent_revision_ref is not None and revision.parent_revision_ref not in by_id:
                raise ValueError("a revision's parent is missing from the lineage")
        for head in self.heads:
            revision = by_id.get(head.head_revision_ref)
            if revision is None or revision.artifact_id != head.artifact_id:
                raise ValueError("a head points to a revision of another artifact")
            if (revision.revision_number, revision.artifact_type) != (head.revision_number, head.artifact_type):
                raise ValueError("a head misstates its revision")
            if revision.status != ArtifactRevisionStatus.DELIVERED and head.status == "active":
                raise ValueError("an active head must point to a delivered revision")
            latest = max(item.revision_number for item in self.revisions if item.artifact_id == head.artifact_id)
            if latest != head.revision_number:
                raise ValueError("a head points to its artifact's latest revision")
        if set(item.artifact_id for item in self.revisions) != set(artifact_ids):
            raise ValueError("every revision belongs to a headed artifact")
        inquiry_ids = tuple(item.inquiry_id for item in self.inquiries)
        if inquiry_ids != tuple(sorted(set(inquiry_ids))):
            raise ValueError("inquiries are unique and in canonical order")
        for inquiry in self.inquiries:
            if inquiry.conversation_ref != self.conversation_ref:
                raise ValueError("an inquiry belongs to a different conversation")
            if inquiry.artifact_ref is not None and inquiry.artifact_ref not in artifact_ids:
                raise ValueError("an inquiry names an unknown artifact")
        if self.authority_mode == WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW and (self.revisions or self.inquiries):
            # Shadow proposals never advance the canonical head, and in
            # observational mode nothing is ever delivered through the shadow.
            raise ValueError("an observational workspace holds no shadow-advanced state")
        if not _DIGEST.fullmatch(self.digest) or self.digest != envelope_digest(self.model_dump(mode="json")):
            raise ValueError("the workspace snapshot digest does not match its contents")
        return self

    @classmethod
    def sealed(cls, **fields: Any) -> WorkspaceSnapshot:
        draft = cls.model_construct(**{**fields, "digest": "0" * 64})
        return cls(**{**fields, "digest": envelope_digest(draft.model_dump(mode="json"))})

    def revision(self, revision_ref: str) -> ArtifactRevision | None:
        return next((item for item in self.revisions if item.revision_id == revision_ref), None)

    def head_revision(self, artifact_id: str) -> ArtifactRevision | None:
        head = next((item for item in self.heads if item.artifact_id == artifact_id), None)
        return self.revision(head.head_revision_ref) if head is not None else None

    @property
    def active_heads(self) -> tuple[ArtifactHead, ...]:
        return tuple(item for item in self.heads if item.status == "active")

    @property
    def open_inquiries(self) -> tuple[OpenInquiryRecord, ...]:
        return tuple(item for item in self.inquiries if item.status == OpenInquiryStatus.OPEN)


def workspace_snapshot_id(conversation_ref: str, sequence: int, previous_ref: str | None, turn_ref: str | None) -> str:
    return "workspace-snapshot:" + sha256_of({
        "conversation": conversation_ref, "sequence": sequence, "previous": previous_ref, "turn": turn_ref,
        "version": WORKSPACE_DIGEST_VERSION,
    })[:32]


def empty_workspace(conversation_ref: str, mode: WorkspaceAuthorityMode) -> WorkspaceSnapshot:
    return WorkspaceSnapshot.sealed(
        snapshot_id=workspace_snapshot_id(conversation_ref, 0, None, None), conversation_ref=conversation_ref,
        authority_mode=mode, sequence=0,
    )


__all__ = [name for name in globals() if not name.startswith("_") and name not in {
    "annotations", "hashlib", "json", "re", "Any", "Annotated", "Literal", "Union", "Mapping", "StrEnum",
    "Field", "model_validator", "FrozenModel",
}]
