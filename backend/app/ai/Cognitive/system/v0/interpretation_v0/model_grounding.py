"""Deterministic compiler for untrusted semantic candidates.

Every rejection is a typed ``SemanticParserError`` so a failed parse becomes a
content-free shadow failure code, never an unexplained worker exception, and
never a partial envelope.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from ..contracts.interpretation import (
    InterpretationEvidence, InterpretationEvidenceKind, SemanticFrame, SemanticParserProvenance, UnknownInput,
)
from ..perception_v0.evidence import stable_id
from .models import InterpretationRequest
from .model_protocol import (
    SEMANTIC_MODEL_PROTOCOL_VERSION, SemanticArgumentSpec, SemanticCandidateModel, SemanticEvidenceRegion,
    SemanticModelPayload, SemanticOpenInquiry, SemanticParserError, SemanticParserErrorCode,
    SemanticParserMessage, SemanticPredicateSpec,
)
from .ontology import CONDITIONAL_MODEL_PREDICATES, MODEL_PREDICATE_MANIFEST, V1_ONLY_MODEL_PREDICATES, validate_semantic_frames
from .semantic_grounding import GroundingResult

# v0.2 compiles protocol v0.2 candidates over the model-exposed manifest,
# rejects values outside a predicate's closed vocabulary, compiles a bare
# deictic referent to null, keeps one frame per repeated identical act,
# records a ``meaning`` unknown for every ``unknown`` frame, and raises typed
# parser codes for every rejection.
# v0.3 compiles protocol v0.3 candidates: ``inquiry_answer`` is offered only
# while open inquiries are supplied, may cite only a supplied inquiry, and an
# ``option`` answer must select one of that inquiry's closed options.
MODEL_GROUNDING_COMPILER_VERSION = "interpretation-semantic-model-compiler-v0.3"

EMPTY_FOCUS_RULE = "grounding.empty-focus-bounded-unknown.v1"
E = SemanticParserErrorCode


def _canonical_argument_value(value):
    # Some strict-schema models choose the string branch of the JSON union for
    # a missing value. Only the exact, case-insensitive sentinel is normalized;
    # no other model-authored value is repaired or guessed.
    if isinstance(value, str) and value.casefold() == "null":
        return None
    return value


# A pronoun or deictic phrase names no referent by itself.  In a referent
# argument it compiles to null, so it becomes an explicit unknown for trusted
# post-parser linking instead of a resolved-looking value.  Nothing else is
# rewritten.
_BARE_DEICTIC = re.compile(
    r"(?:it|that|this|these|those|them|they|him|her|there|here|one|"
    r"(?:the\s+|that\s+|this\s+)?(?:first|second|third|last|other|next|previous|same)?\s*one|"
    r"(?:the\s+)?(?:first|second|third|last|other|next|previous|same)\s+(?:one|thing))",
    re.IGNORECASE,
)


def _is_bare_deictic(value) -> bool:
    return isinstance(value, str) and bool(_BARE_DEICTIC.fullmatch(value.strip(" \t.,;:!?'\"").strip()))


def canonical_candidate_arguments(candidate):
    """Use the semantic compiler's exact normalization when binding local handles."""
    definition = next(item for item in MODEL_PREDICATE_MANIFEST if item.predicate == candidate.predicate)
    arguments = {item.name: _canonical_argument_value(item.value) for item in candidate.arguments}
    for name in definition.referent_arguments:
        if _is_bare_deictic(arguments.get(name)):
            arguments[name] = None
    return arguments


def predicate_specs(*, inquiries: bool = False, v1_predicates=()) -> tuple[SemanticPredicateSpec, ...]:
    """The code-owned predicate manifest, projected for the model.

    Conditional predicates (``inquiry_answer``) are offered only while the
    typed records they link to are supplied.  ``v1_predicates`` names the
    cognitive two-call v1.x predicates a versioned v1 input offers; every
    other caller's payload is unchanged.
    """

    return tuple(
        SemanticPredicateSpec(
            predicate=item.predicate,
            description=item.description,
            arguments=tuple(
                SemanticArgumentSpec(
                    name=name, required=name in item.required_arguments,
                    description=item.argument_descriptions.get(name, ""),
                    allowed_values=item.argument_values.get(name, ()),
                )
                for name in item.allowed_arguments
            ),
        )
        for item in MODEL_PREDICATE_MANIFEST
        if (inquiries or item.predicate not in CONDITIONAL_MODEL_PREDICATES)
        and (item.predicate not in V1_ONLY_MODEL_PREDICATES or item.predicate in set(v1_predicates))
    )


