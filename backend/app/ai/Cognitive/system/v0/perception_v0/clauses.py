"""Deterministic, quote-aware clause scan shared by Perception and Interpretation.

The scan splits the focus text into sentence-level clauses at terminators
that are outside quotation, removes permitted discourse prefixes (greetings,
an addressee, acknowledgements, discourse markers, a short comma-delimited
lead-in), and classifies whether the remaining clause is interrogative.

Punctuation supports detection but is not the only evidence: an owned
terminal ``?`` is sufficient, and an unpunctuated clause is interrogative when
it opens with subject-auxiliary inversion or a wh-word in an interrogative
construction.  False-positive controls keep relative clauses, subordinate
wh-clauses, exclamatives, and quoted questions out.  Nothing here is a broad
natural-language parser: every rule is a reviewed, inspectable pattern, and
nothing depends on particular evaluation sentences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLAUSE_SCANNER_VERSION = "quote-aware-clause-scan-v0.1"

_QUOTE_RE = re.compile(r'(?P<straight>"[^"\n]*")|(?P<curly>“[^”\n]*”)')
_INTRODUCED_QUOTE_RE = re.compile(
    r"(?:\b(?:said|says|wrote|asked|replied|called|read|reads)\b|\btool\b[^.?!]{0,12}\bsaid\b)\s*[:,-]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QuoteSpan:
    start: int
    end: int
    ownership: str  # other | ambiguous


def quote_spans(text: str) -> tuple[QuoteSpan, ...]:
    spans = []
    covered: set[int] = set()
    for match in _QUOTE_RE.finditer(text):
        prefix = text[max(0, match.start() - 40):match.start()]
        ownership = "other" if _INTRODUCED_QUOTE_RE.search(prefix) else "ambiguous"
        spans.append(QuoteSpan(match.start(), match.end(), ownership))
        covered.update(range(match.start(), match.end()))
    for index, character in enumerate(text):
        if character in {'"', "“", "”"} and index not in covered:
            # Malformed/unpaired quotation cannot establish reliable ownership.
            spans.append(QuoteSpan(index, len(text), "ambiguous"))
    spans.sort(key=lambda span: (span.start, span.end))
    return tuple(spans)


def containing_quote(position: int, spans: tuple[QuoteSpan, ...]) -> QuoteSpan | None:
    return next((span for span in spans if span.start <= position < span.end), None)


@dataclass(frozen=True, slots=True)
class Clause:
    """One sentence-level clause of the focus text.

    ``start``/``end`` bound the whole clause (terminator included, surrounding
    whitespace excluded).  ``body_start`` follows the permitted discourse
    prefixes; ``prefix_kinds`` names what was removed, in order.
    """

    start: int
    end: int
    body_start: int
    prefix_kinds: tuple[str, ...]
    # (kind, start, end) of each removed prefix unit, separators excluded.
    prefix_units: tuple[tuple[str, int, int], ...]
    terminator: str | None
    owned: bool
    ambiguous: bool
    interrogative: bool
    interrogative_basis: str | None

    def body(self, text: str) -> str:
        return text[self.body_start:self.end]

    def prefix(self, text: str) -> str:
        return text[self.start:self.body_start]


_WORD = r"[a-z][\w'’-]*"
# Elongated spellings ("heyy", "hiii", "hellooo") are benign variation.
_GREETING = r"(?:hey+|hi+|hello+|hiya|howdy|yo|greetings|good\s+(?:morning|afternoon|evening|day))"
_ACKNOWLEDGEMENT = (
    r"(?:ok|okay|k|alright|all\s+right|sure|yes|yeah|yep|yup|no|nope|thanks|thank\s+you|thx|ty|got\s+it|"
    r"cool|great|nice|perfect|understood|right|i\s+see)"
)
_MARKER = (
    r"(?:so|well|also|and|but|then|anyway|anyways|btw|by\s+the\s+way|um+|uh+|oh|ah|hmm+|quick\s+question|"
    r"question|one\s+more\s+thing|just\s+wondering|out\s+of\s+curiosity)"
)
_SEPARATOR = r"(?:\s*[,;:!—–-]+\s*|\s+)"
_GREETING_RE = re.compile(rf"{_GREETING}\b", re.IGNORECASE)
_ACKNOWLEDGEMENT_RE = re.compile(rf"{_ACKNOWLEDGEMENT}\b", re.IGNORECASE)
_MARKER_RE = re.compile(rf"{_MARKER}\b", re.IGNORECASE)
_VOCATIVE_RE = re.compile(rf"(?P<word>{_WORD})", re.IGNORECASE)
_SEPARATOR_RE = re.compile(_SEPARATOR)
_PUNCTUATED_SEPARATOR_RE = re.compile(r"\s*[,;:!—–-]+\s*")
# A short comma/colon lead-in ("Columbina,", "Quick one:", "If so,") before an
# interrogative clause.  Longer lead-ins are left intact.
_LEAD_IN_RE = re.compile(rf"(?:{_WORD})(?:\s+{_WORD}){{0,2}}\s*[,:;—–]\s*", re.IGNORECASE)

_PRONOUN = r"(?:i|you|he|she|it|we|they)"
_DETERMINER = r"(?:this|that|these|those|there|a|an|the|my|your|his|her|its|our|their|any|some|anyone|someone|anything|something|everyone|everything)"
_BE = r"(?:is|are|am|was|were|isn't|aren't|wasn't|weren't|isn’t|aren’t|wasn’t|weren’t)"
_DO_PAST = r"(?:does|did|doesn't|didn't|doesn’t|didn’t)"
_DO = r"(?:do|don't|don’t)"
_MODAL = r"(?:can|could|would|will|should|shall|might|must|can't|won't|wouldn't|couldn't|shouldn't|can’t|won’t|wouldn’t|couldn’t|shouldn’t)"
_HAVE = r"(?:has|have|had|hasn't|haven't|hadn't|hasn’t|haven’t|hadn’t)"
_AUX = rf"(?:{_BE}|{_DO_PAST}|{_DO}|{_MODAL}|{_HAVE}|may)"

# Subject-auxiliary inversion at clause start.  Imperatives that share a first
# word ("Do this", "Have a look", "May 5 works") are excluded by requiring a
# plausible subject for the ambiguous auxiliaries.
_INVERSION_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    rf"{_BE}\s+{_WORD}",
    rf"{_DO_PAST}\s+{_WORD}",
    rf"{_DO}\s+(?:i|you|we|they|these|those|the|a|an|my|your|our|their|any|some|people|most)\b",
    rf"{_MODAL}\s+(?:{_PRONOUN}|{_DETERMINER})\b",
    rf"may\s+(?:i|we|you|it|this|that|there)\b",
    rf"{_HAVE}\s+(?:{_PRONOUN}|anyone|someone|there|the|this|that)\b",
))
_WH = r"(?:what|who|whom|whose|which|where|when|why|how)"
_WH_PATTERNS = tuple((basis, re.compile(pattern, re.IGNORECASE)) for basis, pattern in (
    ("wh_quantity", r"how\s+(?:much|many|long|often|far|old|big|tall|heavy|soon|come)\b"),
    ("wh_elliptical", r"(?:what|how)\s+about\b|what\s+if\b"),
    ("wh_contraction", rf"{_WH}(?:'s|’s|'re|’re|'ve|’ve|'d|’d|'ll|’ll)\b"),
    # where/when/why/how need the auxiliary immediately ("when does"), which
    # rejects subordinate clauses such as "when I ate".
    ("wh_inversion", rf"(?:where|when|why|how)\s+{_AUX}\b"),
    # what/which/who/whose may take a short non-pronominal phrase first
    # ("what time does", "which brand is"); a pronoun there ("what I did")
    # marks a free relative, not a question.
    ("wh_inversion", rf"(?:what|which|who|whom|whose)(?:\s+(?!(?:{_PRONOUN}|this|that|these|those)\b){_WORD}){{0,2}}\s+{_AUX}\b"),
))
# Non-interrogative uses at clause start.
_WH_EXCLUSIONS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"what\s+an?\b",          # exclamative: "what a meal"
    r"which\s+is\s+why\b",    # connective idiom
))
_OPENER_RE = re.compile(rf"(?:{_WH}|{_AUX})\b", re.IGNORECASE)
_TERMINATOR_RE = re.compile(r"[.?!]+|\n+")


def _segments(text: str, quotes: tuple[QuoteSpan, ...]) -> list[tuple[int, int, str | None]]:
    """(start, end, terminator) for each sentence outside quotation."""

    segments: list[tuple[int, int, str | None]] = []
    cursor = 0
    for match in _TERMINATOR_RE.finditer(text):
        if containing_quote(match.start(), quotes) is not None:
            continue
        following = text[match.end():match.end() + 1]
        run = match.group(0)
        if run[0] == "." and following and not following.isspace() and following not in {'"', "”", ")", "'"}:
            continue  # decimals, abbreviations, and file names are not boundaries
        terminator = None if run.startswith("\n") else ("?" if "?" in run else run[-1])
        end = match.start() if terminator is None else match.end()
        segments.append((cursor, end, terminator))
        cursor = match.end()
    segments.append((cursor, len(text), None))
    trimmed = []
    for start, end, terminator in segments:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            trimmed.append((start, end, terminator))
    return trimmed


def _is_opener(text: str, position: int, end: int) -> bool:
    return bool(_OPENER_RE.match(text, position, end))


def _strip_prefixes(text: str, start: int, end: int) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """Remove permitted discourse prefixes from the front of a clause.

    Punctuated prefixes ("Thanks, ...", "No, ...") are removed wherever they
    occur; unpunctuated ones ("hi columbina how ...", "ok so can ...") only
    when an interrogative opener or another prefix follows, so ordinary
    statements ("no problem") keep their first word.  A short comma lead-in
    ("Columbina, ...", "If so, ...") is removed only before an opener.
    """

    position, units = start, []
    for _ in range(6):  # bounded: at most six prefix units
        matched = False
        for kind, pattern in (("greeting", _GREETING_RE), ("acknowledgement", _ACKNOWLEDGEMENT_RE),
                              ("discourse_marker", _MARKER_RE)):
            unit = pattern.match(text, position, end)
            if unit is None:
                continue
            separator = _SEPARATOR_RE.match(text, unit.end(), end)
            if separator is None:
                continue
            after = separator.end()
            if after >= end:
                continue
            punctuated = bool(_PUNCTUATED_SEPARATOR_RE.fullmatch(text, unit.end(), after))
            if kind == "greeting":
                vocative = _VOCATIVE_RE.match(text, after, end)
                if vocative is not None and not _is_opener(text, after, end):
                    vocative_separator = _SEPARATOR_RE.match(text, vocative.end(), end)
                    if vocative_separator is not None and vocative_separator.end() < end:
                        vocative_after = vocative_separator.end()
                        vocative_punctuated = bool(
                            _PUNCTUATED_SEPARATOR_RE.fullmatch(text, vocative.end(), vocative_after)
                        )
                        if vocative_punctuated or _is_opener(text, vocative_after, end):
                            units.extend(((kind, unit.start(), unit.end()),
                                          ("addressee", vocative.start(), vocative.end())))
                            position, matched = vocative_after, True
                            break
            if punctuated or _is_opener(text, after, end) or _starts_prefix(text, after, end):
                units.append((kind, unit.start(), unit.end()))
                position, matched = after, True
                break
        if not matched:
            break
    if not _is_opener(text, position, end):
        lead_in = _LEAD_IN_RE.match(text, position, end)
        if lead_in is not None and lead_in.end() < end and _is_opener(text, lead_in.end(), end):
            words_end = lead_in.start() + len(lead_in.group(0).rstrip(",:;—– \t"))
            units.append(("lead_in", lead_in.start(), words_end))
            position = lead_in.end()
    return position, tuple(units)


def _starts_prefix(text: str, position: int, end: int) -> bool:
    return any(pattern.match(text, position, end) for pattern in (_GREETING_RE, _ACKNOWLEDGEMENT_RE, _MARKER_RE))


def _interrogative_basis(text: str, body_start: int, end: int, terminator: str | None) -> str | None:
    if terminator == "?":
        return "question_mark"
    if terminator == "!":
        return None  # exclamatives are not questions without an owned "?"
    if any(pattern.match(text, body_start, end) for pattern in _WH_EXCLUSIONS):
        return None
    for basis, pattern in _WH_PATTERNS:
        if pattern.match(text, body_start, end):
            return basis
    if any(pattern.match(text, body_start, end) for pattern in _INVERSION_PATTERNS):
        return "aux_inversion"
    return None


def scan_clauses(text: str) -> tuple[Clause, ...]:
    """Every sentence-level clause of ``text`` with ownership and interrogative status."""

    quotes = quote_spans(text)
    clauses = []
    for start, end, terminator in _segments(text, quotes):
        body_start, units = _strip_prefixes(text, start, end)
        kinds = tuple(kind for kind, _, _ in units)
        opener_quote = containing_quote(body_start, quotes)
        candidate = _interrogative_basis(text, body_start, end, terminator)
        # Ownership follows the decisive evidence: a terminal "?" is always
        # outside quotation (quoted terminators never end a clause); an
        # opener-based reading needs the opener itself to be the speaker's.
        owned = candidate == "question_mark" or opener_quote is None
        basis = candidate if owned else None
        ambiguous = False
        if basis is None:
            # Would the clause be a question if the quoted material were the speaker's?
            question_marks = [
                index for index in range(start, end) if text[index] == "?"
                and (span := containing_quote(index, quotes)) is not None and span.ownership == "ambiguous"
            ]
            ambiguous_opener = (
                opener_quote is not None and opener_quote.ownership == "ambiguous" and candidate is not None
            )
            ambiguous = bool(question_marks) or ambiguous_opener
        clauses.append(Clause(
            start=start, end=end, body_start=body_start, prefix_kinds=kinds, prefix_units=units,
            terminator=terminator,
            owned=owned, ambiguous=ambiguous, interrogative=basis is not None, interrogative_basis=basis,
        ))
    return tuple(clauses)


def interrogative_clauses(text: str) -> tuple[Clause, ...]:
    return tuple(clause for clause in scan_clauses(text) if clause.interrogative)


__all__ = [
    "CLAUSE_SCANNER_VERSION", "Clause", "QuoteSpan", "containing_quote", "interrogative_clauses",
    "quote_spans", "scan_clauses",
]
