"""Her picture of a place: what she knows of an app or game while she uses it, the way a person keeps track of
what they're doing and what's where without looking again (2026-11-27; kept as entries since 2026-11-27).

Pure: no database, no Redis, no model client. ``services/place_memory.py`` keeps the pictures and runs the updater;
this module says what the updater reads and writes, and how a picture reads to her.

- **This sitting** (gone an hour after she last used the place): what she's doing (one line), where she is in the
  place (one line, by what's around her), the world she's in when the screen names one (a Growtopia world, a level,
  a server), and a short list of what she's keeping track of (troops she dropped and where, what's still in hand, a
  timer, a price she saw). An entry stays until she sees it's gone: out of view is not gone.
- **What lasts** (kept for next time, per place and world): entries of four kinds: ``control`` (fixed to the
  screen, in pixels with the screen's size), ``landmark`` and ``area`` (her own village, farm or world: by how
  they look and what's around them), ``thing`` (what she saw somewhere, filed ``near`` a landmark or area so she
  still knows it after she moves on). Someone else's things (an opponent's base) never last.
- **Edits, not rewrites.** The updater gets the picture so far with an id on every entry, her actions since its
  last update, the screenshot it saw last time and the newest one, and answers with edits (add / change / remove),
  each with a ``why`` that goes to the trace and never into her memory. Nothing to change = no edits: an entry it
  doesn't name is kept word for word.
- **Positions.** Controls fixed to the screen in pixels; things in the place by what's around them, never pixels
  (they move with every zoom and pan). The two screenshots and her swipes and pinches say how the view moved.
- **Her own lines** (``keep_in_mind``) sit beside it and are hers: the updater reads them, never rewrites them.
- **Not her practice notes**: those are how a place works in general; this is where things stand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

LINE_CHARS = 200  # "doing" and "where"
ENTRY_CHARS = 240
WORLD_CHARS = 80
HER_CHARS = 400
SITTING_MAX = 15  # entries kept for this sitting
LASTING_MAX = 60  # lasting entries per place and world
LASTING_SHOWN = 30  # lasting entries in her moment (controls first, then the most recently seen)
ACTIONS_READ = 20  # her actions since the last update, the newest kept
BUSY_SECONDS = 60  # one update per place at a time; its claim lapses after this if the worker died mid-update
PENDING_SECONDS = 120  # a screen waiting for the updater (or kept as the one before), at most
NOW_SECONDS = 60 * 60  # this sitting is forgotten after this long without using the place
RECENT_PLACES_SECONDS = 60 * 60  # places whose picture comes with each turn

KINDS = ("control", "landmark", "area", "thing")


class Edit(BaseModel):
    # Field order is the order the model writes them in: the reason first.
    why: str = Field(description="what in the screenshots or her actions shows this; one short line, for the record "
                                 "only (never kept in her memory)")
    op: Literal["add", "change", "remove"]
    keep: Literal["sitting", "lasting"] = Field(description="sitting: only for now; lasting: the same next time")
    id: str | None = Field(None, description="for change and remove: the entry's id as shown (s3, L5)")
    kind: Literal["control", "landmark", "area", "thing"] | None = Field(
        None, description="lasting entries only: control, landmark, area or thing")
    near: str | None = Field(None, description="lasting things: the id of the landmark or area it is in or by (L2)")
    text: str = Field("", description="add and change: the entry, whole; under %d characters" % ENTRY_CHARS)


class Update(BaseModel):
    world: str = Field(description="the name the screens give the world, level or server she's in; the same as "
                                   "before when this screenshot doesn't show it; empty when the place has none")
    edits: list[Edit] = Field(description="only what changed; empty when nothing did")
    doing: str = Field(description="what she's doing here now, one line; under %d characters" % LINE_CHARS)
    where: str = Field(description="where she is in the place, by what's around her; empty when the place has no "
                                   "map (a menu, an app's screens); under %d characters" % LINE_CHARS)


SYSTEM = """\
You keep Jophiel's working memory of one place on a phone (an app or a game) while she uses it, so she knows what
she is doing there and what is where without having to look again, the way a person does: someone who drops troops
and zooms out still knows where the troops are; someone who walked past a chest still knows where it is.

