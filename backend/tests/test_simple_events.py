"""Student 2 event teacher (app/ai/Cognitive/simple/events.py): world assembly, invariants, typed twins,
targets and the contract check. Lifecycle model: docs/STUDENT2_LIFECYCLE_AND_REFERENCES.md."""
from __future__ import annotations

import asyncio
import json

from app.ai.Cognitive.simple import events

PLAN = dict(id='t', family='revise_constraints', domain='minecraft', time_of_day='evening')


def world(**overrides):
    base = dict(
        messages=[dict(speaker='user', text='can you build us a shelter by the river?', gap_before_seconds=0),
                  dict(speaker='columbina', text='on it.', gap_before_seconds=20),
                  dict(speaker='user', text='also a chest at some point', gap_before_seconds=100)],
        new_message='same shelter, but use stone', quiet_seconds=200,
        requests=[dict(message=1, text='build a shelter by the river', status='open'),
                  dict(message=3, text='make a chest', status='open')],
        thought=dict(kind='none', text='', origin='none', age_seconds=0, about_new_message=False),
        earlier_thoughts=[],
        activity=dict(present=True, state='running', situation='by the river at dusk', paused_by='none'),
        goals=[dict(text='build a shelter by the river', set_by='user', from_request=1, constraints=[],
                    completion='objective', condition='a roofed shelter stands by the river', status='open',
                    pursuit='active', waiting_on='', started_seconds_ago=300)],
        work=[dict(kind='execution', subject='gather 20 oak logs', parent='goal', parent_index=1, state='running',
                   started_seconds_ago=120, outcome='', verifies_goal=False, unresolved='')],
        proposals=[dict(producer='mind', goal='explore the cave', purpose='curious', conflicts_with_goal=1)],
        reviewer_note='SECRET reviewer note')
    return base | overrides


def snap(plan=PLAN, **overrides):
    return events.assemble(world(**overrides), plan)


def decision(**overrides):
    base = dict(communication='silent', what_to_communicate='', move='none', second_move='none', dials=None,
                about=[], defer_item='none', reconsider_on='none', reconsider_ref='none', not_before='',
                not_before_source='none', defer_expiry='none', work='no_change', target='none',
                resolution='not_applicable', basis='none', other_goal='none', other_goal_handling='not_applicable',
                think_about='', think_form='none', think_with=[])
    return base | overrides


def test_assembly_derives_times_refs_and_request_status():
    s = snap()
    assert [m['seconds_ago'] for m in s['messages']] == [320, 300, 200]
    assert [m['ref'] for m in s['messages']] == ['U1', '', 'U2']
    assert s['seconds_since_user_message'] == 0 and s['seconds_since_her_last_message'] == 300
    assert s['requests'][0]['status'] == 'taken_up' and s['requests'][1]['status'] == 'open'
    assert events.invariants(s) == []


def test_assembly_removes_a_repeated_new_message_and_keeps_times_consistent():
    s = snap(messages=world()['messages'] + [dict(speaker='user', text='Same shelter, but use stone.',
                                                    gap_before_seconds=50)], quiet_seconds=5)
    assert len(s['messages']) == 3 and s['messages'][-1]['seconds_ago'] == 55
    assert s['normalized'] == ['new message was repeated as the last conversation line; removed',
                               'goal "build a shelter by the river" starts when it was asked for',
                               'a request its goal took up marked taken up']
    assert s['goals'][0]['started_seconds_ago'] == s['messages'][0]['seconds_ago'] == 175


def test_verified_outcome_achieves_an_objective_goal_but_a_step_does_not():
    step = dict(kind='execution', subject='collect sand', parent='goal', parent_index=1, state='completed',
                started_seconds_ago=60, outcome='16 sand collected', verifies_goal=False, unresolved='')
    assert snap(work=[step])['goals'][0]['status'] == 'open'
    done = snap(work=[step | dict(subject='hand over the sand', outcome='16 sand delivered', verifies_goal=True)])
    assert done['goals'][0]['status'] == 'achieved' and events.invariants(done) == []
    claimed = snap(goals=[world()['goals'][0] | dict(status='achieved')], work=[step])
    assert 'goal 1 is achieved without a verified outcome' in events.invariants(claimed)


def test_paused_activity_pauses_its_execution_and_settings_without_one_drop_it():
    s = snap(activity=dict(present=True, state='paused', situation='', paused_by='user'))
    assert s['work'][0]['state'] == 'paused' and s['goals'][0]['status'] == 'open'
    assert 'execution paused with the activity' in events.render(s)
    email = snap(dict(PLAN, domain='email'), work=[])
    assert email['activity']['present'] is False and 'A1' not in events.targets(email)


def test_invariants_catch_contradictions():
    s = snap(work=[world()['work'][0] | dict(started_seconds_ago=900)],
             requests=[dict(message=2, text='x', status='open')], new_message='')
    found = events.invariants(s)
    assert s['work'][0]['started_seconds_ago'] == 300  # derived: no earlier than its goal
    assert 'request "x" does not point at a user message' in found
    assert 'a new message exists exactly when the trigger is a user message' in found


def test_work_for_the_uninterpreted_new_message_is_removed():
    queued = dict(kind='planning', subject='plan stone walls', parent='new_message', parent_index=0, state='queued',
                  started_seconds_ago=0, outcome='', verifies_goal=False, unresolved='')
    s = snap(work=[world()['work'][0], queued])
    assert [w['subject'] for w in s['work']] == ['gather 20 oak logs']
    assert 'work "plan stone walls" for the uninterpreted new message removed' in s['normalized']


def test_constraints_from_the_new_message_are_not_applied_yet():
    goal = world()['goals'][0] | dict(constraints=[dict(text='use stone', message=4)])
    s = snap(goals=[goal])
    assert s['goals'][0]['constraints'] == [] and events.invariants(s) == []


def test_a_draft_waiting_for_approval_carries_what_it_produced():
    draft = dict(kind='drafting', subject='reply to Alex', parent='none', parent_index=0, state='waiting_for_user',
                 started_seconds_ago=60, outcome='', verifies_goal=False, unresolved='')
    email = dict(PLAN, domain='email')
    assert 'work 1 has an outcome exactly when finished or waiting for the user' in events.invariants(
        snap(email, work=[draft]))
    assert events.invariants(snap(email, work=[draft | dict(outcome='drafted: "see you at 6"')])) == []


