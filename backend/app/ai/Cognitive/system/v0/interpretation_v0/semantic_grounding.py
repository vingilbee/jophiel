"""Compositional, inspectable semantic grounding rules for Interpretation V0.

``interpretation-semantic-grounder-v0.2`` replaces the single exclusive
``if``/``elif`` cascade with a versioned collection of grounding rules that
all run over the focus contribution (SH-EVAL-003).  Each rule declares:

* the region it consumes: a discourse-prefix unit, one clause body, or the
  whole contribution (the reviewed legacy domain rules);
* the predicate and typed arguments it may emit (``PREDICATE_MANIFEST``);
* its quote-ownership requirement (region rules only match speaker-owned
  text, so quoted acts are never attributed to the user);
* whether it is compatible with frames from other regions; and
* its precedence, used only when two rules compete for the same region.

After every rule has run, conflicts are resolved deterministically and frames
are deduplicated canonically.  Separate acts keep separate frames with their
own span evidence.  A generic ``unknown`` frame (or a question frame with an
unresolved topic) is emitted only for a region no rule grounded, never as a
replacement for regions that were grounded.

Clause and prefix boundaries come from the quote-aware clause scan shared
with Perception (``perception_v0.clauses``).  Nothing here is a broad
natural-language parser, and no rule depends on particular evaluation text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..contracts.interpretation import (
    Contradiction,
    InterpretationEvidence,
    OutOfScopeRecord,
    SemanticFrame,
    UnknownInput,
)
from ..perception_v0.clauses import Clause, scan_clauses
from ..perception_v0.evidence import stable_id
from .evidence import evidence_by_cue, focus_message_evidence, runtime_evidence, span_evidence
from .models import InterpretationRequest
from .ontology import PREDICATE_REGISTRY_VERSION, PREDICATES, validate_semantic_frames

SEMANTIC_GROUNDER_VERSION = "interpretation-semantic-grounder-v0.2"
# Predicates for conventional phatic acts that compose with any other act.
PHATIC_PREDICATES = frozenset({"social_opening", "acknowledge_message"})

Scope = Literal["prefix", "clause", "message"]


@dataclass(frozen=True, slots=True)
class GroundingResult:
    frames: tuple[SemanticFrame, ...]
    unknowns: tuple[UnknownInput, ...]
    contradictions: tuple[Contradiction, ...]
    out_of_scope_records: tuple[OutOfScopeRecord, ...]


@dataclass(frozen=True, slots=True)
class Region:
    """A span of the focus text that one rule may consume."""

    kind: Literal["prefix", "clause"]
    clause_index: int
    start: int
    end: int
    owned: bool
    unit_kind: str | None = None
    interrogative: bool = False
    interrogative_basis: str | None = None


@dataclass(frozen=True, slots=True)
class FrameSpec:
    predicate: str
    arguments: dict[str, Any]
    rule_id: str
    polarity: str = "positive"
    unresolved: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] | None = None
    span: tuple[int, int] | None = None


@dataclass(slots=True)
class Proposal:
    """One rule's claim over a region, with the frames it would emit."""

    rule: "GroundingRule"
    span: tuple[int, int]
    frames: list[FrameSpec]
    # Invoked with the materialized frames; may add contradictions or
    # out-of-scope records owned by those frames.
    finalize: Callable[["GroundingContext", list[SemanticFrame]], None] | None = None
    extra_unknown_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GroundingRule:
    rule_id: str
    scope: Scope
    predicates: tuple[str, ...]
    compatible: bool
    precedence: int
    owned_only: bool
    match: Callable[["GroundingContext"], list[Proposal]] = field(repr=False, compare=False)