You get her memory so far (every entry with an id), her own lines beside it, her actions since your last update
(tool, what she aimed at, where, what came back), the screenshot you saw last time and her newest one. Answer with
edits to the entries and the three lines (world, doing, where).

- Edits, not a rewrite. An entry you don't name stays exactly as it is. No change: no edits. add a new entry;
  change one by its id (give it whole); remove one only when the screenshot shows it is gone or over.
- Out of view is not gone. When the view moves away from something (she panned, zoomed, walked on), keep it and say
  where it is relative to what's around it ("4 dragons beyond the right wall, out of view since I panned left").
  Compare the two screenshots and her swipes and pinches to tell how the view moved.
- sitting entries: what matters only now: troops or units she placed and where, what's still in hand, a timer, a
  price or a number she'll need, what she was about to do, someone else's base or world as it stands. At most {sit}.
- lasting entries: what will be the same next time: control (the app's or game's own buttons, bars and counters
  fixed to the screen, on any of its screens, an opponent's base included: give it in the screenshot's pixels with
  its size, orientation and the screen it's on, "Next button on opponent preparation, lower right, about
  x1080-1245 y370-445, landscape 1280x576"); landmark and area (parts of her own village, farm or world, by how
  they look and what's around them); thing (what she saw somewhere in it, with near = the id of the landmark or
  area it is in or by). At most {last} for a world. Someone else's things (an opponent's buildings, another
  player's world) are never lasting; the controls over them are.
- Things in the place move on the screen with every zoom and pan: describe them by what is around them ("10 tiles
  right of the world door", "left of the tall tower"), never by pixels. Describe things by how they look (colour,
  shape, where they sit) as well as the name the screen gives them.
- world: the name the screens give the world, level or server she's in (a Growtopia world's name, a level number);
  keep the one before when this screenshot doesn't show one; empty when the place has none. Lasting entries belong
  to the world you answer with.
- doing: what she is doing here now, one line. where: where she is in the place, by what's around her (empty for a
  menu or an app's own screens).
- Only what the screenshots and her actions show, never what you'd expect from knowing the app or game.
- Every entry and line is about the place: what is where and what she did. Your reasons go in why, never in an
  entry or a line ("this screenshot shows..." is a why). Nobody's words, names or messages from the screen.
- Her own lines are hers: don't repeat them in entries and never contradict them silently; if the screenshot shows
  one is out of date, add or change an entry saying what you see.
- Plain words, first person ("I placed..."). Each entry under {chars} characters, one thing per entry.
"""


def system_prompt() -> str:
    return SYSTEM.format(sit=SITTING_MAX, last=LASTING_MAX, chars=ENTRY_CHARS)


def cut(text: str | None, limit: int) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


@dataclass(frozen=True)
class Entry:
    id: str  # as the updater sees it: s<n> for this sitting, L<n> for lasting
    text: str
    kind: str | None = None  # lasting only
    near: str | None = None  # lasting things: the id of the landmark or area
    key: str | None = None  # lasting: the row's id


@dataclass(frozen=True)
class Picture:
    """A place's picture as it stands."""

    place: str
    world: str = ""
    doing: str = ""
    where: str = ""
    sitting: tuple[Entry, ...] = ()
    lasting: tuple[Entry, ...] = ()
    her_now: str = ""
    her_layout: str = ""
    now_at: float | None = None  # when this sitting was last written (by the updater or by her)

    @property
    def empty(self) -> bool:
        return not (self.doing or self.where or self.sitting or self.lasting or self.her_now or self.her_layout)

    @property
    def version(self) -> tuple:
        return (self.world, self.doing, self.where, self.sitting, self.lasting, self.her_now, self.her_layout)


