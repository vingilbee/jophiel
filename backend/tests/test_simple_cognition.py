"""Simple cognition chain + teacher environment (docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from app.ai.Cognitive.simple import render
from app.ai.Cognitive.simple.contracts import (
    ACTS, APPRAISALS, APPRAISALS_SIGNED, DIALS, FEELINGS, GROUPS, MAX_NOTES, Bundle, Message, Note,
    decide_targets, understand_targets,
)
from app.ai.Cognitive.simple.mood import START_MOODS, MoodDynamics, band, update
from app.ai.Cognitive.simple.notes import NoteBook
from app.ai.Cognitive.simple.notice import NOTICE_WIDTH, notice, numeric
from app.ai.Cognitive.simple.teacher import Teacher, View, decide_schema, understand_schema
from app.ai.Cognitive.simple.turn import allowed_groups, flags

ROOT = Path(__file__).resolve().parents[2]
DYNAMICS = MoodDynamics.load(ROOT / 'docs' / 'characters' / 'columbina.mood.json')


def understand(**overrides):
    base = dict(
        acts={a: False for a in ACTS}, tone='neutral', request='no_request', about_her='none', sure='strong',
        appraisal={APPRAISALS_SIGNED[0]: 'neutral', APPRAISALS_SIGNED[1]: 'neutral', APPRAISALS[0]: 'none',
                   APPRAISALS[1]: 'none'},
        feelings={f: 'none' for f in FEELINGS}, note_updates=[])
    acts = overrides.pop('acts', {})
    feelings = overrides.pop('feelings', {})
    appraisal = overrides.pop('appraisal', {})
    base['acts'].update(acts)
    base['feelings'].update(feelings)
    base['appraisal'].update(appraisal)
    base.update(overrides)
    return base


def decide(main='react_briefly', second='none', **dials):
    return dict(main=main, second=second, dials={d: dials.get(d, 'somewhat') for d in DIALS})


def bundle(**overrides):
    data = dict(turn=1, message='hey', window=[], notes=[], memories=[],
                notice=notice('hey', seconds_since_reply=None, first_user_turn=True),
                mood=dict(START_MOODS['calm']), affinity=0.0, time_of_day='morning', previous=None)
    data.update(overrides)
    return Bundle(**data)


# --- words -> numbers ------------------------------------------------------------------------

def test_targets_map_words_to_fixed_numbers():
    u = understand(acts={'teasing': True, 'complaining': True}, tone='playful', about_her='strong',
                   feelings={'hurt': 'a_little'}, appraisal={'good_for_her': 'bad'})
    t = understand_targets(u)
    assert t['acts'][ACTS.index('teasing')] == 1.0 and t['acts'][ACTS.index('complaining')] == 1.0
    assert sum(t['acts']) == 2.0           # acts are independent yes/no
    assert t['about_her'] == 0.75 and t['appraisal'][0] == -0.5
    assert t['feelings'][FEELINGS.index('hurt')] == 0.25
    d = decide_targets(decide('apologise_fix', 'none', length='a_little'))
    assert d['main'] == GROUPS.index('apologise_fix') and d['second'] == len(GROUPS) and d['dials'][0] == 0.25


def test_notice_facts_are_fixed_width():
    facts = notice('haha can you help me? 😅', seconds_since_reply=30, first_user_turn=False)
    assert facts['laughing'] and facts['request'] and facts['question'] and facts['emoji']
    assert len(numeric(facts)) == NOTICE_WIDTH


# --- render: the teacher's text is a view of the bundle ----------------------------------------

def test_every_rendered_line_comes_from_a_bundle_field():
    b = bundle(window=[Message('user', 'hi', 1), Message('columbina', 'hello', 2)],
               notes=[Note('n1', 'her_open_question', 'how was it?', 2)], memories=['Their cat Miso is sick.'],
               previous=dict(understand=understand(acts={'asking': True}), decide=decide('answer_explain')))
    fields = {f for f in Bundle.__dataclass_fields__}
    lines = render.lines(b)
    assert {field for field, _ in lines} <= fields
    text = render.text(b)
    for needle in ('hi', 'hello', 'how was it?', 'Miso', "don't know them well yet", 'Their new message: hey'):
        assert needle in text
    # The full-view teacher reads the conversation separately, not the window lines.
    assert 'Recent messages' not in render.text(b, include_window=False)


def test_full_view_marks_older_messages():
    history = [Message('user' if i % 2 else 'columbina', f'm{i}', i) for i in range(1, 10)]
    b = bundle(turn=10, window=history[-6:])
    view = View('PAGE', b, history, full=True)
    assert view.window_start == 4 and view.has_older
    assert 'RECENT WINDOW START: 4' in view.inputs()
    short = View('PAGE', bundle(turn=3, window=history[:2]), history[:2], full=True)
    assert not short.has_older and 'nothing is older' in short.inputs()


# --- mood --------------------------------------------------------------------------------------

def test_mood_moves_toward_feeling_and_drifts_to_baseline():
    calm = dict(START_MOODS['calm'])
    hurt = update(calm, understand(feelings={'hurt': 'strong'}, appraisal={'good_for_her': 'bad', 'warm_toward_her': 'bad'}),
                  DYNAMICS)
    assert hurt['tension'] > calm['tension'] and hurt['mood'] < calm['mood'] and hurt['closeness'] < calm['closeness']
    neutral = understand()
    tense = dict(START_MOODS['tense'])
    after = tense
    for _ in range(15):
        after = update(after, neutral, DYNAMICS)
    assert after['tension'] < tense['tension']    # nothing tense happening: it eases
    for key, value in after.items():
        assert (-1.0 if key in ('mood', 'closeness') else 0.0) <= value <= 1.0


def test_mood_bands_are_fixed_words():
    assert band('tension', 0.6) == 'tense' and band('energy', 0.1) == 'very tired' and band('mood', 0.6) == 'happy'


# --- flags and allowed moves -------------------------------------------------------------------

def test_apology_needs_something_wrong_and_yes_needs_a_doable_request():
    plain = notice('ok', seconds_since_reply=10, first_user_turn=False)
    u = understand(acts={'small_talk': True})
    f = flags(plain, u, [])
    allowed = allowed_groups(plain, u, f)
    assert 'apologise_fix' not in allowed and 'say_yes_commit' not in allowed
    assert 'greet' not in allowed and 'say_bye' not in allowed
    wrong = understand(acts={'correcting_her': True})
    assert 'apologise_fix' in allowed_groups(plain, wrong, flags(plain, wrong, []))
    ask = understand(acts={'requesting': True}, request='talk_only')
    assert 'say_yes_commit' in allowed_groups(plain, ask, flags(plain, ask, []))
    cant = understand(acts={'requesting': True}, request='outside_ability')
    f = flags(plain, cant, [])
    assert f['cant_do'] and 'say_yes_commit' not in allowed_groups(plain, cant, f)
    assert 'say_no_limit' in allowed_groups(plain, cant, f)


def test_first_turn_allows_greeting():
    first = notice('so i had a weird day', seconds_since_reply=None, first_user_turn=True)
    u = understand(acts={'sharing': True})
    assert 'greet' in allowed_groups(first, u, flags(first, u, []))


# --- notes -------------------------------------------------------------------------------------

def test_offer_becomes_commitment_only_when_accepted_then_completes():
    book = NoteBook()
    book.apply_decision(turn=2, message='i cant decide', decide=decide('suggest_offer'), understand=understand(),
                        reply='want me to help you list pros and cons?')
    assert [n.kind for n in book.notes] == ['her_offer']
    offer = book.notes[0].id
    book.apply_message(turn=3, message='yes please', understand=understand(note_updates=[{'note': offer, 'update': 'accepts'}]),
                       previous_reply='want me to help?')
    assert [n.kind for n in book.notes] == ['her_commitment']
    book.apply_message(turn=5, message='thanks, done', understand=understand(note_updates=[{'note': offer, 'update': 'completes'}]),
                       previous_reply='...')
    assert book.notes == []


def test_questions_open_and_close():
    book = NoteBook()
    asking = understand(acts={'asking': True})
    book.apply_decision(turn=2, message='what do you think?', decide=decide('ask_about_it'), understand=asking, reply='why do you ask?')
    assert {n.kind for n in book.notes} == {'their_open_question', 'her_open_question'}
    # Sharing her view answers an opinion question.
    book.apply_decision(turn=4, message='just curious', decide=decide('share_view'), understand=understand(), reply='i think so')
    assert {n.kind for n in book.notes} == {'her_open_question'}
    her_q = book.notes[0].id
    book.apply_message(turn=5, message='because...', understand=understand(note_updates=[{'note': her_q, 'update': 'answers'}]),
                       previous_reply='i think so')
    assert book.notes == []


def test_corrections_replace_only_the_same_target_and_overflow_keeps_priorities():
    book = NoteBook()
    wrong = understand(acts={'correcting_her': True})
    book.apply_message(turn=3, message='no, i meant green', understand=wrong, previous_reply='so, oatmeal then?')
    book.apply_message(turn=5, message='no, white not cream', understand=wrong, previous_reply='cream it is')
    book.apply_message(turn=7, message='i said white!', understand=wrong, previous_reply='cream it is')
    corrections = [n for n in book.notes if n.kind == 'correction']
    assert len(corrections) == 2 and {n.target for n in corrections} == {'so, oatmeal then?', 'cream it is'}
    share = understand(acts={'sharing': True}, appraisal={'important': 'strong'})
    for t in range(10, 20):
        book.apply_message(turn=t, message=f'shared {t}', understand=share, previous_reply=None)
    assert len(book.notes) == MAX_NOTES
    assert sum(n.kind == 'correction' for n in book.notes) == 2     # higher priority than shared notes


# --- teacher: whole-sample retries, strict schemas, older-message filtering --------------------

class FakeLLM:
    def __init__(self, verdicts, used=None):
        self.verdicts = list(verdicts)
        self.used = used or []
        self.calls = []

    async def text(self, stage, system, user, **_):
        self.calls.append(stage)
        return 'I notice they sound tired. I want to ask gently.'

    async def json(self, stage, system, user, schema, **_):
        self.calls.append(stage)
        if stage == 'label_understand':
            return understand(acts={'sharing': True})
        if stage == 'label_decide':
            return dict(decide('ask_about_it'), wanted_not_available=[], used_older_messages=self.used)
        if stage == 'critic':
            verdict = self.verdicts.pop(0)
            return dict(in_character=True, human_like=True, follows_inputs=verdict == 'accept',
                        older_messages_complete=True, verdict=verdict, reason='')
        raise AssertionError(stage)


def test_rejected_sample_is_regenerated_whole():
    llm = FakeLLM(['reject', 'accept'])
    teacher = Teacher(llm, 'PAGE', DYNAMICS)
    attempts = asyncio.run(teacher.sample(View('PAGE', bundle(), [], full=True), 0))
    assert [a.accepted for a in attempts] == [False, True]
    assert llm.calls == ['think', 'label_understand', 'label_decide', 'critic'] * 2


def test_used_older_messages_only_counts_messages_before_the_window():
    history = [Message('user' if i % 2 else 'columbina', f'm{i}', i) for i in range(1, 10)]
    llm = FakeLLM(['accept'], used=[{'message': 2, 'what': 'a promise'}, {'message': 8, 'what': 'in window'}])
    teacher = Teacher(llm, 'PAGE', DYNAMICS)
    attempts = asyncio.run(teacher.sample(View('PAGE', bundle(turn=10, window=history[-6:]), history, full=True), 0))
    assert attempts[-1].decide['used_older_messages'] == [{'message': 2, 'what': 'a promise'}]


def _all_required(schema):
    if schema.get('type') == 'object':
        assert schema['additionalProperties'] is False and set(schema['required']) == set(schema['properties'])
        for value in schema['properties'].values():
            _all_required(value)
    if schema.get('type') == 'array':
        _all_required(schema['items'])


def test_schemas_are_strict():
    _all_required(understand_schema(['n1', 'n2']))
    _all_required(understand_schema([]))
    _all_required(decide_schema(['react_briefly', 'play']))
    assert 'none' in decide_schema(['play'])['properties']['second']['enum']


# --- student data ------------------------------------------------------------------------------

def _record(episode, family, *, uncovered=False, bounded=True):
    b = bundle().to_dict()
    sample = lambda i, accepted=True: dict(
        index=i, attempt=0, thought='t', understand=understand(acts={'sharing': True}), mood_after=dict(START_MOODS['calm']),
        flags={}, allowed=list(GROUPS), decide=dict(decide('ask_about_it'), wanted_not_available=[],
                                                    used_older_messages=[{'message': 1, 'what': 'x'}] if uncovered and i == 0 else []),
        critic={}, accepted=accepted, view='full', extra={})
    turn = dict(index=0, user={}, situation_here=False, bundle=b, samples=[[sample(i)] for i in range(3)] + [[sample(3, False)]],
                noisy=False, coverage={'0': [False]} if uncovered else {}, continued=0, reply='r')
    if bounded:
        turn['bounded'] = [[sample(0)]]
    turn['replay'] = dict(mood='tense', bundle=b, samples=[[sample(0)]])
    return dict(scenario=dict(id=episode, family=family, situation='tease'), turns=[turn])


def test_examples_one_per_accepted_sample_bounded_excluded_uncovered_weighted():
    from app.ai.Cognitive.simple.student.data import examples_from
    examples = examples_from([_record('e1', 'warm/tease', uncovered=True)])
    assert len(examples) == 4                      # 3 accepted main + 1 replay; rejected and bounded excluded
    assert {e.kind for e in examples} == {'main', 'replay'}
    weights = sorted(e.weight for e in examples if e.kind == 'main')
    assert weights == [0.3, 1.0, 1.0]


def test_split_keeps_families_and_their_episodes_together():
    from app.ai.Cognitive.simple.student.data import examples_from
    from app.ai.Cognitive.simple.student.train import split
    records = [_record(f'e{i}', f'group{i % 4}/s{i % 3}') for i in range(24)]
    train, test = split(examples_from(records), seed=3)
    assert train and test
    assert not ({e.family for e in train} & {e.family for e in test})
    assert not ({e.episode for e in train} & {e.episode for e in test})


def test_predictions_round_trip_to_words():
    from app.ai.Cognitive.simple.student.data import prediction_to_understand, s1_vector
    b = bundle(notes=[Note('n1', 'her_open_question', 'how was it?', 2)])
    pred = dict(acts=np.array([[0.9, 0.1, 0, 0, 0, 0, 0, 0]]), tone=np.array([[0, 0, 1, 0, 0.0]]),
                request=np.array([[1.0, 0, 0]]),
                scalars=np.array([[0.7, 0.8, -0.45, 0.6, 0.1, 0.9, 0.26, 0, 0, 0, 0, 0, 0, 0]]),
                notes=np.array([[[0, 1.0, 0, 0, 0]] * MAX_NOTES]))
    u = prediction_to_understand(pred, 0, b)
    assert u['acts']['asking'] and not u['acts']['sharing'] and u['tone'] == 'playful'
    assert u['about_her'] == 'strong' and u['appraisal']['good_for_her'] == 'bad' and u['feelings']['happy'] == 'a_little'
    assert u['note_updates'] == [{'note': 'n1', 'update': 'answers'}]
    assert len(s1_vector(u)) == 30


@pytest.mark.parametrize('name', ['think', 'label_understand', 'label_decide', 'critic', 'say_it', 'user_card',
                                  'user_turn', 'coverage'])
def test_prompts_exist(name):
    from app.ai.Cognitive.simple.teacher import prompt
    assert prompt(name).strip()


def test_intention_is_split_from_the_thought():
    from app.ai.Cognitive.simple.intention import split_thought
    moment, intention = split_thought('**MOMENT**\nThey changed the subject.\n\n**INTENTION**\n** Go with them, keep it light.')
    assert moment == 'They changed the subject.' and intention == 'Go with them, keep it light.'
    assert split_thought('no headings here') == ('no headings here', '')


def test_world_sample_is_grounded_and_stable():
    from app.ai.Cognitive.simple.world import sample_world
    path = ROOT / 'docs' / 'characters' / 'columbina-v0.6.world.json'   # the untyped world of rounds 1-4
    assert sample_world(path, 3) == sample_world(path, 3)
    assert len(sample_world(path, 3)) == 8
    b = bundle(world=sample_world(path, 3))
    assert 'Her world' in render.text(b) and Bundle.from_dict(b.to_dict()).world == b.world


# --- memory ownership and the ablation ---------------------------------------------------------

def test_typed_memories_render_by_ownership_and_replace_untyped_blocks():
    b = bundle(memories=['Mara and Lena used to listen to radio dramas.'],
               typed_memories=[dict(kind='about_them', text='Mara and Lena used to listen to radio dramas.'),
                               dict(kind='her_own', text='Today: She wondered why people reread old messages.'),
                               dict(kind='her_preference', text="She likes the sound of the word 'drizzle'.")])
    text = render.text(b)
    about = text.index('What she knows about them')
    own = text.index('Her own experiences')
    assert about < text.index('Mara and Lena') < text.index('What the two of them have done together') < own
    assert text.index('reread old messages') > own
    assert 'What she remembers about them' not in text and 'Her world (' not in text
    assert Bundle.from_dict(b.to_dict()).typed_memories == b.typed_memories


def test_untyped_records_render_as_before():
    b = bundle(memories=['Their cat is sick.'])
    assert 'What she remembers about them:' in render.text(b)


def test_scoped_world_has_no_other_peoples_conversations():
    import json
    from app.ai.Cognitive.simple.world import sample_typed_world
    path = ROOT / 'docs' / 'characters' / 'columbina.world.json'
    assert 'noticed_in_conversations' not in json.loads(path.read_text())
    items = sample_typed_world(path, 5)
    assert {i['kind'] for i in items} == {'her_own', 'her_preference'}


def test_ablation_is_a_full_factorial_with_a_named_baseline():
    from app.ai.Cognitive.simple.ablation import ARMS, BASELINE
    assert len({a.name for a in ARMS}) == 8 and BASELINE.name == 'L-p2-r0' and BASELINE in ARMS


def test_ablation_main_effects(tmp_path):
    import json
    from app.ai.Cognitive.simple.ablation import ARMS, report
    for arm in ARMS:
        folder = tmp_path / 'arms' / arm.name
        folder.mkdir(parents=True)
        q = '?' if arm.handoff == 'labels' else '.'
        turns = [dict(reply=f'hi{q}', user=dict(reaction='neutral')), dict(reply=f'ok{q}', user=dict(reaction='good'))]
        (folder / 'e.json').write_text(json.dumps(dict(turns=turns)))
    result = report(tmp_path)
    assert result['main_effects']['intention_vs_labels']['question'] == -1.0
    assert result['main_effects']['page_v06_vs_v02']['question'] == 0.0


def test_her_notes_follow_the_actual_reply_not_a_move_label():
    from app.ai.Cognitive.simple.notes import events_from_reply
    book = NoteBook()
    # Labelled as play, but the reply actually asks something: the note follows the reply.
    events = events_from_reply('ha, fair. what made you pick that one?', decide('play'), source='description')
    assert events.asked and not events.answered
    book.apply_reply(turn=2, message='i picked the green one', understand=understand(), reply='ha, fair. what made you pick that one?',
                     events=events)
    assert [n.kind for n in book.notes] == ['her_open_question']
    # Labelled "ask about it", but the reply asks nothing: no question note.
    quiet = NoteBook()
    events = events_from_reply('that sounds lovely.', decide('ask_about_it'), source='description')
    quiet.apply_reply(turn=2, message='x', understand=understand(), reply='that sounds lovely.', events=events)
    assert quiet.notes == []
    assert events.to_dict()['source'] == 'description'


def test_notes_v1_replay_still_opens_from_labels():
    book = NoteBook()
    book.apply_decision(turn=2, message='x', decide=decide('ask_about_it'), understand=understand(), reply='that sounds lovely.')
    assert [n.kind for n in book.notes] == ['her_open_question']