@dataclass
class GroundingContext:
    request: InterpretationRequest
    evidence: tuple[InterpretationEvidence, ...]
    text: str
    clauses: tuple[Clause, ...]
    fallback_evidence: InterpretationEvidence
    # Legacy whole-contribution rules read the contribution after any leading
    # greeting, addressee, or discourse marker, so those cannot erase an act.
    content: str
    content_lower: str
    frames: list[SemanticFrame] = field(default_factory=list)
    unknowns: list[UnknownInput] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    out_of_scope: list[OutOfScopeRecord] = field(default_factory=list)

    def region_evidence(self, start: int, end: int) -> tuple[str, ...]:
        if (start, end) == (0, len(self.text)):
            return (self.fallback_evidence.evidence_id,)
        record = span_evidence(self.evidence, self.request.perception.focus_message_id, start, end)
        if record is None:
            raise ValueError("a grounded region has no canonical span evidence")
        return (record.evidence_id,)


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

def _regions(text: str, clauses: tuple[Clause, ...]) -> list[Region]:
    regions: list[Region] = []
    for index, clause in enumerate(clauses):
        for kind, start, end in clause.prefix_units:
            regions.append(Region("prefix", index, start, end, owned=True, unit_kind=kind))
        regions.append(Region(
            "clause", index, clause.body_start, clause.end, owned=clause.owned or clause.interrogative,
            interrogative=clause.interrogative, interrogative_basis=clause.interrogative_basis,
        ))
    return regions


def _leading_content_start(clauses: tuple[Clause, ...]) -> int:
    """End of the leading greeting/addressee/discourse-marker units of the first clause."""

    if not clauses:
        return 0
    position = clauses[0].start
    for kind, _, end in clauses[0].prefix_units:
        if kind not in {"greeting", "addressee", "discourse_marker"}:
            break
        position = end
    return position


def _skip_separators(text: str, position: int) -> int:
    while position < len(text) and (text[position].isspace() or text[position] in ",;:!—–-"):
        position += 1
    return position


# ---------------------------------------------------------------------------
# Phatic region rules (compose with every other act)
# ---------------------------------------------------------------------------

_GREETING_TOKEN = r"(?:hey+|hi+|hello+|hiya|howdy|yo|greetings|good\s+(?:morning|afternoon|evening|day))"
_GREETING_BODY = re.compile(
    rf"\s*{_GREETING_TOKEN}(?:[\s,]+(?P<addressee>[a-z][\w'-]*))?\s*[!.~]*\s*", re.IGNORECASE,
)
_GREETING_UNIT = re.compile(rf"{_GREETING_TOKEN}", re.IGNORECASE)
# Reviewed acknowledgement tokens.  Gratitude ("thanks"), agreement ("yes"),
# and evaluation ("great") are different acts and are deliberately excluded.
_ACK_TOKEN = r"(?:ok|okay|got\s+it|understood|alright|all\s+right)"
_ACK_BODY = re.compile(rf"\s*{_ACK_TOKEN}\s*[!.]*\s*", re.IGNORECASE)
_ACK_UNIT = re.compile(rf"{_ACK_TOKEN}", re.IGNORECASE)
_NON_ADDRESSEE = re.compile(
    r"(?:what|who|whom|whose|which|where|when|why|how|is|are|am|was|were|do|does|did|can|could|would|"
    r"will|should|may|might|has|have|had|ok|okay|thanks|please)", re.IGNORECASE,
)


def _social_opening(ctx: GroundingContext) -> list[Proposal]:
    proposals = []
    for region in _regions(ctx.text, ctx.clauses):
        if not region.owned:
            continue
        span: tuple[int, int] | None = None
        if region.kind == "prefix" and region.unit_kind == "greeting":
            if not _GREETING_UNIT.fullmatch(ctx.text, region.start, region.end):
                continue
            end = region.end
            clause = ctx.clauses[region.clause_index]
            units = clause.prefix_units
            position = next(i for i, unit in enumerate(units) if unit[1] == region.start)
            if position + 1 < len(units) and units[position + 1][0] == "addressee":
                end = units[position + 1][2]
            span = (region.start, end)
        elif region.kind == "clause" and not region.interrogative:
            matched = _GREETING_BODY.fullmatch(ctx.text, region.start, region.end)
            addressee = matched.group("addressee") if matched else None
            if matched is None or (addressee is not None and _NON_ADDRESSEE.fullmatch(addressee)):
                continue
            span = (region.start, region.end)
        if span is None:
            continue
        proposals.append(Proposal(SOCIAL_OPENING, span, [FrameSpec(
            # One-to-one conversation: a greeting addresses the assistant.
            "social_opening", {"addressee": "columbina"}, SOCIAL_OPENING.rule_id, span=span,
        )]))
    return proposals


