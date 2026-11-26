"""Her practice notes: what she learns from using her tools, the way a person learns a tool by using it
(the tool-learning revamp, 2026-11-26; replaces the old learner for tool use).

Pure: no database, no Redis, no model client. ``services/practice.py`` keeps the trail and the notes; this module
says what a review reads, what it may write back, and how notes read to her.

- **What it learns from.** One stretch of her hands on a device (a ``surface``: the phone today, her desktop later):
  what the user asked, every step she took (the tool, what she said to it, where it landed, what came back), the
  screen as text after each step and a few pictures with where she aimed drawn on, then what she said.
- **What it writes.** Notes, per place (an app or game) and optionally per tool, in plain words: what works there,
  what doesn't, and ideas to try next time (``to_try``) that the next review settles from what happened. No
  certainty grades, no attribution of blame, nothing about the user or the conversation: how to use the tool well.
  A wrong note is rewritten or removed, the way you'd fix your own notes.
- **How she reads them.** With the screen, the first time a place comes up in a turn: "Your notes from using X".
  Experience of hers, not instructions; what the user asks now always comes first. The user's own notes (written
  by them or by her on their word) are marked as theirs and a review never rewrites them.
- **Tool descriptions say what a tool does**, true in every app; notes say what worked for her, where. When a note and
  a description disagree about what the tool does, the description is the code's own truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

NOTE_CHARS = 500  # the most a review may write; it is asked for NOTE_AIM
NOTE_AIM = 300
EVIDENCE_CHARS = 300
PLACE_CHARS = 80
NOTES_PER_PLACE = 12  # her practice notes per place, held by check(): past it a review merges, never piles up
USER_NOTES_PER_PLACE = 8  # the user's own notes per place (services/practice.keep_user_note): past it one is replaced
NOTES_SHOWN = NOTES_PER_PLACE + USER_NOTES_PER_PLACE  # with a screen, per place: all of them, bounded by the two caps
STEP_TEXT_CHARS = 1500  # a screen's text, per step, in the review
STEPS_REVIEWED = 120
PICTURES_REVIEWED = 20
REMIND_EVERY = 15  # her actions in one place before its notes are shown to her again

Status = Literal["known", "to_try"]


@dataclass(frozen=True)
class Note:
    id: str
    place: str
    tool: str | None
    status: Status
    text: str
    source: Literal["practice", "user"] = "practice"
    evidence: str | None = None
    updated: str | None = None  # when it last changed, as a date for her
    had: bool = False  # in front of her during the stretch being reviewed


@dataclass
class Step:
    """One action of hers, as code recorded it (never text she typed)."""

    at: float
    turn: str
    tool: str  # her tool: phone_tap_on, phone_tap, read_phone_screen, ...
    did: str  # what she did, in code's words ("tapped point (412, 1630)")
    said: str | None = None  # what she said to the tool, when it takes words (phone_tap_on's target)
    item: str | None = None  # the numbered item she acted on, as the screen labelled it
    place: str | None = None  # the app in front after it
    result: str = ""  # the tool's answer, first line
    screen: str | None = None  # the screen after it, as text (cut)
    picture: int | None = None  # index of the picture she aimed on (with her marks), when kept
    aims: list[dict] | None = None  # phone_tap_on: each target she described and where it landed ({"said", "at"})


@dataclass
class Stretch:
    """What one review reads."""

    surface: str
    asked: list[str]  # the user's words while her hands worked, oldest first
    replies: list[str]  # what she said meanwhile
    steps: list[Step]
    pictures: list[str] = field(default_factory=list)  # captions, by index (the images travel beside the text)
    notes: list[Note] = field(default_factory=list)  # her notes for the places in this stretch


class NoteChange(BaseModel):
    action: Literal["create", "rewrite", "remove"]
    note: str | None = Field(None, description="the note's id, for rewrite and remove")
    place: str = Field(description="the app or game, as the screen names it")
    tool: str | None = Field(None, description="the tool it's about, if it's about one tool")
    status: Status = Field("known", description="known: seen in this or an earlier stretch; to_try: an idea not "
                                                "tried yet")
    text: str = Field("", description="the note, in her own words, first person, plain and specific")
    evidence: str = Field("", description="what in this stretch shows it (step numbers, what happened)")


class Tally(BaseModel):
    what: str = Field(description="one kind of action she kept doing, in a few words (\"deploy troops on the map\", "
                                  "\"select a troop card\")")
    tried: int = Field(ge=0, description="how many times she tried it in this stretch")
    worked: int = Field(ge=0, description="how many of those did what she meant, as the screens and pictures show")


class Review(BaseModel):
    summary: str = Field(description="one or two sentences: what she was doing and how it went with her tools")
    user_notes: str = Field("", description="for each of the user's notes she followed or was meant to follow here: "
                                            "whether she did, and what happened when she did; empty when none applied")
    tallies: list[Tally] = Field(default_factory=list, description="the actions she repeated most, counted")
    changes: list[NoteChange] = Field(default_factory=list)


SYSTEM = """\
You are Jophiel looking back over a stretch where your hands used a device, to keep notes that make you better next
time: at using your tools, and at what you were doing there, the way a person gets better at something by doing it
and watching what happens.

