"""Canonical immutable Realization V0 contracts.

Realization V0 converts an already selected Action plan plus an explicit,
authorized content inventory into a *shadow* language candidate.  This module
owns every cross-stage realization type: the closed content inventory, the
compiled request, the resolved delivery profile, the untrusted candidate, the
validation result, attempt records, the terminal realization trace, typed
failures, and the canonical digests.  It contains contracts only: no model,
provider SDK, database, clock, or network access.

The governing rule:

    The cognitive system decides what may be communicated.
    The realization model decides only how that authorized meaning is worded.

There is deliberately no ``model_knowledge``, ``common_knowledge``, or
``llm_pretraining`` content source.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .actions import (
    ActionFamily, ActionTargetRef, ActionVersions, MovePriority, ObjectStatus, QuestionForm,
    RealizationConstraint, SafetyRestriction, canonical_decimal, decimal_from,
)
from .interpretation import FrozenModel

# ---------------------------------------------------------------------------
# Versions (each owned by the module named in the constant)
# ---------------------------------------------------------------------------

# v0.3 (Collaborative Content and Inquiry V0) adds the closed
# ``artifact_presentation`` and ``inquiry_prompt`` content kinds and the
# collaborative-proposal and workspace-artifact source kinds.
REALIZATION_CONTRACT_VERSION = "realization-contract-v0.3"
# v0.2 adds the delivered-dialogue source kinds (Dialogue Working Memory V0).
# Every v0.1 inventory is also a valid v0.2 inventory; stored v0.1 realization
# traces replay as ``version_unavailable`` rather than under v0.2 semantics.
REALIZATION_CONTENT_VERSION = "realization-content-v0.3"
# v0.2 reviews ``collaborate.deliver_artifact`` and ``seek_information.inquire``.
REALIZATION_ACTION_MANIFEST = "realization-action-manifest-v0.2"
REALIZATION_STYLE_MANIFEST = "realization-style-manifest-v0.1"
# v0.2 carries the exact current user utterance as explicitly
# non-authoritative linguistic context. It may shape wording and reference
# realization, but it cannot supply facts, actions, authorization, or safety
# decisions.
REALIZATION_COMPILER_VERSION = "realization-compiler-v0.3"
REALIZATION_MODEL_PROTOCOL_VERSION = "realization-model-protocol-v0.3"
# v0.2 licenses in-conversation recall phrasing from delivered-dialogue
# content; its behaviour on every v0.1 inventory is unchanged.
# v0.3 validates the collaboration content kinds, and recalibrates surface
# checks that rejected faithful wording (Section 13.3): a capitalized
# sentence-initial anchor word, completion language about the artifact a
# move delivers in this very message, and causal or suggesting language that
# a delivered explanation or suggestion licenses.  Hard checks are unchanged.
REALIZATION_VALIDATOR_VERSION = "realization-validator-v0.3"
REALIZATION_ENGINE_VERSION = "realization-engine-v0.1"
# Owns the exact projection the realization semantic digest covers.
REALIZATION_SEMANTIC_DIGEST_VERSION = "realization-semantic-digest-v0.1"

REALIZATION_MAX_ATTEMPTS = 2
SUPPORTED_REALIZATION_LANGUAGE = "en"

# A fixed namespace so every realization identity is a UUID5 of versioned
# lineage: stable across processes, retries, and replays.
REALIZATION_NAMESPACE = uuid.UUID("b7e0c5d2-3f41-5a8e-9c6d-1e2f3a4b5c6d")

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PREDICATE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_INTEGER = re.compile(r"^-?\d+$")
_BINDING_ROLE = re.compile(r"^(target|evidence|parameter):[a-z_]+$")
_CODE = re.compile(r"^[a-z0-9_.]+$")


class RealizationVersions(FrozenModel):
    contract: Literal["realization-contract-v0.3"] = REALIZATION_CONTRACT_VERSION
    content: Literal["realization-content-v0.3"] = REALIZATION_CONTENT_VERSION
    action_manifest: Literal["realization-action-manifest-v0.2"] = REALIZATION_ACTION_MANIFEST
    style_manifest: Literal["realization-style-manifest-v0.1"] = REALIZATION_STYLE_MANIFEST
    compiler: Literal["realization-compiler-v0.3"] = REALIZATION_COMPILER_VERSION
    model_protocol: Literal["realization-model-protocol-v0.3"] = REALIZATION_MODEL_PROTOCOL_VERSION
    validator: Literal["realization-validator-v0.3"] = REALIZATION_VALIDATOR_VERSION
    engine: Literal["realization-engine-v0.1"] = REALIZATION_ENGINE_VERSION
    semantic_digest: Literal["realization-semantic-digest-v0.1"] = REALIZATION_SEMANTIC_DIGEST_VERSION


# ---------------------------------------------------------------------------
# Canonical JSON, digests, and deterministic identities
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    """Sorted keys, stable separators, no NaN/Infinity, exact Unicode."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def realization_uuid(*parts: str) -> str:
    """A UUID5 over versioned lineage parts; never UUID4, hash order, or time."""

    return str(uuid.uuid5(REALIZATION_NAMESPACE, "\x1f".join(parts)))


def _nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _sorted_unique(values: tuple[str, ...], label: str) -> None:
    _unique(values, label)
    if tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted")


def _digest(value: str | None, label: str) -> None:
    if value is not None and not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def valid_text(value: str) -> bool:
    """UTF-8-encodable text with no control or format characters except newline."""

    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"} and char != "\n" for char in value
    )


# ---------------------------------------------------------------------------
# Closed content vocabularies
# ---------------------------------------------------------------------------

class ContentSourceKind(StrEnum):
    """The only sources a realizable meaning may come from.

    Before registry v1.6 there was no model-knowledge source. The two v1.6
    open-world sources are labelled as such and are never worded as verified:
    ``model_general_knowledge`` (hedged unless high confidence) and
    ``columbina_own_view`` (always tentative, attributed to Columbina).
    """

    CURRENT_USER_STATEMENT = "current_user_statement"
    AUTHORIZED_SEMANTIC_MEMORY = "authorized_semantic_memory"
    AUTHORIZED_EPISODIC_MEMORY = "authorized_episodic_memory"
    AUTHORIZED_RELATIONSHIP_MEMORY = "authorized_relationship_memory"
    TRUSTED_RUNTIME_OBSERVATION = "trusted_runtime_observation"
    FINALIZED_TOOL_RESULT = "finalized_tool_result"
    REVIEWED_SYSTEM_KNOWLEDGE = "reviewed_system_knowledge"
    ACCEPTED_PRIOR_REALIZATION = "accepted_prior_realization"
    RETRIEVAL_RESULT = "retrieval_result"
    RETRIEVAL_NO_RESULT = "retrieval_no_result"
    # realization-content-v0.2: exactly what earlier in this conversation was
    # delivered to the user.  A prior user message is not the *current* user
    # statement, and an existing-agent reply was never produced and accepted
    # through the cognitive Realization protocol.  A shadow candidate is never
    # either of these, even when ``accepted_shadow``.
    DELIVERED_USER_UTTERANCE = "delivered_user_utterance"
    DELIVERED_ASSISTANT_UTTERANCE = "delivered_assistant_utterance"
    # realization-content-v0.3: content validated by the collaborative-content
    # compiler (generated proposals, never verified facts) and the content of
    # a delivered workspace revision.
    COLLABORATIVE_PROPOSAL = "collaborative_proposal"
    WORKSPACE_ARTIFACT = "workspace_artifact"
    # registry v1.6 open-world contribution (two_call/contribution.py).
    MODEL_GENERAL_KNOWLEDGE = "model_general_knowledge"
    COLUMBINA_OWN_VIEW = "columbina_own_view"


DIALOGUE_SOURCE_KINDS = frozenset({
    ContentSourceKind.DELIVERED_USER_UTTERANCE, ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE,
})

MEMORY_SOURCE_KINDS = frozenset({
    ContentSourceKind.AUTHORIZED_SEMANTIC_MEMORY, ContentSourceKind.AUTHORIZED_EPISODIC_MEMORY,
    ContentSourceKind.AUTHORIZED_RELATIONSHIP_MEMORY,
})


class ContentKind(StrEnum):
    GROUNDED_PROPOSITION = "grounded_proposition"
    GROUNDED_QUESTION = "grounded_question"
    KNOWLEDGE_GAP = "knowledge_gap"
    DIALOGUE_REFERENCE = "dialogue_reference"
    AUTHORIZED_QUOTE = "authorized_quote"
    ARTIFACT_PRESENTATION = "artifact_presentation"
    INQUIRY_PROMPT = "inquiry_prompt"


COLLABORATION_SOURCE_KINDS = frozenset({ContentSourceKind.COLLABORATIVE_PROPOSAL, ContentSourceKind.WORKSPACE_ARTIFACT})


class SensitivityClass(StrEnum):
    ORDINARY = "ordinary"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


# The V0 realization sensitivity policy: only these classes may be worded.
REALIZABLE_SENSITIVITY = frozenset({SensitivityClass.ORDINARY, SensitivityClass.PERSONAL})


class ContentEpistemicStatus(StrEnum):
    GROUNDED = "grounded"
    ATTRIBUTED = "attributed"
    TENTATIVE = "tentative"
    OBSERVED = "observed"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"
    CORRECTED = "corrected"
    KNOWN_FALSE = "known_false"


# Content that must never be asserted; it may only be the corrected side of a
# correction move.
NON_ASSERTABLE_STATUSES = frozenset({ContentEpistemicStatus.CORRECTED, ContentEpistemicStatus.KNOWN_FALSE})