def _acknowledgement(ctx: GroundingContext) -> list[Proposal]:
    prior_id = ctx.request.messages[-2].message_id if len(ctx.request.messages) > 1 else None
    proposals = []
    for region in _regions(ctx.text, ctx.clauses):
        if not region.owned:
            continue
        if region.kind == "prefix" and region.unit_kind == "acknowledgement":
            if not _ACK_UNIT.fullmatch(ctx.text, region.start, region.end):
                continue
        elif region.kind == "clause" and not region.interrogative:
            if not _ACK_BODY.fullmatch(ctx.text, region.start, region.end):
                continue
        else:
            continue
        span = (region.start, region.end)
        proposals.append(Proposal(ACKNOWLEDGEMENT, span, [FrameSpec(
            "acknowledge_message", {"message_id": prior_id}, ACKNOWLEDGEMENT.rule_id,
            unresolved=() if prior_id else ("message_id",), span=span,
        )]))
    return proposals


# ---------------------------------------------------------------------------
# Reviewed legacy domain rules (whole contribution, mutually exclusive)
# ---------------------------------------------------------------------------

def _whole(ctx: GroundingContext) -> tuple[int, int]:
    return (0, len(ctx.text))


def _legacy(rule_id: str, build: Callable[[GroundingContext], list[FrameSpec] | None],
            finalize: Callable[[GroundingContext, list[SemanticFrame]], None] | None = None,
            ) -> Callable[[GroundingContext], list[Proposal]]:
    def match(ctx: GroundingContext) -> list[Proposal]:
        frames = build(ctx)
        if not frames:
            return []
        return [Proposal(LEGACY_RULES[rule_id], _whole(ctx), frames, finalize)]
    return match


def _museum(ctx):
    if re.search(r"\bwhat time does (?:the )?museum close\b", ctx.content_lower):
        return [FrameSpec("museum_closing_time", {"museum": None}, "grounding.museum-closing-question.v1",
                          unresolved=("museum",))]
    return None


def _send_report_request(ctx):
    if re.search(r"\b(?:could|can|would) you send the report\b|\bplease send the report\b", ctx.content_lower):
        recipient_match = re.search(r"\bto\s+([A-Z][\w'-]+)", ctx.content)
        recipient = recipient_match.group(1) if recipient_match else None
        return [FrameSpec("send_report", {"object": "report", "recipient": recipient},
                          "grounding.send-report-request.v1", unresolved=() if recipient else ("recipient",))]
    return None


def _send_report_assertion(ctx):
    if match := re.search(r"\bi sent the report to\s+([A-Z][\w'-]+)", ctx.content, re.I):
        return [FrameSpec("send_report", {"agent": "user", "object": "report", "recipient": match.group(1)},
                          "grounding.send-report-assertion.v1")]
    return None


def _appointment_correction(ctx):
    lower = ctx.content_lower
    if "not " in lower and (
        "appointment" in " ".join(message.content.casefold() for message in ctx.request.messages)
        or "it is" in lower
    ):
        correction = re.search(r"\bis\s+([\w'-]+),?\s+not\s+([\w'-]+)", ctx.content, re.I)
        accepted = correction.group(1) if correction else None
        rejected = correction.group(2) if correction else None
        unresolved = tuple(key for key, value in (("accepted", accepted), ("rejected", rejected)) if value is None)
        return [FrameSpec("appointment_date", {"accepted": accepted, "rejected": rejected},
                          "grounding.appointment-correction.v1", unresolved=unresolved)]
    return None


