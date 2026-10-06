"""Static Interpretation V0 hypothesis, predicate, and feature manifests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import Field

from ..contracts.interpretation import (
    HYPOTHESIS_CONFLICTS,
    HYPOTHESIS_COMPATIBILITIES,
    FrozenModel,
    HYPOTHESIS_KEYS_BY_DIMENSION,
    HypothesisDimension,
    SemanticFrame,
)

ONTOLOGY_VERSION = "interpretation-ontology-v1"
FEATURE_MANIFEST_VERSION = "interpretation-features-v1"
# v0.2 adds ``information_request``: a recognized question act whose topic has
# no trusted producer yet stays a question with an unresolved topic, instead
# of collapsing the whole contribution to ``unknown`` (SH-EVAL-003).
# v0.3 adds reviewed compositional predicates for ordinary dialogue (greeting,
# social question, identity/runtime-fact/follow-up requests, action requests,
# corrections, statements, constraints, gratitude, unsafe requests), gives
# every predicate a model-facing description, argument descriptions, and closed
# vocabularies, and declares which act family a predicate's frame grounds
# (SH-EVAL-012/019).  A predicate describes the user's act and content; it
# never asserts truth, authorization, capability availability, or success.
# v0.4 (Collaborative Content and Inquiry V0) adds the reviewed
# ``collaborative_content_request`` predicate -- work delivered inside the
# conversation (create, revise, explain, evaluate, describe, compare, or
# summarize a shared artifact) -- distinct from ``action_request``, which is
# now reserved for effects outside the conversation.  It also adds
# ``inquiry_answer``, which links an answer to an exact open inquiry the
# runtime supplied, and a closed requested-outcome classification per
# predicate.  Historical registries stay parseable (``HISTORICAL_PREDICATE_REGISTRIES``)
# but are never interpreted again under the new ontology.
PREDICATE_REGISTRY_VERSION = "interpretation-predicates-v0.4"


class ActFamily(StrEnum):
    """The communicative act a frame of a generic predicate grounds directly."""

    REQUEST_INFORMATION = "request_information"
    REQUEST_ACTION = "request_action"
    CORRECT = "correct"
    INFORM = "inform"
    GREET = "greet"
    SOCIAL_QUESTION = "social_question"
    CLOSE = "close"
    ACKNOWLEDGE = "acknowledge"
    THANK = "thank"
    # Registry v0.4: a directive whose requested outcome is content produced
    # inside the conversation.  It grounds the same request act and
    # cause-action goal as an external request; the predicate's requested
    # outcome, not the act, distinguishes them downstream.
    REQUEST_CONTENT = "request_content"
    # Registry v0.4: an answer bound to an exact open inquiry.
    ANSWER_INQUIRY = "answer_inquiry"


class RequestedOutcome(StrEnum):
    """What the speaker wants to result from their contribution (registry v0.4).

    Part of the pragmatic meaning of the current user contribution, fixed by
    the validated predicate -- never inferred afterwards from Goal or Action.
    ``external_effect`` is the only class that ever needs an external
    capability; ``conversational_artifact`` is content delivered inside the
    conversation, whatever the imperative wording.
    """

    INFORMATION_ANSWER = "information_answer"
    CONVERSATIONAL_ARTIFACT = "conversational_artifact"
    EXTERNAL_EFFECT = "external_effect"
    SOCIAL_EXCHANGE = "social_exchange"


# Closed vocabularies of the collaborative-content frame.  The artifact types
# are the registered conversational-workspace types (``contracts.workspace``
# checks parity at import); ``other`` maps to the generic structured payload.
COLLABORATIVE_OPERATIONS = ("create", "revise", "explain", "evaluate", "describe", "compare", "summarize")
ARTIFACT_TYPES = (
    "meal_plan", "plan", "draft_message", "outline", "checklist", "comparison", "summary", "list", "other",
)
REQUESTED_FORMS = ("list", "paragraph", "steps", "brief", "detailed", "inviting")
# ``ambiguous_external_effect``: the speaker may want a real-world change
# rather than content in the chat; resolved by a targeted question, never by
# guessing an external effect and declining for a missing capability.
OUTCOME_CERTAINTY = ("clear", "ambiguous_external_effect")
INQUIRY_ANSWER_KINDS = ("option", "free_text", "declined")


# Values of the optional ``embedding`` argument.  Anything other than
# ``direct`` means the speaker is reporting, quoting, or imagining the content,
# so a quoted instruction never becomes the speaker's own request.
EMBEDDING_VALUES = ("direct", "quoted", "reported", "hypothetical")


class HypothesisDefinition(FrozenModel):
    hypothesis_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    dimension: HypothesisDimension
    description: str = Field(min_length=1)
    compatible_with: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    rule_references: tuple[str, ...] = Field(min_length=1)
    lifecycle_status: str = "v0_active"


def _hypothesis(key: str, dimension: HypothesisDimension) -> HypothesisDefinition:
    label = key.rsplit(".", 1)[-1].replace("_", " ")
    return HypothesisDefinition(
        hypothesis_key=key,
        definition_version=1,
        dimension=dimension,
        description=f"Deterministic V0 hypothesis for {label}.",
        compatible_with=HYPOTHESIS_COMPATIBILITIES.get(key, ()),
        conflicts_with=HYPOTHESIS_CONFLICTS.get(key, ()),
        rule_references=(f"{key}.rules-v0.1",),
    )


HYPOTHESIS_MANIFEST = tuple(
    _hypothesis(key, dimension)
    for dimension in HypothesisDimension
    for key in HYPOTHESIS_KEYS_BY_DIMENSION[dimension]
)


class PredicateDefinition(FrozenModel):
    predicate: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    # Offline/reference documentation of the surface forms a predicate covers.
    lexical_or_structural_patterns: tuple[str, ...] = Field(min_length=1)
    allowed_arguments: tuple[str, ...]
    required_arguments: tuple[str, ...] = ()
    consequence_band: str
    reversibility: str
    capability_key: str | None = None
    # What the semantic model is told.  Never user text or fixture wording.
    description: str = Field(min_length=1)
    argument_descriptions: Mapping[str, str] = Field(default_factory=dict)
    # Closed vocabularies; a non-null value outside one rejects the result.
    argument_values: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    # Generic predicates ground their act directly (frame dominance); legacy
    # design-case predicates leave this unset and keep cue-driven rules.
    act_family: ActFamily | None = None
    # Only predicates with a reviewed model-facing definition are offered to
    # the semantic model; narrow design-case predicates stay offline-only.
    model_exposed: bool = False
    # Arguments that name a referent: a bare pronoun or deictic value is not
    # a resolved referent and compiles to null (an explicit unknown).
    referent_arguments: tuple[str, ...] = ()
    # Registry v0.4: the closed requested-outcome class of a request-bearing
    # predicate (``None``: the predicate requests nothing, e.g. a statement).
    requested_outcome: RequestedOutcome | None = None


def _predicate(
    name: str,
    patterns: tuple[str, ...],
    arguments: tuple[str, ...],
    required: tuple[str, ...] = (),
    *,
    consequence: str = "low",
    reversibility: str = "reversible",
    capability: str | None = None,
    description: str | None = None,
    argument_descriptions: Mapping[str, str] | None = None,
    argument_values: Mapping[str, tuple[str, ...]] | None = None,
    act: ActFamily | None = None,
    referents: tuple[str, ...] = (),
    outcome: RequestedOutcome | None = None,
) -> PredicateDefinition:
    return PredicateDefinition(
        predicate=name, definition_version=1,
        lexical_or_structural_patterns=patterns,
        allowed_arguments=arguments, required_arguments=required,
        consequence_band=consequence, reversibility=reversibility,
        capability_key=capability,
        description=description or (
            "Reviewed design-case predicate; select only when the focus message expresses exactly "
            f"this situation ({', '.join(patterns)})."
        ),
        argument_descriptions=argument_descriptions or {},
        argument_values=argument_values or {},
        act_family=act,
        model_exposed=description is not None,
        referent_arguments=referents,
        requested_outcome=outcome,
    )


_EMBEDDING = (
    "Optional. How the speaker relates to this content: direct (the speaker's own act), quoted "
    "(words inside quotation marks), reported (what someone else said or asked), or hypothetical "
    "(an imagined or conditional case, e.g. the X in 'if I asked you to X' or 'what if you X'). "
    "Omit or use direct for the speaker's own act."
)
_REFERENT_RULE = (
    " Use null when it is only a pronoun or deictic word (it, that, this one, the second one) "
    "whose referent is not stated in the supplied messages."
)

_LEGACY_PREDICATES = (
    _predicate("museum_closing_time", ("museum close",), ("museum",), ("museum",)),
    _predicate("send_report", ("send the report", "sent the report"), ("agent", "object", "recipient"), ("object",), capability="send_report", outcome=RequestedOutcome.EXTERNAL_EFFECT),
    _predicate("appointment_date", ("appointment", "Tuesday", "Thursday"), ("accepted", "rejected"), ("accepted", "rejected")),
    _predicate("accept_offer", ("yes", "no thanks"), ("offer",), ("offer",)),
    _predicate("submit_form", ("submitted",), ("agent", "object"), ("agent", "object")),
    _predicate(
        "acknowledge_message", ("okay", "ok"), ("message_id",),
        description="The speaker acknowledges or receipts a previous message (ok, okay, got it, sure).",
        argument_descriptions={"message_id": "Optional message_ref of the acknowledged message."},
        act=ActFamily.ACKNOWLEDGE, outcome=RequestedOutcome.SOCIAL_EXCHANGE,
    ),
    _predicate("evaluate_meal", ("soup", "service"), ("soup", "service"), ("soup", "service")),
    _predicate("weather_condition", ("raining",), ("condition", "location"), ("condition", "location")),
    _predicate(
        "social_opening", ("hello", "hey", "heyy"), ("addressee",),
        description="A conventional greeting that opens or re-opens contact (hello, hi, good morning).",
        argument_descriptions={"addressee": "Optional name or term the greeting is addressed to."},
        act=ActFamily.GREET, outcome=RequestedOutcome.SOCIAL_EXCHANGE,
    ),
    _predicate(
        "social_closing", ("goodbye", "bye"), ("future_contact",),
        description="A conventional closing or farewell (bye, see you, good night).",
        argument_descriptions={"future_contact": "Optional stated future contact, e.g. 'tomorrow'."},
        act=ActFamily.CLOSE, outcome=RequestedOutcome.SOCIAL_EXCHANGE,
    ),
    _predicate("train_departure_time", ("train leaves",), ("train", "time"), ("train", "time")),
    _predicate("bridge_closed", ("bridge is closed",), ("speaker", "bridge"), ("bridge",)),
    _predicate("contingency_action", ("what should",), ("condition", "action"), ("condition", "action")),
    _predicate("stop_timer", ("stop", "timer"), ("timer",), ("timer",), capability="stop_timer", outcome=RequestedOutcome.EXTERNAL_EFFECT),
    _predicate("room_temperature_change", ("getting cold",), ("direction", "location"), ("direction",)),
    _predicate("close_window", ("window", "cold"), ("window",), ("window",), capability="close_window", outcome=RequestedOutcome.EXTERNAL_EFFECT),
    _predicate("evaluate_export_event", ("broken export",), ("event", "positive_phrase"), ("event",)),
    _predicate("shared_progress", ("we did", "progress"), ("participants", "time"), ("participants",)),
    _predicate("capability_available", ("cannot export", "can't export"), ("capability", "claimed_value"), ("capability", "claimed_value")),
    _predicate("delete_database", ("delete", "database"), ("database", "authorization"), ("database", "authorization"), consequence="high", reversibility="irreversible", capability="delete_database", outcome=RequestedOutcome.EXTERNAL_EFFECT),
    _predicate("summarize_attachment", ("summarize", "invoice"), ("attachment_id", "object"), ("attachment_id", "object"), capability="summarize_attachment", outcome=RequestedOutcome.EXTERNAL_EFFECT),
    _predicate(
        "information_request", ("interrogative clause",), ("topic_ref", "question_form", "embedding"),
        ("topic_ref", "question_form"),
        description=(
            "The speaker asks for information, advice, or an explanation of general knowledge (including "
            "'tell me', 'give me the steps/order/list'). Do not use it for the assistant's name or identity "
            "(identity_request), the current date or time (runtime_fact_request), a follow-up about something "
            "already said (elaboration_request), a social 'how are you' (social_question), or a question whose "
            "answer is a piece of shared work the assistant should produce or examine in this conversation, such "
            "as proposing a plan or idea, or explaining, evaluating, describing, or summarizing the plan, draft, "
            "or idea being developed together (collaborative_content_request)."
        ),
        argument_descriptions={
            "topic_ref": "Short noun phrase naming what is asked about, copied or condensed from the focus message." + _REFERENT_RULE,
            "question_form": (
                "wh (who/what/when/where/why/how), polar (yes/no), elliptical (what about X?), or "
                "imperative (tell me, explain)."
            ),
            "embedding": _EMBEDDING,
        },
        argument_values={
            "question_form": ("wh", "polar", "elliptical", "imperative"), "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.REQUEST_INFORMATION,
        referents=("topic_ref",), outcome=RequestedOutcome.INFORMATION_ANSWER,
    ),
    _predicate(
        "unknown", ("unregistered predicate",), ("surface_action", "object_reference", "predicate"), (),
        description=(
            "Use only when no other predicate describes a part of the focus message, such as fillers or "
            "fragments with no recoverable meaning. Never use it for greetings, questions, requests, "
            "corrections, statements, constraints, or thanks."
        ),
        argument_descriptions={
            "predicate": "Optional short snake_case label for the unregistered meaning.",
            "surface_action": "Optional main verb phrase.",
            "object_reference": "Optional object phrase.",
        },
    ),
)

_COMPOSITIONAL_PREDICATES = (
    _predicate(
        "social_question", ("how are you",), ("topic_ref", "addressee"), ("topic_ref",),
        description=(
            "Only a conventional social pleasantry about the assistant's own state (how are you, how's it "
            "going, how's your day). Never a factual, identity, advice, or task question."
        ),
        argument_descriptions={
            "topic_ref": "What is asked about, e.g. 'wellbeing' or 'day'.",
            "addressee": "Optional name or term of address.",
        },
        act=ActFamily.SOCIAL_QUESTION, outcome=RequestedOutcome.SOCIAL_EXCHANGE,
    ),
    _predicate(
        "identity_request", ("what is your name", "who are you"), ("identity_attribute", "about", "embedding"),
        ("identity_attribute", "about"),
        description=(
            "The speaker asks about the assistant's (or their own) identity: name, what it is, who made it, "
            "or what it can do. Use this, not information_request, for 'what's your name' or 'who are you'."
        ),
        argument_descriptions={
            "identity_attribute": "name, nature, creator, capabilities, or other.",
            "about": "assistant or user.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "identity_attribute": ("name", "nature", "creator", "capabilities", "other"),
            "about": ("assistant", "user"), "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.REQUEST_INFORMATION, outcome=RequestedOutcome.INFORMATION_ANSWER,
    ),
    _predicate(
        "runtime_fact_request", ("what is today's date", "what time is it"), ("fact_kind", "embedding"), ("fact_kind",),
        description=(
            "The speaker asks for a fact about the present moment: today's date, the current time, weekday, "
            "or time zone. Use this, not information_request, for any 'what's today's date / what time is it'."
        ),
        argument_descriptions={
            "fact_kind": "current_date, current_time, current_weekday, time_zone, or other.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "fact_kind": ("current_date", "current_time", "current_weekday", "time_zone", "other"),
            "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.REQUEST_INFORMATION, outcome=RequestedOutcome.INFORMATION_ANSWER,
    ),
    _predicate(
        "elaboration_request", ("expand on that", "give an example"), ("referent_phrase", "elaboration_kind", "embedding"),
        ("referent_phrase", "elaboration_kind"),
        description="A follow-up asking to expand, clarify, give an example of, or continue something already said.",
        argument_descriptions={
            "referent_phrase": "What should be elaborated, as stated in the messages." + _REFERENT_RULE,
            "elaboration_kind": "example, detail, explanation, continuation, or summary.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "elaboration_kind": ("example", "detail", "explanation", "continuation", "summary"),
            "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.REQUEST_INFORMATION,
        referents=("referent_phrase",), outcome=RequestedOutcome.INFORMATION_ANSWER,
    ),
    _predicate(
        "collaborative_content_request", ("plan a dinner in this chat", "revise our idea", "summarize our conversation"),
        ("operation", "artifact_type", "target_ref", "constraints", "requested_form", "outcome_certainty", "embedding"),
        ("operation", "artifact_type"),
        description=(
            "The speaker asks the assistant to produce or work on content that is delivered inside this "
            "conversation: create (plan, draft, propose, suggest, list, outline), revise (change, update, "
            "adapt, make it X), explain or evaluate (why does it work, is it good, is anything worth changing), "
            "describe, compare, or summarize a plan, draft, idea, list, or the conversation itself -- whether "
            "phrased as a command, a polite request, or a question such as 'what X would work for Y?'. The "
            "result is words in this chat, so no tool, app, or outside system is needed: drafting an email, "
            "making a checklist to copy, or suggesting calendar times is this predicate. Use action_request "
            "instead only when the speaker wants something changed outside this conversation (send, order, "
            "book, buy, create an event or reminder in an app, export a file, edit an online record). Use one "
            "frame per requested operation."
        ),
        argument_descriptions={
            "operation": (
                "create (new content), revise (change existing shared content), explain (why or how it works "
                "or fits), evaluate (assess it, or whether anything should change), describe (present it in a "
                "requested style), compare (weigh alternatives), or summarize (condense it, or the conversation)."
            ),
            "artifact_type": (
                "meal_plan (a meal, menu, or dinner idea), plan, draft_message (an email, text, or note to "
                "send later), outline, checklist, comparison, summary, list, or other."
            ),
            "target_ref": (
                "Optional. The existing shared content the operation applies to, as stated (e.g. 'our dinner "
                "idea', 'the chickpea stew', 'our conversation'). Omit it for create." + _REFERENT_RULE
            ),
            "constraints": (
                "Optional list of short requirements the speaker states for this content in the focus message, "
                "e.g. ['vegetarian', 'six people', 'under 30 minutes', 'dairy-free', 'add an inexpensive side']."
            ),
            "requested_form": "Optional presentation requested: list, paragraph, steps, brief, detailed, or inviting.",
            "outcome_certainty": (
                "clear when the content is plainly wanted inside the chat; ambiguous_external_effect only when "
                "the speaker may instead want a real change outside the conversation and nothing settles which."
            ),
            "embedding": _EMBEDDING,
        },
        argument_values={
            "operation": COLLABORATIVE_OPERATIONS, "artifact_type": ARTIFACT_TYPES,
            "requested_form": REQUESTED_FORMS, "outcome_certainty": OUTCOME_CERTAINTY,
            "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.REQUEST_CONTENT,
        referents=("target_ref",), outcome=RequestedOutcome.CONVERSATIONAL_ARTIFACT,
    ),
    _predicate(
        "action_request", ("create a reminder", "check it"), ("requested_effect", "subject_ref", "parameters", "embedding"),
        ("requested_effect", "subject_ref"),
        description=(
            "The speaker asks the assistant to cause an effect outside this conversation: send, order, book, "
            "buy, check an external account, create or change a reminder, event, file, setting, or record in "
            "an app or service, or delete something -- including polite forms such as 'can you ...' and 'I need "
            "you to ...'. Content the assistant writes in the chat (a plan, draft, list, summary, explanation) "
            "is collaborative_content_request, never this. Use one frame per requested action. Use negative "
            "polarity for a request that something not be done or be stopped (don't send, stop, never include). "
            "Selecting this never means the action is possible or authorized."
        ),
        argument_descriptions={
            "requested_effect": "Short verb phrase for the requested effect, e.g. 'create reminder'.",
            "subject_ref": "The object the action applies to." + _REFERENT_RULE,
            "parameters": "Optional list of short stated details (times, quantities, constraints).",
            "embedding": _EMBEDDING,
        },
        argument_values={"embedding": EMBEDDING_VALUES},
        act=ActFamily.REQUEST_ACTION,
        referents=("subject_ref",), outcome=RequestedOutcome.EXTERNAL_EFFECT,
    ),
    _predicate(
        "unsafe_request", ("steal a password",), ("harm_category", "requested_effect", "embedding"),
        ("harm_category", "requested_effect"),
        description=(
            "The speaker seeks help to harm people, break the law, gain unauthorized access, steal, or "
            "endanger themselves or others, in any form: a command, 'can you', or a 'how do I / how can I' "
            "question. Always use this instead of action_request or information_request for that part."
        ),
        argument_descriptions={
            "harm_category": "unauthorized_access, credential_theft, violence, self_harm, weapons, fraud, or other.",
            "requested_effect": "Short verb phrase for what is requested.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "harm_category": (
                "unauthorized_access", "credential_theft", "violence", "self_harm", "weapons", "fraud", "other",
            ),
            "embedding": EMBEDDING_VALUES,
        },
        consequence="high", reversibility="irreversible",
        act=ActFamily.REQUEST_ACTION, outcome=RequestedOutcome.EXTERNAL_EFFECT,
    ),
    _predicate(
        "correction", ("actually", "change it to"), ("corrected_attribute", "corrected_value", "prior_value", "embedding"),
        ("corrected_attribute", "corrected_value"),
        description="The speaker corrects or changes something said earlier (actually, I meant, make it X instead of Y).",
        argument_descriptions={
            "corrected_attribute": "What is being corrected, e.g. 'number of people'.",
            "corrected_value": "The new value stated by the speaker.",
            "prior_value": "Optional previous value, only when stated in the messages.",
            "embedding": _EMBEDDING,
        },
        argument_values={"embedding": EMBEDDING_VALUES},
        act=ActFamily.CORRECT,
    ),
    _predicate(
        "statement", ("I think", "it is"), ("proposition", "subject", "embedding"), ("proposition",),
        description=(
            "The speaker states a fact, opinion, or piece of information that is not a request, correction, "
            "or constraint. The claim is attributed to the speaker, never accepted as true."
        ),
        argument_descriptions={
            "proposition": "Short paraphrase of what is stated.",
            "subject": "Optional subject the statement is about.",
            "embedding": _EMBEDDING,
        },
        argument_values={"embedding": EMBEDDING_VALUES},
        act=ActFamily.INFORM,
    ),
    _predicate(
        "constraint_statement", ("under 30 minutes", "peanut allergy"), ("constraint", "constraint_kind", "applies_to", "embedding"),
        ("constraint", "constraint_kind"),
        description=(
            "The speaker states a requirement, preference, limit, or condition for the current task "
            "(must take under 30 minutes, no dairy, let's use chickpeas, keep it cheap)."
        ),
        argument_descriptions={
            "constraint": "Short paraphrase of the constraint, e.g. 'no peanuts'.",
            "constraint_kind": "dietary, allergy, time, quantity, budget, preference, or other.",
            "applies_to": "Optional task or item the constraint applies to.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "constraint_kind": ("dietary", "allergy", "time", "quantity", "budget", "preference", "other"),
            "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.INFORM,
    ),
    _predicate(
        "express_gratitude", ("thanks", "thank you"), ("addressee",),
        description="The speaker thanks the assistant.",
        argument_descriptions={"addressee": "Optional name or term of address."},
        act=ActFamily.THANK, outcome=RequestedOutcome.SOCIAL_EXCHANGE,
    ),
    # Offered only while the runtime supplies open inquiries; the compiler
    # accepts an ``inquiry_ref`` only from that exact supplied set.
    _predicate(
        "inquiry_answer", ("it's an allergy", "a preference"), ("inquiry_ref", "answer_kind", "answer_option", "answer_text"),
        ("inquiry_ref", "answer_kind"),
        description=(
            "The speaker answers one of the supplied open_inquiries (a question the assistant asked earlier "
            "whose answer is still awaited), directly or in passing. Use it beside any other frame for the "
            "same words when they also add a constraint or request. Never use it unless open_inquiries lists "
            "the question being answered."
        ),
        argument_descriptions={
            "inquiry_ref": "The inquiry_ref of the supplied open inquiry being answered.",
            "answer_kind": (
                "option when the answer selects one of the inquiry's answer_options, free_text otherwise, or "
                "declined when the speaker refuses or dismisses the question."
            ),
            "answer_option": "For answer_kind option: the selected value from that inquiry's answer_options.",
            "answer_text": "For answer_kind free_text: a short paraphrase of the answer.",
        },
        argument_values={"answer_kind": INQUIRY_ANSWER_KINDS},
        act=ActFamily.ANSWER_INQUIRY,
    ),
)

# Closed affect categories for an experience report (cognitive two-call v1.1)
# and the valence each implies.  Categories classify the speaker's own words;
# the exact words stay a source span.
AFFECT_VALENCE = {
    "overwhelmed": "negative", "stressed": "negative", "anxious": "negative", "worried": "negative",
    "sad": "negative", "lonely": "negative", "hurt": "negative", "frustrated": "negative", "angry": "negative",
    "tired": "negative", "scattered": "negative", "confused": "negative", "bored": "negative",
    "disappointed": "negative", "guilty": "negative", "embarrassed": "negative",
    "happy": "positive", "excited": "positive", "relieved": "positive", "proud": "positive",
    "grateful": "positive", "calm": "positive", "hopeful": "positive", "content": "positive",
    "mixed": "mixed", "other": "unknown",
}

# Offered to the semantic model only by cognitive two-call v1.1 inputs; every
# earlier payload (staged, compact v0.1, two-call v1) is byte-identical.
_V1_PREDICATES = (
    _predicate(
        "experience_report", ("I've been feeling", "I feel"),
        ("experience", "feeling", "affect", "concern", "experiencer", "embedding"),
        ("experience", "experiencer"),
        description=(
            "The speaker shares something they are going through or feeling (I've been feeling scattered, "
            "today was exhausting, I'm nervous about my exam, I finally finished it and I'm proud). Use it "
            "beside statement or a request frame for the same words when they also state a fact or ask for "
            "something. Not for a feeling about the assistant's last reply (use correction or statement)."
        ),
        argument_descriptions={
            "experience": "What the speaker is going through, as stated.",
            "feeling": "Optional exact feeling words, e.g. 'scattered', 'nervous'.",
            "affect": "Closest affect category for the stated feeling; other when none fits.",
            "concern": "Optional thing that worries or burdens the speaker, as stated.",
            "experiencer": "speaker when it is the speaker's own experience; other for someone else's.",
            "embedding": _EMBEDDING,
        },
        argument_values={
            "affect": tuple(AFFECT_VALENCE), "experiencer": ("speaker", "other"), "embedding": EMBEDDING_VALUES,
        },
        act=ActFamily.INFORM,
    ),
    # v1.2: wanting to talk, not to receive a produced artifact.
    _predicate(
        "conversation_request", ("let's talk about", "what do you think"),
        ("topic", "mode", "embedding"), ("mode",),
        description=(
            "The speaker wants to talk something through, think out loud, vent, or hear the assistant's view "
            "in conversation -- not to receive a produced plan, list, draft, summary or other content (I just "
            "want to talk through what matters most, what do you think?, can we talk about my week). Use "
            "collaborative_content_request only when they ask for content to be made."
        ),
        argument_descriptions={
            "topic": "What they want to talk about, as stated." + _REFERENT_RULE,
            "mode": "talk_through, perspective (they ask what the assistant thinks), vent, or other.",
            "embedding": _EMBEDDING,
        },
        argument_values={"mode": ("talk_through", "perspective", "vent", "other"), "embedding": EMBEDDING_VALUES},
        act=ActFamily.INFORM, referents=("topic",),
    ),
)
V1_ONLY_MODEL_PREDICATES = frozenset(item.predicate for item in _V1_PREDICATES)
# The cognitive two-call registry version that introduced each v1-only predicate.
V1_PREDICATES_BY_REGISTRY = {
    "argument-kind-registry-v1": frozenset(),
    "argument-kind-registry-v1.1": frozenset({"experience_report"}),
    "argument-kind-registry-v1.2": frozenset({"experience_report", "conversation_request"}),
}

PREDICATE_MANIFEST = _LEGACY_PREDICATES + _COMPOSITIONAL_PREDICATES + _V1_PREDICATES
# Covered by the model regression corpus rather than the reviewed design
# fixture, which predates them and remains draft design evidence.
COMPOSITIONAL_PREDICATES = tuple(item.predicate for item in _COMPOSITIONAL_PREDICATES)
PREDICATES = tuple(item.predicate for item in PREDICATE_MANIFEST)
# Predicates offered to the semantic model only while the runtime supplies the
# typed records they link to (registry v0.4: open inquiries).
CONDITIONAL_MODEL_PREDICATES = frozenset({"inquiry_answer"})
# What the semantic model is offered: specific compositional predicates first,
# then the generic reviewed ones, and ``unknown`` last.
MODEL_PREDICATE_MANIFEST = tuple(
    sorted(
        (item for item in PREDICATE_MANIFEST if item.model_exposed),
        key=lambda item: (
            item.predicate != "unsafe_request",
            item.predicate == "unknown", item.predicate == "information_request",
            item.predicate == "social_question", item.predicate not in COMPOSITIONAL_PREDICATES,
        ),
    )
)


ACTIVE_PERCEPTION_FEATURES = (
    "conversation.question_form", "conversation.explicit_action_request",
    "conversation.explicit_correction", "conversation.repetition",
    "linguistic.negation", "linguistic.uncertainty_marker",
    "linguistic.self_reference", "runtime.attachment_present", "runtime.tool_failure",
)


def validate_hypothesis_manifest(
    definitions: Iterable[HypothesisDefinition] = HYPOTHESIS_MANIFEST,
) -> tuple[HypothesisDefinition, ...]:
    items = tuple(definitions)
    keys = tuple(item.hypothesis_key for item in items)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate interpretation hypothesis key")
    known = set(keys)
    for item in items:
        if item.lifecycle_status != "v0_active":
            raise ValueError("Interpretation V0 accepts only active hypothesis definitions")
        if item.hypothesis_key not in HYPOTHESIS_KEYS_BY_DIMENSION[item.dimension]:
            raise ValueError("hypothesis definition is assigned to the wrong dimension")
        if item.hypothesis_key in item.conflicts_with:
            raise ValueError("hypothesis cannot conflict with itself")
        if set(item.compatible_with) & set(item.conflicts_with):
            raise ValueError("hypothesis cannot be both compatible and conflicting")
        if not set((*item.compatible_with, *item.conflicts_with)) <= known:
            raise ValueError("hypothesis compatibility reference is unknown")
    return items


def validate_predicate_manifest(
    definitions: Iterable[PredicateDefinition] = PREDICATE_MANIFEST,
) -> tuple[PredicateDefinition, ...]:
    items = tuple(definitions)
    names = tuple(item.predicate for item in items)
    if len(names) != len(set(names)):
        raise ValueError("duplicate interpretation predicate")
    for item in items:
        if not set(item.required_arguments) <= set(item.allowed_arguments):
            raise ValueError(f"predicate {item.predicate} requires a disallowed argument")
        if not set(item.argument_descriptions) <= set(item.allowed_arguments):
            raise ValueError(f"predicate {item.predicate} describes a disallowed argument")
        if not set(item.argument_values) <= set(item.allowed_arguments):
            raise ValueError(f"predicate {item.predicate} constrains a disallowed argument")
    return items


def validate_semantic_frames(frames: Iterable[SemanticFrame]) -> tuple[SemanticFrame, ...]:
    items = tuple(frames)
    definitions = {item.predicate: item for item in PREDICATE_MANIFEST}
    for frame in items:
        definition = definitions.get(frame.predicate)
        if definition is None:
            raise ValueError("semantic grounder emitted an unregistered predicate")
        argument_keys = set(frame.arguments)
        if not argument_keys <= set(definition.allowed_arguments):
            raise ValueError(f"semantic frame {frame.predicate} emitted a disallowed argument")
        if not set(definition.required_arguments) <= argument_keys:
            raise ValueError(f"semantic frame {frame.predicate} omitted a required argument")
        for name, allowed in definition.argument_values.items():
            value = frame.arguments.get(name)
            if value is not None and value not in allowed:
                raise ValueError(f"semantic frame {frame.predicate} used a value outside a closed vocabulary")
    return items


def act_family(predicate: str) -> ActFamily | None:
    return _ACT_FAMILY.get(predicate)


def requested_outcome(predicate: str) -> RequestedOutcome | None:
    """The closed requested-outcome class of a registered predicate (registry v0.4)."""

    return _REQUESTED_OUTCOME.get(predicate)


def embedding_of(frame: SemanticFrame) -> str:
    value = frame.arguments.get("embedding")
    return value if isinstance(value, str) else "direct"


_ACT_FAMILY = {item.predicate: item.act_family for item in PREDICATE_MANIFEST if item.act_family is not None}
_REQUESTED_OUTCOME = {
    item.predicate: item.requested_outcome for item in PREDICATE_MANIFEST if item.requested_outcome is not None
}

# Historical predicate registries: stored requests that name them stay
# parseable for audit and replay reporting.  They are never interpreted
# again: Interpretation accepts only the installed registry.
_PREDICATES_V03 = tuple(
    name for name in PREDICATES if name not in {"collaborative_content_request", "inquiry_answer"}
)
HISTORICAL_PREDICATE_REGISTRIES: Mapping[str, tuple[str, ...]] = {
    "interpretation-predicates-v0.3": _PREDICATES_V03,
}


def registered_predicates(registry_version: str) -> tuple[str, ...] | None:
    """The exact predicate list of an installed or historical registry, else ``None``."""

    if registry_version == PREDICATE_REGISTRY_VERSION:
        return PREDICATES
    return HISTORICAL_PREDICATE_REGISTRIES.get(registry_version)