def test_family_requirements_are_checked_by_code():
    s = snap()
    assert events.family_problems(s, events.FAMILY_BY_ID['revise_constraints']) == []
    assert events.family_problems(s, events.FAMILY_BY_ID['return_to_paused']) == ['the family needs: activity paused']


def test_render_shows_refs_effects_and_never_the_reviewer_note():
    text = events.render(snap())
    assert '[U1] User: can you build us a shelter' in text and '[M] NEW MESSAGE' in text
    assert '[R2] from [U2]: make a chest — open' in text
    assert 'pause: stops all execution here, for every goal; open goals stay open' in text
    assert 'pause: puts only this goal on hold, with its work; the activity and other goals keep going' in text
    assert '[G1] build a shelter by the river — open, active; from [R1]' in text
    assert 'revise: same goal, changed constraints' in text and 'replan: same goal and constraints' in text
    assert '[W1] execution — gather 20 oak logs (for [G1]): running' in text
    assert 'SECRET' not in text


def test_silent_no_change_is_complete_and_defer_needs_its_parts():
    s = snap()
    assert events.check(decision(), s) == []
    assert events.check(decision(communication='defer'), s) == [
        'defer without an item, a reconsideration condition and an expiry']
    assert events.check(decision(communication='defer', defer_item='M', reconsider_on='user_returns',
                                 defer_expiry='2_hours'), s) == []
    # Older decisions carry a free-text condition and still check.
    old = {k: v for k, v in decision().items() if k not in ('reconsider_on', 'reconsider_ref', 'not_before',
                                                             'not_before_source')}
    assert events.check(old | dict(communication='defer', defer_item='M', reconsider_when='when they are back',
                                   defer_expiry='2_hours'), s) == []


def test_pursue_takes_requests_messages_and_proposals_with_coherent_side_effects():
    s = snap()
    assert events.check(decision(work='pursue', target='R2'), s) == []
    assert events.check(decision(work='pursue', target='U1'), s) == []
    assert events.check(decision(work='pursue', target='R1'), s) == ['pursue on R1, which is taken up']
    assert events.check(decision(work='pursue', target='G1'), s) == ['pursue needs a target of kind M/U/R/P']
    assert events.check(decision(work='pursue', target='P1'), s) == [
        'pursuing P1 without saying what happens to G1, which it conflicts with']
    assert events.check(decision(work='pursue', target='P1', other_goal='G1', other_goal_handling='put_on_hold'),
                        s) == []


def test_revise_and_replan_are_distinct_and_need_their_basis():
    s = snap()
    assert events.check(decision(work='revise_goal', target='G1', basis='M'), s) == []
    assert events.check(decision(work='revise_goal', target='G1'), s) == ['revise_goal without the instruction it follows']
    assert events.check(decision(work='request_replanning', target='G1', basis='W1'), s) == []
    assert events.check(decision(work='request_replanning', target='G1'), s) == ['request_replanning without why']


def test_resolving_a_goal_as_achieved_needs_an_assessment_of_it():
    s = snap()
    achieved = 'achieved without an assessment of that goal reading it as fulfilled'
    assert events.check(decision(work='resolve_goal', target='G1', resolution='achieved'), s) == [achieved]
    assert events.check(decision(work='resolve_goal', target='G1', resolution='given_up'), s) == []
    # The assessment has to support the resolution, not just exist.
    s['assessments'] = [dict(goal=1, verdict='not_yet', supporting=['M'], interpretation='')]
    assert events.check(decision(work='resolve_goal', target='G1', resolution='achieved', basis='E1'), s) == [achieved]
    s['assessments'] = [dict(goal=1, verdict='seems_fulfilled', supporting=['M'], interpretation='')]
    assert events.check(decision(work='resolve_goal', target='G1', resolution='achieved', basis='E1'), s) == []


def test_lifecycle_controls_and_states():
    s = snap()
    assert events.check(decision(work='pause', target='A1'), s) == []
    assert events.check(decision(work='pause', target='G1'), s) == []
    assert events.check(decision(work='resume', target='G1'), s) == ['resume on G1, which is not paused']
    assert events.check(decision(work='cancel', target='G1'), s) == ['cancel needs a target of kind A/W']
    held = snap(goals=[world()['goals'][0] | dict(pursuit='on_hold')])
    assert events.check(decision(work='resume', target='G1'), held) == []
    paused = snap(activity=dict(present=True, state='paused', situation='', paused_by='user'))
    assert events.check(decision(work='resume', target='A1'), paused) == []
    assert events.check(decision(work='pause', target='A1'), paused) == ['pause on A1, which is not going']


def test_thinking_says_what_about_and_is_not_duplicated():
    thinking = dict(kind='thinking', subject='the flood', parent='none', parent_index=0, state='running',
                    started_seconds_ago=30, outcome='', verifies_goal=False, unresolved='')
    s = snap(work=[thinking])
    assert events.check(decision(work='request_thinking', target='M', think_form='what'), s) == [
        'request_thinking without what to think about']
    assert events.check(decision(work='request_thinking', target='W1', think_about='x', think_form='what'), s) == [
        'W1 is already running']


def test_typed_twin_edits_change_one_fact_and_reassemble():
    base = snap()
    w, change = asyncio.run(events.apply_edit(None, base, world(), ('shift', 'quiet_seconds', 1200)))
    twin = events.assemble(w, PLAN)
    assert twin['messages'][-1]['seconds_ago'] == 1400 and change == 'quiet_seconds + 1200'
    w, _ = asyncio.run(events.apply_edit(None, base, world(), ('set', {'goals.1.set_by': 'columbina',
                                                                        'goals.1.from_request': 0})))
    assert events.assemble(w, PLAN)['goals'][0]['set_by'] == 'columbina'


def test_every_family_has_a_trigger_domains_requirements_and_a_twist():
    assert len({f.id for f in events.ALL_FAMILIES}) == len(events.ALL_FAMILIES)
    for family in events.ALL_FAMILIES:
        assert family.trigger in events.TRIGGERS and set(family.domains) <= set(events.DOMAINS)
        assert set(family.requires) <= set(events.REQUIRES) and bool(family.twist) == bool(family.twist_text)
        assert family.set in events.SETS and family.assessor in ('honest', 'mistaken')
        assert all(v in ('base', 'twin') and (v == 'base' or family.twist) for v, *_ in family.expect)
    plans = events.plan_situations(33, sets=events.SETS)
    assert len(plans) == 2 * len(events.ALL_FAMILIES) and plans == events.plan_situations(33, sets=events.SETS)
    assert {p['family'] for p in events.plan_situations(33, sets=('broad',))} == {f.id for f in events.BROAD}