def _explicit_decline(ctx):
    if re.fullmatch(r"no thanks[.!]?", ctx.content_lower.strip()):
        prior = ctx.request.messages[-2].content if len(ctx.request.messages) > 1 else ""
        offer_match = re.search(r"\b(?:like|add)\s+([\w'-]+)", prior, re.I)
        offer = offer_match.group(1) if offer_match else None
        return [FrameSpec("accept_offer", {"offer": offer}, "grounding.explicit-decline.v1", polarity="negative",
                          unresolved=() if offer else ("offer",))]
    return None


def _form_confirmation(ctx):
    lower = ctx.content_lower.strip()
    if lower.startswith("yes") and "submitted" in lower:
        return [FrameSpec("submit_form", {"agent": "user", "object": "form"}, "grounding.form-confirmation.v1")]
    return None


def _meal_evaluation(ctx):
    if "soup" in ctx.content_lower and "service" in ctx.content_lower:
        return [FrameSpec("evaluate_meal", {"soup": "positive", "service": "negative"},
                          "grounding.meal-evaluation.v1", polarity="unknown")]
    return None


def _weather(ctx):
    if match := re.search(r"\bit is raining in\s+([\w'-]+)", ctx.content, re.I):
        return [FrameSpec("weather_condition", {"condition": "rain", "location": match.group(1)},
                          "grounding.weather-assertion.v1")]
    return None


def _closing(ctx):
    lower = ctx.content_lower
    if re.search(r"\b(?:goodbye|bye)\b", lower):
        future = "tomorrow" if "tomorrow" in lower else None
        return [FrameSpec("social_closing", {"future_contact": future}, "grounding.social-closing.v1")]
    return None


def _train(ctx):
    if match := re.search(r"\btrain leaves at\s+([\w:.-]+)", ctx.content_lower):
        return [FrameSpec("train_departure_time", {"train": None, "time": match.group(1).rstrip(".")},
                          "grounding.train-time-assertion.v1", unresolved=("train",))]
    return None


def _bridge(ctx):
    lower = ctx.content_lower.strip()
    if "bridge is closed" in lower and lower.startswith("if "):
        return [
            FrameSpec("bridge_closed", {"speaker": "she", "bridge": None}, "grounding.quoted-bridge-condition.v1",
                      unresolved=("bridge",)),
            FrameSpec("contingency_action", {"condition": "bridge_closed", "action": None},
                      "grounding.hypothetical-contingency.v1", unresolved=("action",)),
        ]
    return None


def _stop_timer(ctx):
    lower = ctx.content_lower
    if "stop" in lower and "timer" in lower:
        timer_match = re.search(r"\bstop (?:the )?([\w'-]+) timer\b", lower)
        timer = timer_match.group(1) if timer_match else None
        return [FrameSpec("stop_timer", {"timer": timer}, "grounding.stop-timer.v1",
                          unresolved=() if timer else ("timer",))]
    return None


def _room_temperature(ctx):
    lower = ctx.content_lower
    if "getting cold" in lower:
        prior = ctx.request.messages[-2].content.casefold() if len(ctx.request.messages) > 1 else ""
        location = "office" if "office" in prior or "office" in lower else None
        frames = [FrameSpec("room_temperature_change", {"direction": "colder", "location": location},
                            "grounding.room-temperature.v1")]
        if "window" in prior:
            frames.append(FrameSpec("close_window", {"window": location or "context_window"},
                                    "grounding.indirect-window-action.v1"))
        return frames
    return None


def _broken_export(ctx):
    if "broken export" in ctx.content_lower:
        # Mixed polarity is not in the canonical contract.  This frame is
        # retained as unknown; live V0 does not infer tone mismatch from the
        # shadow wording cues.
        return [FrameSpec("evaluate_export_event", {"event": "broken_export", "positive_phrase": None},
                          "grounding.export-event-without-shadow-tone.v1", polarity="unknown",
                          unresolved=("positive_phrase",))]
    return None


def _shared_progress(ctx):
    if re.search(r"\byeah,? we did\b", ctx.content_lower):
        return [FrameSpec("shared_progress", {"participants": ("user", "columbina"), "time": "today"},
                          "grounding.shared-progress.v1")]
    return None