class ContentAttribution(StrEnum):
    TRUSTED_SOURCE = "trusted_source"
    USER = "user"
    COLUMBINA = "columbina"
    RUNTIME = "runtime"
    THIRD_PARTY = "third_party"


ATTRIBUTED_SPEAKERS = frozenset({ContentAttribution.USER, ContentAttribution.THIRD_PARTY})


class Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class Modality(StrEnum):
    ASSERTED = "asserted"
    HYPOTHETICAL = "hypothetical"
    CONDITIONAL = "conditional"


class Certainty(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    TENTATIVE = "tentative"


class ArgumentValueType(StrEnum):
    DECIMAL = "decimal"
    INTEGER = "integer"
    STRING = "string"
    ENTITY = "entity"
    DATE = "date"
    BOOLEAN = "boolean"


class GapKind(StrEnum):
    """What an actual failed retrieval or observation supports.

    ``no_record`` supports "I do not have a memory of X", never "X never happened".
    ``not_mentioned_in_conversation`` (registry v1.5 dialogue recall) supports
    "you haven't mentioned X", never "X isn't so" or "you never said it".
    """

    NO_GROUNDED_ANSWER = "no_grounded_answer"
    NO_RECORD = "no_record"
    NOT_OBSERVED = "not_observed"
    NOT_MENTIONED = "not_mentioned_in_conversation"


class DialogueReferenceKind(StrEnum):
    USER_CONTRIBUTION = "user_contribution"
    DIALOGUE_SCOPE = "dialogue_scope"
    UNRESOLVED_ELEMENT = "unresolved_element"
    TOPIC = "topic"
    PRIOR_MOVE = "prior_move"
    PRIOR_REALIZATION = "prior_realization"
    REQUEST = "request"
    SUGGESTION = "suggestion"
    EXPERIENCE = "experience"
    CONCERN = "concern"
    GOAL = "goal"
    PLAN = "plan"
    PLAN_STEP = "plan_step"
    CAPABILITY = "capability"
    OUTCOME = "outcome"
    OPTION = "option"
    EVIDENCE = "evidence"


class ReferenceForm(StrEnum):
    """``generic`` references carry no wording ("your message"); ``anchored`` ones do."""

    GENERIC = "generic"
    ANCHORED = "anchored"


# Registered measurement units: key -> accepted surface forms (lowercase).
REALIZATION_UNITS: Mapping[str, tuple[str, ...]] = {
    "gram": ("g", "gram", "grams"),
    "kilogram": ("kg", "kilogram", "kilograms", "kilo", "kilos"),
    "milligram": ("mg", "milligram", "milligrams"),
    "kilocalorie": ("kcal", "kilocalorie", "kilocalories", "calorie", "calories", "cal"),
    "liter": ("l", "liter", "liters", "litre", "litres"),
    "milliliter": ("ml", "milliliter", "milliliters", "millilitre", "millilitres"),
    "meter": ("m", "meter", "meters", "metre", "metres"),
    "kilometer": ("km", "kilometer", "kilometers", "kilometre", "kilometres"),
    "mile": ("mi", "mile", "miles"),
    "percent": ("%", "percent", "per cent", "percentage"),
    "minute": ("min", "mins", "minute", "minutes"),
    "hour": ("h", "hr", "hrs", "hour", "hours"),
    "day": ("day", "days"),
    "week": ("week", "weeks"),
    "month": ("month", "months"),
    "year": ("yr", "yrs", "year", "years"),
    "dollar": ("$", "usd", "dollar", "dollars"),
    "euro": ("€", "eur", "euro", "euros"),
    "item": ("item", "items"),
    "serving": ("serving", "servings"),
}


# ---------------------------------------------------------------------------
# Content items (a closed discriminated union)
# ---------------------------------------------------------------------------

class _ContentBase(FrozenModel):
    content_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    source_kind: ContentSourceKind
    source_refs: tuple[str, ...] = Field(min_length=1)
    # Upstream Action target/evidence/parameter refs this item represents; the
    # content index resolves move references through these.
    represents_refs: tuple[str, ...] = ()
    # Exactly ``True`` is required before an item may enter a request.  The
    # contract admits ``False`` so the compiler can reject it with a typed code.
    authorized_for_realization: bool
    status: ObjectStatus = ObjectStatus.CURRENT
    revision_ref: str | None = None
    sensitivity_class: SensitivityClass = SensitivityClass.ORDINARY
    epistemic_status: ContentEpistemicStatus
    attribution: ContentAttribution
    surface_anchors: tuple[str, ...] = ()

    def _base_valid(self) -> None:
        _nonblank(self.content_id, "content id")
        _nonblank(self.cognitive_turn_id, "content cognitive turn")
        for label, values in (("source refs", self.source_refs), ("represented refs", self.represents_refs),
                              ("surface anchors", self.surface_anchors)):
            _unique(values, label)
            for value in values:
                _nonblank(value, label)
        for anchor in self.surface_anchors:
            if not valid_text(anchor) or "\n" in anchor:
                raise ValueError("surface anchors must be single-line valid text")
        if self.content_id in self.represents_refs:
            raise ValueError("a content item cannot represent itself")
        if self.revision_ref is not None:
            _nonblank(self.revision_ref, "content revision")
        if self.source_kind in {ContentSourceKind.CURRENT_USER_STATEMENT, ContentSourceKind.DELIVERED_USER_UTTERANCE}:
            # A user statement never becomes a verified fact because it can be
            # phrased fluently -- nor because it was repeated or survived turns.
            if self.attribution != ContentAttribution.USER:
                raise ValueError("user statements keep user attribution")
            if self.epistemic_status in {ContentEpistemicStatus.GROUNDED, ContentEpistemicStatus.OBSERVED} and (
                self.kind != ContentKind.DIALOGUE_REFERENCE
            ):
                raise ValueError("a user statement cannot be recorded as grounded fact")
        if self.source_kind == ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE:
            # Delivered existing-agent speech may be quoted or referenced; it is
            # never a grounded proposition or trusted knowledge.
            if self.attribution != ContentAttribution.COLUMBINA:
                raise ValueError("delivered assistant utterances keep Columbina attribution")
            # A recalled answer from her own earlier reply ("I said autumn") may be
            # a proposition, but only an attributed one: what she said, not a fact.
            if self.kind not in {ContentKind.AUTHORIZED_QUOTE, ContentKind.DIALOGUE_REFERENCE} and not (
                self.kind == ContentKind.GROUNDED_PROPOSITION
                and self.epistemic_status == ContentEpistemicStatus.ATTRIBUTED
            ):
                raise ValueError("delivered assistant speech is only quoted, referenced, or recalled as said")
            if self.epistemic_status in {ContentEpistemicStatus.GROUNDED, ContentEpistemicStatus.OBSERVED}:
                raise ValueError("delivered assistant speech is attributed, never grounded")

    @property
    def anchors(self) -> tuple[str, ...]:
        return self.surface_anchors


class PropositionTerm(FrozenModel):
    ref: str = Field(min_length=1)
    surface_anchor: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> PropositionTerm:
        _nonblank(self.ref, "term ref"); _nonblank(self.surface_anchor, "term surface anchor")
        return self


class PropositionArgument(FrozenModel):
    role: str = Field(min_length=1)
    value_type: ArgumentValueType
    value: str
    unit: str | None = None
    surface_anchor: str | None = None

    @model_validator(mode="after")
    def valid(self) -> PropositionArgument:
        _nonblank(self.role, "argument role")
        if self.value_type == ArgumentValueType.DECIMAL:
            decimal_from(self.value)
            if canonical_decimal(self.value) != self.value:
                raise ValueError("decimal arguments use canonical six-place decimal strings")
        elif self.value_type == ArgumentValueType.INTEGER:
            if not _INTEGER.fullmatch(self.value) or self.value in {"-0"} or (
                len(self.value.lstrip("-")) > 1 and self.value.lstrip("-").startswith("0")
            ):
                raise ValueError("integer arguments use canonical integer strings")
        elif self.value_type == ArgumentValueType.DATE:
            try:
                if date.fromisoformat(self.value).isoformat() != self.value:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("date arguments use canonical ISO-8601 dates") from exc
        elif self.value_type == ArgumentValueType.BOOLEAN:
            if self.value not in {"true", "false"}:
                raise ValueError("boolean arguments are 'true' or 'false'")
        else:
            _nonblank(self.value, "argument value")
            if self.surface_anchor is None:
                raise ValueError("string and entity arguments require a surface anchor")
        if self.unit is not None:
            if self.value_type not in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER}:
                raise ValueError("only numeric arguments carry a unit")
            if self.unit not in REALIZATION_UNITS:
                raise ValueError("argument units come from the registered unit vocabulary")
        if self.surface_anchor is not None:
            _nonblank(self.surface_anchor, "argument surface anchor")
        return self