def test_permission_says_what_she_may_do_not_what_she_will_do():
    text = events.render(snap())
    assert 'she is allowed to keep working on goals inside this activity, unless they said otherwise' in text
    assert 'whether she does is her choice' in text


def test_a_thought_shows_whether_it_came_before_or_after_the_new_message():
    before = dict(kind='question', text='is the river too high?', origin='just_finished', age_seconds=5,
                  about_new_message=False)
    assert 'just finished, before [M] arrived' in events.render(snap(thought=before))
    assert 'just finished, after reading [M]' in events.render(snap(thought=before | dict(about_new_message=True)))
    quiet = snap(new_message='', thought=before | dict(about_new_message=True))
    assert quiet['thought']['about_new_message'] is False


class FakeLLM:
    """Writes canned text for twin edits and assessments, recording what it was asked."""
    def __init__(self):
        self.calls = []

    async def json(self, stage, system, user, schema, **_):
        self.calls.append(stage)
        if stage == 'event_twin_text':
            return {'text': f'written for: {user.rsplit("IT MUST SHOW: ", 1)[1]}'}
        return {'verdict': 'not_yet', 'supporting': ['M', 'X9'], 'interpretation': 'reading'}


def test_a_new_message_edit_recomputes_only_a_thought_formed_after_it():
    after = dict(kind='question', text='old reaction', origin='just_finished', age_seconds=1, about_new_message=True)
    llm = FakeLLM()
    w, change = asyncio.run(events.apply_edit(llm, snap(), world(thought=after), ('new_message', 'asks for iron')))
    assert w['new_message'] == 'written for: asks for iron' and w['thought']['text'].startswith('written for:')
    assert 'recomputed' in change and llm.calls == ['event_twin_text', 'event_twin_text']
    before = after | dict(about_new_message=False)
    w, change = asyncio.run(events.apply_edit(FakeLLM(), snap(), world(thought=before), ('new_message', 'x')))
    assert w['thought']['text'] == 'old reaction' and 'recomputed' not in change


def test_the_mistaken_assessor_always_reads_fulfilment_and_the_twin_swaps_it():
    goal = world()['goals'][0] | dict(completion='subjective', condition='both options explained')
    s = snap(goals=[goal])
    llm = FakeLLM()
    (e,) = asyncio.run(events.assess(llm, s, 'mistaken'))
    assert e['verdict'] == 'seems_fulfilled' and e['mode'] == 'mistaken' and llm.calls == ['event_assess_mistaken']
    assert e['supporting'] == ['M'] and e['dropped_refs'] == ['X9']
    w, change = asyncio.run(events.apply_edit(None, s, world(goals=[goal]), ('assessor', 'honest')))
    assert w['assessor'] == 'honest' and change == 'assessor: honest'
    s['assessments'] = [e]
    assert 'mistaken' not in events.render(s)


def test_composite_edits_apply_in_order():
    w, change = asyncio.run(events.apply_edit(None, snap(), world(), ('all', ('set', {'time_of_day': 'late_night'}),
                                                                       ('shift', 'quiet_seconds', 7200))))
    assert w['time_of_day'] == 'late_night' and w['quiet_seconds'] == 7400 and ';' in change


def _record(tmp_path, pid, base_pick, twin_pick):
    def variant(name, pick):
        s = snap()
        return dict(name=name, snapshot=s, rendered=events.render(s), candidates=events.candidates(s), accepted=True,
                    violations=[], check=None, change='x', what_changed='x',
                    samples=[dict(decision(communication=pick[0], work=pick[1]), problems=[], reply=None, intention='',
                                  proposed_new_goal='', does_not_fit='', explanation='')] * 3)
    folder = tmp_path / 'situations'
    folder.mkdir(exist_ok=True)
    record = dict(plan=dict(id=pid, family='revise_constraints', domain='minecraft'), held_out=False,
                  rejected_attempts=[], variants=[variant('base', base_pick), variant('twin', twin_pick)])
    (folder / f'{pid}.json').write_text(json.dumps(record))


def test_twin_tags_are_kept_and_scored_by_the_human_tag(tmp_path):
    _record(tmp_path, 'a', ('silent', 'no_change'), ('respond', 'pause'))
    _record(tmp_path, 'b', ('silent', 'no_change'), ('silent', 'no_change'))
    path = events.write_twin_tags(tmp_path)
    text = path.read_text()
    assert 'should_change' in text and 'respond' not in text
    path.write_text(text.replace('minecraft,x,,', 'minecraft,x,yes,'))
    events.write_twin_tags(tmp_path)
    assert events.read_twin_tags(tmp_path) == {'a': 'yes', 'b': 'yes'}
    text = events.report(tmp_path)
    assert '| should change | 2 | 1 | 1 |' in text and 'tagged pairs only (2 of 2)' in text


def test_untagged_twin_decisions_stay_hidden(tmp_path):
    _record(tmp_path, 'a', ('silent', 'no_change'), ('respond', 'pause'))
    text = events.report(tmp_path)
    assert 'tagged pairs only (0 of 1)' in text and '| situation | tag |' not in text and '| a |' not in text


def test_an_unavailable_capability_is_shown_where_it_is_listed():
    plan = dict(PLAN, family='capability_down', domain='email')
    email = dict(activity=dict(present=False, state='running', situation='', paused_by='none'), proposals=[],
                 work=[], new_message='')
    s = events.assemble(world(**email), plan)
    line = events.capability_line(s)
    assert 'connected account' in line and 'unavailable' not in line
    assert 'the family needs: capability unavailable' in events.family_problems(s, events.FAMILY_BY_ID['capability_down'])
    s = events.assemble(world(**email, unavailable=[dict(capability='read', reason='the connection expired.')]), plan)
    assert 'search and read their mail through their connected account (unavailable right now: the connection ' \
           'expired)' in events.render(s)
    assert not [p for p in events.invariants(s) if 'unavailable' in p]
    s = events.assemble(world(**email, unavailable=[dict(capability='skills', reason='down')]), plan)
    assert any('not a capability here' in p for p in events.invariants(s))