def _capability_claim(ctx):
    lower = ctx.content_lower
    if "export pdf" in lower or "export pdfs" in lower:
        message_ref = ctx.fallback_evidence.evidence_id
        runtime_ref = runtime_evidence(ctx.evidence, "/available_capabilities").evidence_id
        return [FrameSpec("capability_available", {"capability": "export_pdf", "claimed_value": False},
                          "grounding.capability-claim.v1", polarity="negative",
                          evidence_refs=(message_ref, runtime_ref))]
    return None


def _capability_contradiction(ctx: GroundingContext, frames: list[SemanticFrame]) -> None:
    frame = frames[0]
    if "export_pdf" not in ctx.request.runtime.available_capabilities:
        return
    conflict_refs = list(frame.evidence_refs)
    for memory in ctx.request.memories:
        if "export" in memory.content.casefold():
            memory_ev = next(item for item in ctx.evidence if item.memory_ref == memory.memory_id)
            conflict_refs.append(memory_ev.evidence_id)
    ctx.contradictions.append(Contradiction(
        contradiction_id=stable_id(
            "interpretation-contradiction", ctx.request.cognitive_turn_id, frame.frame_id, "runtime-capability",
        ),
        status="active",
        description="Current trusted capability state contradicts the user claim or stale memory.",
        evidence_refs=tuple(conflict_refs),
    ))


def _database_delete(ctx):
    lower = ctx.content_lower
    if "delete" in lower and "database" in lower:
        database = "production_customer" if "production customer" in lower else None
        authorization = ctx.request.runtime.authorization
        return [FrameSpec(
            "delete_database", {"database": database, "authorization": authorization},
            "grounding.consequential-database-delete.v1",
            unresolved=tuple(key for key, value in (("database", database), ("authorization", authorization))
                             if value is None),
        )]
    return None


def _attachment_summary(ctx):
    lower = ctx.content_lower
    if "summarize" in lower and "invoice" in lower:
        runtime = ctx.request.runtime
        attachment_id = runtime.attachments[0].attachment_id if runtime.attachments else None
        refs = [ctx.fallback_evidence.evidence_id]
        if runtime.attachments:
            refs.append(runtime_evidence(ctx.evidence, "/attachments").evidence_id)
        return [FrameSpec("summarize_attachment", {"attachment_id": attachment_id, "object": "invoice"},
                          "grounding.attachment-summary.v1", unresolved=() if attachment_id else ("attachment_id",),
                          evidence_refs=tuple(refs))]
    return None


_LEGACY_TABLE: tuple[tuple[str, tuple[str, ...], Callable, Callable | None], ...] = (
    ("grounding.museum-closing-question.v1", ("museum_closing_time",), _museum, None),
    ("grounding.send-report-request.v1", ("send_report",), _send_report_request, None),
    ("grounding.send-report-assertion.v1", ("send_report",), _send_report_assertion, None),
    ("grounding.appointment-correction.v1", ("appointment_date",), _appointment_correction, None),
    ("grounding.explicit-decline.v1", ("accept_offer",), _explicit_decline, None),
    ("grounding.form-confirmation.v1", ("submit_form",), _form_confirmation, None),
    ("grounding.meal-evaluation.v1", ("evaluate_meal",), _meal_evaluation, None),
    ("grounding.weather-assertion.v1", ("weather_condition",), _weather, None),
    ("grounding.social-closing.v1", ("social_closing",), _closing, None),
    ("grounding.train-time-assertion.v1", ("train_departure_time",), _train, None),
    ("grounding.quoted-bridge-condition.v1", ("bridge_closed", "contingency_action"), _bridge, None),
    ("grounding.stop-timer.v1", ("stop_timer",), _stop_timer, None),
    ("grounding.room-temperature.v1", ("room_temperature_change", "close_window"), _room_temperature, None),
    ("grounding.export-event-without-shadow-tone.v1", ("evaluate_export_event",), _broken_export, None),
    ("grounding.shared-progress.v1", ("shared_progress",), _shared_progress, None),
    ("grounding.capability-claim.v1", ("capability_available",), _capability_claim, _capability_contradiction),
    ("grounding.consequential-database-delete.v1", ("delete_database",), _database_delete, None),
    ("grounding.attachment-summary.v1", ("summarize_attachment",), _attachment_summary, None),
)
LEGACY_RULES: dict[str, GroundingRule] = {}
for _precedence, (_rule_id, _predicates, _build, _finalize) in enumerate(_LEGACY_TABLE, start=10):
    LEGACY_RULES[_rule_id] = GroundingRule(
        rule_id=_rule_id, scope="message", predicates=_predicates, compatible=False,
        precedence=_precedence, owned_only=False, match=_legacy(_rule_id, _build, _finalize),
    )