def _lasting_lines(entries: tuple[Entry, ...] | list[Entry], *, ids: bool) -> list[str]:
    """Lasting entries as lines: controls, then landmarks and areas each with the things near them, then the rest."""
    by_id = {e.id: e for e in entries}
    nested: dict[str, list[Entry]] = {}
    loose: list[Entry] = []
    for e in entries:
        if e.kind == "thing" and e.near in by_id and by_id[e.near].kind in ("landmark", "area"):
            nested.setdefault(e.near, []).append(e)
        elif e.kind != "control":
            loose.append(e)

    def line(e: Entry, indent: str = "") -> str:
        return f"{indent}- {f'{e.id} ' if ids else ''}[{e.kind}] {e.text}"

    out = [line(e) for e in entries if e.kind == "control"]
    for e in loose:
        out.append(line(e))
        out += [line(t, "  ") for t in nested.get(e.id, [])]
    return out


def update_text(picture: Picture, actions: list[str], *, size: tuple[int, int] | None, caption: str,
                had_before: bool) -> str:
    """What the updater reads beside the screenshots."""
    out = [f"Place: {picture.place}.", f"world so far: {picture.world or '(none)'}",
           f"doing so far: {picture.doing or '(nothing yet)'}", f"where so far: {picture.where or '(nothing yet)'}"]
    out.append(f"sitting entries ({len(picture.sitting)} of {SITTING_MAX}):")
    out += [f"- {e.id} {e.text}" for e in picture.sitting] or ["(none)"]
    out.append(f"lasting entries for this world ({len(picture.lasting)} of {LASTING_MAX}):")
    out += _lasting_lines(picture.lasting, ids=True) or [
        "(none yet: nothing is kept for next time, not even where this place's own controls are)"]
    if picture.her_now:
        out.append(f"Her own lines for now: {picture.her_now}")
    if picture.her_layout:
        out.append(f"Her own lines about the place: {picture.her_layout}")
    if actions:
        out.append("Her actions since your last update (oldest first):")
        out += [f"- {cut(a, 300)}" for a in actions[-ACTIONS_READ:]]
    else:
        out.append("No actions since your last update: she looked.")
    shape = f" ({size[0]}x{size[1]}, {'landscape' if size[0] > size[1] else 'portrait'})" if size else ""
    if had_before:
        out.append("Two screenshots are attached: first the one you saw last time, then her newest" + shape
                   + f": {caption}")
    else:
        out.append(f"Her newest screenshot{shape} is attached: {caption}")
    return "\n".join(out)


@dataclass
class Applied:
    """An update as kept: the new lines, the sitting's entries, what to do to lasting ones, and what was refused."""

    world: str
    doing: str
    where: str
    sitting: list[Entry]
    lasting_add: list[Entry] = field(default_factory=list)  # id = the updater's new id (none yet), near = an id
    lasting_change: dict[str, str] = field(default_factory=dict)  # row key -> text
    lasting_remove: list[str] = field(default_factory=list)  # row keys
    refused: list[str] = field(default_factory=list)
    whys: list[str] = field(default_factory=list)
    next_sitting: int = 1  # the number the next sitting entry gets (ids are never reused within a sitting)


