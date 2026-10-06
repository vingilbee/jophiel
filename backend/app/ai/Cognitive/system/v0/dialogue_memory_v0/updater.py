"""Pure deterministic snapshot transition (dialogue-working-memory-updater-v0.1).

``DialogueMemoryUpdaterV0.apply(predecessor, events)`` is a pure function of
its inputs: no model, database, cache, clock, network, retrieval, or provider.
The same predecessor and the same ordered events always produce the same
payload, snapshot id, and digest.

What V0 projects from text is deliberately narrow and structural, using only
the reviewed English cue rules below -- never an LLM and never a summary:

- every exposed utterance becomes an attributed utterance reference (with an
  exact whole-utterance quote when it is short enough);
- complete user speech yields attributed entities (names), propositions,
  questions, requests, topic markers, corrections, and withdrawals;
- complete, durably visible Columbina speech yields questions, option sets,
  plans, and commitments -- as attributed references, never trusted facts;
- partial, failed, cancelled, pending, and unknown outputs yield only what the
  exposure policy allows, and never completion, fulfilment, or answer evidence.

Referents are resolved only when the evidence is unique; otherwise the cue
becomes an unresolved-reference gap naming its competing candidates.  The
newest item is never chosen merely because it is newest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts.dialogue_memory import (
    DIALOGUE_MEMORY_UPDATER_VERSION,
    MAX_ACTIVE_COMMITMENTS,
    MAX_ACTIVE_REQUESTS,
    MAX_ACTIVE_TOPICS,
    MAX_ENTITY_MENTIONS,
    MAX_EXACT_QUOTES,
    MAX_OPEN_QUESTIONS,
    MAX_QUOTE_CHARACTERS,
    MAX_UNRESOLVED_REFERENCES,
    REFERENCE_GAP_KINDS,
    DialogueCommitmentReference,
    DialogueCorrection,
    DialogueCue,
    DialogueCueKind,
    DialogueEntity,
    DialogueEpistemicStatus,
    DialogueExposureState,
    DialogueGap,
    DialogueGapKind,
    DialogueHistoryOmission,
    DialogueItemStatus,
    DialogueItemType,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueMemorySnapshot,
    DialogueMemoryVersions,
    DialogueMessageStatus,
    DialogueOmissionReason,
    DialogueOption,
    DialogueOptionSet,
    DialoguePlanReference,
    DialoguePlanStep,
    DialogueProposition,
    DialogueQuestion,
    DialogueQuote,
    DialogueRequest,
    DialogueResetReason,
    DialogueResolution,
    DialogueRole,
    DialogueSourceEvent,
    DialogueSpanRef,
    DialogueSpeaker,
    DialogueTopic,
    DialogueUtteranceReference,
    canonical_json,
    item_id_for,
    option_ref_for,
    plan_step_ref_for,
    snapshot_id_for,
    snapshot_semantic_digest,
)
from ..contracts.realization import SensitivityClass, valid_text
from .digests import extend_prefix_digest, fingerprint_of, genesis_prefix_digest
from .errors import DialogueMemoryError, fail
from .models import DialogueMemoryLimits
from .ordering import validate_events

UPDATER_VERSION = DIALOGUE_MEMORY_UPDATER_VERSION

# Bounds on what one utterance may contribute.
MAX_SENTENCE_ANCHOR = 240
MAX_LIST_ITEM_ANCHOR = 200
MAX_TOPIC_ANCHOR = 80
MAX_QUESTIONS_PER_MESSAGE = 3
MAX_REQUESTS_PER_MESSAGE = 3
MAX_PROPOSITIONS_PER_MESSAGE = 3
MAX_COMMITMENTS_PER_MESSAGE = 2
MAX_ENTITIES_PER_MESSAGE = 8
MAX_LIST_ITEMS = 12


# ---------------------------------------------------------------------------
# Deterministic text analysis (shared with the projector)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    text: str


def _span(content: str, start: int, end: int) -> TextSpan | None:
    """The exact, whitespace-trimmed substring ``content[start:end]``."""

    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    return TextSpan(start, end, content[start:end])


def sentence_spans(content: str) -> tuple[TextSpan, ...]:
    """Single-line sentences: split at newlines and at ``.``/``!``/``?`` runs followed by space or end."""

    spans: list[TextSpan] = []
    start = 0
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if char == "\n":
            piece = _span(content, start, index)
            if piece is not None:
                spans.append(piece)
            start = index + 1
            index += 1
            continue
        if char in ".!?":
            end = index
            while end < length and content[end] in ".!?":
                end += 1
            if end == length or content[end].isspace():
                piece = _span(content, start, end)
                if piece is not None:
                    spans.append(piece)
                start = end
            index = end
            continue
        index += 1
    piece = _span(content, start, length)
    if piece is not None:
        spans.append(piece)
    return tuple(spans)


def _anchor_ok(text: str, limit: int) -> bool:
    return 0 < len(text) <= limit and "\n" not in text and valid_text(text)


_I = re.IGNORECASE
_APOS = "['’]"
YES_NO_START = re.compile(
    r"^(is|are|am|was|were|do|does|did|can|could|will|would|should|shall|may|might|have|has|had|isn['’]t|"
    r"aren['’]t|don['’]t|doesn['’]t|didn['’]t|can['’]t|won['’]t)\b", _I,
)
REQUEST_START = re.compile(
    r"^(please\b|can you\b|could you\b|would you\b|will you\b|help me\b|tell me\b|show me\b|give me\b|make me\b|"
    r"find me\b|explain\b|list\b|suggest\b|recommend\b|write\b|create\b|draft\b|i want you to\b|"
    r"i need you to\b|i['’]d like you to\b|let['’]s\b)", _I,
)
WITHDRAWAL = re.compile(
    r"\b(never ?mind(?:\s+(?:that|it|this))?|forget (?:it|that|about it)|cancel (?:that|it|this)|"
    r"scratch that|don['’]t bother|no need)\b", _I,
)
CONTINUATION = re.compile(
    r"^\s*(?:(?:yes|yeah|yep|sure|ok(?:ay)?)[\s,.!]*)?(?:please\s+)?"
    r"(continue|go on|keep going|carry on|next step|proceed|go ahead)\b", _I,
)
_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5,
    "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10, "last": -1,
}
ORDINAL_OPTION = re.compile(
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|1st|2nd|3rd|4th|5th|"
    r"6th|7th|8th|9th|10th)\s+(?:one|option|choice|idea|suggestion|item)\b", _I,
)
OPTION_NUMBER = re.compile(r"(?:\b(?:option|choice|number)|#)\s*(\d{1,2})\b", _I)
PRIOR_USER_QUERY = re.compile(
    r"\bwhat (?:did|have) i (?:just )?(?:tell|told|say|said|mention|mentioned)(?: you)?\b|"
    r"\bwhat was i (?:just )?saying\b|\bremind me what i (?:said|told you)\b", _I,
)
PRIOR_ASSISTANT_QUERY = re.compile(
    r"\bwhat (?:did|have) you (?:just )?(?:say|said|tell|told|suggest|suggested|mention|mentioned)\b|"
    r"\bwhat was (?:that|your) (?:last )?(?:answer|suggestion|reply)\b|\brepeat (?:that|what you said)\b|"
    r"\bsay that again\b", _I,
)
_WORDS = r"[A-Za-z][\w'’-]*(?:\s+[A-Za-z][\w'’-]*){0,3}"
CORRECTIONS = (
    re.compile(rf"\b(?:no[,.!]?\s+)?i meant\s+(?P<new>{_WORDS})\s*,?\s+not\s+(?P<old>{_WORDS})", _I),
    re.compile(rf"\b(?:it['’]s|it is|that['’]s|that is)\s+(?P<new>{_WORDS})\s*,\s*not\s+(?P<old>{_WORDS})", _I),
)
TOPIC_RETURN = re.compile(
    r"\b(?:back to|going back to|go back to|return to|returning to)\s+(?:the\s+|our\s+|my\s+|your\s+)?"
    r"(?P<topic>[A-Za-z][\w'’ -]{1,78}?)(?=\s*[.!?,;]|\s*$)", _I,
)
TOPIC_INTRO = re.compile(
    r"\b(?:let['’]s talk about|talk about|speaking of|regarding|i want to talk about|can we talk about)\s+"
    r"(?:the\s+|our\s+|my\s+|your\s+)?(?P<topic>[A-Za-z][\w'’ -]{1,78}?)(?=\s*[.!?,;]|\s*$)", _I,
)
PRONOUN_SINGULAR = re.compile(r"\b(it|its|he|him|his|she|her)\b", _I)
PRONOUN_PLURAL = re.compile(r"\b(they|them|their|those|these)\b", _I)
DEMONSTRATIVE = re.compile(r"\b(that|this)\b(?=\s*[.!?,;]|\s*$)", _I)
NAMED = re.compile(r"\b(?:named|called)\s+(?P<name>[A-Z][A-Za-z'’-]*)")
CAPITALIZED = re.compile(r"\b[A-Z][a-z][A-Za-z'’-]*")
FIRST_PERSON = re.compile(r"\b(i|i['’]m|i am|i['’]ve|i have|my|mine|me|we|our|us)\b", _I)
COMMITMENT = re.compile(r"\b(i['’]ll|i will)\b", _I)
LIST_LINE = re.compile(r"^[ \t]*(?:(?P<number>\d{1,2})[.)]|(?P<bullet>[-*•]))[ \t]+(?P<text>\S[^\n]*)$", re.M)
PLAN_WORDS = re.compile(r"\b(steps?|plan|procedure|instructions)\b", _I)
_NON_ENTITIES = frozenset({"columbina", "i", "i'm", "i've", "i'll", "i'd", "okay", "ok"})


@dataclass(frozen=True)
class RawCue:
    kind: DialogueCueKind
    span: TextSpan
    ordinal: int | None = None
    old: TextSpan | None = None
    new: TextSpan | None = None
    topic: TextSpan | None = None


def _group(content: str, match: re.Match, name: str | int) -> TextSpan | None:
    start, end = match.span(name)
    if start < 0:
        return None
    return _span(content, start, end)


def _clean_phrase(content: str, span: TextSpan) -> TextSpan | None:
    """Drop trailing punctuation from a captured phrase, keeping it an exact substring."""

    end = span.end
    while end > span.start and content[end - 1] in ".,;:!?'\"’":
        end -= 1
    return _span(content, span.start, end)


def detect_cues(content: str) -> tuple[RawCue, ...]:
    """Every reviewed cue in one user utterance, in text order.

    Pronoun and demonstrative matches inside another cue ("forget it",
    "repeat that") belong to that cue and are not separate references.
    """

    cues: list[RawCue] = []
    extra_claims: list[tuple[int, int]] = []

    def add(kind: DialogueCueKind, match: re.Match, **extra: Any) -> None:
        span = _span(content, *match.span())
        if span is not None:
            cues.append(RawCue(kind, span, **extra))

    # One withdrawal per utterance ("Never mind, forget it." is a single act).
    withdrawal = WITHDRAWAL.search(content)
    if withdrawal is not None:
        add(DialogueCueKind.WITHDRAWAL, withdrawal)
        # Later withdrawal phrases stay claimed so their pronouns are not new references.
        for match in WITHDRAWAL.finditer(content, withdrawal.end()):
            span = _span(content, *match.span())
            if span is not None:
                extra_claims.append((span.start, span.end))
    match = CONTINUATION.search(content)
    if match is not None:
        cues.append(RawCue(DialogueCueKind.CONTINUATION, TextSpan(*match.span(1), match.group(1))))
    for match in ORDINAL_OPTION.finditer(content):
        add(DialogueCueKind.ORDINAL_OPTION, match, ordinal=_ORDINALS[match.group(1).casefold()])
    for match in OPTION_NUMBER.finditer(content):
        number = int(match.group(1))
        if number >= 1:
            add(DialogueCueKind.ORDINAL_OPTION, match, ordinal=number)
    for match in PRIOR_USER_QUERY.finditer(content):
        add(DialogueCueKind.PRIOR_USER_STATEMENT_QUERY, match)
    for match in PRIOR_ASSISTANT_QUERY.finditer(content):
        add(DialogueCueKind.PRIOR_ASSISTANT_STATEMENT_QUERY, match)
    for pattern in CORRECTIONS:
        for match in pattern.finditer(content):
            old = _group(content, match, "old")
            new = _group(content, match, "new")
            old = _clean_phrase(content, old) if old else None
            new = _clean_phrase(content, new) if new else None
            if old is not None and new is not None and old.text.casefold() != new.text.casefold():
                add(DialogueCueKind.CORRECTION, match, old=old, new=new)
    for pattern in (TOPIC_RETURN,):
        for match in pattern.finditer(content):
            topic = _group(content, match, "topic")
            if topic is not None and _anchor_ok(topic.text, MAX_TOPIC_ANCHOR):
                add(DialogueCueKind.TOPIC_RETURN, match, topic=topic)

    claimed = [(cue.span.start, cue.span.end) for cue in cues] + extra_claims

    def free(start: int, end: int) -> bool:
        return not any(start < other_end and other_start < end for other_start, other_end in claimed)

    for pattern, kind in ((PRONOUN_SINGULAR, DialogueCueKind.PRONOUN_SINGULAR),
                          (PRONOUN_PLURAL, DialogueCueKind.PRONOUN_PLURAL),
                          (DEMONSTRATIVE, DialogueCueKind.DEMONSTRATIVE)):
        for match in pattern.finditer(content):
            if free(*match.span()):
                add(kind, match)

    unique: dict[tuple[int, int, str], RawCue] = {}
    for cue in cues:
        unique.setdefault((cue.span.start, cue.span.end, cue.kind.value), cue)
    return tuple(unique[key] for key in sorted(unique))


def topic_key(text: str) -> str:
    words = re.sub(r"\s+", " ", text.casefold().replace("’", "'")).strip()
    return re.sub(r"^(the|our|my|your)\s+", "", words)


def entity_key(text: str) -> str:
    name = text.replace("’", "'")
    if name.casefold().endswith("'s"):
        name = name[:-2]
    return name.casefold().strip()


def entity_mentions(content: str, *, speaker: DialogueSpeaker, excluded: Sequence[tuple[int, int]] = ()) -> tuple[TextSpan, ...]:
    """Names in one utterance: ``named/called X`` for any speaker; mid-sentence capitals for the user."""

    found: dict[str, TextSpan] = {}

    def keep(span: TextSpan | None) -> None:
        if span is None:
            return
        name = span.text
        if name.replace("’", "'").casefold().endswith("'s"):
            span = TextSpan(span.start, span.end - 2, content[span.start:span.end - 2])
        key = entity_key(span.text)
        if not key or key in _NON_ENTITIES or len(key) > 60:
            return
        if any(span.start < end and start < span.end for start, end in excluded):
            return
        found.setdefault(key, span)

    for match in NAMED.finditer(content):
        keep(_group(content, match, "name"))
    if speaker == DialogueSpeaker.USER:
        for sentence in sentence_spans(content):
            first = re.search(r"[A-Za-z]", sentence.text)
            for match in CAPITALIZED.finditer(sentence.text):
                if first is not None and match.start() == first.start():
                    continue  # a sentence-initial capital is not evidence of a name
                start = sentence.start + match.start()
                keep(_span(content, start, start + len(match.group())))
    ordered = sorted(found.values(), key=lambda item: (item.start, item.end))
    return tuple(ordered[:MAX_ENTITIES_PER_MESSAGE])


def local_entity_refs(
    items: Sequence[Any], content: str, *, conversation_ref: str, message_ref: str,
    excluded: Sequence[tuple[int, int]] = (),
) -> tuple[tuple[TextSpan, str, str, bool], ...]:
    """User-utterance names with the entity id each has (``True``) or will commit under (``False``).

    Uses exactly the updater's identity rule, so a capture-time focus analysis
    names the same ids the later committed transition creates.
    """

    current = {item.entity_key: item.item_id for item in _current(items, DialogueEntity)}
    result = []
    for piece in entity_mentions(content, speaker=DialogueSpeaker.USER, excluded=excluded):
        key = entity_key(piece.text)
        existing = current.get(key)
        ref = existing or item_id_for(DialogueItemType.ENTITY, conversation_ref, key, message_ref, str(piece.start))
        result.append((piece, key, ref, existing is not None))
    return tuple(result)


CLAIMING_CUES = frozenset({DialogueCueKind.CORRECTION, DialogueCueKind.WITHDRAWAL, DialogueCueKind.TOPIC_RETURN})


def question_shape(text: str) -> str:
    if YES_NO_START.search(text.strip()):
        return "choice" if re.search(r"\bor\b", text, _I) else "yes_no"
    return "open"


def list_block(content: str) -> tuple[TextSpan, ...]:
    """The first block of two or more consecutive list lines, as exact item spans."""

    items: list[TextSpan] = []
    kind: str | None = None
    previous_end: int | None = None
    for match in LIST_LINE.finditer(content):
        this_kind = "number" if match.group("number") else "bullet"
        contiguous = previous_end is not None and content[previous_end:match.start()].strip() == ""
        numbered_ok = this_kind == "bullet" or int(match.group("number")) == (len(items) + 1 if contiguous and kind == this_kind else 1)
        if not items or not contiguous or this_kind != kind or not numbered_ok:
            if len(items) >= 2:
                break
            items = []
            kind = this_kind
            if this_kind == "number" and int(match.group("number")) != 1:
                previous_end = match.end()
                continue
        text = _span(content, *match.span("text"))
        if text is not None:
            items.append(text)
        previous_end = match.end()
    return tuple(items) if len(items) >= 2 else ()


# ---------------------------------------------------------------------------
# Cue resolution (shared with the projector; never mutates)
# ---------------------------------------------------------------------------

def _current(items: Iterable[Any], cls: type) -> list[Any]:
    return [item for item in items if isinstance(item, cls) and item.is_current]


def _latest(items: Iterable[Any]) -> Any | None:
    ordered = sorted(items, key=lambda item: (item.introduced_sequence, item.item_id))
    return ordered[-1] if ordered else None


def active_option_set(items: Iterable[Any]) -> DialogueOptionSet | None:
    return _latest(_current(items, DialogueOptionSet))


def active_plan(items: Iterable[Any]) -> DialoguePlanReference | None:
    return _latest(_current(items, DialoguePlanReference))


def _entity_candidates(items: Sequence[Any], before: int, local: Sequence[tuple[int, str]], cue_start: int) -> tuple[str, ...]:
    """The antecedent group: names earlier in the same utterance, else the latest prior mention group."""

    same_utterance = tuple(sorted({ref for start, ref in local if start < cue_start}))
    if same_utterance:
        return same_utterance
    entities = [item for item in _current(items, DialogueEntity) if item.last_mention_sequence < before]
    if not entities:
        return ()
    latest = max(item.last_mention_sequence for item in entities)
    return tuple(sorted(item.item_id for item in entities if item.last_mention_sequence == latest))


def _latest_utterance(items: Sequence[Any], speaker: DialogueSpeaker, before: int) -> DialogueUtteranceReference | None:
    candidates = [item for item in _current(items, DialogueUtteranceReference)
                  if item.speaker == speaker and item.message_sequence < before]
    return max(candidates, key=lambda item: item.message_sequence) if candidates else None


def resolve_cue(cue: RawCue, items: Sequence[Any], *, before_sequence: int,
                local_entities: Sequence[tuple[int, str]] = ()) -> DialogueCue:
    """Resolve one cue against the committed items preceding ``before_sequence``.

    ``local_entities`` are ``(start, entity id)`` names in the same utterance;
    only those before the cue can be its antecedents.
    """

    resolved: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    kind = cue.kind
    if kind in {DialogueCueKind.PRONOUN_SINGULAR, DialogueCueKind.DEMONSTRATIVE}:
        group = _entity_candidates(items, before_sequence, local_entities, cue.span.start)
        if len(group) == 1:
            resolved = group
        elif len(group) > 1:
            candidates = group  # two viable antecedents stay competing; never the newest
    elif kind == DialogueCueKind.PRONOUN_PLURAL:
        # A plural pronoun refers to a whole group of two or more; a single
        # candidate is not a plural antecedent.
        group = _entity_candidates(items, before_sequence, local_entities, cue.span.start)
        if len(group) > 1:
            resolved = group
    elif kind == DialogueCueKind.ORDINAL_OPTION:
        options = active_option_set(item for item in items if item.introduced_sequence < before_sequence)
        if options is not None:
            index = len(options.options) if cue.ordinal == -1 else cue.ordinal
            if index is not None and 1 <= index <= len(options.options):
                resolved = (option_ref_for(options.item_id, index),)
    elif kind == DialogueCueKind.CONTINUATION:
        plan = active_plan(item for item in items if item.introduced_sequence < before_sequence)
        if plan is not None:
            resolved = (plan.item_id,)
    elif kind in {DialogueCueKind.PRIOR_USER_STATEMENT_QUERY, DialogueCueKind.PRIOR_ASSISTANT_STATEMENT_QUERY}:
        speaker = (DialogueSpeaker.USER if kind == DialogueCueKind.PRIOR_USER_STATEMENT_QUERY
                   else DialogueSpeaker.COLUMBINA)
        utterance = _latest_utterance(items, speaker, before_sequence)
        if utterance is not None:
            resolved = (utterance.item_id,)
    elif kind == DialogueCueKind.CORRECTION:
        key = entity_key(cue.old.text) if cue.old is not None else ""
        targets = [item for item in _current(items, DialogueEntity)
                   if item.entity_key == key and item.last_mention_sequence < before_sequence]
        if len(targets) == 1:
            resolved = (targets[0].item_id,)
        elif len(targets) > 1:
            candidates = tuple(sorted(item.item_id for item in targets))
    elif kind == DialogueCueKind.WITHDRAWAL:
        requests = sorted((item for item in _current(items, DialogueRequest) if item.introduced_sequence < before_sequence),
                          key=lambda item: item.item_id)
        if len(requests) == 1:
            resolved = (requests[0].item_id,)
        elif len(requests) > 1:
            candidates = tuple(item.item_id for item in requests)
    elif kind == DialogueCueKind.TOPIC_RETURN:
        key = topic_key(cue.topic.text) if cue.topic is not None else ""
        topics = [item for item in _current(items, DialogueTopic) if item.topic_key == key]
        if len(topics) == 1:
            resolved = (topics[0].item_id,)
        elif len(topics) > 1:
            candidates = tuple(sorted(item.item_id for item in topics))
    status = (DialogueResolution.RESOLVED if resolved else
              DialogueResolution.AMBIGUOUS if len(candidates) > 1 else DialogueResolution.NO_CANDIDATE)
    return DialogueCue(
        cue_kind=kind, surface_anchor=cue.span.text, start=cue.span.start, end=cue.span.end, resolution=status,
        resolved_refs=tuple(sorted(resolved)), candidate_refs=candidates if status == DialogueResolution.AMBIGUOUS else (),
        ordinal=cue.ordinal,
    )


def is_request_sentence(text: str) -> bool:
    """A request to act -- not a withdrawal, and not a topic introduction ("let's talk about X")."""

    return bool(REQUEST_START.search(text.strip())) and not WITHDRAWAL.search(text) and not TOPIC_INTRO.search(text)


# ---------------------------------------------------------------------------
# The updater
# ---------------------------------------------------------------------------

def _rebuild(item: Any, **updates: Any) -> Any:
    data = item.model_dump(mode="python")
    data.update(updates)
    return type(item).model_validate(data)


class _State:
    """Mutable working state for one transition; never escapes ``apply``."""

    def __init__(self, snapshot: DialogueMemorySnapshot) -> None:
        self.conversation_ref = snapshot.conversation_ref
        self.items: dict[str, Any] = {item.item_id: item for item in snapshot.items}
        self.omissions: list[DialogueHistoryOmission] = list(snapshot.omitted_history)

    # -- helpers -------------------------------------------------------------
    def put(self, item: Any) -> None:
        self.items[item.item_id] = item

    def ordered(self) -> list[Any]:
        return [self.items[key] for key in sorted(self.items)]

    def gap(self, event: DialogueSourceEvent, kind: DialogueGapKind, *, identity: str, cue: DialogueCue | None = None,
            spans: tuple[DialogueSpanRef, ...] = (), anchors: tuple[str, ...] = ()) -> DialogueGap:
        reference = kind in REFERENCE_GAP_KINDS
        gap = DialogueGap(
            item_id=item_id_for(DialogueItemType.GAP, self.conversation_ref, kind.value, event.message_ref, identity),
            conversation_ref=self.conversation_ref, introduced_turn_ref=event.turn_key,
            last_supported_turn_ref=event.turn_key, introduced_sequence=event.conversation_sequence,
            last_supported_sequence=event.conversation_sequence, source_message_refs=(event.message_ref,),
            source_span_refs=spans, surface_anchors=anchors,
            speaker=DialogueSpeaker.USER if reference else DialogueSpeaker.RUNTIME,
            epistemic_status=DialogueEpistemicStatus.UNRESOLVED if reference else DialogueEpistemicStatus.OBSERVED,
            sensitivity_class=SensitivityClass.PERSONAL if reference else SensitivityClass.ORDINARY,
            gap_kind=kind, subject_message_ref=event.message_ref,
            cue_kind=cue.cue_kind if cue is not None else None,
            resolution=cue.resolution if cue is not None else None,
            candidate_refs=cue.candidate_refs if cue is not None else (),
        )
        self.put(gap)
        return gap

    def support(self, item_id: str, event: DialogueSourceEvent, **updates: Any) -> None:
        item = self.items[item_id]
        sources = item.source_message_refs
        if event.message_ref not in sources:
            sources = (*sources, event.message_ref)
        self.put(_rebuild(item, last_supported_turn_ref=event.turn_key,
                          last_supported_sequence=max(item.last_supported_sequence, event.conversation_sequence),
                          source_message_refs=sources, **updates))

    def span(self, event: DialogueSourceEvent, piece: TextSpan) -> DialogueSpanRef:
        return DialogueSpanRef(message_ref=event.message_ref, start=piece.start, end=piece.end)

    def base(self, event: DialogueSourceEvent, speaker: DialogueSpeaker, *, pieces: tuple[TextSpan, ...] = (),
             anchors: int | None = None) -> dict[str, Any]:
        count = len(pieces) if anchors is None else anchors
        return dict(
            conversation_ref=self.conversation_ref, introduced_turn_ref=event.turn_key,
            last_supported_turn_ref=event.turn_key, introduced_sequence=event.conversation_sequence,
            last_supported_sequence=event.conversation_sequence, source_message_refs=(event.message_ref,),
            source_span_refs=tuple(self.span(event, piece) for piece in pieces),
            surface_anchors=tuple(piece.text for piece in pieces[:count]), speaker=speaker,
            sensitivity_class=SensitivityClass.PERSONAL if speaker == DialogueSpeaker.USER else SensitivityClass.ORDINARY,
        )

    # -- per-event transition ----------------------------------------------
    def apply(self, event: DialogueSourceEvent) -> None:
        exposure = event.exposure_state
        if exposure == DialogueExposureState.DELIVERY_PENDING:
            self.gap(event, DialogueGapKind.DELIVERY_PENDING, identity="exposure")
            return
        if exposure == DialogueExposureState.DELIVERY_FAILED:
            self.gap(event, DialogueGapKind.DELIVERY_FAILED, identity="exposure")
            return
        if exposure == DialogueExposureState.VISIBILITY_UNKNOWN:
            self.gap(event, DialogueGapKind.VISIBILITY_UNKNOWN, identity="exposure")
            return
        if exposure == DialogueExposureState.NOT_VISIBLE:
            if event.status == DialogueMessageStatus.FAILED:
                self.gap(event, DialogueGapKind.RESPONSE_FAILED, identity="exposure")
            # A cancelled empty outcome is processed metadata: no content item.
            return
        if event.role == DialogueRole.USER:
            self.user(event)
        else:
            self.assistant(event)

    def apply_exposure_update(self, event: DialogueSourceEvent) -> None:
        """A delivery that was pending when its position was consumed has settled."""

        pending_id = item_id_for(DialogueItemType.GAP, self.conversation_ref, DialogueGapKind.DELIVERY_PENDING.value,
                                 event.message_ref, "exposure")
        pending = self.items.get(pending_id)
        if pending is None or not pending.is_current:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                       "An exposure update names a message without a pending delivery gap.")
        if event.exposure_state == DialogueExposureState.DELIVERY_PENDING:
            return
        self.put(_rebuild(pending, status=DialogueItemStatus.RESOLVED))
        self.apply(event)

    def utterance(self, event: DialogueSourceEvent, speaker: DialogueSpeaker, refers_to: tuple[str, ...] = ()) -> None:
        complete = (event.status == DialogueMessageStatus.COMPLETED
                    and event.exposure_state == DialogueExposureState.DURABLY_VISIBLE)
        quote = None
        if event.content and len(event.content) <= MAX_QUOTE_CHARACTERS and event.content.strip() and valid_text(event.content):
            quote = DialogueQuote(text=event.content, span=DialogueSpanRef(
                message_ref=event.message_ref, start=0, end=len(event.content)))
        self.put(DialogueUtteranceReference(
            item_id=item_id_for(DialogueItemType.UTTERANCE_REFERENCE, self.conversation_ref, event.message_ref),
            **self.base(event, speaker), epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
            message_ref=event.message_ref, message_sequence=event.conversation_sequence,
            terminal_status=event.status, completeness="complete" if complete else "partial",
            exposure_state=event.exposure_state, source_channel=event.source_channel,
            content_digest=event.content_digest, character_count=len(event.content),
            attachment_count=len(event.attachment_refs), quote=quote,
            exposure_evidence_refs=event.exposure_evidence_refs, refers_to_refs=tuple(sorted(set(refers_to))),
        ))

    def entity(self, event: DialogueSourceEvent, piece: TextSpan, speaker: DialogueSpeaker) -> str:
        key = entity_key(piece.text)
        existing = next((item for item in _current(self.items.values(), DialogueEntity) if item.entity_key == key), None)
        if existing is not None:
            mentions = tuple(dict.fromkeys((*existing.mention_message_refs, event.message_ref)))[-MAX_ENTITY_MENTIONS:]
            sources = tuple(dict.fromkeys((existing.source_message_refs[0], *mentions)))
            self.put(_rebuild(existing, mention_message_refs=mentions, source_message_refs=sources,
                              last_mention_sequence=event.conversation_sequence,
                              last_supported_turn_ref=event.turn_key,
                              last_supported_sequence=event.conversation_sequence))
            return existing.item_id
        entity = DialogueEntity(
            item_id=item_id_for(DialogueItemType.ENTITY, self.conversation_ref, key, event.message_ref, str(piece.start)),
            **self.base(event, speaker, pieces=(piece,)), epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
            entity_key=key, mention_message_refs=(event.message_ref,),
            last_mention_sequence=event.conversation_sequence,
        )
        self.put(entity)
        return entity.item_id

    def user(self, event: DialogueSourceEvent) -> None:
        content = event.content
        before = event.conversation_sequence
        prior_items = self.ordered()
        raws = detect_cues(content)
        claimed = [(raw.span.start, raw.span.end) for raw in raws if raw.kind in CLAIMING_CUES]
        local = local_entity_refs(prior_items, content, conversation_ref=self.conversation_ref,
                                  message_ref=event.message_ref, excluded=claimed)
        mentions = tuple((piece.start, ref) for piece, _, ref, _ in local)
        cues = [(raw, resolve_cue(raw, prior_items, before_sequence=before, local_entities=mentions)) for raw in raws]
        refers_to = tuple(ref for _, cue in cues for ref in cue.resolved_refs)
        self.utterance(event, DialogueSpeaker.USER, refers_to)
        complete = event.exposure_state == DialogueExposureState.DURABLY_VISIBLE

        # A user reply is the evidence that closes Columbina's earlier questions.
        for question in _current(self.items.values(), DialogueQuestion):
            if (question.speaker == DialogueSpeaker.COLUMBINA and question.introduced_sequence < before
                    and question.introduced_turn_ref != event.turn_key):
                self.put(_rebuild(question, status=DialogueItemStatus.RESOLVED,
                                  resolution_basis="subsequent_user_turn", resolved_by_message_ref=event.message_ref))
        if not complete:
            return

        # Names first, so a cue resolved to a name in this same utterance cites
        # an entity that exists (under the id the resolution predicted).
        mentioned: dict[int, str] = {}
        for piece, _, ref, _ in local:
            created = self.entity(event, piece, DialogueSpeaker.USER)
            if created != ref:
                raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                           "An entity identity differs from the one its resolution predicted.")
            mentioned[piece.start] = created
        for raw, cue in cues:
            spans = (self.span(event, raw.span),)
            anchors = (raw.span.text,)
            if raw.kind == DialogueCueKind.CORRECTION:
                self.correction(event, raw, cue)
            elif raw.kind == DialogueCueKind.WITHDRAWAL:
                if cue.resolution == DialogueResolution.RESOLVED:
                    request = self.items[cue.resolved_refs[0]]
                    self.put(_rebuild(request, status=DialogueItemStatus.WITHDRAWN,
                                      withdrawn_by_message_ref=event.message_ref,
                                      last_supported_turn_ref=event.turn_key,
                                      last_supported_sequence=event.conversation_sequence,
                                      source_message_refs=tuple(dict.fromkeys((*request.source_message_refs,
                                                                               event.message_ref)))))
                else:
                    self.gap(event, DialogueGapKind.UNRESOLVED_WITHDRAWAL, identity=f"{raw.span.start}:{raw.span.end}",
                             cue=cue, spans=spans, anchors=anchors)
            elif raw.kind == DialogueCueKind.TOPIC_RETURN:
                self.topic(event, raw.topic, cue)
            elif raw.kind == DialogueCueKind.CONTINUATION and cue.resolution == DialogueResolution.RESOLVED:
                plan = self.items[cue.resolved_refs[0]]
                self.support(plan.item_id, event, continuation_message_refs=tuple(dict.fromkeys(
                    (*plan.continuation_message_refs, event.message_ref))))
            elif cue.resolution == DialogueResolution.RESOLVED:
                # A reference supports its current targets; an utterance keeps
                # exactly its own message as its source, and a name mentioned in
                # this utterance is already supported by it.
                for target in dict.fromkeys(ref.partition("#")[0] for ref in cue.resolved_refs):
                    item = self.items.get(target)
                    if (item is not None and item.is_current and not isinstance(item, DialogueUtteranceReference)
                            and item.last_supported_sequence != event.conversation_sequence):
                        self.support(target, event)
            else:
                self.gap(event, DialogueGapKind.UNRESOLVED_REFERENCE, identity=f"{raw.span.start}:{raw.span.end}",
                         cue=cue, spans=spans, anchors=anchors)

        for match in TOPIC_INTRO.finditer(content):
            topic = _group(content, match, "topic")
            if topic is not None and _anchor_ok(topic.text, MAX_TOPIC_ANCHOR):
                self.topic(event, topic, None)

        questions = requests = propositions = 0
        for sentence in sentence_spans(content):
            if any(sentence.start < end and start < sentence.end for start, end in claimed):
                continue
            if not _anchor_ok(sentence.text, MAX_SENTENCE_ANCHOR):
                continue
            if is_request_sentence(sentence.text):
                if requests < MAX_REQUESTS_PER_MESSAGE:
                    requests += 1
                    self.put(DialogueRequest(
                        item_id=item_id_for(DialogueItemType.REQUEST, self.conversation_ref, event.message_ref,
                                            f"{sentence.start}:{sentence.end}"),
                        **self.base(event, DialogueSpeaker.USER, pieces=(sentence,)),
                        epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
                    ))
                continue
            if sentence.text.endswith("?"):
                if questions < MAX_QUESTIONS_PER_MESSAGE:
                    questions += 1
                    self.put(DialogueQuestion(
                        item_id=item_id_for(DialogueItemType.QUESTION, self.conversation_ref, event.message_ref,
                                            f"{sentence.start}:{sentence.end}"),
                        **self.base(event, DialogueSpeaker.USER, pieces=(sentence,)),
                        epistemic_status=DialogueEpistemicStatus.UNRESOLVED, question_shape=question_shape(sentence.text),
                    ))
                continue
            if propositions >= MAX_PROPOSITIONS_PER_MESSAGE or len(sentence.text.split()) < 3:
                continue
            entity_refs = tuple(sorted({ref for start, ref in mentioned.items() if sentence.start <= start < sentence.end}))
            if not entity_refs and not FIRST_PERSON.search(sentence.text):
                continue
            if any(cue_span_inside(raw, sentence) for raw, _ in cues if raw.kind in {
                DialogueCueKind.CONTINUATION, DialogueCueKind.PRIOR_USER_STATEMENT_QUERY,
                DialogueCueKind.PRIOR_ASSISTANT_STATEMENT_QUERY,
            }):
                continue
            propositions += 1
            self.put(DialogueProposition(
                item_id=item_id_for(DialogueItemType.PROPOSITION, self.conversation_ref, event.message_ref,
                                    f"{sentence.start}:{sentence.end}"),
                **self.base(event, DialogueSpeaker.USER, pieces=(sentence,)),
                epistemic_status=DialogueEpistemicStatus.ATTRIBUTED, entity_refs=entity_refs,
            ))

    def topic(self, event: DialogueSourceEvent, piece: TextSpan | None, cue: DialogueCue | None) -> None:
        if piece is None:
            return
        key = topic_key(piece.text)
        if not key:
            return
        existing = next((item for item in _current(self.items.values(), DialogueTopic) if item.topic_key == key), None)
        if existing is not None:
            returns = existing.return_message_refs
            if cue is not None and event.message_ref not in returns:
                returns = (*returns, event.message_ref)
            self.support(existing.item_id, event, return_message_refs=returns)
            return
        self.put(DialogueTopic(
            item_id=item_id_for(DialogueItemType.TOPIC, self.conversation_ref, key, event.message_ref, str(piece.start)),
            **self.base(event, DialogueSpeaker.USER, pieces=(piece,)),
            epistemic_status=DialogueEpistemicStatus.ATTRIBUTED, topic_key=key,
        ))

    def correction(self, event: DialogueSourceEvent, raw: RawCue, cue: DialogueCue) -> None:
        spans = (self.span(event, raw.span),)
        if (cue.resolution != DialogueResolution.RESOLVED or raw.old is None or raw.new is None
                or entity_key(raw.old.text) == entity_key(raw.new.text)):
            self.gap(event, DialogueGapKind.UNRESOLVED_CORRECTION, identity=f"{raw.span.start}:{raw.span.end}",
                     cue=cue, spans=spans, anchors=(raw.span.text,))
            return
        old_entity = self.items[cue.resolved_refs[0]]
        new_id = self.entity(event, raw.new, DialogueSpeaker.USER)
        correction_id = item_id_for(DialogueItemType.CORRECTION, self.conversation_ref, event.message_ref,
                                    f"{raw.span.start}:{raw.span.end}")
        superseded = [item for item in _current(self.items.values(), DialogueProposition)
                      if old_entity.item_id in item.entity_refs]
        for proposition in superseded:
            self.put(_rebuild(proposition, status=DialogueItemStatus.SUPERSEDED, superseded_by_refs=(correction_id,)))
        self.put(_rebuild(old_entity, status=DialogueItemStatus.SUPERSEDED, superseded_by_ref=new_id))
        pieces = (raw.old, raw.new)
        self.put(DialogueCorrection(
            item_id=correction_id,
            **{**self.base(event, DialogueSpeaker.USER, pieces=pieces),
               "source_message_refs": tuple(dict.fromkeys((event.message_ref, *old_entity.source_message_refs)))},
            epistemic_status=DialogueEpistemicStatus.ATTRIBUTED, correcting_message_ref=event.message_ref,
            old_anchor=raw.old.text, new_anchor=raw.new.text,
            old_refs=tuple(sorted({old_entity.item_id, *(item.item_id for item in superseded)})),
            new_refs=(new_id,),
        ))

    def assistant(self, event: DialogueSourceEvent) -> None:
        self.utterance(event, DialogueSpeaker.COLUMBINA)
        if event.exposure_state != DialogueExposureState.DURABLY_VISIBLE or event.status != DialogueMessageStatus.COMPLETED:
            return  # partial or failed output never becomes completion, answer, plan, or promise evidence
        content = event.content
        if event.turn_ref is not None:
            # A completed, delivered reply in the same turn answers that turn's
            # questions and records a response to its requests (not fulfilment).
            for question in _current(self.items.values(), DialogueQuestion):
                if question.speaker == DialogueSpeaker.USER and question.introduced_turn_ref == event.turn_key:
                    self.put(_rebuild(question, status=DialogueItemStatus.RESOLVED,
                                      resolution_basis="completed_response_in_turn",
                                      resolved_by_message_ref=event.message_ref))
            for request in _current(self.items.values(), DialogueRequest):
                if request.introduced_turn_ref == event.turn_key and event.message_ref not in request.response_message_refs:
                    self.put(_rebuild(request, response_message_refs=(*request.response_message_refs, event.message_ref)))

        block = tuple(piece for piece in list_block(content) if _anchor_ok(piece.text, MAX_LIST_ITEM_ANCHOR))[:MAX_LIST_ITEMS]
        if len(block) >= 2:
            if PLAN_WORDS.search(content):
                plan_id = item_id_for(DialogueItemType.PLAN_REFERENCE, self.conversation_ref, event.message_ref)
                self.put(DialoguePlanReference(
                    item_id=plan_id, **self.base(event, DialogueSpeaker.COLUMBINA, pieces=block, anchors=0),
                    epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
                    steps=tuple(DialoguePlanStep(step_ref=plan_step_ref_for(plan_id, index), index=index,
                                                 surface_anchor=piece.text, span=self.span(event, piece))
                                for index, piece in enumerate(block, start=1)),
                ))
            else:
                set_id = item_id_for(DialogueItemType.OPTION_SET, self.conversation_ref, event.message_ref)
                self.put(DialogueOptionSet(
                    item_id=set_id, **self.base(event, DialogueSpeaker.COLUMBINA, pieces=block, anchors=0),
                    epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
                    options=tuple(DialogueOption(option_ref=option_ref_for(set_id, index), index=index,
                                                 surface_anchor=piece.text, span=self.span(event, piece))
                                  for index, piece in enumerate(block, start=1)),
                ))
        listed = [(piece.start, piece.end) for piece in block]
        questions = commitments = 0
        for sentence in sentence_spans(content):
            if any(sentence.start < end and start < sentence.end for start, end in listed):
                continue
            if not _anchor_ok(sentence.text, MAX_SENTENCE_ANCHOR):
                continue
            if sentence.text.endswith("?") and questions < MAX_QUESTIONS_PER_MESSAGE:
                questions += 1
                self.put(DialogueQuestion(
                    item_id=item_id_for(DialogueItemType.QUESTION, self.conversation_ref, event.message_ref,
                                        f"{sentence.start}:{sentence.end}"),
                    **self.base(event, DialogueSpeaker.COLUMBINA, pieces=(sentence,)),
                    epistemic_status=DialogueEpistemicStatus.UNRESOLVED, question_shape=question_shape(sentence.text),
                ))
            elif COMMITMENT.search(sentence.text) and commitments < MAX_COMMITMENTS_PER_MESSAGE:
                commitments += 1
                self.put(DialogueCommitmentReference(
                    item_id=item_id_for(DialogueItemType.COMMITMENT_REFERENCE, self.conversation_ref,
                                        event.message_ref, f"{sentence.start}:{sentence.end}"),
                    **self.base(event, DialogueSpeaker.COLUMBINA, pieces=(sentence,)),
                    epistemic_status=DialogueEpistemicStatus.ATTRIBUTED,
                ))
        for piece in entity_mentions(content, speaker=DialogueSpeaker.COLUMBINA, excluded=listed):
            self.entity(event, piece, DialogueSpeaker.COLUMBINA)

    # -- bounds ------------------------------------------------------------
    def bound_quotes(self) -> None:
        quoted = sorted((item for item in self.items.values()
                         if isinstance(item, DialogueUtteranceReference) and item.quote is not None),
                        key=lambda item: (item.message_sequence, item.item_id))
        for item in quoted[:-MAX_EXACT_QUOTES] if len(quoted) > MAX_EXACT_QUOTES else ():
            self.put(_rebuild(item, quote=None))

    def compact(self, limits: DialogueMemoryLimits, *, through: int) -> None:
        if len(self.items) <= limits.max_items:
            return
        protected = set(indexes(self.ordered()).protected)
        for item in self.items.values():
            if item.last_supported_sequence == through and through > 0:
                protected.add(item.item_id)  # the latest turn's referents are never dropped
            if isinstance(item, DialogueGap) and item.gap_kind == DialogueGapKind.DELIVERY_PENDING and item.is_current:
                protected.add(item.item_id)  # needed to settle a late delivery
            if isinstance(item, DialogueCorrection) and item.is_current:
                protected.add(item.item_id)  # current corrections outrank superseded content
        candidates = sorted(
            (item for item in self.items.values() if item.item_id not in protected),
            key=lambda item: (_tier(item), item.last_supported_sequence, item.introduced_sequence, item.item_id),
        )
        excess = len(self.items) - limits.max_items
        if excess > len(candidates):
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.REQUIRED_DIALOGUE_CONTENT_MISSING,
                       "Compaction would drop a referent the current turn requires.")
        removed = candidates[:excess]
        for item in removed:
            del self.items[item.item_id]
        self.record_compaction(removed)

    def record_compaction(self, removed: Sequence[Any]) -> None:
        """One rolling compaction record naming only removed items still cross-referenced."""

        previous = [item for item in self.omissions if item.reason == DialogueOmissionReason.COMPACTED]
        others = [item for item in self.omissions if item.reason != DialogueOmissionReason.COMPACTED]
        removed_ids = {item.item_id for item in removed} | {ref for item in previous for ref in item.item_refs}
        referenced = {ref.partition("#")[0] for item in self.items.values() for ref in _referenced(item)}
        starts = [item.introduced_sequence for item in removed] + [item.sequence_start for item in previous]
        ends = [item.last_supported_sequence for item in removed] + [item.sequence_end for item in previous]
        if not starts:
            return
        rolling = DialogueHistoryOmission(
            reason=DialogueOmissionReason.COMPACTED, sequence_start=min(starts), sequence_end=max(ends),
            item_refs=tuple(sorted(removed_ids & referenced)), recoverable=True,
        )
        self.omissions = sorted([*others, rolling], key=lambda item: item.sort_key)


def cue_span_inside(raw: RawCue, sentence: TextSpan) -> bool:
    return sentence.start <= raw.span.start and raw.span.end <= sentence.end


def _referenced(item: Any) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(item, DialogueEntity) and item.superseded_by_ref:
        refs.append(item.superseded_by_ref)
    if isinstance(item, DialogueProposition):
        refs.extend(item.superseded_by_refs)
    if isinstance(item, DialogueCorrection):
        refs.extend((*item.old_refs, *item.new_refs))
    if isinstance(item, DialogueUtteranceReference):
        refs.extend(item.refers_to_refs)
    return tuple(refs)


def _tier(item: Any) -> int:
    """Removal order under pressure: the least load-bearing lineage first."""

    if isinstance(item, DialogueGap):
        return 0
    if item.status in {DialogueItemStatus.RESOLVED, DialogueItemStatus.WITHDRAWN, DialogueItemStatus.INACCESSIBLE}:
        return 1
    if item.status == DialogueItemStatus.SUPERSEDED:
        return 2
    if isinstance(item, DialogueUtteranceReference):
        return 3
    if isinstance(item, (DialogueQuestion, DialogueRequest, DialogueCommitmentReference, DialogueTopic,
                         DialogueOptionSet, DialoguePlanReference)):
        return 4
    return 5


@dataclass(frozen=True)
class SnapshotIndexes:
    active_topic_refs: tuple[str, ...]
    focus_entity_refs: tuple[str, ...]
    unresolved_reference_refs: tuple[str, ...]
    open_question_refs: tuple[str, ...]
    active_request_refs: tuple[str, ...]
    active_option_set_refs: tuple[str, ...]
    active_plan_refs: tuple[str, ...]
    active_commitment_refs: tuple[str, ...]
    current_item_refs: tuple[str, ...]

    @property
    def protected(self) -> tuple[str, ...]:
        return (*self.active_topic_refs, *self.focus_entity_refs, *self.unresolved_reference_refs,
                *self.open_question_refs, *self.active_request_refs, *self.active_option_set_refs,
                *self.active_plan_refs, *self.active_commitment_refs)


def _most_recent(items: Iterable[Any], limit: int) -> list[Any]:
    return sorted(items, key=lambda item: (-item.last_supported_sequence, -item.introduced_sequence, item.item_id))[:limit]


def indexes(items: Sequence[Any]) -> SnapshotIndexes:
    """Derive every active index deterministically from the (sorted) items."""

    topics = _most_recent(_current(items, DialogueTopic), MAX_ACTIVE_TOPICS)
    entities = _current(items, DialogueEntity)
    latest_mention = max((item.last_mention_sequence for item in entities), default=None)
    reference_gaps = [item for item in _current(items, DialogueGap) if item.gap_kind in REFERENCE_GAP_KINDS]
    option_set = active_option_set(items)
    plan = active_plan(items)
    return SnapshotIndexes(
        active_topic_refs=tuple(item.item_id for item in topics),
        focus_entity_refs=tuple(sorted(item.item_id for item in entities if item.last_mention_sequence == latest_mention)),
        unresolved_reference_refs=tuple(sorted(item.item_id for item in _most_recent(reference_gaps, MAX_UNRESOLVED_REFERENCES))),
        open_question_refs=tuple(sorted(item.item_id for item in _most_recent(_current(items, DialogueQuestion), MAX_OPEN_QUESTIONS))),
        active_request_refs=tuple(sorted(item.item_id for item in _most_recent(_current(items, DialogueRequest), MAX_ACTIVE_REQUESTS))),
        active_option_set_refs=(option_set.item_id,) if option_set is not None else (),
        active_plan_refs=(plan.item_id,) if plan is not None else (),
        active_commitment_refs=tuple(sorted(item.item_id for item in _most_recent(
            _current(items, DialogueCommitmentReference), MAX_ACTIVE_COMMITMENTS))),
        current_item_refs=tuple(item.item_id for item in items if item.is_current),
    )


def build_snapshot(
    *, user_ref: str, conversation_ref: str, revision: int, predecessor_snapshot_ref: str | None, segment_id: str,
    segment_start_sequence: int, reset_reason: DialogueResetReason | None, through_conversation_sequence: int,
    through_message_id: str | None, source_event_count: int, source_prefix_digest: str, items: Sequence[Any],
    omitted_history: Sequence[DialogueHistoryOmission],
) -> DialogueMemorySnapshot:
    """Assemble, digest, identify, validate, and JSON-round-trip one snapshot."""

    ordered = sorted(items, key=lambda item: item.item_id)
    derived = indexes(ordered)
    data = dict(
        user_ref=user_ref, conversation_ref=conversation_ref, revision=revision,
        predecessor_snapshot_ref=predecessor_snapshot_ref, segment_id=segment_id,
        segment_start_sequence=segment_start_sequence, reset_reason=reset_reason,
        through_conversation_sequence=through_conversation_sequence, through_message_id=through_message_id,
        source_event_count=source_event_count, source_prefix_digest=source_prefix_digest,
        active_topic_refs=derived.active_topic_refs, focus_entity_refs=derived.focus_entity_refs,
        unresolved_reference_refs=derived.unresolved_reference_refs, open_question_refs=derived.open_question_refs,
        active_request_refs=derived.active_request_refs, active_option_set_refs=derived.active_option_set_refs,
        active_plan_refs=derived.active_plan_refs, active_commitment_refs=derived.active_commitment_refs,
        current_item_refs=derived.current_item_refs, items=tuple(ordered),
        omitted_history=tuple(sorted(omitted_history, key=lambda item: item.sort_key)),
        versions=DialogueMemoryVersions(),
    )
    provisional = DialogueMemorySnapshot.model_construct(snapshot_id="", semantic_digest="", **data)
    digest = snapshot_semantic_digest(provisional)
    try:
        snapshot = DialogueMemorySnapshot(snapshot_id=snapshot_id_for(conversation_ref, digest), semantic_digest=digest,
                                          **data)
        revalidated = DialogueMemorySnapshot.model_validate_json(canonical_json(snapshot.model_dump(mode="json")))
    except ValueError as exc:
        raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                   "The produced dialogue snapshot failed contract validation.") from exc
    if revalidated != snapshot:
        raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                   "The produced dialogue snapshot is not JSON round-trip stable.")
    return snapshot


class DialogueMemoryUpdaterV0:
    """``apply(predecessor, events)`` -> the deterministic successor snapshot."""

    version = UPDATER_VERSION

    def __init__(self, limits: DialogueMemoryLimits | None = None) -> None:
        self.limits = limits or DialogueMemoryLimits()

    @staticmethod
    def genesis(
        *, user_ref: str, conversation_ref: str, revision: int, segment_start_sequence: int,
        reset_reason: DialogueResetReason, omitted_history: Sequence[DialogueHistoryOmission] = (),
    ) -> DialogueMemorySnapshot:
        """The empty state a new segment begins from (never persisted on its own)."""

        from ..contracts.dialogue_memory import segment_id_for

        return build_snapshot(
            user_ref=user_ref, conversation_ref=conversation_ref, revision=revision, predecessor_snapshot_ref=None,
            segment_id=segment_id_for(conversation_ref, segment_start_sequence, reset_reason, revision),
            segment_start_sequence=segment_start_sequence, reset_reason=reset_reason,
            through_conversation_sequence=segment_start_sequence - 1, through_message_id=None, source_event_count=0,
            source_prefix_digest=genesis_prefix_digest(conversation_ref, segment_start_sequence), items=(),
            omitted_history=omitted_history,
        )

    def apply(
        self,
        predecessor: DialogueMemorySnapshot,
        events: Sequence[DialogueSourceEvent],
        *,
        exposure_updates: Sequence[DialogueSourceEvent] = (),
        continue_lineage: bool = True,
    ) -> DialogueMemorySnapshot:
        """Apply new events (and settled deliveries) to ``predecessor``.

        ``continue_lineage=False`` is used only by bootstrap/reset: the
        successor then keeps the genesis revision, segment, and reset reason
        instead of citing the (never persisted) genesis as its predecessor.
        """

        try:
            validated = DialogueMemorySnapshot.model_validate(predecessor.model_dump(mode="python"))
        except ValueError as exc:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                       "The predecessor snapshot failed contract validation.") from exc
        if validated != predecessor:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                       "The predecessor snapshot changed during validation.")
        if predecessor.versions != DialogueMemoryVersions():
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.PROJECTION_VERSION_UNSUPPORTED,
                       "The predecessor snapshot was produced under different versions.")
        validate_events(events, after_sequence=predecessor.through_conversation_sequence,
                        conversation_ref=predecessor.conversation_ref, user_ref=predecessor.user_ref)
        for update in exposure_updates:
            if (update.conversation_ref, update.user_ref) != (predecessor.conversation_ref, predecessor.user_ref):
                raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                           "An exposure update belongs to a different user or conversation.")
            if update.conversation_sequence > predecessor.through_conversation_sequence:
                raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.FUTURE_EVIDENCE_DETECTED,
                           "An exposure update names a message the predecessor has not consumed.")
        if len({item.message_ref for item in exposure_updates}) != len(exposure_updates):
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID,
                       "An exposure update appears twice in one transition.")

        state = _State(predecessor)
        try:
            # Bounds are enforced after every event, so the resulting state does
            # not depend on how events were batched (eager per-turn updates and
            # a lazy multi-turn catch-up reach the same items).
            for update in sorted(exposure_updates, key=lambda item: item.conversation_sequence):
                state.apply_exposure_update(update)
                state.bound_quotes()
                state.compact(self.limits, through=predecessor.through_conversation_sequence)
            prefix = predecessor.source_prefix_digest
            for event in events:
                state.apply(event)
                prefix = extend_prefix_digest(prefix, fingerprint_of(event))
                state.bound_quotes()
                state.compact(self.limits, through=event.conversation_sequence)
            through = events[-1].conversation_sequence if events else predecessor.through_conversation_sequence
        except DialogueMemoryError:
            raise
        except ValueError as exc:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                       "A dialogue memory item failed contract validation.") from exc

        if not events and not exposure_updates:
            return predecessor
        return build_snapshot(
            user_ref=predecessor.user_ref, conversation_ref=predecessor.conversation_ref,
            revision=predecessor.revision + 1 if continue_lineage else predecessor.revision,
            predecessor_snapshot_ref=predecessor.snapshot_id if continue_lineage else None,
            segment_id=predecessor.segment_id, segment_start_sequence=predecessor.segment_start_sequence,
            reset_reason=None if continue_lineage else predecessor.reset_reason,
            through_conversation_sequence=through,
            through_message_id=events[-1].message_ref if events else predecessor.through_message_id,
            source_event_count=predecessor.source_event_count + len(events), source_prefix_digest=prefix,
            items=state.ordered(), omitted_history=state.omissions,
        )


__all__ = [
    "DialogueMemoryUpdaterV0", "RawCue", "SnapshotIndexes", "TextSpan", "UPDATER_VERSION", "active_option_set",
    "active_plan", "build_snapshot", "detect_cues", "entity_key", "entity_mentions", "indexes",
    "CLAIMING_CUES", "is_request_sentence", "list_block", "local_entity_refs", "question_shape", "resolve_cue",
    "sentence_spans", "topic_key",
]