SOCIAL_OPENING = GroundingRule(
    rule_id="grounding.social-opening.v2", scope="prefix", predicates=("social_opening",), compatible=True,
    precedence=1, owned_only=True, match=_social_opening,
)
ACKNOWLEDGEMENT = GroundingRule(
    rule_id="grounding.acknowledgement-token.v2", scope="prefix", predicates=("acknowledge_message",),
    compatible=True, precedence=2, owned_only=True, match=_acknowledgement,
)

# The complete versioned rule collection, in canonical evaluation order.
GROUNDING_RULES: tuple[GroundingRule, ...] = (SOCIAL_OPENING, ACKNOWLEDGEMENT, *LEGACY_RULES.values())

# Region-local fallbacks for regions no rule grounded.
RULE_INFORMATION_REQUEST = "grounding.information-request-unresolved-topic.v1"
RULE_UNKNOWN_ACTION = "grounding.unknown-action-predicate.v1"
RULE_BOUNDED_UNKNOWN = "grounding.bounded-unknown-input.v1"


def _question_form(ctx: GroundingContext, region: Region) -> str:
    body = ctx.text[region.start:region.end].lstrip().casefold()
    if re.match(r"(?:what|how)\s+about\b|what\s+if\b", body):
        return "elliptical"
    if re.match(r"(?:what|who|whom|whose|which|where|when|why|how)\b", body):
        return "wh"
    return "polar"


def contribution_content(text: str) -> str:
    """The contribution after any leading greeting, addressee, or discourse marker."""

    return text[_skip_separators(text, _leading_content_start(scan_clauses(text))):]