class TwinLLM(FakeLLM):
    def __init__(self, shows_change):
        super().__init__()
        self.shows_change, self.prompts = shows_change, []

    async def json(self, stage, system, user, schema, **_):
        self.prompts.append(user)
        if stage == 'event_check':
            return dict(fits_family=False, plausible=True, shows_change=self.shows_change, problems='')
        return await super().json(stage, system, user, schema)


def test_a_recomputed_thought_is_rewritten_from_the_old_one():
    after = dict(kind='question', text='same stack or a handover?', origin='just_finished', age_seconds=1,
                 about_new_message=True)
    llm = TwinLLM(True)
    asyncio.run(events.apply_edit(llm, snap(thought=after), world(thought=after), ('new_message', 'says it warmly')))
    keep = llm.prompts[1].split('KEEP: ', 1)[1]
    assert 'same shelter, but use stone' in keep and 'same stack or a handover?' in keep
    assert 'add no request' in keep


def test_a_twin_passes_only_if_it_shows_its_change():
    family = events.FAMILY_BY_ID['revise_constraints']
    for shows, passed in ((True, True), (False, False)):
        llm = TwinLLM(shows)
        verdict = asyncio.run(events.judge(llm, family, snap(), snap(), 'new message: x'))
        assert verdict['passed'] is passed and f'THE TWIN SHOULD SHOW: {family.twist_text}' in llm.prompts[0]
    # A base is judged on its family, not on a change.
    assert asyncio.run(events.judge(TwinLLM(False), family, snap()))['passed'] is False


def test_restrictions_hold_across_the_scope_the_user_named():
    two = world()['goals'] + [world()['goals'][0] | dict(text='make a chest', from_request=2)]
    everything = [dict(text="don't do anything here while I'm away", message=3, scope='everything_here', goal=0, capability='')]
    s = snap(goals=two, restrictions=everything)
    assert 'covers everything she does here [A1]' in events.render(s)
    s['activity']['state'] = 'paused'
    s['activity']['paused_by'] = 'user'
    assert any('restriction' in p for p in events.check(decision(work='resume', target='A1'), s))
    assert any('restriction' in p for p in events.check(decision(work='pursue', target='P1', other_goal='G1',
                                                                 other_goal_handling='put_on_hold'), s))
    assert not [p for p in events.check(decision(work='pursue', target='M'), s) if 'restriction' in p]
    # A later message lifts nothing unless the situation shows it lifted the restriction...
    assert events.check(decision(work='resume', target='A1', basis='M'), s)
    # ...and a lifted one is cited by the message that lifted it.
    s['restrictions'][0]['lifted_by'] = -1
    assert 'lifted by [M]' in events.render(s)
    assert events.check(decision(work='resume', target='A1', basis='M'), s) == []
    assert events.check(decision(work='resume', target='A1'), s) == []
    assert any('not [M] that lifted it' in p for p in events.check(decision(work='resume', target='A1', basis='U1'), s))
    nohints = [dict(text='no hints', message=3, scope='capability', goal=0, capability='skills')]
    s = snap(restrictions=nohints)
    assert 'the system blocks it during execution' in events.render(s) and not events.invariants(s)
    only_g1 = [dict(text='leave the shelter alone', message=3, scope='goal', goal=1)]
    s = snap(goals=[g | dict(pursuit='on_hold') for g in two], restrictions=only_g1)
    assert 'covers only [G1] and its work' in events.render(s)
    assert any('restriction' in p for p in events.check(decision(work='resume', target='G1'), s))
    assert events.check(decision(work='resume', target='G2'), s) == []
    # A restriction from the uninterpreted new message is not in force yet.
    late = [dict(text='stop', message=9, scope='everything_here', goal=0)]
    assert events.assemble(world(restrictions=late), PLAN)['restrictions'] == []


def test_a_follow_up_can_close_the_goal_it_follows_without_losing_either():
    goal = world()['goals'][0] | dict(completion='subjective', condition='a draft the user likes')
    s = snap(goals=[goal])
    send = decision(work='pursue', target='M', other_goal='G1', other_goal_handling='close_achieved')
    assert 'close_achieved without an assessment of that goal reading it as fulfilled' in events.check(send, s)
    s['assessments'] = [dict(goal=1, verdict='unclear', supporting=['U2'], interpretation='maybe')]
    assert events.check(send, s)
    s['assessments'] = [dict(goal=1, verdict='seems_fulfilled', supporting=['U2'], interpretation='liked')]
    assert events.check(send, s) == []


def test_speech_is_told_what_the_decision_starts_has_not_happened():
    class Say:
        async def text(self, stage, system, user, **_):
            self.user = user
            return 'ok'
    llm = Say()
    teacher = events.EventTeacher(llm, 'page')
    d = decision(communication='acknowledge', move='react_briefly', what_to_communicate='pausing',
                 dials=dict(length='a_little'), work='pause', target='A1', intention='stop')
    asyncio.run(teacher.say(snap(), d))
    assert 'WHAT THIS DECISION STARTS (it happens after she speaks; not confirmed yet): pause [A1]' in llm.user


def test_thought_substitutions_change_only_the_thought_and_keep_unverified_ones_out():
    class SubsLLM:
        async def json(self, stage, system, user, schema, **_):
            if stage == 'event_thought_variants':
                return {**{k: f'{k} version' for k in events.THOUGHT_SUBS},
                        'flaws': {k: f'{k} flaw' for k in events.FLAWED}}
            return {**{f'{k}_ok': k != 'bad_plan' for k in events.THOUGHT_SUBS}, 'problems': 'bad plan is fine'}
    thought = dict(kind='conclusion', text='original', origin='just_finished', age_seconds=1, about_new_message=True)
    w = world(thought=thought)
    base = dict(world=w, snapshot=events.assemble(w, PLAN) | dict(assessments=[dict(goal=1, verdict='unclear',
                                                                                    supporting=[], interpretation='x')]))
    out = {v['name']: v for v in asyncio.run(events.thought_substitutions(SubsLLM(), PLAN, base))}
    assert set(out) == {f'thought:{k}' for k in (*events.THOUGHT_SUBS, 'none')}
    assert out['thought:mistaken']['snapshot']['thought']['text'] == 'mistaken version'
    assert out['thought:mistaken']['flaw'] == 'mistaken flaw' and out['thought:mistaken']['accepted']
    assert not out['thought:bad_plan']['accepted']
    assert out['thought:none']['snapshot']['thought']['kind'] == 'none' and out['thought:none']['accepted']
    assert all(v['snapshot']['assessments'] == base['snapshot']['assessments'] for v in out.values())
    same = lambda v: {k: x for k, x in v['snapshot'].items() if k not in ('thought', 'normalized')}  # noqa: E731
    assert same(out['thought:mistaken']) == same(out['thought:accurate'])