class GroundedProposition(_ContentBase):
    kind: Literal["grounded_proposition"] = "grounded_proposition"
    subject: PropositionTerm
    predicate: str = Field(min_length=1)
    arguments: tuple[PropositionArgument, ...] = ()
    polarity: Polarity = Polarity.POSITIVE
    modality: Modality = Modality.ASSERTED
    certainty: Certainty = Certainty.EXACT

    @model_validator(mode="after")
    def valid(self) -> GroundedProposition:
        self._base_valid()
        if not _PREDICATE.fullmatch(self.predicate):
            raise ValueError("predicates are registered dotted keys")
        _unique(tuple(item.role for item in self.arguments), "argument roles")
        if self.source_kind == ContentSourceKind.RETRIEVAL_NO_RESULT:
            raise ValueError("a failed retrieval supports a knowledge gap, never a proposition")
        allowed = {
            ContentEpistemicStatus.GROUNDED, ContentEpistemicStatus.ATTRIBUTED, ContentEpistemicStatus.TENTATIVE,
            ContentEpistemicStatus.CORRECTED, ContentEpistemicStatus.KNOWN_FALSE,
        }
        if self.epistemic_status not in allowed:
            raise ValueError("unsupported proposition epistemic status")
        if self.attribution in ATTRIBUTED_SPEAKERS and self.epistemic_status == ContentEpistemicStatus.GROUNDED:
            raise ValueError("attributed propositions keep attributed or tentative status")
        if self.epistemic_status == ContentEpistemicStatus.ATTRIBUTED and self.attribution not in ATTRIBUTED_SPEAKERS and not (
            # Her own earlier reply, recalled as what she said.
            self.attribution == ContentAttribution.COLUMBINA
            and self.source_kind == ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE
        ):
            raise ValueError("attributed status requires a user or third-party attribution")
        if self.epistemic_status == ContentEpistemicStatus.TENTATIVE and self.certainty == Certainty.EXACT:
            raise ValueError("tentative propositions cannot carry exact certainty")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        extra = tuple(item.surface_anchor for item in self.arguments if item.surface_anchor)
        return tuple(dict.fromkeys((self.subject.surface_anchor, *extra, *self.surface_anchors)))

    @property
    def requires_uncertainty(self) -> bool:
        return self.certainty != Certainty.EXACT or self.epistemic_status == ContentEpistemicStatus.TENTATIVE


class GroundedQuestion(_ContentBase):
    kind: Literal["grounded_question"] = "grounded_question"
    question_form: QuestionForm
    topic_surface_anchor: str | None = None
    option_content_refs: tuple[str, ...] = ()
    scale_min: int | None = None
    scale_max: int | None = None
    requested_evidence_type: Literal["document", "record", "observation", "source", "other"] | None = None

    @model_validator(mode="after")
    def valid(self) -> GroundedQuestion:
        self._base_valid()
        if self.epistemic_status != ContentEpistemicStatus.UNRESOLVED:
            raise ValueError("a grounded question represents unresolved content")
        if self.source_kind == ContentSourceKind.RETRIEVAL_NO_RESULT:
            raise ValueError("a failed retrieval supports a knowledge gap, not a question")
        _unique(self.option_content_refs, "question options")
        if (self.scale_min is None) != (self.scale_max is None):
            raise ValueError("scale bounds are supplied together")
        if self.scale_min is not None and self.scale_min >= self.scale_max:
            raise ValueError("scale bounds must be ordered")
        if (self.question_form == QuestionForm.SCALE) != (self.scale_min is not None):
            raise ValueError("only scale questions, and all of them, carry scale bounds")
        if self.question_form == QuestionForm.CHOICE and len(self.option_content_refs) < 2:
            raise ValueError("choice questions carry at least two option contents")
        if self.topic_surface_anchor is not None:
            _nonblank(self.topic_surface_anchor, "question topic anchor")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        head = (self.topic_surface_anchor,) if self.topic_surface_anchor else ()
        return tuple(dict.fromkeys((*head, *self.surface_anchors)))


class KnowledgeGap(_ContentBase):
    kind: Literal["knowledge_gap"] = "knowledge_gap"
    topic_ref: str = Field(min_length=1)
    surface_anchor: str = Field(min_length=1)
    gap_kind: GapKind

    @model_validator(mode="after")
    def valid(self) -> KnowledgeGap:
        self._base_valid()
        _nonblank(self.topic_ref, "gap topic"); _nonblank(self.surface_anchor, "gap surface anchor")
        expected = (ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION if self.gap_kind == GapKind.NOT_OBSERVED
                    else ContentSourceKind.RETRIEVAL_NO_RESULT)
        if self.source_kind != expected:
            raise ValueError("a knowledge gap is backed by an actual failed retrieval or observation")
        if self.epistemic_status != ContentEpistemicStatus.UNKNOWN:
            raise ValueError("a knowledge gap carries unknown epistemic status")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.surface_anchor, *self.surface_anchors)))


class DialogueReference(_ContentBase):
    kind: Literal["dialogue_reference"] = "dialogue_reference"
    reference_kind: DialogueReferenceKind
    target_ref: str = Field(min_length=1)
    reference_form: ReferenceForm

    @model_validator(mode="after")
    def valid(self) -> DialogueReference:
        self._base_valid()
        _nonblank(self.target_ref, "dialogue reference target")
        if (self.reference_form == ReferenceForm.ANCHORED) != bool(self.surface_anchors):
            raise ValueError("anchored references, and only they, carry surface anchors")
        if self.epistemic_status not in {
            ContentEpistemicStatus.OBSERVED, ContentEpistemicStatus.UNRESOLVED, ContentEpistemicStatus.ATTRIBUTED,
        }:
            raise ValueError("a dialogue reference is observed, unresolved, or attributed")
        return self


class AuthorizedQuote(_ContentBase):
    kind: Literal["authorized_quote"] = "authorized_quote"
    quote_text: str = Field(min_length=1)
    source_message_ref: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=1)
    permission: Literal["verbatim_quote_permitted"] = "verbatim_quote_permitted"

    @model_validator(mode="after")
    def valid(self) -> AuthorizedQuote:
        self._base_valid()
        if not valid_text(self.quote_text) or not self.quote_text.strip():
            raise ValueError("quotes must be non-empty valid text")
        if self.span_end - self.span_start != len(self.quote_text):
            raise ValueError("the quote span must cover exactly the quoted text")
        if self.epistemic_status != ContentEpistemicStatus.ATTRIBUTED:
            raise ValueError("a quote is attributed to its speaker")
        if self.attribution not in {ContentAttribution.USER, ContentAttribution.THIRD_PARTY, ContentAttribution.COLUMBINA}:
            raise ValueError("a quote names its speaker")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.quote_text, *self.surface_anchors)))


class ArtifactPresentation(_ContentBase):
    """One validated collaborative-content item, worded as part of the delivered work.

    Its wording authority is exactly its flags: ``requires_hedge`` (an
    inference or uncertain suggestion keeps a visible uncertainty marker),
    ``licenses_causal`` (an explanation or evaluation may say why),
    ``licenses_suggestion`` (an alternative or guidance may be phrased as a
    suggestion), and ``reports_change`` (a create or revise may say the shared
    artifact was made or changed -- in this very message, never elsewhere).
    It never licenses a verified-fact, tool, or external-outcome claim.
    """

    kind: Literal["artifact_presentation"] = "artifact_presentation"
    artifact_ref: str = Field(min_length=1)
    revision_ref: str = Field(min_length=1)
    operation: Literal["create", "revise", "explain", "evaluate", "describe", "compare", "summarize"]
    role: Literal["change", "explanation", "evaluation", "description", "comparison", "summary", "alternative",
                  "safety_guidance"]
    content_class: Literal[
        "user_supplied_constraint", "attributed_user_claim", "artifact_content", "generated_proposal",
        "reasoned_inference", "uncertain_suggestion",
    ]
    # The validated meaning to word (structured content, not final prose).
    meaning: str = Field(min_length=1, max_length=240)
    arguments: tuple[PropositionArgument, ...] = ()
    requires_hedge: bool
    licenses_causal: bool
    licenses_suggestion: bool
    reports_change: bool

    @model_validator(mode="after")
    def valid(self) -> ArtifactPresentation:
        self._base_valid()
        if not valid_text(self.meaning) or "\n" in self.meaning or not self.meaning.strip():
            raise ValueError("an artifact presentation's meaning is single-line valid text")
        if not self.surface_anchors:
            raise ValueError("an artifact presentation carries the surface anchors its wording must keep")
        if self.source_kind not in COLLABORATION_SOURCE_KINDS:
            raise ValueError("an artifact presentation comes only from a validated proposal or delivered revision")
        attributed = self.content_class == "attributed_user_claim"
        if attributed != (self.attribution == ContentAttribution.USER):
            raise ValueError("an attributed user claim, and only it, keeps user attribution")
        expected = (ContentEpistemicStatus.ATTRIBUTED if attributed else ContentEpistemicStatus.TENTATIVE
                    if self.requires_hedge else ContentEpistemicStatus.OBSERVED)
        if self.epistemic_status != expected:
            raise ValueError("an artifact presentation's epistemic status follows its content class")
        if self.requires_hedge != (self.content_class in {"reasoned_inference", "uncertain_suggestion"}):
            raise ValueError("exactly inferences and uncertain suggestions require a hedge")
        if self.reports_change != (self.role == "change" and self.operation in {"create", "revise"}):
            raise ValueError("only a create or revise change reports a change")
        if any(item.value_type not in {ArgumentValueType.INTEGER} for item in self.arguments):
            raise ValueError("artifact presentation arguments are the artifact's typed integers")
        _unique(tuple((item.value, item.unit) for item in self.arguments), "presentation arguments")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.surface_anchors, *(item.surface_anchor for item in self.arguments
                                                             if item.surface_anchor))))


class InquiryPrompt(_ContentBase):
    """A typed inquiry worded as one question (never an answer, never a new question)."""

    kind: Literal["inquiry_prompt"] = "inquiry_prompt"
    inquiry_ref: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    timing: Literal["before_primary", "after_primary", "checkpoint"]
    question_form: Literal["open_set", "yes_no", "choice"]
    option_anchors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> InquiryPrompt:
        self._base_valid()
        if not self.surface_anchors:
            raise ValueError("an inquiry prompt names what it asks about")
        if self.epistemic_status != ContentEpistemicStatus.UNRESOLVED or self.attribution != ContentAttribution.COLUMBINA:
            raise ValueError("an inquiry prompt is Columbina's unresolved question")
        if self.source_kind != ContentSourceKind.COLLABORATIVE_PROPOSAL:
            raise ValueError("an inquiry prompt comes only from a validated proposal")
        if (self.question_form == "choice") != (len(self.option_anchors) >= 2):
            raise ValueError("a choice prompt, and only it, carries at least two option anchors")
        _unique(self.option_anchors, "option anchors")
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.surface_anchors, *self.option_anchors)))


