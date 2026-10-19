"""Conversation notes: decision-relevant meaning that outlives the 6-message window.

Opened by code from the turn's labels; closed by the per-note updates the teacher (and later
Student 1) gives for each open note. Never written freely by a model, so the student and the
teacher always see identical notes.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import MAX_NOTES, NOTE_KINDS, Note, scale

_TEXT_LIMIT = 240
# Moves that respond to what they asked (an opinion question is answered by sharing her view).
ANSWERING_MOVES = frozenset({'answer_explain', 'share_view', 'say_yes_commit', 'say_no_limit', 'agree', 'push_back',
                             'share_about_herself'})


NOTES_VERSION = 'notes-v2'


@dataclass(frozen=True)
class ReplyEvents:
    """What her actual reply did, for the notes."""
    asked: bool        # read by code from the reply text itself
    answered: bool
    offered: bool
    committed: bool
    source: str        # 'description' (labels extracted from the actual reply) or 'plan' (labels the reply followed)

    def to_dict(self) -> dict:
        return dict(asked=self.asked, answered=self.answered, offered=self.offered, committed=self.committed,
                    source=self.source)


def events_from_reply(reply: str, did: dict, *, source: str) -> ReplyEvents:
    """Events of an actual reply. "Asked" comes from the text; the rest from labels describing it.

    With the intention teacher the labels are extracted after the reply, from the reply
    (``source='description'``). With the label-first teacher they're the plan the wording followed
    (``source='plan'``), recorded so the difference stays visible.
    """
    moves = {did['main'], did['second']}
    return ReplyEvents(asked=_asks(reply), answered=bool(moves & ANSWERING_MOVES), offered='suggest_offer' in moves,
                       committed='say_yes_commit' in moves, source=source)


def _asks(reply: str) -> bool:
    return '?' in reply


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_LIMIT else text[:_TEXT_LIMIT - 1] + '…'


class NoteBook:
    def __init__(self, notes: list[Note] | None = None, counter: int = 0):
        self.notes = list(notes or [])
        self.counter = counter

    def copy(self) -> 'NoteBook':
        return NoteBook([Note.from_dict(n.to_dict()) for n in self.notes], self.counter)

    def _open(self, kind: str, text: str, turn: int, target: str | None = None) -> None:
        self.counter += 1
        self.notes.append(Note(id=f'n{self.counter}', kind=kind, text=_clip(text), opened_turn=turn, target=target))
        while len(self.notes) > MAX_NOTES:
            # Drop the lowest-priority kind first, oldest within it.
            worst = max(self.notes, key=lambda n: (NOTE_KINDS.index(n.kind), -n.opened_turn))
            self.notes.remove(worst)

    def apply_message(self, *, turn: int, message: str, understand: dict, previous_reply: str | None) -> None:
        """The user's message: apply its note updates, then open what it creates."""
        updates = {u['note']: u['update'] for u in understand.get('note_updates', [])}
        kept = []
        for note in self.notes:
            update = updates.get(note.id, 'none')
            if note.kind == 'her_offer' and update == 'accepts':
                kept.append(Note(id=note.id, kind='her_commitment', text=note.text, opened_turn=note.opened_turn,
                                 target=note.target))
            elif update in ('answers', 'completes', 'drops') and note.kind != 'correction':
                continue
            elif update == 'drops' and note.kind == 'correction':
                continue
            else:
                kept.append(note)
        self.notes = kept
        acts = understand['acts']
        if acts['correcting_her']:
            target = _clip(previous_reply) if previous_reply else None
            # A newer correction replaces only one of the same target.
            self.notes = [n for n in self.notes if not (n.kind == 'correction' and n.target == target)]
            self._open('correction', message, turn, target=target)
        if acts['sharing'] and scale(understand['appraisal']['important']) >= 0.75:
            self._open('shared_matters', message, turn)

    def apply_reply(self, *, turn: int, message: str, understand: dict, reply: str, events: 'ReplyEvents') -> None:
        """What her actual reply did: close what she answered, open what she created.

        Notes about her follow events in the reply she actually gave, never a predicted decision:
        a classifier's guess must not become the memory of an event.
        """
        if events.answered:
            open_questions = [n for n in self.notes if n.kind == 'their_open_question']
            if open_questions:
                self.notes.remove(open_questions[-1])
        elif understand['acts']['asking']:
            self._open('their_open_question', message, turn)
        if events.committed:
            self._open('her_commitment', message, turn, target=_clip(message))
        if events.offered:
            self._open('her_offer', reply, turn)
        if events.asked:
            self._open('her_open_question', reply, turn)

    def apply_decision(self, *, turn: int, message: str, decide: dict, understand: dict, reply: str) -> None:
        """notes-v1 (pilot, experiments, batch teacher1): her notes opened from move labels. Kept for replay."""
        moves = {decide['main'], decide['second']}
        self.apply_reply(turn=turn, message=message, understand=understand, reply=reply, events=ReplyEvents(
            asked=bool(moves & {'ask_about_it', 'check_meaning'}), answered=bool(moves & ANSWERING_MOVES),
            offered='suggest_offer' in moves, committed='say_yes_commit' in moves, source='labels-v1'))

    def end_conversation(self) -> None:
        self.notes = [n for n in self.notes if n.kind != 'shared_matters']