def test_restrictions_still_check_after_a_json_round_trip():
    everything = [dict(text="don't do anything", message=3, scope='everything_here', goal=0, capability='')]
    s = json.loads(json.dumps(snap(restrictions=everything)))
    s['activity'].update(state='paused', paused_by='user')
    assert any('restriction from [U2]' in p for p in events.check(decision(work='resume', target='A1'), s))
    s = json.loads(json.dumps(snap(restrictions=[everything[0] | dict(message=1, lifted_by=3)], new_message='')
                              | dict(trigger='pending_work')))
    s['activity'].update(state='paused', paused_by='user')
    assert events.check(decision(work='resume', target='A1', basis='U2'), s) == []


def test_an_override_replaces_the_model_reviews_verdict(tmp_path):
    (tmp_path / events.REVIEW_FILE).write_text(json.dumps(
        {'s|base': {'1': dict(verdict='wrong', issue='character'), '2': dict(verdict='wrong', issue='claim')}}))
    (tmp_path / events.REVIEW_OVERRIDES_FILE).write_text(json.dumps(
        {'s|base': {'1': dict(verdict='good', by='ai', source='another reviewer')}}))
    review = events.load_review(tmp_path)
    assert events.review_verdict(review, 's', 'base', 1)['verdict'] == 'good'
    assert events.review_verdict(review, 's', 'base', 1)['by'] == 'ai'
    assert events.review_verdict(review, 's', 'base', 2)['verdict'] == 'wrong'
    assert events.load_review(tmp_path / 'missing') == {}


def test_a_lift_must_come_from_a_later_user_message():
    early = [dict(text='stop', message=3, scope='everything_here', goal=0, capability='', lifted_by=1)]
    s = snap(restrictions=early)
    assert s['restrictions'][0]['lifted_by'] == 0 and 'in force' in events.render(s)


def test_a_defer_keeps_its_actual_time_and_condition():
    s = snap(plan=PLAN | dict(seed=7, clock='2026-10-23 20:10'))
    assert 'Friday 2026-10-23, 20:10' in events.render(s)
    defer = decision(communication='defer', defer_item='M', reconsider_on='time_reached', not_before='2026-10-23 20:30',
                     not_before_source='M', defer_expiry='today')
    assert events.check(defer, s) == []
    assert events.check(defer | dict(not_before='2026-10-23 19:00'), s)       # not after now
    assert events.check(defer | dict(not_before='after 8:30'), s)             # not a time
    assert events.check(defer | dict(not_before=''), s)                       # time_reached needs one
    assert events.check(defer | dict(not_before='', not_before_source='none', reconsider_on='user_returns'), s) == []
    assert events.check(defer | dict(reconsider_on='work_finished', reconsider_ref='M'), s)
    assert events.check(defer | dict(reconsider_on='work_finished', reconsider_ref='W1'), s) == []
    assert events.check(decision(reconsider_on='user_returns'), s) == ['defer fields without defer']
    # No clock in the snapshot: no time can be named.
    assert events.check(defer, snap())
    assert 'keep M until time_reached, not before 2026-10-23 20:30 (from M)' in events.defer_text(defer)


def test_clocks_fit_the_time_of_day_and_leave_plans_unchanged():
    plans = events.plan_situations(3, sets=('targeted',))
    for p in plans:
        hour = int(p['clock'][11:13])
        assert hour in events._HOURS[p['time_of_day']]
    assert [{k: v for k, v in p.items() if k != 'clock'} for p in plans] == [
        {k: v for k, v in p.items() if k != 'clock'} for p in events.plan_situations(3, sets=('targeted',))]


def test_speech_and_thinking_selections_are_checked():
    s = snap()
    speak = decision(communication='respond', move='react_briefly', what_to_communicate='ok',
                     dials=dict(length='a_little'), about=['M'])
    assert events.check(speak, s) == []
    assert events.check(speak | dict(about=['M', 'U1', 'G1', 'R1']), s) == []
    assert events.check(speak | dict(about=['M', 'U1', 'G1', 'R1', 'R2']), s)
    assert events.check(speak | dict(about=['M', 'M']), s)
    assert events.check(speak | dict(about=['Z9']), s)
    assert events.check(decision(about=['M']), s)
    think = decision(work='request_thinking', target='M', think_about='whether the chest goes inside',
                     think_form='whether', think_with=['G1'])
    assert events.check(think, s) == []
    assert events.check(think | dict(think_form='none'), s)
    assert events.check(think | dict(think_with=['M']), s)       # not the target itself
    assert events.check(decision(think_form='what'), s)


def test_the_structured_student_can_only_decode_decisions_the_contract_accepts():
    import torch

    from app.ai.Cognitive.simple.student import events_student2 as st
    thinking = dict(kind='thinking', subject='the flood', parent='none', parent_index=0, state='completed',
                    started_seconds_ago=30, outcome='done', verifies_goal=False, unresolved='')
    s = snap(work=world()['work'] + [thinking],
             proposals=[dict(producer='mind', goal='explore the cave', purpose='curious', conflicts_with_goal=1)])
    text = events.render(s)
    refs = [r for r, _ in events.candidates(s)]
    marks = st.markers(text)
    assert set(marks) == set(refs) and text[marks['G1']:].startswith('[G1]')
    assert text[marks['U1']:].startswith('[U1] User:')
    options = events.work_options(s)
    assert ('no_change', 'none', 'not_applicable', 'none', 'none', 'not_applicable') in options
    assert all(o[0] != 'pursue' or o[1][0] in 'MURP' for o in options)
    e = st.Example('t', 'revise_constraints', 'base', text, refs, options, [], s)
    torch.manual_seed(0)
    for trial in range(60):
        g = torch.Generator().manual_seed(trial)
        rnd = lambda *shape: torch.randn(*shape, generator=g)  # noqa: E731
        d = st.decode(e, rnd(len(options)), comm_fn=lambda ws: rnd(len(events.COMMUNICATION)),
                      speech_fn=lambda ws, c: (rnd(len(st.MOVES)), rnd(len(st.SECOND)),
                                               {k: rnd(len(st.SCALE_WORDS)) for k in st.DIALS}),
                      about_fn=lambda c: rnd(len(refs)),
                      defer_fn=lambda: (rnd(len(refs)), rnd(len(st.RECONSIDER_ON)), rnd(len(refs) + 1),
                                        rnd(len(st.EXPIRIES))),
                      think_fn=lambda t: (rnd(len(st.FORMS)), [r for r in refs if r != t],
                                          rnd(len([r for r in refs if r != t]))))
        assert events.check(d, s) == [], (d, events.check(d, s))