RealizationContentItem = Annotated[
    Union[GroundedProposition, GroundedQuestion, KnowledgeGap, DialogueReference, AuthorizedQuote,
          ArtifactPresentation, InquiryPrompt],
    Field(discriminator="kind"),
]

KIND_SHORTHAND: Mapping[ContentKind, str] = {
    ContentKind.GROUNDED_PROPOSITION: "P", ContentKind.GROUNDED_QUESTION: "Q", ContentKind.KNOWLEDGE_GAP: "G",
    ContentKind.DIALOGUE_REFERENCE: "D", ContentKind.AUTHORIZED_QUOTE: "A",
    ContentKind.ARTIFACT_PRESENTATION: "W", ContentKind.INQUIRY_PROMPT: "I",
}


def content_is_generic(item: Any) -> bool:
    return isinstance(item, DialogueReference) and item.reference_form == ReferenceForm.GENERIC


class RealizationContentInventory(FrozenModel):
    """The one immutable set of authorized meanings a turn may word.

    Trusted producers fix authorization, sensitivity, status, attribution, and
    provenance when projecting into this inventory; the realizer never receives
    the memory database, retrieved memory objects, or conversation history.
    """

    inventory_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    content_version: Literal["realization-content-v0.3"] = REALIZATION_CONTENT_VERSION
    producer_refs: tuple[str, ...] = Field(min_length=1)
    items: tuple[RealizationContentItem, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> RealizationContentInventory:
        _nonblank(self.inventory_id, "inventory id")
        _sorted_unique(self.producer_refs, "content producers")
        _unique(tuple(item.content_id for item in self.items), "content ids")
        return self


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class RealizationMoveSpec(FrozenModel):
    """The exact selected move copied from ``ActionSelectionResult``."""

    move_ref: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    action_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    family: ActionFamily
    priority: MovePriority
    target_refs: tuple[ActionTargetRef, ...] = Field(min_length=1)
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    realization_constraints: tuple[RealizationConstraint, ...] = ()


class RealizationActionPlan(FrozenModel):
    plan_ref: str = Field(min_length=1)
    moves: tuple[RealizationMoveSpec, ...] = Field(min_length=1)
    primary_move_ref: str = Field(min_length=1)
    supporting_move_refs: tuple[str, ...] = ()
    termination: Literal["stop"] = "stop"

    @model_validator(mode="after")
    def valid(self) -> RealizationActionPlan:
        refs = tuple(item.move_ref for item in self.moves)
        _unique(refs, "plan moves")
        if tuple(item.step_index for item in self.moves) != tuple(range(len(self.moves))):
            raise ValueError("realization moves keep the exact selection order")
        if self.primary_move_ref not in refs:
            raise ValueError("the primary move is one of the plan moves")
        if self.supporting_move_refs != tuple(item for item in refs if item != self.primary_move_ref):
            raise ValueError("every non-primary move is supporting, in plan order")
        for move in self.moves:
            expected = MovePriority.PRIMARY if move.move_ref == self.primary_move_ref else MovePriority.SUPPORTING
            if move.priority != expected:
                raise ValueError("move priority differs from the plan designation")
        return self


class ContentBinding(FrozenModel):
    """One authorized content item bound to a move through an upstream Action ref."""

    role: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)
    content_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ContentBinding:
        if not _BINDING_ROLE.fullmatch(self.role):
            raise ValueError("binding roles are target:, evidence:, or parameter: roles")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.role, self.action_ref, self.content_ref)


class RealizationMoveBinding(FrozenModel):
    move_ref: str = Field(min_length=1)
    action_key: str = Field(min_length=1)
    # All selected moves are required in V0; optionality belongs to Action Selection.
    required: Literal[True] = True
    content_refs: tuple[ContentBinding, ...] = ()
    question_refs: tuple[str, ...] = ()
    target_surface_refs: tuple[str, ...] = ()
    constraint_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> RealizationMoveBinding:
        keys = tuple(item.sort_key for item in self.content_refs)
        _unique(keys, "content bindings")
        if keys != tuple(sorted(keys)):
            raise ValueError("content bindings must be in canonical order")
        bound = {item.content_ref for item in self.content_refs}
        for label, values in (("question refs", self.question_refs), ("target surface refs", self.target_surface_refs)):
            _sorted_unique(values, label)
            if not set(values) <= bound:
                raise ValueError(f"{label} must name bound content")
        _sorted_unique(self.constraint_codes, "move constraint codes")
        return self

    @property
    def bound_content_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.content_ref for item in self.content_refs))


class ReferenceFormKind(StrEnum):
    SECOND_PERSON = "second_person"
    FIRST_PERSON_SELF = "first_person_self"
    DEICTIC = "deictic"
    ANCHORED_SURFACE = "anchored_surface"
    GENERIC_DESCRIPTION = "generic_description"


class DiscourseControls(FrozenModel):
    move_order: tuple[str, ...] = Field(min_length=1)
    maximum_sentences: int = Field(ge=1)
    maximum_characters: int = Field(ge=1)
    response_mode: Literal["single_turn_text"] = "single_turn_text"
    allowed_reference_forms: tuple[ReferenceFormKind, ...] = Field(min_length=1)
    already_expressed_content_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DiscourseControls:
        _unique(self.move_order, "move order")
        _unique(self.allowed_reference_forms, "reference forms")
        _sorted_unique(self.already_expressed_content_refs, "already expressed content")
        return self


class DeliveryBand(StrEnum):
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    UNKNOWN = "unknown"


class TraitBandSource(FrozenModel):
    """Retained numeric provenance and its deterministic band; never sent raw to a model."""

    trait: Literal["curiosity", "warmth", "playfulness", "directness"]
    value: str | None = None
    band: DeliveryBand

    @model_validator(mode="after")
    def valid(self) -> TraitBandSource:
        if (self.value is None) != (self.band == DeliveryBand.UNKNOWN):
            raise ValueError("unknown traits, and only they, have no value")
        if self.value is not None:
            decimal_from(self.value)
        return self


class StateControlSource(FrozenModel):
    control: Literal[
        "response_energy", "repair_pressure", "clarification_pressure", "exploration_readiness",
        "initiative_readiness", "slow_cognition_pressure",
    ]
    band: str | None = None
    effect: Literal["applied", "not_applicable", "intentionally_unused", "unavailable"]


class DeliveryControls(FrozenModel):
    warmth_band: DeliveryBand
    directness_band: DeliveryBand
    playfulness_band: DeliveryBand
    curiosity_band: DeliveryBand
    transition_style: Literal["cushioned", "plain", "minimal"]
    correction_style: Literal["not_applicable", "gentle_explicit", "explicit", "direct_explicit"]
    follow_up_style: Literal["not_applicable", "narrow", "neutral", "inviting"]
    question_tone: Literal["not_applicable", "tentative", "neutral", "direct"]
    content_lead_style: Literal["context_first", "balanced", "content_first"]
    compression_preference: Literal["concise", "balanced", "expansive"]
    style_marker_budget: int = Field(ge=0, le=1)
    playfulness_intensity: Literal["none", "subtle", "light"]
    endearment_policy: Literal["forbid"] = "forbid"
    emotional_claim_policy: Literal["forbid_unprovided"] = "forbid_unprovided"
    exclamation_policy: Literal["avoid"] = "avoid"
    question_directness: Literal["not_applicable", "standard", "direct"]
    question_openness: Literal["not_applicable", "narrow", "standard", "open"]
    repair_explicitness: Literal["not_applicable", "standard", "explicit"]


class ResolvedDeliveryProfile(FrozenModel):
    style_manifest_version: Literal["realization-style-manifest-v0.1"] = REALIZATION_STYLE_MANIFEST
    personality_profile_ref: str = Field(min_length=1)
    personality_mapper_version: str = Field(min_length=1)
    personality_status: Literal["known", "unknown"]
    trait_sources: tuple[TraitBandSource, ...]
    state_snapshot_ref: str = Field(min_length=1)
    state_sources: tuple[StateControlSource, ...]
    controls: DeliveryControls

    @model_validator(mode="after")
    def valid(self) -> ResolvedDeliveryProfile:
        if tuple(item.trait for item in self.trait_sources) != ("curiosity", "warmth", "playfulness", "directness"):
            raise ValueError("every trait source is recorded in fixed order")
        if tuple(item.control for item in self.state_sources) != (
            "response_energy", "repair_pressure", "clarification_pressure", "exploration_readiness",
            "initiative_readiness", "slow_cognition_pressure",
        ):
            raise ValueError("every state control is recorded in fixed order")
        for item in self.state_sources:
            if item.control in {"initiative_readiness", "slow_cognition_pressure"} and item.effect != "intentionally_unused":
                raise ValueError("initiative and slow-cognition pressure have no Realization V0 effect")
        return self


class ConstraintOrigin(StrEnum):
    ACTION = "action"
    APPLICATION_SAFETY = "application_safety"
    CONTENT = "content"
    GLOBAL = "global"
    ACTION_MANIFEST = "action_manifest"


class RealizationConstraintItem(FrozenModel):
    code: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    origin: ConstraintOrigin
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> RealizationConstraintItem:
        if not _CODE.fullmatch(self.code):
            raise ValueError("constraint codes are stable lowercase codes")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (self.code, self.subject_ref, self.origin.value, self.evidence_refs)