def _validate_inquiry_answer(arguments, inquiries) -> None:
    """An answer cites exactly one supplied inquiry in a shape that inquiry admits."""

    inquiry = inquiries.get(arguments.get("inquiry_ref"))
    if inquiry is None:
        raise SemanticParserError(E.FOREIGN_INQUIRY)
    kind = arguments.get("answer_kind")
    option, text = arguments.get("answer_option"), arguments.get("answer_text")
    if kind == "option":
        if option is None or option not in inquiry.answer_options or text is not None:
            raise SemanticParserError(E.INQUIRY_ANSWER_INVALID)
    elif kind == "free_text":
        if not isinstance(text, str) or not text.strip() or option is not None:
            raise SemanticParserError(E.INQUIRY_ANSWER_INVALID)
    elif kind == "declined":
        if option is not None or text is not None:
            raise SemanticParserError(E.INQUIRY_ANSWER_INVALID)
    else:
        raise SemanticParserError(E.INQUIRY_ANSWER_INVALID)


def _drop_repeated_acts(
    frames: list[SemanticFrame], regions: tuple[SemanticEvidenceRegion, ...],
) -> list[SemanticFrame]:
    """Keep one frame per act when the parser repeats an identical frame.

    Frames are the same act only when predicate, arguments, and polarity are
    all identical; such a repeat carries no additional meaning (repetition is
    a Perception cue, not a second act).  The frame citing the smallest owned
    span is kept, so the result cites the smallest available region.
    """

    bounds = {item.evidence_ref: (item.start, item.end) for item in regions}

    def size(frame: SemanticFrame) -> tuple[int, int]:
        spans = [bounds[ref] for ref in frame.evidence_refs]
        start, end = min(item[0] for item in spans), max(item[1] for item in spans)
        return end - start, start

    kept: list[SemanticFrame] = []
    for frame in frames:
        signature = (frame.predicate, frame.polarity, dict(frame.arguments))
        match = next(
            (index for index, item in enumerate(kept)
             if (item.predicate, item.polarity, dict(item.arguments)) == signature),
            None,
        )
        if match is None:
            kept.append(frame)
        elif size(frame) < size(kept[match]):
            kept[match] = frame
    return kept


