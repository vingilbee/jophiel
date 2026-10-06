"""What the user actually heard of a spoken reply.

A voice reply is written first and spoken clause by clause. When the user cuts
it off, everything after that point was generated but never heard, and the
assistant must not later treat it as said ("as I mentioned..."). The browser
reports how far playback got, in characters of the stored reply; this module
turns that into the text the model and memory should see.

The full generated text stays in ``chat_messages.content`` (diagnostics, UI
fading); ``heard_chars`` records the cut. Precision: clause boundaries are
exact, but the voice engine has no word timings, so inside the clause that was
playing the cut is estimated from the fraction of its audio played and then
moved back to a word boundary. Erring early is the safe direction — at worst
she repeats a few words the user did hear.
"""

from __future__ import annotations

from app.services import stickers

# Shown to the model in place of the unheard rest of an interrupted reply.
INTERRUPTED_MARKER = "[interrupted: the user did not hear the rest of this reply]"


def heard_cut(content: str, heard_chars: int | None) -> int:
    """Index where heard text ends, snapped back to a word boundary."""
    if heard_chars is None or heard_chars >= len(content):
        return len(content)
    cut = max(0, heard_chars)
    if cut > 0 and not content[cut].isspace() and not content[cut - 1].isspace():
        # Mid-word: keep only whole words.
        space = max(content.rfind(" ", 0, cut), content.rfind("\n", 0, cut))
        cut = space + 1 if space >= 0 else 0
    return cut


def heard_text(content: str, heard_chars: int | None) -> str:
    """``content`` as the user heard it, with a marker where it was cut off."""
    cut = heard_cut(content, heard_chars)
    if cut >= len(content):
        return content
    heard = content[:cut].rstrip()
    return f"{heard} {INTERRUPTED_MARKER}" if heard else INTERRUPTED_MARKER


def raw_end_of_spoken(raw: str, start: int, spoken: str) -> int:
    """Index in ``raw`` just past the text the spoken clause ``spoken`` covers.

    Spoken clauses are the reply with sticker markers removed and whitespace
    trimmed by the clause chunker, so a clause's non-space characters are
    exactly the next non-space characters of ``raw`` (from ``start``) that lie
    outside sticker markers. Counting them maps the clause back onto the
    stored reply without any fuzzy matching.
    """
    need = sum(1 for c in spoken if not c.isspace())
    markers = [(m.start(), m.end()) for m in stickers.MARKER.finditer(raw)]
    i = start
    while need and i < len(raw):
        inside = next((end for begin, end in markers if begin <= i < end), None)
        if inside is not None:
            i = inside
            continue
        if not raw[i].isspace():
            need -= 1
        i += 1
    return i