GLOBAL_PROHIBITIONS: tuple[str, ...] = (
    "introduce_unsupported_claim", "answer_from_model_knowledge", "change_numeric_value_or_unit",
    "drop_required_uncertainty", "change_attribution", "claim_unobserved_tool_execution",
    "claim_unrecorded_memory", "add_unplanned_question", "add_unplanned_offer", "add_unplanned_commitment",
    "add_unplanned_advice", "assert_hidden_affect", "expose_internal_cognitive_state",
    "reassert_corrected_content",
)
GLOBAL_SUBJECT = "realization:request"


class RealizationProvenance(FrozenModel):
    action_result_ref: str = Field(min_length=1)
    action_request_digest: str = Field(min_length=1)
    action_context_ref: str = Field(min_length=1)
    action_versions: ActionVersions
    safety_decision_ref: str = Field(min_length=1)
    safety_restriction: SafetyRestriction
    inventory_ref: str = Field(min_length=1)
    content_producer_refs: tuple[str, ...] = Field(min_length=1)
    configured_maximum_characters: int = Field(ge=1)
    versions: RealizationVersions = Field(default_factory=RealizationVersions)


class RealizationLinguisticContext(FrozenModel):
    """Exact user wording available only as non-authoritative language context."""

    source: Literal["current_user_utterance"] = "current_user_utterance"
    text: str
    authority: Literal["linguistic_context_only"] = "linguistic_context_only"
    permitted_use: Literal["wording_and_reference_resolution_only"] = "wording_and_reference_resolution_only"
    fact_authority: Literal[False] = False
    action_authority: Literal[False] = False
    authorization_authority: Literal[False] = False
    safety_authority: Literal[False] = False


MAX_CONVERSATION_CONTEXT_MESSAGES = 8
MAX_CONVERSATION_CONTEXT_CHARACTERS = 2000


class RealizationConversationTurn(FrozenModel):
    speaker: Literal["user", "columbina"]
    text: str = Field(min_length=1, max_length=MAX_CONVERSATION_CONTEXT_CHARACTERS)


class RealizationConversationContext(FrozenModel):
    """Realization policy v1.4+: the messages before the current one, for comprehension only.

    The wording model reads them to understand what is being asked and what
    earlier words refer to. Like the current utterance, they carry no fact,
    action, authorization or safety authority: only ``content_items`` may be
    asserted, and the validator still checks every assertion.
    """

    source: Literal["recent_conversation"] = "recent_conversation"
    messages: tuple[RealizationConversationTurn, ...] = Field(min_length=1, max_length=MAX_CONVERSATION_CONTEXT_MESSAGES)
    authority: Literal["linguistic_context_only"] = "linguistic_context_only"
    permitted_use: Literal["comprehension_and_reference_resolution_only"] = "comprehension_and_reference_resolution_only"
    fact_authority: Literal[False] = False
    action_authority: Literal[False] = False
    authorization_authority: Literal[False] = False
    safety_authority: Literal[False] = False