class ModelSemanticGrounder:
    """Calls one semantic model, then validates every candidate in code."""

    def __init__(self, model: SemanticCandidateModel) -> None:
        self.model = model

    @property
    def version(self) -> str:
        return f"{MODEL_GROUNDING_COMPILER_VERSION}:{SEMANTIC_MODEL_PROTOCOL_VERSION}:{self.model.version}"

    @property
    def provenance(self) -> SemanticParserProvenance | None:
        return getattr(self.model, "provenance", None)

    @staticmethod
    def _regions(
        request: InterpretationRequest, evidence: tuple[InterpretationEvidence, ...],
    ) -> tuple[SemanticEvidenceRegion, ...]:
        return tuple(
            SemanticEvidenceRegion(
                evidence_ref=item.evidence_id, message_ref=item.message_id,
                start=item.span.start, end=item.span.end, text=item.span.quoted_text,
            )
            for item in evidence
            if item.source_kind == InterpretationEvidenceKind.MESSAGE_SPAN
            and item.message_id == request.focus_message.message_id and item.span is not None
        )

    def _payload(
        self, request: InterpretationRequest, evidence: tuple[InterpretationEvidence, ...],
    ) -> SemanticModelPayload:
        regions = self._regions(request, evidence)
        return SemanticModelPayload(
            protocol_version=SEMANTIC_MODEL_PROTOCOL_VERSION,
            focus_message_ref=request.focus_message.message_id,
            messages=tuple(SemanticParserMessage(
                message_ref=item.message_id, role=item.role, text=item.content,
            ) for item in request.messages),
            evidence_regions=regions,
            predicates=predicate_specs(inquiries=bool(request.open_inquiries)),
            open_inquiries=tuple(
                SemanticOpenInquiry(
                    inquiry_ref=item.inquiry_ref, purpose=item.purpose, asked_about=item.asked_about,
                    answer_options=item.answer_options,
                )
                for item in request.open_inquiries
            ),
        )

    def ground(
        self, request: InterpretationRequest, evidence: tuple[InterpretationEvidence, ...],
    ) -> GroundingResult:
        if not request.focus_message.content.strip() or not self._regions(request, evidence):
            return self._empty_focus(request, evidence)
        payload = self._payload(request, evidence)
        output = self.model.generate(payload)
        offered = {item.predicate for item in payload.predicates}
        definitions = {item.predicate: item for item in MODEL_PREDICATE_MANIFEST if item.predicate in offered}
        inquiries = {item.inquiry_ref: item for item in request.open_inquiries}
        permitted_refs = {item.evidence_ref for item in payload.evidence_regions}
        frames: list[SemanticFrame] = []
        unknowns_by_frame: dict[str, list[UnknownInput]] = {}
        for ordinal, candidate in enumerate(output.frames):
            definition = definitions.get(candidate.predicate)
            if definition is None:
                raise SemanticParserError(E.UNREGISTERED_PREDICATE)
            arguments = canonical_candidate_arguments(candidate)
            keys = set(arguments)
            if not keys <= set(definition.allowed_arguments):
                raise SemanticParserError(E.DISALLOWED_ARGUMENT)
            if not set(definition.required_arguments) <= keys:
                raise SemanticParserError(E.MISSING_REQUIRED_ARGUMENT)
            for name, allowed in definition.argument_values.items():
                if arguments.get(name) is not None and arguments[name] not in allowed:
                    raise SemanticParserError(E.ARGUMENT_VALUE_OUTSIDE_VOCABULARY)
            if not set(candidate.evidence_refs) <= permitted_refs:
                raise SemanticParserError(E.FOREIGN_EVIDENCE)
            if candidate.predicate == "inquiry_answer":
                _validate_inquiry_answer(arguments, inquiries)
            frame_id = stable_id(
                "semantic-frame", request.cognitive_turn_id, request.perception.perception_event_id,
                candidate.predicate, arguments, candidate.polarity, candidate.evidence_refs, ordinal,
                MODEL_GROUNDING_COMPILER_VERSION,
            )
            try:
                frame = SemanticFrame(
                    frame_id=frame_id, predicate=candidate.predicate, arguments=arguments,
                    polarity=candidate.polarity, evidence_refs=candidate.evidence_refs,
                    unresolved_arguments=tuple(key for key, value in arguments.items() if value is None),
                    grounding_rule_ids=(MODEL_GROUNDING_COMPILER_VERSION,),
                )
            except (ValidationError, ValueError) as exc:
                raise SemanticParserError(E.FRAME_INVALID) from exc
            frames.append(frame)
            if frame.predicate == "unknown":
                # Code, not the model, records that the meaning is unresolved.
                unknowns_by_frame.setdefault(frame_id, []).append(UnknownInput(
                    unknown_id=stable_id("interpretation-unknown", request.cognitive_turn_id, frame_id, "meaning"),
                    key="meaning", owner="user", evidence_refs=candidate.evidence_refs,
                ))
            for key, value in arguments.items():
                if value is not None:
                    continue
                unknowns_by_frame.setdefault(frame_id, []).append(UnknownInput(
                    unknown_id=stable_id("interpretation-unknown", request.cognitive_turn_id, frame_id, key),
                    key=key, owner="user", evidence_refs=candidate.evidence_refs,
                ))
        if not frames:
            raise SemanticParserError(E.NO_FRAMES)
        frames = _drop_repeated_acts(frames, payload.evidence_regions)
        unknowns = [item for frame in frames for item in unknowns_by_frame.get(frame.frame_id, ())]
        try:
            validate_semantic_frames(frames)
        except ValueError as exc:
            raise SemanticParserError(E.FRAME_INVALID) from exc
        return GroundingResult(tuple(frames), tuple(unknowns), (), ())


    @staticmethod
    def _empty_focus(
        request: InterpretationRequest, evidence: tuple[InterpretationEvidence, ...],
    ) -> GroundingResult:
        """Degraded-input policy: a focus message with no non-whitespace text
        has nothing to parse.  No call is made; the turn compiles to one bounded semantic
        unknown citing the Perception cue record that observed the message.
        This is a semantic unknown, not a parser-unavailable failure, and it
        is not the rule grounder."""

        cited = next(
            (item for item in evidence if item.source_kind == InterpretationEvidenceKind.PERCEPTION_CUE), None,
        )
        if cited is None:
            raise SemanticParserError(E.NO_FRAMES)
        refs = (cited.evidence_id,)
        frame = SemanticFrame(
            frame_id=stable_id(
                "semantic-frame", request.cognitive_turn_id, request.perception.perception_event_id,
                "unknown", EMPTY_FOCUS_RULE, MODEL_GROUNDING_COMPILER_VERSION,
            ),
            predicate="unknown", arguments={"predicate": None}, polarity="unknown", evidence_refs=refs,
            unresolved_arguments=("predicate",),
            grounding_rule_ids=(MODEL_GROUNDING_COMPILER_VERSION, EMPTY_FOCUS_RULE),
        )
        unknowns = tuple(
            UnknownInput(
                unknown_id=stable_id("interpretation-unknown", request.cognitive_turn_id, frame.frame_id, key),
                key=key, owner="user", evidence_refs=refs,
            )
            for key in ("predicate", "meaning")
        )
        return GroundingResult((frame,), unknowns, (), ())


class UnavailableSemanticGrounder:
    """Installed when the configured parser cannot be constructed.

    It makes no call and never substitutes the rule grounder: every turn fails
    with the stable configuration code through the ordinary typed path, so a
    misconfiguration is recorded instead of consuming worker retries.
    """

    def __init__(self, code: SemanticParserErrorCode) -> None:
        if code not in {E.MODEL_NOT_CONFIGURED, E.CONFIGURATION_INCOMPATIBLE}:
            raise ValueError("an unavailable grounder carries a configuration code")
        self.code = code

    @property
    def version(self) -> str:
        return f"{MODEL_GROUNDING_COMPILER_VERSION}:{SEMANTIC_MODEL_PROTOCOL_VERSION}:unavailable"

    provenance = None

    def ground(self, request, evidence) -> GroundingResult:
        raise SemanticParserError(self.code)


__all__ = [
    "MODEL_GROUNDING_COMPILER_VERSION", "ModelSemanticGrounder", "UnavailableSemanticGrounder", "predicate_specs",
]