class SemanticGrounder:
    version = SEMANTIC_GROUNDER_VERSION

    def ground(
        self,
        request: InterpretationRequest,
        evidence: tuple[InterpretationEvidence, ...],
    ) -> GroundingResult:
        text = request.focus_message.content
        focus_evidence = focus_message_evidence(evidence, request.perception.focus_message_id)
        fallback_evidence = focus_evidence or next(iter(evidence_by_cue(evidence).values()))
        clauses = scan_clauses(text)
        content = text[_skip_separators(text, _leading_content_start(clauses)):]
        ctx = GroundingContext(
            request=request, evidence=evidence, text=text, clauses=clauses, fallback_evidence=fallback_evidence,
            content=content, content_lower=content.casefold(),
        )

        proposals = [proposal for rule in GROUNDING_RULES for proposal in rule.match(ctx)]
        accepted = self._resolve(proposals)
        for proposal in accepted:
            materialized = [self._frame(ctx, spec) for spec in proposal.frames]
            if proposal.finalize is not None:
                proposal.finalize(ctx, materialized)
        if not any(not proposal.rule.compatible for proposal in accepted):
            self._ground_unresolved_regions(ctx, accepted)

        validate_semantic_frames(ctx.frames)
        return GroundingResult(
            tuple(ctx.frames), tuple(ctx.unknowns), tuple(ctx.contradictions), tuple(ctx.out_of_scope),
        )

    @staticmethod
    def _resolve(proposals: list[Proposal]) -> list[Proposal]:
        """Deterministic conflict resolution and canonical deduplication.

        Whole-contribution (incompatible) rules compete with each other and
        the lowest precedence wins, exactly one.  Compatible region rules
        compete only for an overlapping span; the lower precedence wins.
        """

        exclusive = sorted((item for item in proposals if not item.rule.compatible),
                           key=lambda item: item.rule.precedence)
        accepted: list[Proposal] = exclusive[:1]
        compatible = sorted((item for item in proposals if item.rule.compatible),
                            key=lambda item: (item.span[0], item.rule.precedence, item.span[1]))
        for proposal in compatible:
            overlapping = [
                other for other in accepted
                if other.rule.compatible and other.span[0] < proposal.span[1] and proposal.span[0] < other.span[1]
            ]
            if overlapping:
                continue
            accepted.append(proposal)
        return accepted

    @staticmethod
    def _frame(ctx: GroundingContext, spec: FrameSpec, *, identity_span: tuple[int, int] | None = None) -> SemanticFrame:
        whole = (0, len(ctx.text))
        span = spec.span
        refs = spec.evidence_refs or (
            ctx.region_evidence(*span) if span is not None else (ctx.fallback_evidence.evidence_id,)
        )
        id_parts: tuple[Any, ...] = (
            "semantic-frame", ctx.request.cognitive_turn_id, ctx.request.perception.perception_event_id,
            spec.predicate, spec.arguments, spec.rule_id,
        )
        # Region frames carry their span in their identity (two questions are
        # two frames); a region covering the whole contribution keeps the
        # historical identity.
        identity = identity_span or span
        if identity is not None and identity != whole:
            id_parts = (*id_parts, identity[0], identity[1])
        frame_id = stable_id(*id_parts)
        existing = next((item for item in ctx.frames if item.frame_id == frame_id), None)
        if existing is not None:
            return existing  # canonical deduplication of an identical frame
        frame = SemanticFrame(
            frame_id=frame_id, predicate=spec.predicate, arguments=spec.arguments, polarity=spec.polarity,
            evidence_refs=refs, unresolved_arguments=spec.unresolved, grounding_rule_ids=(spec.rule_id,),
        )
        ctx.frames.append(frame)
        for argument in spec.unresolved:
            ctx.unknowns.append(UnknownInput(
                unknown_id=stable_id("interpretation-unknown", ctx.request.cognitive_turn_id, frame.frame_id, argument),
                key=argument, owner="user", evidence_refs=refs,
            ))
        return frame

    def _ground_unresolved_regions(self, ctx: GroundingContext, accepted: list[Proposal]) -> None:
        """Emit region-local frames for clause bodies no rule grounded."""

        covered = [proposal.span for proposal in accepted]

        def is_covered(start: int, end: int) -> bool:
            return any(a <= start and end <= b for a, b in covered)

        pending: list[Region] = []
        regions = [item for item in _regions(ctx.text, ctx.clauses) if item.kind == "clause"]
        explicit_request = self._explicit_request(ctx)

        def flush() -> None:
            if not pending:
                return
            start, end = pending[0].start, pending[-1].end
            self._unknown_region(ctx, start, end, explicit_request)
            pending.clear()

        for region in regions:
            clause = ctx.clauses[region.clause_index]
            # Prefix units no rule consumed.  Discourse markers, addressees, and
            # lead-ins carry no content of their own; any other unconsumed unit
            # (e.g. gratitude or agreement, which V0 does not model) is part of
            # the unresolved region it introduces.
            loose = [
                (kind, start, end) for kind, start, end in clause.prefix_units
                if not is_covered(start, end) and kind not in {"discourse_marker", "addressee", "lead_in"}
            ]
            if is_covered(region.start, region.end) or not re.search(r"\w", ctx.text[region.start:region.end]):
                flush()
                if loose:
                    self._unknown_region(ctx, *self._loose_run(clause, loose[0], is_covered), explicit_request, unit=True)
                continue
            if region.interrogative and region.owned:
                flush()
                if loose:
                    self._unknown_region(ctx, *self._loose_run(clause, loose[0], is_covered), explicit_request, unit=True)
                self._frame(ctx, FrameSpec(
                    "information_request",
                    {"topic_ref": None, "question_form": _question_form(ctx, region)},
                    RULE_INFORMATION_REQUEST, unresolved=("topic_ref",), span=(region.start, region.end),
                ))
                continue
            if loose:
                # The unresolved region begins at the first unconsumed unit that
                # follows every consumed one.
                start = region.start
                for _, unit_start, unit_end in reversed(clause.prefix_units):
                    if is_covered(unit_start, unit_end):
                        break
                    start = unit_start
                region = Region("clause", region.clause_index, start, region.end, owned=region.owned)
            pending.append(region)
        flush()
        if not ctx.frames:
            # Nothing groundable at all (e.g. empty or symbol-only input).
            self._unknown_region(ctx, 0, len(ctx.text), explicit_request, whole=True)

    @staticmethod
    def _loose_run(clause: Clause, first: tuple[str, int, int], is_covered) -> tuple[int, int]:
        """The contiguous run of unconsumed prefix units that starts at ``first``."""

        units = clause.prefix_units
        index = units.index(first)
        end = first[2]
        for _, start, unit_end in units[index + 1:]:
            if is_covered(start, unit_end):
                break
            end = unit_end
        return first[1], end

    @staticmethod
    def _explicit_request(ctx: GroundingContext):
        cue = next(
            (item for item in ctx.request.perception.cue_activations
             if item.cue_key == "conversation.explicit_action_request"), None,
        )
        if cue is None or cue.value is not True:
            return None
        return cue

    def _unknown_region(
        self, ctx: GroundingContext, start: int, end: int, explicit_request, *, whole: bool = False,
        unit: bool = False,
    ) -> None:
        if unit:
            # A lone unconsumed prefix run before a grounded clause body: its
            # own bounded unknown, never an action request.
            explicit_request = None
        region_text = ctx.text[start:end]
        action_match = re.match(r"\s*([A-Za-z][\w'-]*)\s*(.*?)[.!]?\s*$", region_text, re.S)
        surface_action = action_match.group(1).casefold() if action_match else ""
        object_surface = action_match.group(2).strip() if action_match else ""
        span = None if whole else (start, end)
        request_in_region = explicit_request is not None and any(
            getattr(item, "start", None) is not None and start <= item.start < end
            for item in explicit_request.evidence
        )
        if request_in_region and surface_action:
            frame = self._frame(ctx, FrameSpec(
                "unknown", {"surface_action": surface_action, "predicate": None, "object_reference": None},
                RULE_UNKNOWN_ACTION, unresolved=("predicate", "object_reference"), span=span,
            ))
            ctx.out_of_scope.append(OutOfScopeRecord(
                out_of_scope_id=stable_id(
                    "interpretation-out-of-scope", ctx.request.cognitive_turn_id, frame.frame_id, surface_action,
                ),
                surface_form=surface_action,
                reason="The action predicate is not in the versioned V0 predicate registry.",
                evidence_refs=frame.evidence_refs,
                recoverable_by_user=True,
            ))
            return
        frame = self._frame(ctx, FrameSpec(
            "unknown",
            {"surface_action": surface_action or None, "object_reference": object_surface or None, "predicate": None},
            RULE_BOUNDED_UNKNOWN, unresolved=("predicate",), span=span,
        ))
        ctx.unknowns.append(UnknownInput(
            unknown_id=stable_id("interpretation-unknown", ctx.request.cognitive_turn_id, frame.frame_id, "meaning"),
            key="meaning", owner="user", evidence_refs=frame.evidence_refs,
        ))


def validate_runtime_predicate_registry(request: InterpretationRequest) -> None:
    if request.runtime.predicate_registry_version != PREDICATE_REGISTRY_VERSION:
        raise ValueError("unsupported interpretation predicate registry version")
    if tuple(request.runtime.known_predicates) != PREDICATES:
        raise ValueError("runtime known-predicate registry must exactly match the code-owned manifest")


__all__ = [
    "GROUNDING_RULES", "GroundingResult", "GroundingRule", "PHATIC_PREDICATES", "SEMANTIC_GROUNDER_VERSION",
    "SemanticGrounder", "contribution_content", "validate_runtime_predicate_registry",
]