class RealizationRequestV0(FrozenModel):
    schema_version: Literal["realization-contract-v0.3"] = REALIZATION_CONTRACT_VERSION
    request_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    language_tag: Literal["en"]
    linguistic_context: RealizationLinguisticContext
    action_result_ref: str = Field(min_length=1)
    action_plan: RealizationActionPlan
    content_items: tuple[RealizationContentItem, ...] = ()
    move_bindings: tuple[RealizationMoveBinding, ...] = Field(min_length=1)
    discourse_controls: DiscourseControls
    delivery_profile: ResolvedDeliveryProfile
    constraints: tuple[RealizationConstraintItem, ...] = Field(min_length=1)
    provenance: RealizationProvenance
    request_digest: str
    # Realization policy v1.4+ only; omitted otherwise, so earlier requests keep their digests.
    conversation_context: RealizationConversationContext | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_conversation(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("conversation_context") is None:
            data.pop("conversation_context", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> RealizationRequestV0:
        moves = self.action_plan.moves
        move_refs = tuple(item.move_ref for item in moves)
        if tuple(item.move_ref for item in self.move_bindings) != move_refs:
            raise ValueError("every selected move has exactly one binding, in plan order")
        if tuple(item.action_key for item in self.move_bindings) != tuple(item.action_key for item in moves):
            raise ValueError("move bindings name their moves' action keys")
        if self.discourse_controls.move_order != move_refs:
            raise ValueError("discourse move order is the exact selected order")
        if self.provenance.action_result_ref != self.action_result_ref:
            raise ValueError("request provenance cites a different action result")
        if self.provenance.versions != RealizationVersions():
            raise ValueError("request versions differ from the installed realization versions")
        items = {item.content_id: item for item in self.content_items}
        if len(items) != len(self.content_items):
            raise ValueError("request content ids must be unique")
        if tuple(sorted(items)) != tuple(item.content_id for item in self.content_items):
            raise ValueError("request content items must be in canonical id order")
        bound: set[str] = set()
        for binding in self.move_bindings:
            for ref in binding.bound_content_refs:
                if ref not in items:
                    raise ValueError("a move binding names content outside the request")
                bound.add(ref)
        if bound != set(items):
            raise ValueError("every request content item is bound to a selected move")
        for item in self.content_items:
            if item.authorized_for_realization is not True:
                raise ValueError("only authorized content may enter a realization request")
            if item.cognitive_turn_id != self.cognitive_turn_id:
                raise ValueError("request content belongs to a different cognitive turn")
            if item.status != ObjectStatus.CURRENT:
                raise ValueError("request content must be at its current revision")
            if item.sensitivity_class not in REALIZABLE_SENSITIVITY:
                raise ValueError("request content exceeds the realization sensitivity policy")
        keys = tuple(item.sort_key for item in self.constraints)
        _unique(keys, "constraints")
        if keys != tuple(sorted(keys)):
            raise ValueError("constraints must be in canonical order")
        globals_present = {item.code for item in self.constraints if item.origin == ConstraintOrigin.GLOBAL}
        if globals_present != set(GLOBAL_PROHIBITIONS):
            raise ValueError("every global realization prohibition is present")
        for move in moves:
            for constraint in move.realization_constraints:
                if not any(item.origin == ConstraintOrigin.ACTION and item.code == constraint.code
                           and item.subject_ref == constraint.subject_ref for item in self.constraints):
                    raise ValueError("an inherited action realization constraint was dropped")
        _digest(self.request_digest, "request digest")
        if self.request_digest != request_digest_for(self):
            raise ValueError("request digest does not match its canonical contents")
        return self

    def content(self, content_ref: str) -> Any:
        return next(item for item in self.content_items if item.content_id == content_ref)

    def binding(self, move_ref: str) -> RealizationMoveBinding:
        return next(item for item in self.move_bindings if item.move_ref == move_ref)

    def move(self, move_ref: str) -> RealizationMoveSpec:
        return next(item for item in self.action_plan.moves if item.move_ref == move_ref)


def request_digest_for(request: RealizationRequestV0 | Mapping[str, Any]) -> str:
    payload = request.model_dump(mode="json") if isinstance(request, FrozenModel) else dict(request)
    payload = {key: value for key, value in payload.items() if key != "request_digest"}
    return sha256_json(payload)


# ---------------------------------------------------------------------------
# Candidate (untrusted model output)
# ---------------------------------------------------------------------------

class TextSpanAlignment(FrozenModel):
    """A self-reported alignment: diagnostic evidence, never self-validating proof."""

    ref: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid(self) -> TextSpanAlignment:
        if self.end <= self.start:
            raise ValueError("alignment spans are non-empty")
        return self


def _validate_spans(text: str, alignments: tuple[TextSpanAlignment, ...], label: str) -> None:
    keys = tuple((item.start, item.end, item.ref) for item in alignments)
    if keys != tuple(sorted(keys)):
        raise ValueError(f"{label} must be ordered by span")
    _unique(keys, label)
    for item in alignments:
        if item.end > len(text) or not text[item.start:item.end].strip():
            raise ValueError(f"{label} must slice a non-empty substring inside the text")


# Resolved request options recorded by a capability-validated adapter
# (SH-EVAL-007).  Provenance recorded before capability validation existed
# carries none of them; such payloads keep their exact serialization.
RESOLVED_PROVIDER_OPTION_FIELDS = (
    "endpoint", "capability_manifest_version", "model_family", "token_limit_parameter",
    "sampling_controls", "reasoning_controls", "candidate_count", "sdk_retries",
)


class RealizationModelProvenance(FrozenModel):
    # Optional extension for an already captured orchestration candidate.
    generation_source: Literal['captured_two_call'] | None = None
    source_call_digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    protocol_version: Literal["realization-model-protocol-v0.3"] = REALIZATION_MODEL_PROTOCOL_VERSION
    temperature: str
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    structured_output: Literal["json_schema_strict"] = "json_schema_strict"
    tools: Literal["none"] = "none"
    streaming: Literal[False] = False
    provider_session_memory: Literal["disabled"] = "disabled"
    # Every behavior-affecting option the adapter resolved from its validated
    # capability description.  All present or all absent (legacy provenance).
    endpoint: Literal["chat.completions"] | None = None
    capability_manifest_version: str | None = None
    model_family: str | None = None
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens"] | None = None
    sampling_controls: Literal["temperature"] | None = None
    reasoning_controls: Literal["not_sent", "none"] | None = None
    reasoning_effort: Literal["none"] | None = None
    # ``conversational-v1``: the natural-voice instruction set paired with the
    # conversational validation policy.  Omitted for the strict instructions.
    # ``conversational-v2`` (realization policy v1.4) also reads recent conversation.
    instruction_variant: Literal["conversational-v1", "conversational-v2", "conversational-v3", "conversational-v4"] | None = None
    candidate_count: Literal[1] | None = None
    sdk_retries: Literal[0] | None = None

    @model_serializer(mode="wrap")
    def _omit_unresolved(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in (*RESOLVED_PROVIDER_OPTION_FIELDS, "reasoning_effort", "generation_source", "source_call_digest",
                         "instruction_variant"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data

    @model_validator(mode="after")
    def valid(self) -> RealizationModelProvenance:
        decimal_from(self.temperature)
        if (self.generation_source is None) != (self.source_call_digest is None):
            raise ValueError("captured generation requires its exact source call digest")
        if (self.reasoning_controls == "none") != (self.reasoning_effort == "none"):
            raise ValueError("explicit reasoning control and effort must agree")
        resolved = [getattr(self, name) is not None for name in RESOLVED_PROVIDER_OPTION_FIELDS]
        if any(resolved) and not all(resolved):
            raise ValueError("resolved provider options are recorded together or not at all")
        if self.capability_manifest_version is not None and not self.capability_manifest_version.strip():
            raise ValueError("the capability manifest version must not be blank")
        if self.model_family is not None and not self.model_family.strip():
            raise ValueError("the resolved model family must not be blank")
        return self

    @property
    def resolved(self) -> bool:
        return self.endpoint is not None


class RealizationCandidateV0(FrozenModel):
    """Untrusted model output.  ``request_ref`` is the exact request digest."""

    schema_version: Literal["realization-contract-v0.3"] = REALIZATION_CONTRACT_VERSION
    candidate_id: str = Field(min_length=1)
    request_ref: str = Field(min_length=1)
    attempt_number: int = Field(ge=1, le=REALIZATION_MAX_ATTEMPTS)
    text: str = Field(min_length=1)
    move_alignments: tuple[TextSpanAlignment, ...] = ()
    content_alignments: tuple[TextSpanAlignment, ...] = ()
    omitted_move_refs: tuple[str, ...] = ()
    model_provenance: RealizationModelProvenance

    @model_validator(mode="after")
    def valid(self) -> RealizationCandidateV0:
        if not self.text.strip():
            raise ValueError("candidate text must be non-empty")
        try:
            self.text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("candidate text must be UTF-8 safe") from exc
        _validate_spans(self.text, self.move_alignments, "move alignments")
        _validate_spans(self.text, self.content_alignments, "content alignments")
        _sorted_unique(self.omitted_move_refs, "omitted move refs")
        if self.candidate_id != candidate_id_for(self.request_ref, self.attempt_number, self.model_dump(mode="json")):
            raise ValueError("candidate id is derived from the request, attempt, and canonical candidate")
        return self


def candidate_id_for(request_digest_or_ref: str, attempt_number: int, payload: Mapping[str, Any]) -> str:
    """Derived after generation; never trusted provider identity."""

    body = {key: value for key, value in dict(payload).items() if key != "candidate_id"}
    return realization_uuid(
        REALIZATION_CONTRACT_VERSION, "candidate", request_digest_or_ref, str(attempt_number), sha256_json(body),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationPhase(StrEnum):
    SCHEMA = "schema"
    OWNERSHIP = "ownership"
    MOVE_COVERAGE = "move_coverage"
    MOVE_ORDER = "move_order"
    CONTENT_GROUNDING = "content_grounding"
    EPISTEMIC_FIDELITY = "epistemic_fidelity"
    CONSTRAINT_ENFORCEMENT = "constraint_enforcement"
    STYLE_BOUNDS = "style_bounds"
    FINAL_INTEGRITY = "final_integrity"


VALIDATION_PHASE_ORDER: tuple[ValidationPhase, ...] = tuple(ValidationPhase)


class ViolationCode(StrEnum):
    OUTPUT_SCHEMA_INVALID = "output_schema_invalid"
    ALIGNMENT_INVALID = "alignment_invalid"
    REQUEST_OWNERSHIP_MISMATCH = "request_ownership_mismatch"
    REQUIRED_MOVE_MISSING = "required_move_missing"
    UNPLANNED_MOVE_DETECTED = "unplanned_move_detected"
    MOVE_ORDER_CHANGED = "move_order_changed"
    CONTENT_ALIGNMENT_MISSING = "content_alignment_missing"
    UNSUPPORTED_CLAIM_DETECTED = "unsupported_claim_detected"
    NUMERIC_VALUE_CHANGED = "numeric_value_changed"
    UNIT_CHANGED = "unit_changed"
    QUOTE_ALTERED = "quote_altered"
    POLARITY_CHANGED = "polarity_changed"
    ATTRIBUTION_DROPPED = "attribution_dropped"
    REQUIRED_UNCERTAINTY_DROPPED = "required_uncertainty_dropped"
    CONDITIONALITY_DROPPED = "conditionality_dropped"
    KNOWLEDGE_GAP_ANSWERED = "knowledge_gap_answered"
    ANSWER_CONTRADICTED = "answer_contradicted"
    ANSWER_DROPPED = "answer_dropped"
    UNRECORDED_MEMORY_CLAIMED = "unrecorded_memory_claimed"
    FACTUAL_ENDORSEMENT_DETECTED = "factual_endorsement_detected"
    FALSE_TOOL_COMPLETION_CLAIM = "false_tool_completion_claim"
    UNPLANNED_QUESTION = "unplanned_question"
    UNPLANNED_OFFER = "unplanned_offer"
    UNPLANNED_COMMITMENT = "unplanned_commitment"
    UNPLANNED_ADVICE = "unplanned_advice"
    GUIDANCE_STRENGTH_CHANGED = "guidance_strength_changed"
    UNSUPPORTED_GUARANTEE = "unsupported_guarantee"
    HIDDEN_AFFECT_ASSERTED = "hidden_affect_asserted"
    CORRECTED_CONTENT_REASSERTED = "corrected_content_reasserted"
    PLAY_FRAME_VIOLATED = "play_frame_violated"
    MOVE_FORM_INVALID = "move_form_invalid"
    STYLE_MARKER_BUDGET_EXCEEDED = "style_marker_budget_exceeded"
    ENDEARMENT_USED = "endearment_used"
    EXCLAMATION_USED = "exclamation_used"
    OUTPUT_TOO_LONG = "output_too_long"
    INTERNAL_STATE_DISCLOSED = "internal_state_disclosed"
    CONTROL_CHARACTER_DETECTED = "control_character_detected"


VIOLATION_PHASES: Mapping[ViolationCode, ValidationPhase] = {
    ViolationCode.OUTPUT_SCHEMA_INVALID: ValidationPhase.SCHEMA,
    ViolationCode.ALIGNMENT_INVALID: ValidationPhase.SCHEMA,
    ViolationCode.REQUEST_OWNERSHIP_MISMATCH: ValidationPhase.OWNERSHIP,
    ViolationCode.REQUIRED_MOVE_MISSING: ValidationPhase.MOVE_COVERAGE,
    ViolationCode.UNPLANNED_MOVE_DETECTED: ValidationPhase.MOVE_COVERAGE,
    ViolationCode.MOVE_ORDER_CHANGED: ValidationPhase.MOVE_ORDER,
    ViolationCode.CONTENT_ALIGNMENT_MISSING: ValidationPhase.CONTENT_GROUNDING,
    ViolationCode.UNSUPPORTED_CLAIM_DETECTED: ValidationPhase.CONTENT_GROUNDING,
    ViolationCode.NUMERIC_VALUE_CHANGED: ValidationPhase.CONTENT_GROUNDING,
    ViolationCode.UNIT_CHANGED: ValidationPhase.CONTENT_GROUNDING,
    ViolationCode.QUOTE_ALTERED: ValidationPhase.CONTENT_GROUNDING,
    ViolationCode.POLARITY_CHANGED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.ATTRIBUTION_DROPPED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.REQUIRED_UNCERTAINTY_DROPPED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.CONDITIONALITY_DROPPED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.KNOWLEDGE_GAP_ANSWERED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.ANSWER_CONTRADICTED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.ANSWER_DROPPED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.UNRECORDED_MEMORY_CLAIMED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.FACTUAL_ENDORSEMENT_DETECTED: ValidationPhase.EPISTEMIC_FIDELITY,
    ViolationCode.FALSE_TOOL_COMPLETION_CLAIM: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.UNPLANNED_QUESTION: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.UNPLANNED_OFFER: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.UNPLANNED_COMMITMENT: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.UNPLANNED_ADVICE: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.GUIDANCE_STRENGTH_CHANGED: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.UNSUPPORTED_GUARANTEE: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.HIDDEN_AFFECT_ASSERTED: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.CORRECTED_CONTENT_REASSERTED: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.PLAY_FRAME_VIOLATED: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.MOVE_FORM_INVALID: ValidationPhase.CONSTRAINT_ENFORCEMENT,
    ViolationCode.STYLE_MARKER_BUDGET_EXCEEDED: ValidationPhase.STYLE_BOUNDS,
    ViolationCode.ENDEARMENT_USED: ValidationPhase.STYLE_BOUNDS,
    ViolationCode.EXCLAMATION_USED: ValidationPhase.STYLE_BOUNDS,
    ViolationCode.OUTPUT_TOO_LONG: ValidationPhase.FINAL_INTEGRITY,
    ViolationCode.INTERNAL_STATE_DISCLOSED: ValidationPhase.FINAL_INTEGRITY,
    ViolationCode.CONTROL_CHARACTER_DETECTED: ValidationPhase.FINAL_INTEGRITY,
}
if set(VIOLATION_PHASES) != set(ViolationCode):
    raise RuntimeError("every realization violation code has exactly one owning phase")


class RealizationViolation(FrozenModel):
    phase: ValidationPhase
    code: ViolationCode
    subject_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RealizationViolation:
        if VIOLATION_PHASES[self.code] != self.phase:
            raise ValueError("a violation code belongs to exactly one validation phase")
        return self

    @property
    def sort_key(self) -> tuple[int, str, str]:
        return (VALIDATION_PHASE_ORDER.index(self.phase), self.code.value, self.subject_ref)


def violation(code: ViolationCode, subject_ref: str) -> RealizationViolation:
    return RealizationViolation(phase=VIOLATION_PHASES[code], code=code, subject_ref=subject_ref)


RealizationValidationPolicy = Literal["strict-v0.1", "style_advisory-v0.1", "segmented_response-v0.1", "segmented_response-v0.2",
                                      "conversational-v1", "conversational-v2", "conversational-v3", "conversational-v4"]
CONVERSATIONAL_POLICIES = frozenset({"conversational-v1", "conversational-v2", "conversational-v3", "conversational-v4"})
STYLE_ADVISORY_CODES = frozenset({
    ViolationCode.STYLE_MARKER_BUDGET_EXCEEDED,
    ViolationCode.ENDEARMENT_USED,
    ViolationCode.EXCLAMATION_USED,
})
SEGMENTED_SOCIAL_ADVISORY_SUBJECTS = frozenset({
    "detector:greeting",
    "detector:thanks",
    "detector:acknowledgement",
})


# ``conversational-v1`` (cognitive two-call v1): style and alignment bookkeeping
# are review findings, never failures.  Honesty and safety stay hard: invented
# facts or numbers, false completion, unrecorded memory, hidden affect,
# guarantees, commitments, offers, advice, endorsement, corrected content,
# attribution and uncertainty of content, internal disclosure, and the
# configured maximum length.
CONVERSATIONAL_ADVISORY_CODES = frozenset({
    ViolationCode.STYLE_MARKER_BUDGET_EXCEEDED, ViolationCode.ENDEARMENT_USED, ViolationCode.EXCLAMATION_USED,
    ViolationCode.UNPLANNED_QUESTION, ViolationCode.MOVE_ORDER_CHANGED, ViolationCode.MOVE_FORM_INVALID,
})
CONVERSATIONAL_ADVISORY_SUBJECTS = frozenset({
    "detector:greeting", "detector:closing", "detector:thanks", "detector:congratulation",
    "detector:acknowledgement", "detector:apology", "detector:reassurance", "detector:play", "text:unaligned",
})


# ``conversational-v2`` (realization policy ``separate_realization-v1.2``): say
# what Columbina wants to say.  Only a malformed or incomplete candidate, the
# configured maximum length, and the two claims that misrepresent the real
# world block: an action she did not take, and a memory she does not have.
# Every other finding is recorded as a warning, so its value can be judged
# from evidence before any rule is removed or restored.
CONVERSATIONAL_V2_BLOCKING_CODES = frozenset({
    ViolationCode.OUTPUT_SCHEMA_INVALID, ViolationCode.ALIGNMENT_INVALID, ViolationCode.REQUEST_OWNERSHIP_MISMATCH,
    ViolationCode.REQUIRED_MOVE_MISSING, ViolationCode.CONTROL_CHARACTER_DETECTED,
    ViolationCode.FALSE_TOOL_COMPLETION_CLAIM, ViolationCode.UNRECORDED_MEMORY_CLAIMED,
})
# ``conversational-v3`` (realization policy ``separate_realization-v1.3``):
# conversational-v2 plus answer fidelity.  Wording stays free, but an answer
# move must not deny or reverse a known answer, and must not drop an answer
# taken from the conversation.  Both codes are only emitted under v3.
CONVERSATIONAL_V3_BLOCKING_CODES = CONVERSATIONAL_V2_BLOCKING_CODES | frozenset({
    ViolationCode.ANSWER_CONTRADICTED, ViolationCode.ANSWER_DROPPED,
})


def advisory_finding_allowed(
    policy: RealizationValidationPolicy, item: RealizationViolation,
) -> bool:
    """Return whether a finding is non-blocking under the selected policy.

    Compact two-call wording may carry a small social marker without changing
    the response's material behavior.  Only segmented-response v0.2 treats the
    three closed incidental detectors below as advisory; unaligned prose and
    substantive acts such as apology, reassurance, play, offers, commitments,
    questions, and advice remain hard failures.
    """

    if policy in {"conversational-v2", "conversational-v3", "conversational-v4"}:
        if item.code == ViolationCode.OUTPUT_TOO_LONG:
            return item.subject_ref == "text:discourse_length"
        blocking = CONVERSATIONAL_V3_BLOCKING_CODES if policy in {"conversational-v3", "conversational-v4"} else CONVERSATIONAL_V2_BLOCKING_CODES
        return item.code not in blocking
    if policy == "conversational-v1":
        return (item.code in CONVERSATIONAL_ADVISORY_CODES
                or (item.code == ViolationCode.UNPLANNED_MOVE_DETECTED and item.subject_ref in CONVERSATIONAL_ADVISORY_SUBJECTS)
                or (item.code == ViolationCode.OUTPUT_TOO_LONG and item.subject_ref == "text:discourse_length"))
    if policy not in {"style_advisory-v0.1", "segmented_response-v0.1", "segmented_response-v0.2"}:
        return False
    if item.code in STYLE_ADVISORY_CODES:
        return True
    return (
        policy == "segmented_response-v0.2"
        and item.code == ViolationCode.UNPLANNED_MOVE_DETECTED
        and item.subject_ref in SEGMENTED_SOCIAL_ADVISORY_SUBJECTS
    )


class RealizationValidationResult(FrozenModel):
    validator_version: Literal["realization-validator-v0.3"] = REALIZATION_VALIDATOR_VERSION
    request_ref: str = Field(min_length=1)
    candidate_ref: str | None = None
    attempt_number: int = Field(ge=1, le=REALIZATION_MAX_ATTEMPTS)
    status: Literal["accepted_for_shadow_review", "rejected"]
    validation_policy: RealizationValidationPolicy = "strict-v0.1"
    warnings: tuple[RealizationViolation, ...] = ()
    phases_evaluated: tuple[ValidationPhase, ...] = VALIDATION_PHASE_ORDER
    violations: tuple[RealizationViolation, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_legacy_defaults(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.validation_policy == "strict-v0.1":
            data.pop("validation_policy", None)
        if not self.warnings:
            data.pop("warnings", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> RealizationValidationResult:
        if self.phases_evaluated != VALIDATION_PHASE_ORDER:
            raise ValueError("every validation phase is evaluated in fixed order")
        warning_keys = tuple(item.sort_key for item in self.warnings)
        _unique(warning_keys, "warnings")
        if warning_keys != tuple(sorted(warning_keys)):
            raise ValueError("warnings must be in canonical phase order")
        if self.warnings and any(not advisory_finding_allowed(self.validation_policy, item) for item in self.warnings):
            raise ValueError("only policy-approved findings may be advisory")
        if any(advisory_finding_allowed(self.validation_policy, item) for item in self.violations):
            raise ValueError("policy-approved advisory findings must be recorded as warnings")
        keys = tuple(item.sort_key for item in self.violations)
        _unique(keys, "violations")
        if keys != tuple(sorted(keys)):
            raise ValueError("violations must be in canonical phase order")
        if (self.status == "rejected") != bool(self.violations):
            raise ValueError("a candidate is rejected exactly when violations exist")
        if self.candidate_ref is None and (
            self.status != "rejected"
            or any(item.code != ViolationCode.OUTPUT_SCHEMA_INVALID for item in self.violations)
        ):
            raise ValueError("only an output-schema failure lacks a candidate")
        return self

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.code.value for item in self.violations))


# ---------------------------------------------------------------------------
# Attempts, trace, failures
# ---------------------------------------------------------------------------

class RealizationStatus(StrEnum):
    ACCEPTED_SHADOW = "accepted_shadow"
    REJECTED = "rejected"
    SKIPPED_MISSING_CONTENT = "skipped_missing_content"
    SKIPPED_UNSUPPORTED_ACTION = "skipped_unsupported_action"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_FAILED = "provider_failed"


SKIPPED_STATUSES = frozenset({RealizationStatus.SKIPPED_MISSING_CONTENT, RealizationStatus.SKIPPED_UNSUPPORTED_ACTION})


class AttemptOutcome(StrEnum):
    CANDIDATE_ACCEPTED = "candidate_accepted"
    CANDIDATE_REJECTED = "candidate_rejected"
    PROVIDER_ERROR = "provider_error"


class ProviderErrorCode(StrEnum):
    TIMEOUT = "provider_timeout"
    TRANSPORT = "provider_transport_error"
    REFUSED = "provider_refused"
    OUTPUT_TRUNCATED = "provider_output_truncated"
    # The provider rejected the request itself (e.g. an unsupported parameter).
    UNSUPPORTED = "provider_unsupported"
    # The provider answered with a response shape the protocol cannot accept
    # (choice count, tool calls, unexpected finish reason, non-text content).
    OUTPUT_INVALID = "provider_output_invalid"
    INTERNAL = "provider_internal_error"


class ProviderUnavailableReason(StrEnum):
    """Why no provider call was made; stable and content-free."""

    MODEL_NOT_CONFIGURED = "model_not_configured"
    # The configured model/endpoint cannot carry the protocol's request
    # features; detected locally before any API call.
    CONFIGURATION_INCOMPATIBLE = "provider_configuration_incompatible"


class PriorValidationCode(FrozenModel):
    """Codes and refs only; never free-form coaching."""

    code: ViolationCode
    subject_ref: str = Field(min_length=1)


class RealizationAttemptRecord(FrozenModel):
    attempt_number: int = Field(ge=1, le=REALIZATION_MAX_ATTEMPTS)
    outcome: AttemptOutcome
    prior_validation_codes: tuple[PriorValidationCode, ...] = ()
    candidate: RealizationCandidateV0 | None = None
    validation: RealizationValidationResult | None = None
    provider_error_code: ProviderErrorCode | None = None
    # Diagnostic only; excluded from the semantic digest.
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def valid(self) -> RealizationAttemptRecord:
        if self.outcome == AttemptOutcome.PROVIDER_ERROR:
            if self.provider_error_code is None or self.candidate is not None or self.validation is not None:
                raise ValueError("a provider error records its code and nothing generated")
        else:
            if self.provider_error_code is not None or self.validation is None:
                raise ValueError("a generated attempt carries no provider error and is always validated")
            if self.validation.attempt_number != self.attempt_number:
                raise ValueError("an attempt carries its own validation")
            accepted = self.validation.status == "accepted_for_shadow_review"
            if accepted != (self.outcome == AttemptOutcome.CANDIDATE_ACCEPTED):
                raise ValueError("the attempt outcome is the validation verdict")
            if accepted and self.candidate is None:
                raise ValueError("an accepted attempt carries its candidate")
            if (self.candidate is None) != (self.validation.candidate_ref is None):
                raise ValueError("the validation cites exactly the attempt's candidate")
            if self.candidate is not None and self.validation.candidate_ref != self.candidate.candidate_id:
                raise ValueError("the validation cites a different candidate")
        if self.attempt_number == 1 and self.prior_validation_codes:
            raise ValueError("only a retry carries prior validation codes")
        return self


class RealizationSkip(FrozenModel):
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_.]+$")
    subject_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> RealizationSkip:
        _sorted_unique(self.subject_refs, "skip subjects")
        return self


class RealizationTimings(FrozenModel):
    """Diagnostic measurements only; excluded from every semantic digest."""

    compile_ms: float = Field(ge=0)
    generation_ms: float = Field(ge=0)
    validation_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)


class RealizationTraceV0(FrozenModel):
    schema_version: Literal["realization-contract-v0.3"] = REALIZATION_CONTRACT_VERSION
    trace_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    action_result_ref: str = Field(min_length=1)
    status: RealizationStatus
    request: RealizationRequestV0 | None = None
    request_digest: str | None = None
    skip: RealizationSkip | None = None
    attempts: tuple[RealizationAttemptRecord, ...] = ()
    final_candidate: RealizationCandidateV0 | None = None
    validation: RealizationValidationResult | None = None
    model_provenance: RealizationModelProvenance | None = None
    versions: RealizationVersions = Field(default_factory=RealizationVersions)
    timings: RealizationTimings
    shadow_only: Literal[True] = True
    live_approved: Literal[False] = False
    # Present only on ``provider_unavailable``; omitted when absent so earlier
    # traces keep their exact serialization.
    unavailable_reason: ProviderUnavailableReason | None = None
    validation_policy: RealizationValidationPolicy = "strict-v0.1"

    @model_serializer(mode="wrap")
    def _omit_absent_reason(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and self.validation_policy == "strict-v0.1":
            data.pop("validation_policy", None)
        if isinstance(data, dict) and data.get("unavailable_reason") is None:
            data.pop("unavailable_reason", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> RealizationTraceV0:
        if self.unavailable_reason is not None and self.status != RealizationStatus.PROVIDER_UNAVAILABLE:
            raise ValueError("only an unavailable provider records an unavailable reason")
        if self.trace_id != trace_id_for(self.action_result_ref, self.request_digest):
            raise ValueError("trace id is derived from the action result and request lineage")
        if self.versions != RealizationVersions():
            raise ValueError("trace versions differ from the installed realization versions")
        if (self.request is None) != (self.request_digest is None):
            raise ValueError("a request and its digest are recorded together")
        if self.request is not None:
            if self.request.request_digest != self.request_digest:
                raise ValueError("trace request digest differs from its request")
            if (self.request.cognitive_turn_id, self.request.action_result_ref) != (
                self.cognitive_turn_id, self.action_result_ref,
            ):
                raise ValueError("trace request belongs to a different turn or action result")
        numbers = tuple(item.attempt_number for item in self.attempts)
        if numbers != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("attempts are numbered contiguously from one")
        if self.attempts and self.request is None:
            raise ValueError("generation attempts require a compiled request")
        for attempt in self.attempts:
            if attempt.validation is not None and attempt.validation.validation_policy != self.validation_policy:
                raise ValueError("every attempt uses the trace validation policy")
            # A candidate and its validation reference the exact request contents by digest.
            if attempt.candidate is not None and attempt.candidate.request_ref != self.request_digest:
                raise ValueError("an attempt candidate belongs to a different request")
            if attempt.validation is not None and attempt.validation.request_ref != self.request_digest:
                raise ValueError("an attempt validation belongs to a different request")
            if attempt.candidate is not None and attempt.candidate.attempt_number != attempt.attempt_number:
                raise ValueError("an attempt candidate cites a different attempt")
        for earlier in self.attempts[:-1]:
            if earlier.outcome == AttemptOutcome.CANDIDATE_ACCEPTED:
                raise ValueError("generation stops at the first accepted candidate")
        if self.model_provenance is not None:
            for attempt in self.attempts:
                if attempt.candidate is not None and attempt.candidate.model_provenance != self.model_provenance:
                    raise ValueError("every candidate records the trace's one model provenance")

        status = self.status
        if status in SKIPPED_STATUSES:
            if self.skip is None or self.request is not None or self.attempts or self.final_candidate or self.validation:
                raise ValueError("a skipped realization records only its typed skip")
            if self.model_provenance is not None:
                raise ValueError("a skipped realization made no model call")
            return self
        if self.skip is not None or self.request is None:
            raise ValueError("only a skipped realization lacks a compiled request")
        if status == RealizationStatus.PROVIDER_UNAVAILABLE:
            if self.attempts or self.final_candidate or self.validation or self.model_provenance is not None:
                raise ValueError("an unavailable provider was never called")
            return self
        if not self.attempts or self.model_provenance is None:
            raise ValueError("a called provider records its attempts and model provenance")
        last = self.attempts[-1]
        rejected = [item for item in self.attempts if item.outcome == AttemptOutcome.CANDIDATE_REJECTED]
        if status == RealizationStatus.ACCEPTED_SHADOW:
            if last.outcome != AttemptOutcome.CANDIDATE_ACCEPTED:
                raise ValueError("an accepted realization ends with its accepted attempt")
            expected_candidate, expected_validation = last.candidate, last.validation
        elif status == RealizationStatus.REJECTED:
            if not rejected or last.outcome == AttemptOutcome.CANDIDATE_ACCEPTED:
                raise ValueError("a rejected realization has a rejected attempt and no acceptance")
            expected_candidate, expected_validation = rejected[-1].candidate, rejected[-1].validation
        else:  # provider_failed
            if rejected or any(item.outcome != AttemptOutcome.PROVIDER_ERROR for item in self.attempts):
                raise ValueError("a failed provider produced no validated candidate")
            expected_candidate, expected_validation = None, None
        if (self.final_candidate, self.validation) != (expected_candidate, expected_validation):
            raise ValueError("the final candidate and validation are derived from the attempts")
        return self


def trace_id_for(action_result_ref: str, request_digest: str | None) -> str:
    return realization_uuid(REALIZATION_ENGINE_VERSION, "trace", action_result_ref, request_digest or "no-request")


class RealizationFailurePhase(StrEnum):
    REQUEST_VALIDATION = "request_validation"
    ACTION_MANIFEST_RESOLUTION = "action_manifest_resolution"
    CONTENT_RESOLUTION = "content_resolution"
    STYLE_MAPPING = "style_mapping"
    REQUEST_COMPILATION = "request_compilation"
    MODEL_PROTOCOL = "model_protocol"
    PROVIDER_CALL = "provider_call"
    CANDIDATE_VALIDATION = "candidate_validation"
    TRACE_VALIDATION = "trace_validation"


class RealizationFailure(FrozenModel):
    """A content-free failure envelope: no user text, model output, memory, or provider body."""

    phase: RealizationFailurePhase
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_.]+$")
    safe_message: str = Field(min_length=1, max_length=300)


# ---------------------------------------------------------------------------
# Semantic projection and digest
# ---------------------------------------------------------------------------

def realization_semantic_projection(trace: RealizationTraceV0 | Mapping[str, Any]) -> dict[str, Any]:
    """The trace without wall-clock timings or attempt latency.

    Covers the canonical request, every attempt's candidate and validation,
    the final candidate and validation, the status, and the policy versions.
    """

    payload = trace.model_dump(mode="json") if isinstance(trace, FrozenModel) else json.loads(canonical_json(trace))
    if not isinstance(payload, dict) or "timings" not in payload or "attempts" not in payload:
        raise ValueError("realization trace payload does not have the reviewed semantic shape")
    payload.pop("timings")
    attempts = []
    for attempt in payload["attempts"]:
        if not isinstance(attempt, dict) or "latency_ms" not in attempt:
            raise ValueError("realization attempt payload does not have the reviewed semantic shape")
        attempts.append({key: value for key, value in attempt.items() if key != "latency_ms"})
    payload["attempts"] = attempts
    return payload


def realization_semantic_digest(trace: RealizationTraceV0 | Mapping[str, Any]) -> str:
    return sha256_json({
        "semantic_digest_version": REALIZATION_SEMANTIC_DIGEST_VERSION,
        "trace": realization_semantic_projection(trace),
    })


__all__ = [name for name in globals() if not name.startswith("_") and name not in {
    "annotations", "hashlib", "json", "re", "unicodedata", "uuid", "Mapping", "date", "StrEnum",
    "Annotated", "Any", "Literal", "Union", "Field", "model_validator", "model_serializer",
    "SerializerFunctionWrapHandler",
}]
