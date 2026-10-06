"""Reviewed Action-to-realization manifest (``realization-action-manifest-v0.1``).

One static entry for exactly every definition in
``action_selection_v0.registry.SHADOW_KEYS``, expanded from the normative
profiles and assignments in ``REALIZATION_V0_IMPLEMENTATION.md`` section 8.1.
Profile names never appear in a model payload.  Import fails when the entry set
drifts from the shadow keys, a definition version or lifecycle changes, an
assertion-capable action permits zero authorized content without a reviewed
exception, or a question action does not require a question/unknown binding.

Candidate, boundary-only, deprecated, or future definitions are not realizable
until separately added under a new manifest version.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..action_selection_v0.registry import DEFAULT_REGISTRY, SHADOW_KEYS
from ..contracts.actions import ActionLifecycle
from ..contracts.realization import (
    REALIZATION_ACTION_MANIFEST, ContentKind, DialogueReferenceKind,
)

MANIFEST_VERSION = REALIZATION_ACTION_MANIFEST
ACCEPTED_DEFINITION_VERSION = 1

P, Q, G, D, A = (
    ContentKind.GROUNDED_PROPOSITION, ContentKind.GROUNDED_QUESTION, ContentKind.KNOWLEDGE_GAP,
    ContentKind.DIALOGUE_REFERENCE, ContentKind.AUTHORIZED_QUOTE,
)
# Manifest v0.2: validated collaborative content and typed inquiries.
W, I = ContentKind.ARTIFACT_PRESENTATION, ContentKind.INQUIRY_PROMPT


class FrozenModel(BaseModel):
    """Static manifest rows: immutable, closed, and never serialized into a model payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AssertionPermission(StrEnum):
    NONE = "none"                          # no declarative claim beyond the named dialogue/social act
    GROUNDED_ONLY = "grounded_only"        # only propositions explicitly bound to the move
    ATTRIBUTED_ONLY = "attributed_only"    # only user/prior-source content with retained attribution
    SELF_STATUS_ONLY = "self_status_only"  # only the represented capability, uncertainty, or dialogue status


class AlignmentKind(StrEnum):
    ASSERTION = "assertion"; CORRECTION = "correction"; UNCERTAINTY = "uncertainty"; QUESTION = "question"
    RECEIPT = "receipt"; MISUNDERSTANDING = "misunderstanding"; PARAPHRASE = "paraphrase"
    REFLECTION = "reflection"; SOCIAL_ACT = "social_act"; REASSURANCE = "reassurance"; APOLOGY = "apology"
    ADVICE = "advice"; COORDINATION_ACT = "coordination_act"; DIALOGUE_ACT = "dialogue_act"
    OPENING = "opening"; CLOSING = "closing"; REPAIR = "repair"; PLAY_FRAME = "play_frame"
    ARTIFACT_DELIVERY = "artifact_delivery"


class StylePermission(StrEnum):
    COMPRESSION = "compression"; TRANSITION = "transition"; CLAUSE_SHAPE = "clause_shape"
    WARMTH_MARKER = "warmth_marker"; QUESTION_TONE = "question_tone"; PLAYFULNESS = "playfulness"


_NEUTRAL = frozenset({StylePermission.COMPRESSION, StylePermission.TRANSITION, StylePermission.CLAUSE_SHAPE})
STYLE_PERMISSION_SETS: Mapping[str, frozenset[StylePermission]] = {
    "neutral": _NEUTRAL,
    "warm": _NEUTRAL | {StylePermission.WARMTH_MARKER},
    "question": _NEUTRAL | {StylePermission.QUESTION_TONE},
    "play": _NEUTRAL | {StylePermission.PLAYFULNESS},
}


class RealizationProfile(FrozenModel):
    name: str
    allowed_content_kinds: frozenset[ContentKind]
    minimum_content_items: int = Field(ge=0)
    maximum_content_items: int | None = Field(default=None, ge=1)
    question_required: bool
    assertion_permission: AssertionPermission
    required_alignment_kind: AlignmentKind
    style_permission_set: str
    mandatory_constraint_codes: tuple[str, ...]


def _profile(name, kinds, minimum, maximum, question, assertion, alignment, style, constraints) -> RealizationProfile:
    return RealizationProfile(
        name=name, allowed_content_kinds=frozenset(kinds), minimum_content_items=minimum,
        maximum_content_items=maximum, question_required=question, assertion_permission=assertion,
        required_alignment_kind=alignment, style_permission_set=style, mandatory_constraint_codes=constraints,
    )


