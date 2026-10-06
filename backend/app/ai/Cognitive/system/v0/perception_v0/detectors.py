"""Pure, versioned detectors for the thirteen Perception V0 cues."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# Quote ownership is shared with the clause scan so both use one definition.
from .clauses import QuoteSpan, quote_spans, scan_clauses
from .evidence import message_sequence, runtime_fact, text_span, whole_message
from .models import CueState, PerceptionEvidence, PerceptionRequest

# v0.2 replaces the whole-message question-form rule with the shared
# quote-aware clause scan (SH-EVAL-004).  Every other detector is unchanged,
# and the v0.1 bundle stays executable for historical cue-manifest-v1 events.
DETECTOR_BUNDLE_VERSION = "perception-deterministic-detectors-v0.2"
DETECTOR_BUNDLE_VERSIONS = {
    "cue-manifest-v1": "perception-deterministic-detectors-v0.1",
    "cue-manifest-v2": DETECTOR_BUNDLE_VERSION,
}


@dataclass(frozen=True, slots=True)
class Detection:
    state: CueState
    value: bool | int | None
    evidence: tuple[PerceptionEvidence, ...] = ()
    missing_information: tuple[str, ...] = ()
    observation_key: str = ""
    observation_value: bool | int | str | tuple[str, ...] | None = None




def _inside(position: int, span: QuoteSpan) -> bool:
    return span.start < position < span.end


def _lexical_matches(
    request: PerceptionRequest,
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[tuple[re.Match[str], ...], bool]:
    content = request.focus_message.content
    quotes = quote_spans(content)
    owned: list[re.Match[str]] = []
    ambiguous = False
    for pattern in patterns:
        for match in pattern.finditer(content):
            containing = next((span for span in quotes if _inside(match.start(), span)), None)
            if containing is None:
                owned.append(match)
            elif containing.ownership == "ambiguous":
                ambiguous = True
    owned.sort(key=lambda match: (match.start(), match.end()))
    return tuple(owned), ambiguous


def _language_supported(request: PerceptionRequest) -> bool:
    if request.language_tag is None:
        return False
    return request.language_tag.lower().split("-", 1)[0] == "en"


def _unknown(reason: str, observation_key: str) -> Detection:
    return Detection(
        state=CueState.UNKNOWN,
        value=None,
        missing_information=(reason,),
        observation_key=observation_key,
        observation_value=None,
    )


def _boolean_detection(
    request: PerceptionRequest,
    matches: tuple[re.Match[str], ...],
    ambiguous_quote: bool,
    observation_key: str,
) -> Detection:
    if matches:
        evidence = tuple(
            text_span(request.focus_message_id, request.focus_message.content, item.start(), item.end())
            for item in matches
        )
        return Detection(CueState.PRESENT, True, evidence, observation_key=observation_key, observation_value=True)
    if ambiguous_quote:
        return _unknown("quote ownership is unresolved", observation_key)
    return Detection(CueState.ABSENT, False, observation_key=observation_key, observation_value=False)


_QUESTION_START = re.compile(
    r"^\s*(?:who|what|when|where|why|how|which|is|are|am|was|were|do|does|did|can|could|would|will|should|may)\b",
    re.IGNORECASE,
)


def detect_question_form(request: PerceptionRequest) -> Detection:
    """Clause-level question structure (``question-form-rules-v0.2``).

    One Boolean cue; each owned interrogative clause contributes its own text
    span (after permitted discourse prefixes).  Quoted questions are never the
    speaker's; a question that exists only inside ambiguous quotation leaves
    the cue unknown.  Detailed question objects belong downstream.
    """

    content = request.focus_message.content
    clauses = scan_clauses(content)
    questions = [clause for clause in clauses if clause.interrogative]
    if questions:
        evidence = tuple(
            text_span(request.focus_message_id, content, clause.body_start, clause.end) for clause in questions
        )
        return Detection(
            CueState.PRESENT, True, evidence,
            observation_key="focus.question_structure_present", observation_value=True,
        )
    if any(clause.ambiguous for clause in clauses):
        return _unknown("quote ownership changes whether the focus asks a question", "focus.question_structure_present")
    return Detection(
        CueState.ABSENT, False,
        observation_key="focus.question_structure_present", observation_value=False,
    )


def detect_question_form_v0_1(request: PerceptionRequest) -> Detection:
    """Historical whole-message rule, kept executable for cue-manifest-v1 events only."""

    content = request.focus_message.content
    quotes = quote_spans(content)
    question_positions = [index for index, character in enumerate(content) if character == "?"]
    owned_question = any(not any(_inside(index, span) for span in quotes) for index in question_positions)
    ambiguous_question = any(
        any(_inside(index, span) and span.ownership == "ambiguous" for span in quotes)
        for index in question_positions
    )
    present = owned_question or bool(_QUESTION_START.search(content))
    if not present and ambiguous_question:
        return _unknown("quote ownership changes whether the focus asks a question", "focus.question_structure_present")
    evidence = (text_span(request.focus_message_id, content, 0, len(content)),) if present and content else ()
    return Detection(
        CueState.PRESENT if present else CueState.ABSENT,
        present,
        evidence,
        observation_key="focus.question_structure_present",
        observation_value=present,
    )


_MODAL_REQUEST = re.compile(r"\b(?:can|could|would|will)\s+you\s+[a-z][\w'-]*[^?!]*(?:[?!]|$)", re.I)
_POLITE_REQUEST = re.compile(r"\b(?:please|pls)\s+[a-z][\w'-]*[^?!]*(?:[?!]|$)", re.I)
_IMPERATIVE_REQUEST = re.compile(
    r"^\s*(?:send|show|tell|make|create|generate|fix|open|close|add|remove|delete|save|find|check|book|schedule|email|call|summarize|explain|help)\b[^?!]*(?:[?!]|$)",
    re.I,
)
_UNKNOWN_IMPERATIVE_REQUEST = re.compile(
    r"^\s*[a-z][\w'-]*\s+(?:this|that|it|the|a|an|my|your)\b[^?!]*(?:[?!]|$)",
    re.I,
)


def detect_explicit_action_request(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent request rules support English only", "focus.action_request_structure")
    content = request.focus_message.content
    quotes = quote_spans(content)
    all_candidates = [
        match
        for pattern in (
            _MODAL_REQUEST, _POLITE_REQUEST, _IMPERATIVE_REQUEST,
            _UNKNOWN_IMPERATIVE_REQUEST,
        )
        for match in pattern.finditer(content)
    ]
    candidates = [
        match for match in all_candidates
        if not any(_inside(match.start(), span) for span in quotes)
    ]
    candidates.sort(key=lambda match: (match.start(), -(match.end() - match.start())))
    if not candidates:
        if any(
            any(_inside(match.start(), span) and span.ownership == "ambiguous" for span in quotes)
            for match in all_candidates
        ):
            return _unknown("quote ownership changes whether the request is speaker-owned", "focus.action_request_structure")
        return Detection(CueState.ABSENT, False, observation_key="focus.action_request_structure", observation_value=False)
    match = candidates[0]
    return Detection(
        CueState.PRESENT, True,
        (text_span(request.focus_message_id, content, match.start(), match.end()),),
        observation_key="focus.action_request_structure", observation_value=True,
    )


_CORRECTION_MARKER = re.compile(
    r"(?:^\s*no[,;:]\s+(?:(?:i\s+(?:said|meant)|you\s+(?:said|wrote))\b|it\s+is\b)|\b(?:actually|correction:)\b|\bi\s+meant\b)",
    re.I,
)
_REJECTED_TERM = re.compile(r"\bnot\s+([\w'-]+)", re.I)


def detect_explicit_correction(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent correction rules support English only", "focus.explicit_correction_structure")
    content = request.focus_message.content
    marker = _CORRECTION_MARKER.search(content)
    if marker is None:
        return Detection(CueState.ABSENT, False, observation_key="focus.explicit_correction_structure", observation_value=False)
    containing_quote = next((span for span in quote_spans(content) if _inside(marker.start(), span)), None)
    if containing_quote is not None:
        if containing_quote.ownership == "ambiguous":
            return _unknown("quote ownership changes whether the correction is speaker-owned", "focus.explicit_correction_structure")
        return Detection(CueState.ABSENT, False, observation_key="focus.explicit_correction_structure", observation_value=False)
    prior = request.messages[:-1]
    if not prior:
        return _unknown("the correction target is outside the bounded context", "focus.explicit_correction_structure")
    evidence: list[PerceptionEvidence] = [text_span(request.focus_message_id, content, 0, len(content))]
    rejected = _REJECTED_TERM.search(content)
    target_found = False
    if rejected is not None:
        target = rejected.group(1)
        for message in reversed(prior):
            found = re.search(rf"\b{re.escape(target)}\b", message.content, re.I)
            if found is not None:
                evidence.append(text_span(message.message_id, message.content, found.start(), found.end()))
                target_found = True
                break
    if rejected is not None and not target_found:
        return _unknown("the stated correction target is not present in bounded context", "focus.explicit_correction_structure")
    if rejected is None:
        evidence.append(whole_message(prior[-1].message_id))
    return Detection(
        CueState.PRESENT, True, tuple(evidence),
        observation_key="focus.explicit_correction_structure", observation_value=True,
    )


_NORMALIZE_PUNCTUATION = re.compile(r"[^\w\s']+")
_NORMALIZE_SPACE = re.compile(r"\s+")


def _normalized(text: str) -> str:
    return _NORMALIZE_SPACE.sub(" ", _NORMALIZE_PUNCTUATION.sub(" ", text.casefold())).strip()


def detect_repetition(request: PerceptionRequest) -> Detection:
    focus = request.focus_message
    normalized = _normalized(focus.content)
    if not normalized:
        return Detection(CueState.ABSENT, 0, observation_key="context.prior_matching_occurrences", observation_value=0)
    exact = [
        message for message in request.messages[:-1]
        if message.role == focus.role and _normalized(message.content) == normalized
    ]
    if exact:
        evidence = tuple(
            text_span(message.message_id, message.content, 0, len(message.content))
            for message in (*exact, focus)
        ) + (message_sequence(*(message.message_id for message in (*exact, focus))),)
        return Detection(
            CueState.PRESENT, len(exact), evidence,
            observation_key="context.prior_matching_occurrences", observation_value=len(exact),
        )
    focus_tokens = set(normalized.split())
    for message in request.messages[:-1]:
        if message.role != focus.role:
            continue
        prior_tokens = set(_normalized(message.content).split())
        overlap = len(focus_tokens & prior_tokens) / max(1, min(len(focus_tokens), len(prior_tokens)))
        if overlap >= 0.65:
            return _unknown(
                "conservative repetition matching cannot resolve a paraphrase",
                "context.prior_matching_occurrences",
            )
    return Detection(CueState.ABSENT, 0, observation_key="context.prior_matching_occurrences", observation_value=0)


_NEGATION = (
    re.compile(r"\b(?:never|no|cannot|neither|nor)\b", re.I),
    re.compile(r"\bnot\b", re.I),
    re.compile(r"\b(?:can't|couldn't|won't|wouldn't|shouldn't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|haven't|hasn't|hadn't)\b", re.I),
)


def detect_negation(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent negation rules support English only", "focus.negation_marker")
    matches, ambiguous = _lexical_matches(request, _NEGATION)
    if matches and _CORRECTION_MARKER.search(request.focus_message.content):
        scoped = []
        for match in matches:
            if match.group(0).casefold() == "not":
                end_match = re.match(r"\s+[\w'-]+", request.focus_message.content[match.end():])
                if end_match is not None:
                    end = match.end() + end_match.end()
                    proxy = re.compile(re.escape(request.focus_message.content[match.start():end]), re.I).search(
                        request.focus_message.content, match.start(), end
                    )
                    if proxy is not None:
                        scoped.append(proxy)
                        continue
            scoped.append(match)
        matches = tuple(scoped)
    return _boolean_detection(request, matches, ambiguous, "focus.negation_marker")


_UNCERTAINTY = tuple(
    re.compile(pattern, re.I)
    for pattern in (r"\bi\s+think\b", r"\bmaybe\b", r"\bperhaps\b", r"\bprobably\b", r"\bnot\s+sure\b", r"\bunsure\b", r"\bi\s+guess\b")
)


def detect_uncertainty(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent uncertainty rules support English only", "focus.uncertainty_marker")
    matches, ambiguous = _lexical_matches(request, _UNCERTAINTY)
    return _boolean_detection(request, matches, ambiguous, "focus.uncertainty_marker")


_SELF_REFERENCE = (
    re.compile(r"\b(?:i|me|my|mine|myself|we|us|our|ours|ourselves)\b", re.I),
    re.compile(r"\b(?:i'm|i've|i'd|i'll|we're|we've|we'd|we'll)\b", re.I),
)


def detect_self_reference(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent self-reference rules support English only", "focus.first_person_reference_count")
    matches, ambiguous = _lexical_matches(request, _SELF_REFERENCE)
    # Prefer the full contraction over its nested pronoun match.
    unique: list[re.Match[str]] = []
    for match in sorted(matches, key=lambda item: (item.start(), -(item.end() - item.start()))):
        if any(match.start() >= item.start() and match.end() <= item.end() for item in unique):
            continue
        unique.append(match)
    if unique:
        evidence = tuple(
            text_span(request.focus_message_id, request.focus_message.content, item.start(), item.end())
            for item in unique
        )
        return Detection(
            CueState.PRESENT, len(unique), evidence,
            observation_key="focus.first_person_reference_count", observation_value=len(unique),
        )
    if ambiguous:
        return _unknown("quote ownership is unresolved", "focus.first_person_reference_count")
    return Detection(CueState.ABSENT, 0, observation_key="focus.first_person_reference_count", observation_value=0)


def detect_attachment_present(request: PerceptionRequest) -> Detection:
    if not request.runtime.attachments_available:
        return _unknown(
            "trusted runtime attachment data is unavailable",
            "runtime.attachment_count",
        )
    count = len(request.runtime.attachments)
    if count:
        evidence = tuple(runtime_fact(request, f"/attachments/{index}") for index in range(count))
        return Detection(
            CueState.PRESENT, True, evidence,
            observation_key="runtime.attachment_count", observation_value=count,
        )
    return Detection(CueState.ABSENT, False, observation_key="runtime.attachment_count", observation_value=0)


_FAILURE_STATUSES = frozenset({"failed", "failure", "error", "timed_out", "timeout", "cancelled"})
_NON_FAILURE_STATUSES = frozenset({"succeeded", "success", "completed", "ok", "running", "pending"})


def detect_tool_failure(request: PerceptionRequest) -> Detection:
    event = request.runtime.last_tool_event
    if event is None:
        return Detection(
            CueState.NOT_APPLICABLE, None,
            observation_key="runtime.last_tool_event_status", observation_value=None,
        )
    evidence = (runtime_fact(request, "/last_tool_event"),)
    if not event.trusted_runtime:
        return Detection(
            CueState.UNKNOWN, None, (), ("tool event is not from the trusted runtime",),
            "runtime.last_tool_event_status", event.status,
        )
    if not event.authorized:
        return Detection(
            CueState.UNKNOWN, None, (), ("tool invocation authorization is not established",),
            "runtime.last_tool_event_status", event.status,
        )
    normalized_status = event.status.casefold()
    if normalized_status not in _FAILURE_STATUSES | _NON_FAILURE_STATUSES:
        return Detection(
            CueState.UNKNOWN, None, (), ("tool event status is not recognized",),
            "runtime.last_tool_event_status", event.status,
        )
    failed = normalized_status in _FAILURE_STATUSES
    return Detection(
        CueState.PRESENT if failed else CueState.ABSENT,
        failed,
        evidence if failed else (),
        observation_key="runtime.last_tool_event_status", observation_value=event.status,
    )


_STOPWORDS = frozenset(
    "a an and are as at be by for from has have i in is it me my of on or please the this to was we when with you your".split()
)


def _topic_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z][a-z0-9_-]+", text.casefold()) if token not in _STOPWORDS}


def detect_topic_shift(request: PerceptionRequest) -> Detection:
    focus_tokens = _topic_tokens(request.focus_message.content)
    prior = request.messages[-2] if len(request.messages) > 1 else None
    if prior is None:
        return _unknown("no recent topic-bearing message or comparable working topic is supplied", "context.topic_overlap")
    prior_tokens = _topic_tokens(prior.content)
    if not focus_tokens or not prior_tokens:
        return _unknown("supplied topic context is insufficient for comparison", "context.topic_overlap")
    overlap = focus_tokens & prior_tokens
    shifted = not overlap
    evidence = (whole_message(prior.message_id), whole_message(request.focus_message_id)) if shifted else ()
    return Detection(
        CueState.PRESENT if shifted else CueState.ABSENT,
        shifted,
        evidence,
        observation_key="context.topic_overlap", observation_value=tuple(sorted(overlap)),
    )


_POSITIVE = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bthanks\b", r"\bthank\s+you\b", r"\bgreat\b", r"\bgood\b", r"\bexcellent\b", r"\bperfect\b", r"\blove\b", r"\bhelpful\b",
))
_NEGATIVE = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bbroken\s+[a-z][\w'-]*\b", r"\bawful\b", r"\bfailed\b", r"\bfailure\b", r"\berror\b", r"\bterrible\b", r"\bbad\b", r"\bhate\b",
))


def detect_positive_wording(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent positive-wording rules support English only", "focus.positive_wording_marker")
    matches, ambiguous = _lexical_matches(request, _POSITIVE)
    return _boolean_detection(request, matches, ambiguous, "focus.positive_wording_marker")


def detect_negative_wording(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent negative-wording rules support English only", "focus.negative_wording_marker")
    matches, ambiguous = _lexical_matches(request, _NEGATIVE)
    return _boolean_detection(request, matches, ambiguous, "focus.negative_wording_marker")


_URGENCY = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bby\s+(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|today|tomorrow|end of day|eod)\b\.?",
    r"\b(?:urgent|urgently|immediately|right\s+now|asap|as soon as possible)\b",
    r"\bwithin\s+\d+\s+(?:minutes?|hours?|days?)\b",
    r"\bin\s+the\s+next\s+\d+\s+(?:minutes?|hours?|days?)\b",
))


def detect_urgency(request: PerceptionRequest) -> Detection:
    if not _language_supported(request):
        return _unknown("language-dependent urgency rules support English only", "focus.explicit_time_pressure")
    matches, ambiguous = _lexical_matches(request, _URGENCY)
    return _boolean_detection(request, matches, ambiguous, "focus.explicit_time_pressure")


DETECTORS: dict[str, Callable[[PerceptionRequest], Detection]] = {
    "question-form-rules-v0.1": detect_question_form_v0_1,
    "question-form-rules-v0.2": detect_question_form,
    "action-request-rules-v0.1": detect_explicit_action_request,
    "explicit-correction-rules-v0.1": detect_explicit_correction,
    "repetition-normalization-v0.1": detect_repetition,
    "english-negation-lexicon-v0.1": detect_negation,
    "english-uncertainty-lexicon-v0.1": detect_uncertainty,
    "english-self-reference-v0.1": detect_self_reference,
    "trusted-attachment-runtime-v0.1": detect_attachment_present,
    "trusted-tool-event-runtime-v0.1": detect_tool_failure,
    "topic-overlap-shadow-v0.1": detect_topic_shift,
    "english-positive-wording-shadow-v0.1": detect_positive_wording,
    "english-negative-wording-shadow-v0.1": detect_negative_wording,
    "english-urgency-shadow-v0.1": detect_urgency,
}