You get what the user asked, every step you took (the tool, what you said to it, where it landed, what came back),
the screen after each step as text, some pictures (where you aimed is drawn on in red: a ring per tap, an arrow per
swipe, numbered in order), what you said, and your notes so far for these places.

Look for what this stretch teaches about using the tools here:
- What worked and what didn't: a tap that missed (the screen after didn't change the way you meant, or the marked
  point isn't on the thing), a description the pointing tool got wrong and the one it got right, a step you didn't
  need, a quicker way you found or could have used, how this app behaves (what's where, what each screen leads to,
  how long it takes to load, what interrupts).
- How well the task itself went, if the stretch shows it (a game's result screen, the thing the user wanted done or
  not), and what in your use of the tools made the difference.
- Your "to try" notes: if you tried one, rewrite it as known with what happened; if it didn't work, say so or remove
  it.
- Every phone_tap_on target: compare what you described with what is under its numbered mark in the picture. When
  it landed on something else (a card or button instead of the ground, the wrong card), work out which kinds of
  descriptions the pointing tool gets right here and which it gets wrong, and what to use instead (a different
  description, or your own pixels with phone_tap_point / phone_tap_points).
- When the task went badly or slowly, find the specific cause in how you used the tools: compare where your marks
  actually landed with what is there in the pictures (a card bar, a button, a wall, water), not what you meant to
  hit. Write the note that would have prevented it.
- How the place itself behaves, beyond your tools: what happened in response to what you did, and what the place
  did by itself. In a game, what hurt you and what didn't (a building whose shots wiped out a group of troops, a
  trap where troops vanished), what worked against what, what the result screen credits or blames, what it costs
  and how long things take; in an app, what it does after an action, what it asks for, where things end up. Only
  what the pictures and screens show (cause and effect you can see across them, a count that changed, a message
  the place gave), never what you'd expect from knowing the game or app. Describe things by how they look in the
  pictures (colour, shape, where they sit) as well as their name if the screen gives one.
- One stretch shows what happened once. A new note about how the place behaves (no tool) is kept as to_try whatever
  status you give it ("seems to"; what would show it again); it becomes known when a later stretch agrees and you
  rewrite it as known. A stretch that contradicts a note of yours says so: rewrite or remove it.
- Notes marked "you had it" were in front of you during this stretch. If you made the mistake one of them warns
  about anyway, it wasn't specific enough to stop you: rewrite it sharper (where exactly, what it looks like, what
  to do instead) rather than adding another note beside it.