N, GO, AO, SS = (AssertionPermission.NONE, AssertionPermission.GROUNDED_ONLY, AssertionPermission.ATTRIBUTED_ONLY,
                 AssertionPermission.SELF_STATUS_ONLY)
K = AlignmentKind

# The normative profile table (section 8.1), row for row.
PROFILES: Mapping[str, RealizationProfile] = {item.name: item for item in (
    _profile("fact", (P, G), 1, None, False, GO, K.ASSERTION, "neutral",
             ("preserve_epistemic_status", "preserve_attribution")),
    _profile("correction", (P, D, A), 2, None, False, GO, K.CORRECTION, "neutral",
             ("replacement_required", "basis_required", "do_not_reassert_corrected_content")),
    _profile("uncertainty", (P, G), 1, None, False, SS, K.UNCERTAINTY, "neutral",
             ("uncertainty_marker_required", "no_gap_answer")),
    _profile("question", (Q, G, P, D), 1, None, True, AO, K.QUESTION, "question",
             ("one_selected_question_form", "no_embedded_answer")),
    _profile("receipt", (D, A), 1, 1, False, N, K.RECEIPT, "warm",
             ("no_agreement", "no_correctness_claim", "no_full_understanding_claim")),
    _profile("misunderstanding", (G, D), 1, 2, False, SS, K.MISUNDERSTANDING, "warm",
             ("scope_required", "no_user_fault_claim")),
    _profile("paraphrase", (P, D, A), 1, None, False, AO, K.PARAPHRASE, "neutral",
             ("preserve_meaning", "preserve_epistemic_status", "no_new_claim")),
    _profile("reflection", (P, D, A), 1, 2, False, AO, K.REFLECTION, "warm",
             ("preserve_tentativeness", "no_hidden_affect")),
    _profile("social_support", (P, D, A), 1, 2, False, AO, K.SOCIAL_ACT, "warm",
             ("no_external_fact_upgrade", "no_hidden_affect")),
    _profile("reassurance", (P, D), 2, None, False, GO, K.REASSURANCE, "warm",
             ("basis_required", "preserve_uncertainty", "no_promise", "no_guarantee")),
    _profile("apology", (D, P), 1, 2, False, SS, K.APOLOGY, "warm",
             ("involvement_or_impact_required", "no_invented_culpability")),
    _profile("advice", (P, D), 1, None, False, GO, K.ADVICE, "neutral",
             ("feasible_option_required", "preserve_guidance_strength", "no_guarantee")),
    _profile("coordination", (P, D), 1, None, False, SS, K.COORDINATION_ACT, "neutral",
             ("preserve_commitment_level", "no_completion_claim")),
    _profile("dialogue", (D, P, A), 1, 2, False, SS, K.DIALOGUE_ACT, "neutral",
             ("preserve_dialogue_scope",)),
    _profile("opening", (D,), 0, 1, False, N, K.OPENING, "warm",
             ("no_unplanned_question", "no_unplanned_offer")),
    _profile("closing", (D, P), 0, 1, False, N, K.CLOSING, "warm",
             ("no_completion_claim_without_content", "no_unplanned_offer")),
    _profile("repair", (D, P, A, G), 1, None, False, SS, K.REPAIR, "warm",
             ("represented_failure_required", "preserve_repair_scope")),
    _profile("play", (P, D, A), 1, 2, False, AO, K.PLAY_FRAME, "play",
             ("preserve_factual_frame", "no_new_factual_claim")),
    # Manifest v0.2: shared work delivered in the conversation.  It may word
    # only its validated items (proposals stay proposals), never a verified,
    # tool, or external-outcome claim.
    _profile("collaboration", (W, D), 1, None, False, GO, K.ARTIFACT_DELIVERY, "warm",
             ("preserve_epistemic_status", "preserve_artifact_values", "no_external_outcome_claim")),
    _profile("inquiry", (I,), 1, 1, True, N, K.QUESTION, "question",
             ("one_selected_question_form", "no_embedded_answer")),
)}