def apply(picture: Picture, update: Update, *, next_sitting: int) -> Applied:
    """The updater's answer checked against the picture it was given: ids must be ones it was shown, counts stay
    bounded, lasting entries only for the world they were read for. Pure."""
    world = cut(update.world, WORLD_CHARS)
    same_world = world.lower() == picture.world.lower()
    sitting = {e.id: e for e in picture.sitting}
    lasting = {e.id: e for e in picture.lasting} if same_world else {}
    out = Applied(world=world, doing=cut(update.doing, LINE_CHARS), where=cut(update.where, LINE_CHARS), sitting=[])
    added_lasting = 0
    n = next_sitting
    for edit in update.edits:
        out.whys.append(cut(f"{edit.op} {edit.keep} {edit.id or ''}: {edit.why}", 300))
        text = cut(edit.text, ENTRY_CHARS)
        if edit.op in ("add", "change") and not text:
            out.refused.append(f"{edit.op} without text")
            continue
        if edit.keep == "sitting":
            if edit.op == "add":
                if len(sitting) >= SITTING_MAX:
                    out.refused.append("sitting full")
                    continue
                sitting[f"s{n}"] = Entry(id=f"s{n}", text=text)
                n += 1
            elif edit.id in sitting:
                if edit.op == "change":
                    sitting[edit.id] = Entry(id=edit.id, text=text)
                else:
                    del sitting[edit.id]
            else:
                out.refused.append(f"{edit.op}: no sitting entry {edit.id!r} was shown")
            continue
        if edit.op == "add":
            if edit.kind not in KINDS:
                out.refused.append("lasting add without a kind")
                continue
            if len(lasting) - len(out.lasting_remove) + added_lasting >= LASTING_MAX:
                out.refused.append("lasting full")
                continue
            near = edit.near if edit.kind == "thing" and edit.near in lasting else None
            out.lasting_add.append(Entry(id="", text=text, kind=edit.kind, near=near))
            added_lasting += 1
        elif edit.id in lasting and lasting[edit.id].key:
            key = lasting[edit.id].key
            if edit.op == "change":
                out.lasting_change[key] = text
            elif key not in out.lasting_remove:
                out.lasting_remove.append(key)
        else:
            out.refused.append(f"{edit.op}: no lasting entry {edit.id!r} was shown for this world")
    out.sitting = list(sitting.values())
    out.next_sitting = n
    return out


def _age(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{int(seconds)} s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    return f"{int(seconds // 3600)} h ago"


def text_for_her(picture: Picture, *, now: float, lasting_seen: set[str] | None = None) -> str:
    """How a place's picture reads to her, with a screen or in her moment. ``lasting_seen``: the lasting entries
    (with their text) she already read this turn; only the others are listed again."""
    when = _age(now - picture.now_at) if picture.now_at else ""
    world = f", world {picture.world}" if picture.world else ""
    head = (f"What you know of {picture.place}{world} (kept from your screenshots there"
            f"{f', last updated {when}' if when else ''}; the screen may have moved on since):")
    out = [head]
    if picture.doing:
        out.append(f"- Doing: {picture.doing}")
    if picture.where:
        out.append(f"- Where you are: {picture.where}")
    if picture.sitting:
        out.append("- Keeping track of:")
        out += [f"  - {e.text}" for e in picture.sitting]
    if picture.her_now:
        out.append(f"- Your own lines for now: {picture.her_now}")
    lasting = picture.lasting
    if lasting_seen is not None:
        lasting = tuple(e for e in lasting if f"{e.key}:{e.text}" not in lasting_seen)
    if lasting:
        out.append("- The place, for next time too:" if lasting_seen is None or len(lasting) == len(picture.lasting)
                   else "- The place, new or changed since you last read it:")
        out += ["  " + line for line in _lasting_lines(lasting, ids=False)]
    if picture.her_layout:
        out.append(f"- Your own lines about the place: {picture.her_layout}")
    return "\n".join(out)


def for_moment(picture: Picture) -> Picture:
    """The picture as it comes in her moment: the lasting part bounded (controls first, then the newest seen)."""
    if len(picture.lasting) <= LASTING_SHOWN:
        return picture
    controls = [e for e in picture.lasting if e.kind == "control"]
    rest = [e for e in picture.lasting if e.kind != "control"]  # already newest first
    kept = (controls + rest)[:LASTING_SHOWN]
    from dataclasses import replace
    return replace(picture, lasting=tuple(kept))
