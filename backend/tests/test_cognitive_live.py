"""Simple cognition in the live chat (services/cognitive_live.py), off by default."""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.Cognitive.simple.contracts import ACTS, APPRAISALS, APPRAISALS_SIGNED, FEELINGS
from app.ai.Cognitive.simple.live import CHARACTER_DIR
from app.ai.context import AgentRuntimeContext
from app.ai.prompts import build_system_prompt
from app.services import cognitive_live

DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'characters'
BLOCK = ("- [preference] Likes their tea very strong.\n"
         "- [episode, 2026-10-12, game] We built a small house together and got lost in a cave.")


def _settings(**over):
    base = dict(cognitive_live_character=False, cognitive_live_student='off', cognitive_live_student_dir=None,
                cognitive_live_events_model=None, subagent_model='gpt-4.1-mini', openai_api_key='x',
                cognitive_live_log_path='var/test_cognitive_live.jsonl')
    return SimpleNamespace(**(base | over))


def test_off_by_default_and_prompt_unchanged(monkeypatch):
    monkeypatch.setattr(cognitive_live, 'get_settings', lambda: _settings())
    assert not cognitive_live.enabled()
    assert cognitive_live.prepare(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), history=[], message='hi',
                                  long_term_memory=BLOCK) is None
    prompt = build_system_prompt(AgentRuntimeContext(user_id=uuid.uuid4(), long_term_memory=BLOCK))
    for heading in ('## Who you are', 'by whose it is', 'A quick reading', 'Still open in this conversation'):
        assert heading not in prompt
    assert 'Relevant long-term memory' in prompt


def test_sections_appear_only_with_their_fields():
    ctx = AgentRuntimeContext(user_id=uuid.uuid4(), long_term_memory=BLOCK, character_page='PAGE',
                              typed_memory=cognitive_live.typed_memory_text(BLOCK),
                              cognitive_reading='- Their tone seems: playful.', conversation_notes='- you offered: "x"')
    prompt = build_system_prompt(ctx)
    assert '## Who you are' in prompt and 'PAGE' in prompt
    assert 'by whose it is' in prompt and 'Relevant long-term memory' not in prompt   # typed replaces untyped
    assert 'A quick reading' in prompt and 'Still open in this conversation' in prompt


def test_game_recaps_are_shared_not_her_own():
    about, shared = cognitive_live.split_memories(BLOCK)
    assert about == ['Likes their tea very strong.'] and len(shared) == 1 and 'cave' in shared[0]
    text = cognitive_live.typed_memory_text(BLOCK)
    assert text.index('cave') > text.index('What the two of you have done together')
    assert text.index('cave') < text.index('Your own experiences')
    assert 'none recorded yet' in text


def test_live_character_copies_match_the_docs():
    page = (DOCS / 'columbina-v0.6.md').read_text()
    assert (CHARACTER_DIR / 'columbina.md').read_text() == page[page.index('**In one line:**'):]
    assert (CHARACTER_DIR / 'columbina.world.json').read_text() == (DOCS / 'columbina-teacher-2026-10-19.world.json').read_text()
    assert (CHARACTER_DIR / 'columbina.mood.json').read_text() == (DOCS / 'columbina.mood.json').read_text()


def _fake_reading(acts=(), request='talk_only', tone='friendly'):
    understand = dict(acts={a: a in acts for a in ACTS}, tone=tone, request=request, about_her='none', sure='strong',
                      appraisal={**{k: 'neutral' for k in APPRAISALS_SIGNED}, **{k: 'none' for k in APPRAISALS}},
                      feelings={f: 'none' for f in FEELINGS}, note_updates=[])
    return dict(understand=understand, acts={a: float(a in acts) for a in ACTS}, tone={tone: 1.0},
                request={request: 1.0}, experimental={})


@pytest.fixture
def live(monkeypatch, tmp_path):
    store: dict = {}
    monkeypatch.setattr(cognitive_live, 'load_state', lambda u, c: dict(store.get(c, {})))
    monkeypatch.setattr(cognitive_live, 'save_state', lambda u, c, state: store.__setitem__(c, dict(state)))
    monkeypatch.setattr(cognitive_live, 'read', lambda bundle, model_dir: _fake_reading(acts=('asking',)))
    return store, tmp_path


def test_reading_shown_only_in_its_arm_and_arm_sticks(monkeypatch, live):
    store, tmp = live
    monkeypatch.setattr(cognitive_live, 'get_settings',
                        lambda: _settings(cognitive_live_student='ab', cognitive_live_log_path=str(tmp / 'log.jsonl')))
    convo = uuid.uuid4()
    first = cognitive_live.prepare(user_id=uuid.uuid4(), conversation_id=convo, history=[], message='what do you think?',
                                   long_term_memory=None)
    assert first.arm in cognitive_live.ARMS and store[convo]['arm'] == first.arm
    assert (first.reading is not None) == (first.arm == 'reading')
    again = cognitive_live.prepare(user_id=uuid.uuid4(), conversation_id=convo, history=[], message='and?',
                                   long_term_memory=None)
    assert again.arm == first.arm


def test_her_notes_follow_her_actual_reply(monkeypatch, live):
    store, tmp = live
    monkeypatch.setattr(cognitive_live, 'get_settings',
                        lambda: _settings(cognitive_live_student='on', cognitive_live_log_path=str(tmp / 'log.jsonl')))
    monkeypatch.setattr(cognitive_live, '_reply_events', lambda m, r: cognitive_live.ReplyEvents(
        asked='?' in r, answered=False, offered=True, committed=False, source='reply-reading'))
    user, convo = uuid.uuid4(), uuid.uuid4()
    prep = cognitive_live.prepare(user_id=user, conversation_id=convo, history=[], message='any ideas for tonight?',
                                  long_term_memory=None)
    assert prep.reading is not None
    cognitive_live.after_reply(prep, user_id=user, conversation_id=convo, message='any ideas for tonight?',
                               reply='you could try the old film. want me to pick one?')
    kinds = sorted(n['kind'] for n in store[convo]['notes'])
    assert kinds == ['her_offer', 'her_open_question', 'their_open_question']
    assert (tmp / 'log.jsonl').read_text().count('\n') == 1
    # Next turn the notes reach the prompt.
    nxt = cognitive_live.prepare(user_id=user, conversation_id=convo,
                                 history=[{'role': 'user', 'content': 'any ideas for tonight?'},
                                          {'role': 'assistant', 'content': 'you could try the old film. want me to pick one?'}],
                                 message='sure', long_term_memory=None)
    assert 'you offered' in nxt.notes


def test_a_failing_student_never_breaks_the_turn(monkeypatch, live):
    monkeypatch.setattr(cognitive_live, 'get_settings', lambda: _settings(cognitive_live_student='on'))
    monkeypatch.setattr(cognitive_live, 'read', lambda *a: (_ for _ in ()).throw(RuntimeError('model missing')))
    assert cognitive_live.prepare(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), history=[], message='hi',
                                  long_term_memory=None) is None