class BindingRequirement(FrozenModel):
    """A typed binding the action requires beyond its profile cardinality.

    ``selector`` names binding roles (``target:primary``); ``every`` requires
    each upstream ref under the selector to resolve, otherwise ``minimum``
    distinct items must.  ``anchored`` excludes generic dialogue references.
    """

    selector: str = Field(min_length=1)
    kinds: frozenset[ContentKind]
    minimum: int = Field(default=1, ge=1)
    every: bool = False
    anchored: bool = True
    # Bound propositions must be tentative (the represented unknown of a
    # verification or understanding check), never grounded fact.
    tentative: bool = False

    @model_validator(mode="after")
    def valid(self) -> BindingRequirement:
        if self.tentative and self.kinds != frozenset({ContentKind.GROUNDED_PROPOSITION}):
            raise ValueError("only proposition bindings can require tentative status")
        return self


class ActionRealizationEntry(FrozenModel):
    action_key: str = Field(min_length=1)
    accepted_definition_version: int = Field(ge=1)
    profile: str
    allowed_content_kinds: frozenset[ContentKind]
    minimum_content_items: int = Field(ge=0)
    maximum_content_items: int | None = Field(default=None, ge=1)
    question_required: bool
    assertion_permission: AssertionPermission
    required_alignment_kind: AlignmentKind
    style_permissions: frozenset[StylePermission]
    additional_constraint_codes: tuple[str, ...]
    mandatory_constraint_codes: tuple[str, ...]
    required_bindings: tuple[BindingRequirement, ...] = ()
    # Content-light exceptions: roles and generic dialogue-reference kinds that
    # may satisfy the move without anchored wording (reviewed per action).
    generic_roles: tuple[str, ...] = ()
    generic_reference_kinds: frozenset[DialogueReferenceKind] = frozenset()

    @model_validator(mode="after")
    def valid(self) -> ActionRealizationEntry:
        for requirement in self.required_bindings:
            if not requirement.kinds <= self.allowed_content_kinds:
                raise ValueError("a required binding cannot demand a kind its profile disallows")
        if bool(self.generic_roles) != bool(self.generic_reference_kinds):
            raise ValueError("generic roles and generic reference kinds are declared together")
        if self.generic_roles and D not in self.allowed_content_kinds:
            raise ValueError("only actions admitting dialogue references may accept generic references")
        return self


def _req(selector: str, *kinds: ContentKind, minimum: int = 1, every: bool = False,
         anchored: bool = True, tentative: bool = False) -> BindingRequirement:
    return BindingRequirement(selector=selector, kinds=frozenset(kinds), minimum=minimum, every=every,
                              anchored=anchored, tentative=tentative)


_UR, _UC, _DS = (DialogueReferenceKind.UNRESOLVED_ELEMENT, DialogueReferenceKind.USER_CONTRIBUTION,
                 DialogueReferenceKind.DIALOGUE_SCOPE)

