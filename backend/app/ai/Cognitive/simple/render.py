"""The teacher's text view of a bundle: fixed templates over the bundle's fields, nothing else.

``lines()`` returns (field, line) pairs so a test can check every line comes from a field. The full
conversation is not in the bundle; the full-view teacher gets it as a separate block.
"""
from __future__ import annotations

from typing import Any

from .contracts import (FEELINGS, GROUP_LABELS, MEMORY_KINDS, MEMORY_LABELS, MOOD_KEYS, NOTE_LABELS, NO_SECOND,
                        Bundle, Message, scale)
from .mood import band
from .rhythm import rhythm_text

_TIME = {'morning': 'It is morning for them.', 'afternoon': 'It is afternoon for them.',
         'evening': 'It is evening for them.', 'late_night': 'It is late at night for them.'}


def _speaker(message: Message) -> str:
    return 'Them' if message.speaker == 'user' else 'Columbina'


def previous_text(previous: dict[str, Any]) -> str:
    u, d = previous['understand'], previous['decide']
    acts = [a.replace('_', ' ') for a, on in u['acts'].items() if on] or ['nothing in particular']
    felt = [f'{f} ({u["feelings"][f].replace("_", " ")})' for f in FEELINGS if scale(u['feelings'][f]) >= 0.25]
    moves = GROUP_LABELS[d['main']] + ('' if d['second'] == NO_SECOND else f', then {GROUP_LABELS[d["second"]]}')
    text = (f'Last turn she read their message as {", ".join(acts)}, in a {u["tone"]} tone; '
            f'she felt {", ".join(felt) if felt else "nothing much"}; she chose: {moves}.')
    if previous.get('intention'):
        text += f' What she meant to do: {previous["intention"]}'
    return text


def lines(bundle: Bundle, *, include_window: bool = True) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    out.append(('affinity', "How well she knows them: you don't know them well yet."))
    out.append(('time_of_day', _TIME[bundle.time_of_day]))
    out.append(('mood', 'Her mood right now: ' + ', '.join(
        f'{key} {band(key, bundle.mood[key])}' for key in MOOD_KEYS) + '.'))
    if bundle.previous:
        out.append(('previous', previous_text(bundle.previous)))
    if bundle.notes:
        out.append(('notes', 'Open notes from this conversation:'))
        for note in bundle.notes:
            out.append(('notes', f'- [{note.id}] {NOTE_LABELS[note.kind]} (turn {note.opened_turn}): "{note.text}"'
                        + (f' — about her earlier words: "{note.target}"' if note.kind == 'correction' and note.target else '')))
    else:
        out.append(('notes', 'Open notes from this conversation: none.'))
    if bundle.typed_memories:
        for kind in MEMORY_KINDS:
            items = [m['text'] for m in bundle.typed_memories if m['kind'] == kind]
            out.append(('typed_memories', f'{MEMORY_LABELS[kind]}:' + ('' if items else ' nothing yet.')))
            out.extend(('typed_memories', f'- {text}') for text in items)
    else:
        if bundle.memories:
            out.append(('memories', 'What she remembers about them:'))
            out.extend(('memories', f'- {memory}') for memory in bundle.memories)
        else:
            out.append(('memories', 'What she remembers about them: nothing relevant.'))
        if bundle.world:
            out.append(('world', 'Her world (what she actually has to talk about):'))
            out.extend(('world', f'- {item}') for item in bundle.world)
    if bundle.rhythm:
        out.extend(('rhythm', line) for line in rhythm_text(bundle.rhythm))
    if include_window:
        out.append(('window', 'Recent messages (oldest first):' if bundle.window else 'Recent messages: none, this is the start.'))
        out.extend(('window', f'{_speaker(m)}: {m.text}') for m in bundle.window)
    out.append(('message', f'Their new message: {bundle.message}'))
    return out


def text(bundle: Bundle, *, include_window: bool = True) -> str:
    return '\n'.join(line for _, line in lines(bundle, include_window=include_window))


def conversation_text(history: list[Message]) -> str:
    return '\n'.join(f'[{m.turn}] {_speaker(m)}: {m.text}' for m in history) or '(nothing yet)'