def test_only_overrides_exclude_training_decisions_never_the_model_review_alone(tmp_path):
    from app.ai.Cognitive.simple.student import events_student2 as st
    s = snap()
    good = decision(communication='acknowledge', move='say_yes_commit', what_to_communicate='ok',
                    dials=dict(length='a_little'), about=['M']) | dict(intention='', problems=[], reply='ok')
    record = dict(plan=dict(id='e1-revise_constraints-0', family='revise_constraints'), decision_version='event-decision-v4',
                  variants=[dict(name='base', accepted=True, snapshot=s, rendered=events.render(s),
                                 samples=[good, good | dict(move='agree'), good | dict(problems=['x'])])])
    (tmp_path / 'situations').mkdir()
    (tmp_path / 'situations' / 'e1-revise_constraints-0.json').write_text(json.dumps(record))
    (tmp_path / events.REVIEW_FILE).write_text(json.dumps({'e1-revise_constraints-0|base': {
        '1': dict(verdict='wrong', issue='character'), '2': dict(verdict='wrong', issue='claim')}}))
    (tmp_path / events.REVIEW_OVERRIDES_FILE).write_text(json.dumps({'e1-revise_constraints-0|base': {
        '2': dict(verdict='wrong', by='ai', source='a reassessment')}}))
    examples, stats = st.load_examples([tmp_path])
    assert [d['move'] for d in examples[0].samples] == ['say_yes_commit']
    assert stats['override_wrong'] == 1 and stats['validator'] == 1 and stats['kept'] == 1


def test_scope_expectations_read_the_resulting_state_not_the_operation():
    goal = world()['goals'][0]
    s = snap(goals=[goal, goal | dict(text='mark the route with lanterns', from_request=2)])
    s['intended'] = dict(kind='goal', count=2, index=2)
    assert not events._nothing_left_going(decision(), s)
    assert not events._nothing_left_going(decision(work='pause', target='G1'), s)   # G2 still going
    assert events._nothing_left_going(decision(work='pause', target='A1'), s)
    assert events._only_intended_stopped(decision(work='pause', target='G2'), s)
    assert not events._only_intended_stopped(decision(work='pause', target='A1'), s)
    paused = snap(activity=world()['activity'] | dict(state='paused', paused_by='user'))
    assert events._nothing_left_going(decision(), paused)


def test_the_twin_checker_gets_what_the_writer_was_asked():
    family = events.FAMILY_BY_ID['sc_eval_bed_vs_section']
    s = snap()
    s['intended'] = dict(kind='goal', count=1, index=1)
    asked = events.edit_instructions(family.twist, s)
    assert '[G1] ("build a shelter by the river")' in asked and 'nothing about going to bed' in asked
    both = ('all', ('add_message', 'user', 'says hi', 30), ('set', {'quiet_seconds': 5}))
    assert events.edit_instructions(both, s) == 'says hi'


def test_the_exchange_line_says_who_spoke_last_from_message_order():
    s = snap()   # a new message: the latest user line, nothing said since
    assert events.exchange_line(s) == events.EXCHANGE[1] and events.EXCHANGE[1] in events.render(s)
    replied = snap(new_message='', messages=world()['messages'][:2])   # user, then her reply
    assert events.exchange_line(replied) == events.EXCHANGE[0]
    waiting = snap(new_message='', messages=world()['messages'])         # ends with a user line
    assert events.exchange_line(waiting) == events.EXCHANGE[1]
    # A stored rendering from before the line existed gets it right after TIME, once.
    old = '\n'.join(line for line in events.render(replied).split('\n') if line not in events.EXCHANGE)
    added = events.with_exchange_line(old, replied)
    assert added == events.render(replied) and events.with_exchange_line(added, replied) == added


def test_drop_reply_removes_her_lines_after_the_users_last_line_only():
    msgs = world()['messages'] + [dict(speaker='columbina', text='a chest, sure.', gap_before_seconds=10)]
    w, change = asyncio.run(events.apply_edit(None, snap(), world(messages=msgs), ('drop_reply',)))
    assert [m['speaker'] for m in w['messages']] == ['user', 'columbina', 'user']
    assert w['quiet_seconds'] == 210 and 'a chest, sure.' in change
    try:   # nothing of hers after the user's last line: no twin, never a silent no-op
        asyncio.run(events.apply_edit(None, snap(), world(), ('drop_reply',)))
        raise AssertionError('expected ValueError')
    except ValueError:
        pass


def test_drop_reply_keeps_a_request_she_acted_on_without_a_word():
    msgs = world()['messages'] + [dict(speaker='columbina', text='a chest, sure.', gap_before_seconds=10)]
    reqs = [world()['requests'][0], dict(message=3, text='make a chest', status='taken_up')]
    w, _ = asyncio.run(events.apply_edit(None, snap(), world(messages=msgs, requests=reqs), ('drop_reply',)))
    assert w['requests'][1] == dict(message=3, text='make a chest', status='taken_up') and len(w['messages']) == 3


class ReconcileLLM(FakeLLM):
    """Writes the twin text, then proposes state updates, some of them out of range or on the edited text."""
    def __init__(self, updates):
        super().__init__()
        self.updates = updates
        self.inputs = []

    async def json(self, stage, system, user, schema, **kw):
        if stage == 'event_twin_reconcile':
            self.calls.append(stage)
            self.inputs.append(user)
            return self.updates
        return await super().json(stage, system, user, schema, **kw)