# action key -> (profile, additional constraint codes, required bindings,
#                generic roles, generic reference kinds).  Section 8.1, row for row.
_ASSIGNMENTS: tuple[tuple, ...] = (
    ("provide_information.answer", "fact", ("gap_yields_no_grounded_answer_only",),
     (_req("evidence:basis", P, G),), (), ()),
    ("provide_information.explain", "fact", ("causal_language_must_be_represented",),
     (_req("target:primary", P), _req("evidence:basis", P)), (), ()),
    ("provide_information.exemplify", "fact", ("no_invented_example",),
     (_req("evidence:dependency", P), _req("evidence:example", P)), (), ()),
    ("provide_information.correct", "correction", ("bind_corrected_replacement_and_basis",),
     (_req("target:primary", P, D, A), _req("evidence:replacement", P), _req("evidence:basis", P, D, A)), (), ()),
    ("provide_information.disclose_uncertainty", "uncertainty", ("qualify_only_bound_targets",),
     (_req("target:primary", P, G, every=True),), (), ()),
    ("seek_information.clarify", "question", ("bind_unresolved_element",),
     (_req("target:primary", Q, G, P, D, anchored=False),), ("target:primary",), (_UR,)),
    ("seek_information.disambiguate", "question", ("preserve_candidate_labels",),
     (_req("target:primary", Q, G, P, D), _req("target:support", P, D, minimum=2)), (), ()),
    ("seek_information.verify", "question", ("preserve_tentative_status",),
     (_req("target:primary", P, tentative=True),), (), ()),
    ("seek_information.explore_goal", "question", ("do_not_suggest_goal",),
     (_req("target:primary", Q, G, D),), (), ()),
    ("seek_information.explore_preference", "question", ("do_not_infer_preference",),
     (_req("target:primary", Q, G, D), _req("target:support", P, D, every=True)), (), ()),
    ("seek_information.request_evidence", "question", ("preserve_requested_evidence_type",
                                                       "no_implied_existing_evidence"),
     (_req("target:primary", Q, P, D),), (), ()),
    ("seek_information.explore_reason", "question", ("bind_represented_stance",),
     (_req("target:primary", P, D, Q),), (), ()),
    ("seek_information.explore_experience", "question", ("never_assert_hidden_affect",),
     (_req("target:primary", D, P, Q),), (), ()),
    ("seek_information.check_understanding", "question", ("question_form_check_only",
                                                          "externalize_exact_interpretation"),
     (_req("target:primary", P, tentative=True),), (), ()),
    ("seek_information.request_feedback", "question", ("bind_evaluated_object",),
     (_req("target:primary", D, P),), (), ()),
    ("give_feedback.acknowledge", "receipt", ("receipt_attention_only",),
     (_req("target:primary", D, A, anchored=False),), ("target:primary",), (_UC,)),
    ("give_feedback.signal_misunderstanding", "misunderstanding", ("disclose_own_status_not_user_defect",),
     (_req("target:primary", G, D),), (), ()),
    ("give_feedback.paraphrase", "paraphrase", ("no_scope_or_modality_change",),
     (_req("target:member", P, D, A, every=True),), (), ()),
    ("give_feedback.reflect", "reflection", ("tentative_observation_only",),
     (_req("target:primary", P, D, A),), (), ()),
    ("support_socially.reassure", "reassurance", ("no_unsupported_outcome_prediction",),
     (_req("target:primary", P, D), _req("evidence:basis", P)), (), ()),
    ("support_socially.validate_experience", "social_support", ("validate_understandability_not_truth",),
     (_req("target:primary", P, D, A),), (), ()),
    ("support_socially.congratulate", "social_support", ("supported_achievement_only",),
     (_req("target:primary", P, D, A),), (), ()),
    ("support_socially.apologize", "apology", ("actual_involvement_only",),
     (_req("target:primary", D, P),), (), ()),
    ("advise_or_influence.advise", "advice", ("preserve_conditionality",),
     (_req("target:member", P, D, every=True), _req("parameter:option_refs", P, D, every=True)), (), ()),
    ("commit_or_coordinate.offer", "coordination", ("keep_contribution_prospective",),
     (_req("target:primary", P, D),), (), ()),
    ("commit_or_coordinate.accept_request", "coordination", ("acceptance_is_not_completion",),
     (_req("target:primary", D, P),), (), ()),
    ("commit_or_coordinate.decline_request", "coordination", ("no_general_boundary",),
     (_req("target:primary", D, P), _req("parameter:reason_refs", P, every=True)), (), ()),
    ("commit_or_coordinate.accept_suggestion", "coordination", ("preserve_conditionality",),
     (_req("target:primary", D, P),), (), ()),
    ("commit_or_coordinate.decline_suggestion", "coordination", ("no_general_boundary",),
     (_req("target:primary", D, P), _req("parameter:reason_refs", P, every=True)), (), ()),
    ("commit_or_coordinate.propose_plan", "coordination", ("ordered_steps_required",),
     (_req("target:primary", D, P), _req("target:support", P, D, minimum=2, every=True)), (), ()),
    ("commit_or_coordinate.state_next_step", "coordination", ("forbid_completion_language",),
     (_req("target:primary", D, P),), (), ()),
    ("manage_dialogue.greet", "opening", ("reviewed_opening_context_only",),
     (), ("target:scope",), (_DS,)),
    ("manage_dialogue.close", "closing", ("goal_completion_requires_content",),
     (), ("target:scope",), (_DS,)),
    ("manage_dialogue.introduce_topic", "dialogue", ("grounded_inactive_topic_only",),
     (_req("target:primary", D, P),), (), ()),
    ("manage_dialogue.repair", "repair", ("preserve_repair_scope",),
     (_req("target:member", D, P, A, G, every=True),), (), ()),
    ("manage_dialogue.return_to_topic", "dialogue", ("no_unrepresented_prior_content",),
     (_req("target:primary", D, P),), (), ()),
    ("manage_dialogue.self_correct", "correction", ("bind_corrected_replacement_and_basis",),
     (_req("target:primary", P, D, A), _req("evidence:correction", P)), (), ()),
    ("social_play.joke", "play", ("visibly_nonfactual_joke_material",),
     (_req("target:primary", P, D, A),), (), ()),
    ("social_play.playful_reframe", "play", ("preserve_original_proposition",),
     (_req("target:primary", P, D, A),), (), ()),
    ("collaborate.deliver_artifact", "collaboration", ("artifact_operation_exact",),
     (_req("target:primary", W), _req("parameter:basis_refs", W, every=True)), (), ()),
    ("seek_information.inquire", "inquiry", ("inquiry_timing_preserved",),
     (_req("target:primary", I),), (), ()),
)


