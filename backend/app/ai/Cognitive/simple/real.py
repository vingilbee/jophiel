"""The teacher on real conversations, shadow only.

Reads stored copies of a conversation after the fact (read-only: no writes to chat, memory or
access counters) and labels each user turn like a simulated one. The continuation is what she
actually said, not a teacher reply. Never runs during a live chat and never changes a reply.
"""
from __future__ import annotations

import uuid
from datetime import timezone
from typing import Any

from sqlalchemy import select

from app.db.models import ChatMessage, Conversation, SemanticMemory
from app.db.session import SessionLocal

from .contracts import SCHEMA_VERSION, WINDOW, Bundle, Message
from .episode import Env, _final, _turn_record
from .mood import START_MOODS
from .notes import NoteBook
from .notice import notice
from .retrieval import MemoryItem, MemoryPool
from .teacher import View


def _time_of_day(hour: int) -> str:
    return 'morning' if 5 <= hour < 12 else 'afternoon' if hour < 17 else 'evening' if hour < 22 else 'late_night'


def load_conversation(conversation_id: uuid.UUID) -> tuple[uuid.UUID, list[dict[str, Any]], list[MemoryItem]]:
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        rows = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
                          .order_by(ChatMessage.conversation_sequence)).all()
        memories = db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == conversation.user_id,
                                                           SemanticMemory.status == 'active')).all()
        turns: list[dict[str, Any]] = []
        for row in rows:
            text = (row.content or '').strip()
            if not text:
                continue
            created = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
            if turns and turns[-1]['role'] == row.role:
                turns[-1]['text'] += '\n' + text   # consecutive same-role messages read as one turn
            else:
                turns.append(dict(role=row.role, text=text, at=created))
        pool = [MemoryItem(content=m.content, kind=m.kind, importance=m.importance, confidence=m.confidence,
                           embedding=m.embedding) for m in memories]
        return conversation.user_id, turns, pool


async def run_real(env: Env, conversation_id: uuid.UUID, *, limit: int) -> dict[str, Any]:
    _, turns, items = load_conversation(conversation_id)
    pool = MemoryPool(items, env.llm, env.embedding_model)
    await pool.prepare()
    teacher = env.teacher
    history: list[Message] = []
    notes = NoteBook()
    mood = dict(START_MOODS['calm'])
    previous = None
    last_reply_at = None
    scenario = dict(id=f'real-{str(conversation_id)[:8]}', family='real/real', group='real', user_type='real',
                    situation='real', situation_turn=-1, memories=bool(items), turns=limit)
    record: dict[str, Any] = dict(schema=SCHEMA_VERSION, scenario=scenario, card=None, turns=[], real=True)
    count, labelled = 0, 0
    for i, item in enumerate(turns):
        if item['role'] != 'user':
            continue
        if labelled >= limit:
            break
        reply_item = turns[i + 1] if i + 1 < len(turns) and turns[i + 1]['role'] == 'assistant' else None
        count += 1
        seconds = (item['at'] - last_reply_at).total_seconds() if last_reply_at else None
        bundle = Bundle(turn=count, message=item['text'], window=history[-WINDOW:], notes=list(notes.notes),
                        memories=await pool.retrieve(item['text']),
                        notice=notice(item['text'], seconds_since_reply=seconds, first_user_turn=labelled == 0),
                        mood=dict(mood), affinity=0.0, time_of_day=_time_of_day(item['at'].hour), previous=previous)
        full = View(teacher.page, bundle, history, full=True)
        samples = await teacher.samples(full)
        accepted = [s for s in (_final(a) for a in samples) if s]
        turn = dict(index=labelled, user=dict(message=item['text']), situation_here=False, **_turn_record(bundle, samples),
                    noisy=len(accepted) < 3, coverage={})
        labelled += 1
        reply = reply_item['text'] if reply_item else ''
        chosen = accepted[0] if accepted else None
        turn['continued'] = chosen.index if chosen else None
        turn['reply'] = reply
        record['turns'].append(turn)
        history.append(Message('user', item['text'], count))
        if chosen:
            notes.apply_message(turn=count, message=item['text'], understand=chosen.understand,
                                previous_reply=history[-2].text if len(history) > 1 else None)
            mood = dict(chosen.mood_after)
            previous = dict(understand=chosen.understand,
                            decide={k: chosen.decide[k] for k in ('main', 'second', 'dials')})
        if reply_item:
            count += 1
            if chosen:
                notes.apply_decision(turn=count, message=item['text'], decide=chosen.decide,
                                     understand=chosen.understand, reply=reply)
            history.append(Message('columbina', reply, count))
            last_reply_at = reply_item['at']
    record['history'] = [m.to_dict() for m in history]
    return record