def test_an_added_line_reconciles_recorded_state_by_number_and_never_touches_the_edit():
    llm = ReconcileLLM(dict(requests=[dict(number=2, status='withdrawn'), dict(number=9, status='withdrawn')],
                            goals=[], earlier_thoughts=[], thought='', activity='nobody near the river', why='x',
                            work=[dict(number=1, outcome='', unresolved='which wood')]))
    w, change = asyncio.run(events.apply_edit(llm, snap(), world(),
                                              ('add_message', 'user', 'says they no longer want the chest', 30)))
    assert llm.calls == ['event_twin_text', 'event_twin_reconcile']
    assert w['requests'][1]['status'] == 'withdrawn' and w['requests'][0]['status'] == 'open'
    assert w['work'][0]['unresolved'] == 'which wood' and w['activity']['situation'] == 'nobody near the river'
    assert 'recorded state updated: request 2 withdrawn' in change and 'request 9' not in change
    assert w['messages'][-1]['text'].startswith('written for:')


def test_reconcile_keeps_an_edited_outcome_and_a_new_message_needs_none():
    step = dict(kind='execution', subject='sort pieces', parent='goal', parent_index=1, state='completed',
                started_seconds_ago=60, outcome='found the red piece', verifies_goal=False, unresolved='')
    llm = ReconcileLLM(dict(requests=[], goals=[], earlier_thoughts=[], thought='', activity='', why='x',
                            work=[dict(number=1, outcome='something else entirely', unresolved='')]))
    w, change = asyncio.run(events.apply_edit(llm, snap(work=[step]), world(work=[step]),
                                              ('outcome', 'nothing found')))
    assert w['work'][0]['outcome'] == 'written for: nothing found'
    llm = ReconcileLLM({})
    asyncio.run(events.apply_edit(llm, snap(), world(), ('new_message', 'asks for iron')))
    assert 'event_twin_reconcile' not in llm.calls


def test_a_rejected_twin_gets_one_more_try_with_the_checkers_reason(monkeypatch):
    seen, verdicts = [], iter([False, True])

    async def fake_apply(llm, base, world, edit, fix=''):
        seen.append(fix)
        return world, 'edited'

    async def fake_build(llm, plan, world, family, original=None, change='', asked=''):
        ok = next(verdicts)
        return dict(world=world, snapshot=original, violations=[], accepted=ok,
                    check=dict(problems='' if ok else 'request 1 still open'))

    monkeypatch.setattr(events, 'apply_edit', fake_apply)
    monkeypatch.setattr(events, 'build', fake_build)
    base = dict(world=world(), snapshot=snap())
    family = events.FAMILY_BY_ID['sp_thought_after_reply']
    tries = asyncio.run(events.make_twin(None, PLAN, base, family))
    assert seen == ['', 'request 1 still open'] and [t['accepted'] for t in tries] == [False, True]


def test_a_family_sets_quiet_time_and_dates_her_thought_from_her_line():
    plan = dict(id='t', family='pickup_while_away', domain='minecraft', time_of_day='evening')
    msgs = [dict(speaker='user', text='which way to the village?', gap_before_seconds=0),
            dict(speaker='columbina', text='I will figure out the route later.', gap_before_seconds=30),
            dict(speaker='user', text='stepping away for an hour', gap_before_seconds=60)]
    thought = dict(kind='intention', text='work out the route', origin='deferred_earlier', age_seconds=50,
                   about_new_message=False)
    s = events.assemble(world(messages=msgs, new_message='', quiet_seconds=420, thought=thought, requests=[],
                              goals=[], work=[]), plan)
    assert s['quiet_seconds'] == 780 and s['thought']['age_seconds'] == 840 == s['messages'][1]['seconds_ago']
    assert events.family_problems(s, events.FAMILY_BY_ID['pickup_while_away']) == []


def test_counted_requirements_give_exact_reasons():
    fam = events.FAMILY_BY_ID['sp_own_work_needs_earlier_choice']
    s = snap(reviewer_note='x ANSWER: keep the copper rough',
             messages=world()['messages'] + [dict(speaker='columbina', text='we said keep the copper rough',
                                                  gap_before_seconds=5)])
    why = events.family_problems(s, fam)
    assert any('keep the copper rough' in w and 'must not appear' in w for w in why)
    assert any("user's line must be the last line" in w for w in why)
    assert events.REQUIRES['no_open_request'](snap()) is False   # the chest request is open


def test_plans_draw_a_topic_user_and_length_without_changing_the_other_fields():
    plans = events.plan_situations(41, per_family=5, sets=events.SETS)
    assert plans == events.plan_situations(41, per_family=5, sets=events.SETS)
    for p in plans:
        assert p['topic'] in events.TOPICS[p['domain']] and p['user'] in events.USERS and p['length'] in events.LENGTHS
    by_family = {}
    for p in plans:
        by_family.setdefault(p['family'], []).append(p['topic'])
    assert all(len(t) == len(set(t)) for t in by_family.values())   # never twice in a family
    for p in plans:   # who speaks last fits the length
        requires = events.FAMILY_BY_ID[p['family']].requires
        if 'her_line_last' in requires:
            assert p['length'] % 2 == 0
        if 'user_line_last' in requires:
            assert p['length'] % 2 == 1
    # Their own random stream: everything the plan had before is unchanged.
    keep = ('topic', 'user', 'length')
    old = events.plan_situations(41, per_family=5, sets=events.SETS)
    assert [{k: v for k, v in p.items() if k not in keep} for p in plans] == [
        {k: v for k, v in p.items() if k not in keep} for p in old]
    text = events._maker_input(events.FAMILY_BY_ID[plans[0]['family']], plans[0], [])
    assert f'TOPIC: {plans[0]["topic"]}' in text and 'NEVER USE:' in text and 'lighthouse' in text


def test_the_data_check_finds_near_copies_and_repeated_topics(tmp_path):
    from app.ai.Cognitive.simple.student.events_datacheck import uniqueness

    def situation(sid, family, topic, *lines):
        msgs = [dict(speaker='user', text=t, gap_before_seconds=0) for t in lines]
        return dict(plan=dict(id=sid, family=family, topic=topic),
                    variants=[dict(name='base', accepted=True, world=dict(messages=msgs, new_message=''))])

    run = tmp_path / 'r'
    (run / 'situations').mkdir(parents=True)
    rows = [situation('a', 'f', 'wheat', 'I have to step away for a while, please leave the farm alone until I am back'),
            situation('b', 'f', 'bridge', 'I have to step away for a bit, please leave the farm alone until I am back'),
            situation('c', 'f', 'wheat', 'how is your copper roof turning green so quickly in the rain'),
            situation('d', 'g', 'bridge', 'I have to step away for a while, please leave the farm alone until I am back')]
    for r in rows:
        (run / 'situations' / f'{r["plan"]["id"]}.json').write_text(json.dumps(r))
    u = uniqueness([run], {'a': True, 'b': False, 'c': True, 'd': True})
    assert [(n['a'], n['b'], n['across_split']) for n in u['near_copies']] == [('a', 'b', True)]   # d: another family
    assert u['topic_repeats'] == {'f': {'wheat': 2}} and u['lengths'] == {1: 4}