def _expand(row: tuple) -> ActionRealizationEntry:
    key, profile_name, additional, required, generic_roles, generic_kinds = row
    profile = PROFILES[profile_name]
    return ActionRealizationEntry(
        action_key=key, accepted_definition_version=ACCEPTED_DEFINITION_VERSION, profile=profile_name,
        allowed_content_kinds=profile.allowed_content_kinds,
        minimum_content_items=profile.minimum_content_items, maximum_content_items=profile.maximum_content_items,
        question_required=profile.question_required, assertion_permission=profile.assertion_permission,
        required_alignment_kind=profile.required_alignment_kind,
        style_permissions=STYLE_PERMISSION_SETS[profile.style_permission_set],
        additional_constraint_codes=tuple(additional),
        mandatory_constraint_codes=profile.mandatory_constraint_codes,
        required_bindings=tuple(required), generic_roles=tuple(generic_roles),
        generic_reference_kinds=frozenset(generic_kinds),
    )


# Content-light actions that may legitimately bind zero content (the reviewed exception).
CONTENT_LIGHT_EXCEPTIONS = frozenset({"manage_dialogue.greet", "manage_dialogue.close"})
QUESTION_BINDING_KINDS = frozenset({Q, G, D, I})


def validate_manifest(entries: Mapping[str, ActionRealizationEntry], shadow_keys: frozenset[str]) -> None:
    """Fail when the manifest drifts from the installed Action shadow subset or its own rules."""

    if set(entries) != set(shadow_keys):
        missing, unknown = sorted(set(shadow_keys) - set(entries)), sorted(set(entries) - set(shadow_keys))
        raise RuntimeError(f"realization manifest differs from Action shadow keys: missing={missing} unknown={unknown}")
    for key, entry in entries.items():
        definition = DEFAULT_REGISTRY.by_key(key)
        if definition is None or definition.lifecycle != ActionLifecycle.SHADOW:
            raise RuntimeError(f"realization manifest action is not an installed shadow definition: {key}")
        if definition.definition_ref.definition_version != entry.accepted_definition_version:
            raise RuntimeError(f"realization manifest reviewed a different definition version: {key}")
        if entry.action_key != key:
            raise RuntimeError("realization manifest entry is filed under a different key")
        assertion_capable = entry.assertion_permission != AssertionPermission.NONE
        demands_content = entry.minimum_content_items >= 1 or any(item.minimum >= 1 for item in entry.required_bindings)
        if assertion_capable and not demands_content and key not in CONTENT_LIGHT_EXCEPTIONS:
            raise RuntimeError(f"assertion-capable action permits zero authorized content: {key}")
        if entry.question_required and not any(
            item.selector == "target:primary" and (item.kinds & QUESTION_BINDING_KINDS or item.tentative)
            for item in entry.required_bindings
        ):
            raise RuntimeError(f"question action does not require a question or unknown binding: {key}")
        if key.startswith("seek_information.") != entry.question_required:
            raise RuntimeError(f"only seek-information actions require a question form: {key}")


MANIFEST: Mapping[str, ActionRealizationEntry] = {entry.action_key: entry for entry in map(_expand, _ASSIGNMENTS)}
if len(MANIFEST) != len(_ASSIGNMENTS):
    raise RuntimeError("realization manifest assigns an action twice")
validate_manifest(MANIFEST, SHADOW_KEYS)


def entry_for(action_key: str, definition_version: int) -> ActionRealizationEntry | None:
    """The reviewed entry, or ``None`` when the action/version is not realizable."""

    entry = MANIFEST.get(action_key)
    if entry is None or entry.accepted_definition_version != definition_version:
        return None
    return entry


__all__ = [
    "ACCEPTED_DEFINITION_VERSION", "ActionRealizationEntry", "AlignmentKind", "AssertionPermission",
    "BindingRequirement", "CONTENT_LIGHT_EXCEPTIONS", "MANIFEST", "MANIFEST_VERSION", "PROFILES",
    "RealizationProfile", "STYLE_PERMISSION_SETS", "StylePermission", "entry_for", "validate_manifest",
]
