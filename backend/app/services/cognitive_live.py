"""Simple cognition in the live chat (docs/COLUMBINA_LIVE_INTENTION_PATH_DESIGN.md).

Off unless ``COGNITIVE_LIVE_CHARACTER`` or ``COGNITIVE_LIVE_STUDENT`` is set; with both off nothing
here runs and chat is exactly as before. Per turn:

- ``prepare`` (before the reply): her character page and typed, scoped memories for the prompt;
  Student 1 reads the message (acts, request, tone); the conversation's notes are updated from that
  reading of *their* message. The reading reaches the prompt only in the ``reading`` arm; in ``ab``
  mode half the conversations get it, so its effect can be compared.
- ``after_reply`` (after the reply is sent, off the critical path): notes about what *she* did follow
  her actual reply: "she asked" from its text, answered/offered/committed from a small model reading
  that reply. A predicted move never becomes the record of an event.

Feelings and appraisal are predicted but experimental: logged, never shown. Student 2 stays offline.
Never raises into the chat turn.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import and_, literal, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from app.ai.Cognitive.simple.contracts import WINDOW, Bundle, Message, Note
from app.ai.Cognitive.simple.live import CHARACTER_DIR, read, reading_text
from app.ai.Cognitive.simple.mood import START_MOODS
from app.ai.Cognitive.simple.notes import NoteBook, ReplyEvents
from app.ai.Cognitive.simple.notice import notice
from app.core.config import get_settings
from app.db.models import WorkingMemory
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

STATE_KEY = 'simple_live'
BACKEND = Path(__file__).resolve().parents[2]
DEFAULT_STUDENT_DIR = BACKEND / 'var' / 'cognitive_simple' / 'teacher-1-2' / 'models-1.0'
ARMS = ('reading', 'page_only')

_NOTE_WORDS = {
    'her_commitment': 'you agreed to', 'correction': 'they corrected you',
    'their_open_question': "they asked and you haven't answered yet", 'her_open_question': "you asked and they haven't answered yet",
    'her_offer': 'you offered', 'shared_matters': 'they shared something that matters to them',
}
_EPISODE = re.compile(r'^\[episode[^\]]*\]\s*')
_KIND = re.compile(r'^\[[a-z_]+\]\s*')


def enabled() -> bool:
    s = get_settings()
    return s.cognitive_live_character or s.cognitive_live_student != 'off'


@lru_cache(maxsize=1)
def character_page() -> str:
    return (CHARACTER_DIR / 'columbina.md').read_text().strip()


@lru_cache(maxsize=1)
def her_preferences() -> tuple[str, ...]:
    data = json.loads((CHARACTER_DIR / 'columbina.world.json').read_text())
    lines = [f'You are interested in {x}.' for x in data['interests']]
    lines += [re.sub(r'^she ', 'You ', p).rstrip('.') + '.' for p in data['preferences']]
    return tuple(lines)


def warm() -> None:
    """Load Student 1 ahead of the first turn (about 5 s), so no user waits for it."""
    if get_settings().cognitive_live_student == 'off':
        return
    try:
        from app.ai.Cognitive.simple.live import load
        load(get_settings().cognitive_live_student_dir or str(DEFAULT_STUDENT_DIR))
    except Exception:
        logger.exception('cognitive_live: could not load Student 1; live reading will be skipped')


def split_memories(block: str | None) -> tuple[list[str], list[str]]:
    """The live memory block -> (about them, shared). Episodes are things you two did together."""
    about, shared = [], []
    for line in (block or '').splitlines():
        text = line.strip().removeprefix('- ').strip()
        if not text:
            continue
        if _EPISODE.match(text):
            shared.append(text)        # keeps its date label
        else:
            about.append(_KIND.sub('', text))
    return about, shared


def typed_memory_text(block: str | None) -> str:
    about, shared = split_memories(block)

    def section(title: str, items: list[str] | tuple[str, ...], empty: str) -> str:
        return f'{title}:\n' + ('\n'.join(f'- {i}' for i in items) if items else f'- {empty}')

    return '\n\n'.join([
        section("What you know about them (things they told you: their experiences, not yours)", about, 'nothing relevant'),
        section('What the two of you have done together', shared, 'nothing recorded'),
        section('Your own experiences (things you yourself did or thought)', [], 'none recorded yet'),
        section('What you like (your own preferences and interests)', her_preferences(), 'nothing recorded'),
    ])


def notes_text(notes: list[Note]) -> str | None:
    if not notes:
        return None
    return '\n'.join(f'- {_NOTE_WORDS[n.kind]}: "{n.text}"' for n in notes)


def _time_of_day(hour: int) -> str:
    return 'morning' if 5 <= hour < 12 else 'afternoon' if hour < 17 else 'evening' if hour < 22 else 'late_night'


# --- per-conversation state (working_memories.cognitive_state[STATE_KEY]) ---------------------

def load_state(user_id: uuid.UUID, conversation_id: uuid.UUID) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.scalar(select(WorkingMemory).where(WorkingMemory.conversation_id == conversation_id,
                                                    WorkingMemory.user_id == user_id))
        return dict((row.cognitive_state or {}).get(STATE_KEY, {})) if row else {}


def save_state(user_id: uuid.UUID, conversation_id: uuid.UUID, state: dict[str, Any]) -> None:
    payload = {STATE_KEY: state}
    with SessionLocal() as db:
        statement = insert(WorkingMemory).values(user_id=user_id, conversation_id=conversation_id,
                                                 cognitive_state=payload)
        # Merge under our own key only: other keys (e.g. the v0 dynamic state snapshot) stay untouched.
        statement = statement.on_conflict_do_update(
            index_elements=[WorkingMemory.conversation_id],
            set_={'cognitive_state': WorkingMemory.cognitive_state.op('||')(literal(payload, type_=JSONB))},
            where=and_(WorkingMemory.user_id == user_id),
        )
        db.execute(statement)
        db.commit()


def arm_for(state: dict[str, Any]) -> str | None:
    mode = get_settings().cognitive_live_student
    if mode == 'off':
        return None
    if mode == 'on':
        return 'reading'
    return state.get('arm') or random.choice(ARMS)


# --- the turn ---------------------------------------------------------------------------------

@dataclass
class LivePrep:
    character_page: str | None = None
    typed_memory: str | None = None
    reading: str | None = None            # shown only in the 'reading' arm
    notes: str | None = None
    arm: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    log: dict[str, Any] = field(default_factory=dict)


def prepare(*, user_id: uuid.UUID, conversation_id: uuid.UUID, history: list[dict[str, Any]], message: str,
            long_term_memory: str | None, now: datetime | None = None) -> LivePrep | None:
    if not enabled():
        return None
    try:
        return _prepare(user_id=user_id, conversation_id=conversation_id, history=history, message=message,
                        long_term_memory=long_term_memory, now=now or datetime.now())
    except Exception:
        logger.exception('cognitive_live.prepare failed; continuing without it')
        return None


def _prepare(*, user_id, conversation_id, history, message, long_term_memory, now) -> LivePrep:
    s = get_settings()
    prep = LivePrep()
    if s.cognitive_live_character:
        prep.character_page = character_page()
        prep.typed_memory = typed_memory_text(long_term_memory)
    prep.log = dict(at=datetime.now(timezone.utc).isoformat(), user_id=str(user_id),
                    conversation_id=str(conversation_id), character=s.cognitive_live_character,
                    student_mode=s.cognitive_live_student, message=message)
    if s.cognitive_live_student == 'off':
        return prep
    state = load_state(user_id, conversation_id)
    arm = arm_for(state)
    if arm != state.get('arm'):
        # Fix the conversation's arm now, so a failed turn can't move it to the other half.
        state = dict(state, arm=arm)
        save_state(user_id, conversation_id, state)
    book = NoteBook([Note.from_dict(n) for n in state.get('notes', [])], state.get('counter', 0))
    earlier = [h for h in history if h.get('content')]
    if earlier and earlier[-1].get('role') == 'user' and earlier[-1]['content'].strip() == message.strip():
        earlier = earlier[:-1]     # the history may already end with this message
    window = [Message('user' if h['role'] == 'user' else 'columbina', h['content'], i + 1)
              for i, h in enumerate(earlier)][-WINDOW:]
    turn = len(earlier) + 1
    last_reply_at = state.get('last_reply_at')
    seconds = ((datetime.now(timezone.utc) - datetime.fromisoformat(last_reply_at)).total_seconds()
               if last_reply_at else None)
    about, _ = split_memories(long_term_memory)
    bundle = Bundle(turn=turn, message=message, window=window, notes=list(book.notes), memories=about[:5],
                    notice=notice(message, seconds_since_reply=seconds,
                                  first_user_turn=not any(h['role'] == 'user' for h in earlier)),
                    mood=dict(START_MOODS['calm']), affinity=0.0, time_of_day=_time_of_day(now.hour), previous=None)
    started = time.monotonic()
    reading = read(bundle, s.cognitive_live_student_dir or str(DEFAULT_STUDENT_DIR))
    previous_reply = next((h['content'] for h in reversed(earlier) if h['role'] == 'assistant'), None)
    book.apply_message(turn=turn, message=message, understand=reading['understand'], previous_reply=previous_reply)
    prep.arm = arm
    prep.notes = notes_text(book.notes)
    if arm == 'reading':
        prep.reading = reading_text(reading)
    prep.state = dict(state, arm=arm, notes=[n.to_dict() for n in book.notes], counter=book.counter,
                      turn=turn, understand=reading['understand'])
    prep.log.update(arm=arm, student_ms=round((time.monotonic() - started) * 1000), reading=dict(
        acts=reading['acts'], tone=reading['tone'], request=reading['request']),
        experimental=reading['experimental'], notes_before_reply=prep.state['notes'],
        shown=dict(reading=prep.reading is not None, notes=prep.notes is not None))
    return prep


_EVENTS_SCHEMA = {'type': 'object', 'additionalProperties': False,
                  'required': ['answered', 'offered', 'committed'],
                  'properties': {k: {'type': 'boolean'} for k in ('answered', 'offered', 'committed')}}


def _reply_events(message: str, reply: str) -> ReplyEvents:
    """What her actual reply did. 'Asked' from its text; the rest from a small model reading it."""
    from openai import OpenAI

    from app.ai.Cognitive.simple.teacher import prompt
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key, timeout=20, max_retries=1)
    result = client.chat.completions.create(
        model=s.cognitive_live_events_model or s.subagent_model or 'gpt-4.1-mini', temperature=0, store=False,
        messages=[{'role': 'system', 'content': prompt('reply_events')},
                  {'role': 'user', 'content': f'THEIR MESSAGE\n{message}\n\nHER REPLY\n{reply}'}],
        response_format={'type': 'json_schema', 'json_schema': {'name': 'reply_events', 'strict': True,
                                                                 'schema': _EVENTS_SCHEMA}})
    data = json.loads(result.choices[0].message.content or '{}')
    return ReplyEvents(asked='?' in reply, answered=bool(data.get('answered')), offered=bool(data.get('offered')),
                       committed=bool(data.get('committed')), source='reply-reading')


def after_reply(prep: LivePrep | None, *, user_id: uuid.UUID, conversation_id: uuid.UUID, message: str,
                reply: str) -> None:
    if prep is None:
        return
    try:
        if prep.arm is not None and reply:
            try:
                events = _reply_events(message, reply)
            except Exception as exc:   # still record what the text shows
                events = ReplyEvents(asked='?' in reply, answered=False, offered=False, committed=False,
                                     source=f'text-only ({type(exc).__name__})')
            state = prep.state
            book = NoteBook([Note.from_dict(n) for n in state.get('notes', [])], state.get('counter', 0))
            book.apply_reply(turn=state['turn'] + 1, message=message, understand=state['understand'], reply=reply,
                             events=events)
            state = dict(state, notes=[n.to_dict() for n in book.notes], counter=book.counter,
                         last_reply_at=datetime.now(timezone.utc).isoformat())
            state.pop('understand', None)
            save_state(user_id, conversation_id, state)
            prep.log.update(events=events.to_dict(), notes_after_reply=state['notes'])
        prep.log.update(reply=reply)
        path = Path(get_settings().cognitive_live_log_path)
        if not path.is_absolute():
            path = BACKEND / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as f:
            f.write(json.dumps(prep.log, ensure_ascii=False) + '\n')
    except Exception:
        logger.exception('cognitive_live.after_reply failed')