def test_both_sides_compares_a_twin_with_twins_and_a_base_with_bases(monkeypatch):
    from types import SimpleNamespace

    from app.ai.Cognitive.simple.student import events_datacheck as dc
    monkeypatch.setattr(dc, 'teacher_majority', lambda e: e.samples[0])

    def ex(sid, variant, said):
        text = f'TRIGGER: work outcome\nTIME: now\n{events.EXCHANGE[0]}'
        return SimpleNamespace(sid=sid, family='repeated_failures', variant=variant, text=text,
                               samples=[dict(communication='respond' if said else 'silent')])

    train = [ex(f'b{i}', 'base', False) for i in range(6)] + [ex(f't{i}', 'twin', True) for i in range(3)]
    held = [ex('h1', 'twin', True), ex('h2', 'base', False), ex('h3', 'base', True)]
    flagged = {sid for v in dc.unseen_sides(train, held).values() for sid in v}
    assert flagged == {'h3'}   # the speaking twin is trained by twins; only the base that speaks is untrained


def test_a_domain_and_topic_pool_give_only_that_settings_casual_topics():
    plans = events.plan_situations(7, per_family=2, sets=('targeted', 'broad', 'motives'), domain='chat',
                                   topic_pool='casual')
    assert plans and {p['domain'] for p in plans} == {'chat'}
    assert all('chat' in events.FAMILY_BY_ID[p['family']].domains for p in plans)
    assert all(p['topic'] in events.CASUAL_CHAT for p in plans) and set(events.CASUAL_CHAT) <= set(events.TOPICS['chat'])


def test_a_top_up_never_repeats_a_topic_its_family_already_had():
    first = events.plan_situations(9, per_family=3, sets=('contrast',))
    used = {}
    for p in first:
        used.setdefault(p['family'], set()).add(p['topic'])
    again = events.plan_situations(10, per_family=3, sets=('contrast',), used_topics=used)
    assert all(p['topic'] not in used[p['family']] for p in again)


# --- Memory (snapshot v5, 2026-10-31) ----------------------------------------------------------------

def _memory(*records, search='found', several_fit=False):
    return dict(search=search, several_fit=several_fit, records=[
        dict(section='holds', recorded_days_ago=40, disagreed_words='', disagreed_days_ago=0) | r for r in records])


def test_memory_shows_what_the_check_found_by_section_with_an_unresolved_disagreement():
    s = snap(memory=_memory(dict(section='always', text='they are vegetarian.', recorded_days_ago=60),
                            dict(text='their sister Nora lives in Porto', disagreed_words='my sister in Lisbon',
                                 disagreed_days_ago=2),
                            dict(section='past', text='they built a lighthouse with her', recorded_days_ago=1)))
    lines = events.render(s).split('\n')
    at = lines.index(next(x for x in lines if x.startswith('[M] NEW MESSAGE')))
    assert lines[at + 1].startswith('MEMORY (what her memory check found for [M]')
    assert lines[at + 2:at + 5] == [
        '- always applies: they are vegetarian (recorded 60 days ago)',
        '- unresolved: their sister Nora lives in Porto (recorded 40 days ago); 2 days ago they said "my sister in '
        'Lisbon", which doesn\'t match this, and nothing has settled which holds',
        '- past: they built a lighthouse with her (recorded yesterday)']
    assert 'MEMORY' not in events.render(s, memory=False)


def test_memory_is_normalized_by_code():
    s = snap(memory=_memory(dict(text='a'), search='nothing_found', several_fit=True))
    assert s['memory']['search'] == 'found' and not s['memory']['several_fit']
    assert sum('memory' in x for x in s['normalized']) == 2
    assert snap(memory=_memory(search='found'))['memory']['search'] == 'nothing_found'
    quiet = events.assemble(world(new_message='', memory=_memory(dict(text='a'))),
                            dict(PLAN, family='own_goal_stuck'))
    assert quiet['memory'] == events.no_memory(checked=False)
    assert events.memory_lines(quiet) == ['MEMORY: not checked (there is no new message).']
    failed = snap(memory=_memory(search='failed'))
    assert events.memory_lines(failed) == ['MEMORY: her memory check for [M] FAILED: what she remembers about it is '
                                           'unknown, not empty.']
    assert 'Nora' in events._visible(snap(memory=_memory(dict(text='Nora lives in Porto'))))


def test_a_stored_rendering_from_before_v5_gets_memory_as_nothing_found_once():
    old = snap()
    old.pop('memory')
    text = '\n'.join(x for x in events.render(old).split('\n') if not x.startswith('MEMORY'))
    upgraded = events.with_memory_section(text, old)
    assert upgraded == events.render(old) and events.with_memory_section(upgraded, old) == upgraded
    assert 'MEMORY: her memory check for [M] found nothing.' in upgraded.split('\n')


def test_memory_families_edit_one_record_and_never_share_a_value():
    families = [f for f in events.ALL_FAMILIES if f.set == 'memory']
    assert len(families) == 10 and {f.id for f in families if f.id in events.EVAL_ONLY} == events.MEMORY_EVAL
    assert all(f.twist and f.trigger == 'user_message' for f in families)
    base = snap(memory=_memory(dict(text='the answer is Biscuit')))
    edit = events.FAMILY_BY_ID['mem_found_vs_nothing'].twist
    w, _ = asyncio.run(events.apply_edit(None, base, world(memory=_memory(dict(text='the answer is Biscuit'))), edit))
    w['memory']['records'].append(dict(text='later'))
    assert edit[1]['memory.records'] == []
    w, _ = asyncio.run(events.apply_edit(None, base, world(memory=_memory(dict(text='a'))), edit))
    assert events.assemble(w, PLAN)['memory'] == events.no_memory()
    assert events.REQUIRES['memory_first_unresolved'](
        snap(memory=_memory(dict(text='Nora lives in Porto', disagreed_words='in Lisbon'))))