- When the same kind of action both failed and worked in this stretch (some taps refused or missed, others did what
  you meant), the note says what the ones that worked had in common: where they landed in the picture and what was
  there, so next time you can aim at that. A note that only tells you to look harder or be careful ("inspect the
  boundary", "check first") changes nothing next time: say what to do instead, or don't write it.

Write notes that will help the next time you're in this place, about the tools and about the place, never about the
user, the conversation or yourself in general. A note about the place with no single tool behind it has no tool.
- One concrete thing per note, about {aim} characters at most (never over {limit}), in your words, first person: "Describing troop cards by look and label works with
  phone_tap_on ('old man with white beard, labelled 60'); by game name ('Grand Warden') it picked the wrong card."
- Say where things are in the picture's pixels, with the picture's size and orientation and which screen it is, so
  you can aim by it next time. Read positions off the grid's labelled lines drawn on the picture: they are the
  picture's own pixels (its size is in the caption), the same ones you tap. Don't record positions that only hold
  for one army or one base (which card sits where changes with the army): "In battle (landscape 1600x720) the troop cards are the bottom row, y about 600-650;
  tapping there only selects a card. Deploy by tapping ground above y 560."
- known: you saw it in this stretch or an earlier one (for how the place behaves: as above). to_try: an idea you
  haven't tried, or something the place did once, said as one ("Next time, try dropping a whole troop group in one
  phone_tap_points"; "The purple tower near the centre seemed to destroy my dragons fastest; watch it next attack").
- One lesson is one note, whatever tools it touches. If a note of yours already says it (under any tool, or none),
  rewrite that one and remove the others that repeat it; never add beside them. Give a tool only when the note is
  about how that one tool behaves here (what it hit, what it took to make it work); a lesson about the place (where
  things can be done, what a screen leads to) has no tool. Remove one this stretch shows is wrong. Keep at most
  {per_place} per place: merge when you'd go over.
- Step numbers, and positions of things that only stood in this attack or on this screen, go in evidence, not in
  the note: the note is read later, when those steps are gone.
- Notes marked as the user's are theirs: never rewrite or remove them; add your own beside them if experience
  differs. In user_notes, say for each one that applied here whether you followed it and what happened when you
  did (what the pictures and screens showed), so the user can see whether their advice works.
- In tallies, count the one to three actions you repeated most (tried, and how many did what you meant): the same
  wording each time for the same action, so stretches can be compared.
- A tool's description says what the tool does; don't write notes that repeat it. Your notes are what you found
  using it here.
- Nothing worth keeping is a fine answer: no changes. Don't invent lessons from a stretch where things just worked
  as expected and nothing new showed.
"""


def system_prompt() -> str:
    return SYSTEM.format(per_place=NOTES_PER_PLACE, aim=NOTE_AIM, limit=NOTE_CHARS)


def _cut(text: str | None, limit: int) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


def note_line(note: Note, *, review: bool = False) -> str:
    whose = "the user's" if note.source == "user" else ("to try" if note.status == "to_try" else "known")
    tool = f", {note.tool}" if note.tool else ""
    had = ", you had it" if review and note.had else ""
    return f"- ({whose}{tool}{had}) {note.text}"


def render_stretch(stretch: Stretch) -> str:
    """The stretch as text for the reviewer (the pictures travel beside it, captioned by index)."""
    out = [f"Surface: {stretch.surface}."]
    if stretch.asked:
        out.append("What the user said while your hands worked (oldest first):")
        out += [f"- \"{_cut(a, 600)}\"" for a in stretch.asked]
    if stretch.replies:
        out.append("What you said:")
        out += [f"- \"{_cut(r, 400)}\"" for r in stretch.replies]
    steps = stretch.steps[-STEPS_REVIEWED:]
    skipped = len(stretch.steps) - len(steps)
    out.append(f"Your steps ({len(stretch.steps)}{f'; the first {skipped} not shown' if skipped else ''}):")
    turn = None
    for i, s in enumerate(steps, skipped + 1):
        if s.turn != turn:
            if turn is not None:
                out.append("  (a new turn of yours starts here)")
            turn = s.turn
        head = f"{i}. {s.tool}: {s.did}"
        if s.aims:
            head += " — you described, and where each landed (mark number): " + "; ".join(
                f"{n}. \"{_cut(a.get('said'), 120)}\" → {tuple(a.get('at') or ())}" for n, a in enumerate(s.aims, 1))
        elif s.said:
            head += f" — you said \"{_cut(s.said, 200)}\""
        if s.item:
            head += f" — item {_cut(s.item, 160)}"
        if s.picture is not None:
            head += f" — aimed on picture {s.picture}"
        out.append(head)
        out.append(f"   came back: {_cut(s.result, 240)}")
        if s.screen:
            out.append(f"   screen after: {_cut(s.screen, STEP_TEXT_CHARS)}")
    if stretch.pictures:
        out.append("Pictures (attached in this order):")
        out += [f"- picture {i}: {caption}" for i, caption in enumerate(stretch.pictures)]
    places = sorted(acted_places(stretch.steps))
    out.append(f"Places in this stretch: {', '.join(places) if places else 'none named'}.")
    if stretch.notes:
        out.append("Your notes so far for these places (id, then the note):")
        out += [f"- {n.id} [{n.place}] {note_line(n, review=True)[2:]}" for n in stretch.notes]
    else:
        out.append("You have no notes for these places yet.")
    return "\n".join(out)


@dataclass(frozen=True)
class Checked:
    action: str
    note: str | None
    place: str
    tool: str | None
    status: Status
    text: str
    evidence: str


_STEP_REFS = re.compile(r"\s*\((?:see\s+)?steps?\s+\d[\d,\s\-–&and]*\)", re.IGNORECASE)


def _text(text: str) -> str:
    """A note's text as kept: one line, without references to this stretch's step numbers (they belong in its
    evidence; the note is read when those steps are gone)."""
    return " ".join(_STEP_REFS.sub("", text or "").split())


LOOKING = frozenset({"read_phone_screen", "phone_press", "recent_phone_screens", "end_phone_control"})


def acted_places(steps: list[Step]) -> set[str]:
    """Places where her hands did something (not only looked or pressed back / home on the way through)."""
    return {s.place for s in steps if s.place and s.tool not in LOOKING}


def check(review: Review, stretch: Stretch, *, tools: set[str]) -> tuple[list[Checked], list[str]]:
    """Keep only changes the stretch can carry: a note about a place in it (or one already kept), never the user's
    notes, an existing id for rewrite / remove, text within bounds. A new note about how the place behaves (no tool)
    starts as to_try: one stretch is one time; a later review that agrees makes it known. Returns (kept, why each
    other went)."""
    places = acted_places(stretch.steps)
    by_id = {n.id: n for n in stretch.notes}
    canonical = {p.lower(): p for p in places | {n.place for n in stretch.notes}}
    kept: list[Checked] = []
    dropped: list[str] = []
    removed = {c.note for c in review.changes if c.action == "remove" and c.note in by_id
               and by_id[c.note].source != "user"}
    held: dict[str, int] = {}  # her practice notes per place once this review's removes are done
    for n in stretch.notes:
        if n.source != "user" and n.id not in removed:
            held[n.place.lower()] = held.get(n.place.lower(), 0) + 1
    for c in review.changes:
        place = canonical.get(_cut(c.place, PLACE_CHARS).lower())
        if c.action in ("rewrite", "remove"):
            old = by_id.get(c.note or "")
            if old is None:
                dropped.append(f"{c.action}: no note {c.note!r} was shown")
                continue
            if old.source == "user":
                dropped.append(f"{c.action}: {old.id} is the user's note")
                continue
            place = old.place
        if place is None:
            dropped.append(f"{c.action}: place {c.place!r} isn't in this stretch")
            continue
        tool = c.tool if c.tool in tools else None
        text = _text(c.text)
        if c.action != "remove" and not text:
            dropped.append(f"{c.action}: no text")
            continue
        if len(text) > NOTE_CHARS:
            dropped.append(f"{c.action}: text over {NOTE_CHARS} characters")
            continue
        if c.action == "create":
            if held.get(place.lower(), 0) >= NOTES_PER_PLACE:
                dropped.append(f"create: {place} already has {NOTES_PER_PLACE} of her notes (rewrite or merge instead)")
                continue
            held[place.lower()] = held.get(place.lower(), 0) + 1
        status = "to_try" if c.action == "create" and tool is None else c.status
        kept.append(Checked(c.action, c.note if c.action != "create" else None, place, tool, status, text,
                            _cut(c.evidence, EVIDENCE_CHARS)))
    return kept, dropped


def notes_text(place: str, notes: list[Note]) -> str:
    """What she reads with a screen, the first time a place comes up in a turn."""
    shown = notes[:NOTES_SHOWN]
    more = f" ({len(notes) - len(shown)} more not shown)" if len(notes) > len(shown) else ""
    return (f"Your notes from using {place}{more}: your own experience here, to weigh with what you see; what the "
            "user asks now comes first.\n" + "\n".join(note_line(n) for n in shown))
