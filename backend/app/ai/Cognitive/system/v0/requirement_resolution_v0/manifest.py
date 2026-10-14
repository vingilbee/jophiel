"""The closed requirement-resolution manifest (``requirement-resolution-manifest-v0.1``).

Which prerequisites each current request goal declares is decided here, by
Goal definition and semantic predicate -- never by message text.  Three
tables are closed and versioned together:

- **declarations**: per (goal definition, predicate), the prerequisites and
  the precedence tier each is evaluated in;
- **content query compilation**: the typed ``SemanticQuery`` a supported
  predicate compiles to, built only from closed-vocabulary frame arguments;
- **capability map**: the registered capability a predicate's requested
  effect needs, with its authorization and consent requirements.

Precedence tiers order what blocks a goal first.  Only a goal's lowest
unsatisfied tier blocks it; later tiers are still resolved and traced but do
not decide the next action (you do not ask for a missing detail of a
prohibited request, and you do not decline for a missing capability before
the request is fully specified).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..contracts.requirement_resolution import (
    REQUIREMENT_CAPABILITY_MAP_VERSION, REQUIREMENT_RESOLUTION_MANIFEST_VERSION, RequirementKind as K,
)
from ..interpretation_v0.ontology import PREDICATE_MANIFEST, PredicateDefinition
from ..producers_v0.manifest import QueryKind

MANIFEST_VERSION = REQUIREMENT_RESOLUTION_MANIFEST_VERSION
CAPABILITY_MAP_VERSION = REQUIREMENT_CAPABILITY_MAP_VERSION

ANSWER_GOAL = "interaction.answer_information_request"
HANDLER_GOAL = "interaction.handle_action_request"
# Manifest v0.2 (Collaborative Content and Inquiry V0).
DELIVERABLE_GOAL = "interaction.fulfill_conversational_deliverable"
INQUIRY_ANSWER_GOAL = "interaction.incorporate_inquiry_answer"
REQUEST_GOAL_DEFINITIONS: tuple[str, ...] = (ANSWER_GOAL, HANDLER_GOAL, DELIVERABLE_GOAL, INQUIRY_ANSWER_GOAL)
COLLABORATION_GOAL_DEFINITIONS: frozenset[str] = frozenset({DELIVERABLE_GOAL, INQUIRY_ANSWER_GOAL})

# Precedence tiers (lower blocks first).  A trusted ``prohibited`` safety
# decision always evaluates in the safety tier, whatever its declaration.
TIER_SAFETY = 0
TIER_USER_INPUT = 1
TIER_CAPABILITY = 2
TIER_EXECUTION = 3
TIER_CONSENT = 4
TIER_CONTENT = 5
# Quality-improving context never blocks; it is traced and informs inquiries.
TIER_QUALITY = 6

# Arguments a user is never asked to supply: authorization is a trusted
# runtime fact, never a conversational argument.
NON_USER_SUPPLIABLE_ARGUMENTS = frozenset({"authorization"})
# Predicates whose request is refused before any detail is requested: asking
# for the missing detail of an unsafe request would advance it.
UNSAFE_PREDICATES = frozenset({"unsafe_request"})
# Referent cue kinds that can name an argument's referent.
REFERENT_CUE_KINDS = frozenset({"pronoun_singular", "pronoun_plural", "demonstrative", "ordinal_option"})

PREDICATES: Mapping[str, PredicateDefinition] = {item.predicate: item for item in PREDICATE_MANIFEST}


@dataclass(frozen=True)
class PrerequisiteDeclaration:
    kind: K
    key: str
    tier: int
    argument: str | None = None
    criticality: str = "blocking"


def _argument(item: PredicateDefinition, name: str) -> PrerequisiteDeclaration:
    kind = K.DIALOGUE_REFERENT if name in item.referent_arguments else K.REQUIRED_ARGUMENT
    return PrerequisiteDeclaration(kind, f"argument.{name}", TIER_USER_INPUT, name)


def answer_declarations(predicate: str) -> tuple[PrerequisiteDeclaration, ...]:
    """An information request: its referent (when it names one), then trusted content."""

    item = PREDICATES.get(predicate)
    referents = tuple(name for name in (item.referent_arguments if item else ()) if name in item.required_arguments)
    return (*(_argument(item, name) for name in referents),
            PrerequisiteDeclaration(K.TRUSTED_CONTENT, "content.answer", TIER_CONTENT))


def handler_declarations(predicate: str) -> tuple[PrerequisiteDeclaration, ...]:
    """An action request: user arguments, capability, safety (auth/consent follow the capability)."""

    item = PREDICATES.get(predicate)
    unsafe = predicate in UNSAFE_PREDICATES
    arguments = () if unsafe or item is None else tuple(
        _argument(item, name) for name in item.required_arguments if name not in NON_USER_SUPPLIABLE_ARGUMENTS
    )
    return (
        *arguments,
        PrerequisiteDeclaration(K.CAPABILITY, "capability.requested_effect", TIER_CAPABILITY),
        PrerequisiteDeclaration(K.SAFETY, "safety.request", TIER_SAFETY if unsafe else TIER_EXECUTION),
    )


# Operations that act on existing content and so need an exact target.
TARGETED_OPERATIONS = frozenset({"revise", "explain", "evaluate", "describe", "compare", "summarize"})
# Artifact types whose domain carries safety-relevant context.
SAFETY_CONTEXT_ARTIFACTS = frozenset({"meal_plan"})


def deliverable_declarations(predicate: str, operation: str | None, artifact_type: str | None,
                             ) -> tuple[PrerequisiteDeclaration, ...]:
    """Content delivered in the conversation (Section 10.2).

    Declared by operation; never an external capability, application-safety,
    authorization, or consent prerequisite -- content-provider availability
    is not external execution capability.
    """

    item = PREDICATES[predicate]
    out = [_argument(item, name) for name in item.required_arguments]
    out.append(PrerequisiteDeclaration(K.REQUESTED_OUTCOME, "outcome.requested", TIER_USER_INPUT))
    if operation in TARGETED_OPERATIONS:
        out.append(PrerequisiteDeclaration(K.ARTIFACT_TARGET, "target.artifact", TIER_USER_INPUT))
    if operation == "revise":
        out.append(PrerequisiteDeclaration(K.REQUESTED_CHANGE, "change.requested", TIER_USER_INPUT))
    if artifact_type in SAFETY_CONTEXT_ARTIFACTS and operation in {"create", "revise", "evaluate"}:
        out.append(PrerequisiteDeclaration(K.DOMAIN_CONTEXT, "context.domain_safety", TIER_QUALITY,
                                           criticality="quality_improving"))
    return tuple(out)


def inquiry_answer_declarations(resumed_operation: str | None) -> tuple[PrerequisiteDeclaration, ...]:
    """An answer: the exact open inquiry it answers, then the target it updates."""

    out = [PrerequisiteDeclaration(K.INQUIRY_LINK, "inquiry.open", TIER_USER_INPUT)]
    if resumed_operation in TARGETED_OPERATIONS:
        out.append(PrerequisiteDeclaration(K.ARTIFACT_TARGET, "target.artifact", TIER_USER_INPUT))
    return tuple(out)


def declarations_for(goal_definition: str, predicate: str, *, operation: str | None = None,
                     artifact_type: str | None = None) -> tuple[PrerequisiteDeclaration, ...]:
    if goal_definition == ANSWER_GOAL:
        return answer_declarations(predicate)
    if goal_definition == HANDLER_GOAL:
        return handler_declarations(predicate)
    if goal_definition == DELIVERABLE_GOAL:
        return deliverable_declarations(predicate, operation, artifact_type)
    if goal_definition == INQUIRY_ANSWER_GOAL:
        return inquiry_answer_declarations(operation)
    raise ValueError("the requirement manifest does not cover this goal definition")


# ---------------------------------------------------------------------------
# Capability map
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityEntry:
    """A registered capability and what executing it additionally requires.

    Every registered external effect needs a trusted authorization record; an
    irreversible one also needs the user's explicit consent.
    """

    capability_key: str
    requires_authorization: bool
    requires_consent: bool


def capability_for_predicate(predicate: str) -> CapabilityEntry | None:
    """The registered capability a predicate's requested effect needs, if any.

    Read from the typed predicate registry (``capability_key``,
    ``reversibility``).  A generic ``action_request`` carries its requested
    effect as free text, which is never mapped to a capability: its effect
    is unregistered, which is a limitation to disclose, not user ambiguity.
    """

    item = PREDICATES.get(predicate)
    if item is None or item.capability_key is None:
        return None
    return CapabilityEntry(item.capability_key, True, item.reversibility == "irreversible")


def capability_ref(entry: CapabilityEntry | None, predicate: str) -> str:
    return f"capability:{entry.capability_key}" if entry is not None else f"capability-unregistered:{predicate}"


# ---------------------------------------------------------------------------
# Content query compilation (typed arguments only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompiledQuery:
    query_kind: str
    arguments: Mapping[str, str]
    subject_anchor: str


# Reviewed topic anchors: the wording a grounded answer or explicit gap is
# anchored to.  They are never copied from the user's message or a parser
# paraphrase (which a realizer rarely reproduces verbatim).  A capitalized
# anchor stays valid when it opens a sentence (the proper-name check is case
# sensitive; anchor matching is not).
GENERIC_ANCHOR = "that"
ASSISTANT_NAME_ANCHOR = "my name"
UTC_ZONE = "UTC"

# Reviewed anchors for the one thing a clarification asks the user for, keyed
# by the missing semantic argument.  They name the *gap* in the assistant's
# understanding ("what you are referring to"), so a question realized from
# them can never echo, reverse, or re-ask the user's own request.
ELICITATION_ANCHORS: dict[str, str] = {
    "subject_ref": "what you are referring to",
    "referent_phrase": "what you are referring to",
    "object": "what you are referring to",
    "requested_effect": "what you would like me to do",
    "topic_ref": "what you are asking about",
    # Manifest v0.2: conversational deliverables.
    "target_ref": "which plan or draft you mean",
    "operation": "what you would like me to do with it",
    "artifact_type": "what kind of content you would like",
    "requested_change": "what you would like me to change",
    "requested_outcome": "whether you want it written here in our chat or done outside it",
}
DEFAULT_ELICITATION_ANCHOR = "what you mean"


def elicitation_anchor(argument_name: str | None) -> str:
    return ELICITATION_ANCHORS.get(argument_name or "", DEFAULT_ELICITATION_ANCHOR)


def current_date_anchor(time_zone: str | None) -> str:
    return "Today's date" if time_zone else f"Today's date in {UTC_ZONE}"


def compile_content_query(
    predicate: str, arguments: Mapping[str, object], *, time_zone: str | None,
    referent_item_refs: tuple[str, ...] = (), referent_anchor: str | None = None,
) -> CompiledQuery:
    """The typed query one information request compiles to.

    Only closed-vocabulary arguments select a query kind.  A kind absent from
    the producer manifest stays explicit and resolves to *unsupported*; there
    is no fallback to model knowledge.
    """

    def text(name: str) -> str | None:
        value = arguments.get(name)
        return value if isinstance(value, str) and value.strip() else None

    if predicate == "identity_request":
        attribute, about = text("identity_attribute"), text("about")
        if (attribute, about) == ("name", "assistant"):
            return CompiledQuery(QueryKind.ASSISTANT_NAME.value, {}, ASSISTANT_NAME_ANCHOR)
        scope = "application_identity" if about == "assistant" else "user_profile"
        return CompiledQuery(f"{scope}.{attribute or 'unspecified'}", {}, GENERIC_ANCHOR)
    if predicate == "runtime_fact_request":
        fact = text("fact_kind") or "unspecified"
        if fact == "current_date":
            return CompiledQuery(QueryKind.CURRENT_DATE.value, {}, current_date_anchor(time_zone))
        return CompiledQuery(f"runtime.{fact}", {}, GENERIC_ANCHOR)
    if predicate == "elaboration_request":
        # Only an exact, unique delivered dialogue item can be the claim; its
        # anchor is the exact cue span the user wrote ("it"), never a paraphrase.
        claim = {"claim_ref": referent_item_refs[0]} if len(referent_item_refs) == 1 else {}
        return CompiledQuery(QueryKind.DELIVERED_CLAIM.value, claim, referent_anchor or GENERIC_ANCHOR)
    if predicate == "information_request":
        # Open questions have no reviewed producer: the query is explicit and
        # unsupported, and the answer is an explicit knowledge gap.
        return CompiledQuery("reviewed_knowledge.open_question", {}, GENERIC_ANCHOR)
    if predicate == "dialogue_recall_request":
        return CompiledQuery("captured_dialogue.recall", {}, text("referent_phrase") or GENERIC_ANCHOR)
    return CompiledQuery(f"reviewed_knowledge.{predicate}", {}, GENERIC_ANCHOR)


__all__ = [
    "COLLABORATION_GOAL_DEFINITIONS", "DELIVERABLE_GOAL", "INQUIRY_ANSWER_GOAL", "SAFETY_CONTEXT_ARTIFACTS",
    "TARGETED_OPERATIONS", "TIER_QUALITY", "deliverable_declarations", "inquiry_answer_declarations",
    "ANSWER_GOAL", "ASSISTANT_NAME_ANCHOR", "CAPABILITY_MAP_VERSION", "CapabilityEntry", "CompiledQuery",
    "DEFAULT_ELICITATION_ANCHOR", "ELICITATION_ANCHORS", "GENERIC_ANCHOR", "HANDLER_GOAL", "MANIFEST_VERSION", "NON_USER_SUPPLIABLE_ARGUMENTS", "PREDICATES",
    "PrerequisiteDeclaration", "REFERENT_CUE_KINDS", "REQUEST_GOAL_DEFINITIONS", "TIER_CAPABILITY", "TIER_CONSENT",
    "TIER_CONTENT", "TIER_EXECUTION", "TIER_SAFETY", "TIER_USER_INPUT", "UNSAFE_PREDICATES", "UTC_ZONE",
    "answer_declarations", "capability_for_predicate", "capability_ref", "compile_content_query",
    "current_date_anchor", "declarations_for", "elicitation_anchor", "handler_declarations",
]
