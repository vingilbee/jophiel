"""Stage-boundary regressions from the October 10 conversation diagnostic."""
from test_cognitive_two_call_v1 import (
    NULL, V1Double, event, reading, run, select, understanding, assert_replay, span_of,
)
from app.ai.Cognitive.system.v0.requirement_resolution_v0 import RequirementResolutionEngine
from test_requirement_resolution_engine import _dialogue, _request
from app.ai.Cognitive.system.v0.contracts.requirement_resolution import RequirementKind as K
from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of, understanding_content_of
from app.ai.Cognitive.system.v0.two_call.v1_content import conversational_content
from app.ai.Cognitive.system.v0.two_call.v1_training import project_training_example
from app.ai.Cognitive.system.v0.two_call import v1
from app.ai.Cognitive.system.v0.two_call.provider import prompt
import pytest


def citation(payload, message_index, phrase):
    message = payload['dialogue_context']['messages'][message_index]
    return dict(kind='dialogue_span', message_ref=message['ref'],
                span_ref=span_of({'focus': {'text': message['text']}}, phrase))


def recall(status, source_index=0, phrase='hiking'):
    def build(payload):
        return understanding(event('dialogue_recall_request', {
            'referent_phrase': {'kind': 'span', 'ref': payload['focus']['authorized_span_refs'][0]},
            'answer_ref': citation(payload, source_index, phrase) if status == 'known' else NULL,
            'candidate_refs': (dict(kind='dialogue_span_set', spans=[citation(payload, 0, 'Maya'), citation(payload, 0, 'Priya')])
                               if status == 'ambiguous_reference' else NULL),
            'knowledge_status': {'kind': 'enum', 'value': status}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
    return build


def unresolved_followup(payload):
    return understanding(event('elaboration_request', {
        'referent_phrase': NULL,
        'elaboration_kind': {'kind': 'enum', 'value': 'detail'},
        'embedding': NULL,
    }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))


def test_unresolved_followup_reaches_clarification_not_knowledge_gap():
    model = V1Double(unresolved_followup, select('seek_information.clarify'))
    record, _, _ = run(model, text='How long does it take?')
    assert record.pipeline_trace is not None, record.failure
    assert_replay(record)


def test_accepted_null_is_not_overridden_by_deterministic_dialogue_cue():
    record, _, _ = run(V1Double(unresolved_followup, select('seek_information.clarify')),
                       text='How long does it take?')
    trace = record.pipeline_trace
    frame = trace.interpretation.semantic_frames[0]
    context = _dialogue(trace, frame, resolution='resolved', resolved=('dialogue-item-tides',))
    request = _request(trace, dialogue=context)
    # Reproduce the historical failure: a cue supersedes the accepted null.
    old = RequirementResolutionEngine().resolve(request)
    assert any(r.kind == K.TRUSTED_CONTENT for r in old.snapshot.resolutions)
    current = RequirementResolutionEngine(preserve_accepted_uncertainty=True).resolve(request)
    assert not any(r.kind == K.TRUSTED_CONTENT for r in current.snapshot.resolutions)
    [referent] = current.snapshot.resolutions
    assert referent.blocking and referent.status.value == 'missing_user_input'
    assert referent.reason_code.value == 'referent_unresolved'


@pytest.mark.parametrize('contents,source,phrase', [
    ((('user', "I'm deciding whether to spend Saturday painting or hiking."),
      ('user', 'The first one appeals to me.'), ('user', 'What was the other option?')), 0, 'hiking'),
    ((('user', 'I lean toward painting.'), ('user', 'Actually, I keep leaning toward hiking, not painting.'),
      ('user', 'Given that correction, which activity am I leaning toward?')), 1,
     'I keep leaning toward hiking, not painting'),
])
def test_source_backed_recall_reaches_answer_content_and_replays(contents, source, phrase):
    model = V1Double(recall('known', source, phrase), select('provide_information.answer'))
    record, _, _ = run(model, contents=contents)
    assert record.pipeline_trace is not None, record.failure
    [content] = requirement_content_of(record.pipeline_trace)
    assert content.attribution.value == 'user'
    assert content.epistemic_status.value == 'attributed'
    assert content.arguments[0].value == phrase
    assert_replay(record)


@pytest.fixture
def registry_v1_4(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.4')


@pytest.mark.parametrize('status,action', [
    ('ambiguous_reference', 'seek_information.clarify'),
    ('missing_dialogue_fact', 'provide_information.answer'),
])
def test_ambiguous_and_missing_dialogue_fact_have_different_paths(status, action, registry_v1_4):
    model = V1Double(recall(status), select(action))
    record, _, _ = run(model, contents=(('user', 'Maya lent Priya a novel because she had finished it.'),
                                      ('user', 'Who had finished it?' if status == 'ambiguous_reference' else 'Who owns it?')))
    assert record.pipeline_trace is not None, record.failure
    assert_replay(record)
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == [action]
    content = conversational_content(requirement_content_of(record.pipeline_trace), record.pipeline_trace)
    if status == 'ambiguous_reference':
        assert any(item.surface_anchors == ('Maya', 'Priya') for item in content), (content, record.pipeline_trace.interpretation.semantic_frames)
    else:
        assert any(item.gap_kind.value == 'no_record' for item in content)


def perspective(payload):
    return understanding(event('contextual_perspective_request', {
        'first_basis_ref': citation(payload, 0, 'I care about seeing my friend'),
        'second_basis_ref': citation(payload, 1, "I'm exhausted"),
        'relation': {'kind': 'enum', 'value': 'tension'}, 'embedding': NULL,
    }, payload['focus']['authorized_span_refs'][:1], [reading()]))


def test_grounded_perspective_carries_both_sources_not_a_missing_goal():
    record, _, model = run(V1Double(perspective, select('give_feedback.reflect')), contents=(
        ('user', 'I care about seeing my friend.'), ('user', "I'm exhausted."),
        ('user', 'No advice, what makes this difficult?')))
    assert record.pipeline_trace is not None, record.failure
    [content] = understanding_content_of(record.pipeline_trace)
    assert content.predicate == 'dialogue.possible_tension'
    assert content.certainty.value == 'tentative'
    assert [a.value for a in content.arguments] == ['I care about seeing my friend', "I'm exhausted"]
    assert not any(o.object_type.value == 'unknown' for o in record.pipeline_trace.cognitive_turn.input_snapshot.action_grounded_objects)
    assert_replay(record)


def test_citation_rejects_assistant_text():
    def bad(payload):
        proposal = recall('known', 1, 'hiking')(payload)
        return proposal
    record, _, _ = run(V1Double(bad), contents=(('user', 'I like painting.'),
                                               ('assistant', 'You prefer hiking.'), ('user', 'What do I prefer?')))
    assert record.pipeline_trace is None


@pytest.mark.parametrize('mutation', ['outside_span', 'foreign_message', 'forged_source', 'duplicate_candidates'])
def test_citation_integrity(mutation):
    from test_cognitive_two_call_v1 import payload_and_entries
    payload, entries = payload_and_entries('Maya met Priya on Saturday')
    proposal = recall('known', 0, 'Maya')(payload)
    value = proposal['events'][0]['arguments']['answer_ref']
    if mutation == 'outside_span':
        value['span_ref'] = 'span:999'
    elif mutation == 'foreign_message':
        value['message_ref'] = 'message:999'
    elif mutation == 'forged_source':
        entries[value['message_ref']]['text'] = 'Other people said something else'
    else:
        proposal = recall('ambiguous_reference')(payload)
        spans = proposal['events'][0]['arguments']['candidate_refs']['spans']
        spans[1] = spans[0]
    with pytest.raises(ValueError):
        v1.compile_understanding(proposal, payload, entries)


def test_training_citations_are_local_and_unreviewed():
    record, _, _ = run(V1Double(recall('known', 0, 'hiking'), select('provide_information.answer')),
                       contents=(('user', 'I prefer hiking.'), ('user', 'What do I prefer?')))
    example = project_training_example(record)
    import json
    serialized = json.dumps(example)
    assert '#dialogue-span:' not in serialized
    assert record.compiled_request.messages[0].message_id not in serialized
    assert example['evaluation']['cognitive_review_status'] == 'unreviewed'


# ---------------------------------------------------------------------------
# Registry v1.5: conversational grounding (COGNITIVE_CONTINUITY_V1_4_E2E_EVALUATION_2026-10-10.md)
# ---------------------------------------------------------------------------

from app.ai.Cognitive.system.v0.contracts.realization import (
    DialogueReference, GapKind, GroundedQuestion, KnowledgeGap, ReferenceForm,
)
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record, wording_content_of
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from app.ai.Cognitive.system.v0.realization_v0.inventory import realization_inventory
from app.ai.Cognitive.system.v0.action_selection_v0.construction import OPTIONAL_STATEMENT_RULE_ID
from app.ai.Cognitive.system.v0.two_call.v1_contracts import CONVERSATION_REGISTRIES


def _inventory(record):
    trace = record.pipeline_trace
    return realization_inventory(trace.action_selection, None, *wording_content_of(trace),
                                 understanding_content_of(trace)).items


def _compiles(record):
    realized = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=1200)
    assert realized.realization_status.value == 'provider_unavailable', realized.realization_failure


def test_v1_5_is_the_default_registry_and_v1_4_replays_unchanged(registry_v1_4):
    record, _, model = run(V1Double(recall('ambiguous_reference'), select('seek_information.clarify')),
                           contents=(('user', 'Maya lent Priya a novel because she had finished it.'),
                                     ('user', 'Who had finished it?')))
    assert model.calls[0][1]['ontology']['predicate_registry_version'] == 'argument-kind-registry-v1.4'
    assert record.pipeline_trace.orchestration.calls[0].provenance['prompt_version'] == 'call_one-v1.4'
    assert_replay(record)


def test_ambiguous_recall_clarifies_as_a_choice_between_the_users_candidates():
    record, _, model = run(V1Double(recall('ambiguous_reference'), select('seek_information.clarify')),
                           contents=(('user', 'Maya lent Priya a novel because she had finished it.'),
                                     ('user', 'Who had finished it?')))
    assert record.pipeline_trace is not None, record.failure
    assert model.calls[0][1]['ontology']['predicate_registry_version'] in CONVERSATION_REGISTRIES
    assert_replay(record)
    items = _inventory(record)
    [question] = [i for i in items if isinstance(i, GroundedQuestion)]
    assert question.question_form.value == 'choice'
    options = {i.content_id: i for i in items if i.content_id in question.option_content_refs}
    assert sorted(o.surface_anchors[0] for o in options.values()) == ['Maya', 'Priya']
    assert all(o.reference_kind.value == 'option' and o.attribution.value == 'user' for o in options.values())
    # No reference whose anchors are the bare candidates ("Who do you mean by Maya or Priya?").
    assert not any(isinstance(i, DialogueReference) and i.reference_kind.value == 'unresolved_element'
                   and i.surface_anchors == ('Maya', 'Priya') for i in items)
    _compiles(record)


def test_unstated_dialogue_fact_is_typed_as_not_mentioned():
    record, _, _ = run(V1Double(recall('missing_dialogue_fact'), select('provide_information.answer')),
                       contents=(('user', 'Maya lent Priya a novel because she had finished it.'),
                                 ('user', 'Who owns it?')))
    assert record.pipeline_trace is not None, record.failure
    content = conversational_content(requirement_content_of(record.pipeline_trace), record.pipeline_trace)
    [gap] = [item for item in content if isinstance(item, KnowledgeGap)]
    assert gap.gap_kind == GapKind.NOT_MENTIONED
    assert_replay(record)
    _compiles(record)


def _dog_recall(payload):
    return understanding(event('dialogue_recall_request', {
        'referent_phrase': {'kind': 'span', 'ref': span_of(payload, 'that')},
        'answer_ref': citation(payload, 0, 'Rex'), 'candidate_refs': NULL,
        'knowledge_status': {'kind': 'enum', 'value': 'known'}, 'embedding': NULL,
    }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))


DOGS = (('user', 'I have two dogs, Rex and Bo. Rex is the big one.'),
        ('user', 'The big one dug up the garden this morning.'), ('user', 'Which dog did that?'))


def test_v1_4_cited_recall_with_a_deictic_referent_still_blocks(registry_v1_4):
    """The historical failure (smoke run of H_refs3), kept for replay fidelity."""
    record, _, _ = run(V1Double(_dog_recall, select('seek_information.clarify')), contents=DOGS)
    assert record.pipeline_trace is not None, record.failure
    resolutions = record.pipeline_trace.requirement_resolution.handoff.snapshot.resolutions
    assert any(r.blocking and r.argument_name == 'referent_phrase' for r in resolutions)


def test_cited_recall_satisfies_a_deictic_referent():
    """'Which dog did that?': grounding nulls 'that'; the citation, not the cue, resolves it."""
    def build(payload):
        return understanding(event('dialogue_recall_request', {
            'referent_phrase': {'kind': 'span', 'ref': span_of(payload, 'that')},
            'answer_ref': citation(payload, 0, 'Rex'), 'candidate_refs': NULL,
            'knowledge_status': {'kind': 'enum', 'value': 'known'}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
    record, _, model = run(V1Double(build, select('provide_information.answer')), contents=(
        ('user', 'I have two dogs, Rex and Bo. Rex is the big one.'),
        ('user', 'The big one dug up the garden this morning.'), ('user', 'Which dog did that?')))
    assert record.pipeline_trace is not None, record.failure
    resolutions = record.pipeline_trace.requirement_resolution.handoff.snapshot.resolutions
    assert not any(r.blocking for r in resolutions), resolutions
    assert 'provide_information.answer' in {c['action_key'] for c in model.calls[1][1]['eligible_candidates']}
    [content] = requirement_content_of(record.pipeline_trace)
    assert content.arguments[0].value == 'Rex'
    assert_replay(record)


def test_referent_clarification_without_candidates_is_generic_not_a_placeholder_quote():
    model = V1Double(unresolved_followup, select('seek_information.clarify'))
    record, _, _ = run(model, contents=(('user', 'How do volcanoes form?'), ('user', 'How long does it take?')))
    assert record.pipeline_trace is not None, record.failure
    items = _inventory(record)
    assert not any('what you are referring to' in i.surface_anchors for i in items), items
    assert any(isinstance(i, DialogueReference) and i.reference_kind.value == 'unresolved_element'
               and i.reference_form == ReferenceForm.GENERIC for i in items)
    assert_replay(record)
    _compiles(record)


def _statement(phrase):
    def build(payload):
        return understanding(event('statement', {
            'proposition': {'kind': 'span', 'ref': span_of(payload, phrase)}, 'subject': NULL,
            'embedding': {'kind': 'enum', 'value': 'direct'}}, payload['focus']['authorized_span_refs'][:1],
            [reading('interpretation.goal.transfer_information')]))
    return build


def test_shared_statement_offers_an_optional_follow_up():
    text = 'My brother just moved to Oslo for a new job.'
    offered = {}

    def two(payload):
        offered.update(optional=payload['optional_exploration_candidate_refs'],
                       keys={c['candidate_ref']: c['action_key'] for c in payload['eligible_candidates']},
                       guidance=payload['policy_guidance'])
        return select('seek_information.explore_experience')(payload)
    record, _, model = run(V1Double(_statement(text.rstrip('.')), two), text=text)
    assert record.pipeline_trace is not None, record.failure
    assert offered['guidance'] == 'policy-guidance-v1.6'
    assert [offered['keys'][ref] for ref in offered['optional']] == ['seek_information.explore_experience']
    # An inferred experience: no reflect/validate (those need an explicit expression).
    assert not {'give_feedback.reflect', 'support_socially.validate_experience'} & set(offered['keys'].values())
    [move] = record.pipeline_trace.action_selection.plan.moves
    [reference] = understanding_content_of(record.pipeline_trace)
    assert reference.reference_kind.value == 'topic' and reference.surface_anchors == (text.rstrip('.'),)
    assert_replay(record)
    _compiles(record)


def test_turn_with_a_question_gets_no_statement_follow_up():
    def build(payload):
        statement = _statement('I moved to Oslo')(payload)['events'][0]
        question = event('information_request', {
            'topic_ref': {'kind': 'span', 'ref': span_of(payload, 'how cold')},
            'question_form': {'kind': 'enum', 'value': 'wh'}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')])
        return understanding(statement, question)
    offered = {}

    def two(payload):
        offered['optional'] = payload['optional_exploration_candidate_refs']
        return select('provide_information.answer')(payload)
    record, _, _ = run(V1Double(build, two), text='I moved to Oslo, how cold does it get?')
    assert record.pipeline_trace is not None, record.failure
    assert offered['optional'] == []


def test_correction_is_acknowledged_with_its_corrected_value():
    text = "Sorry, I misspoke. It's Friday, not Thursday."

    def build(payload):
        return understanding(event('correction', {
            'corrected_attribute': {'kind': 'span', 'ref': span_of(payload, "It's")},
            'corrected_value': {'kind': 'span', 'ref': span_of(payload, 'Friday')},
            'prior_value': {'kind': 'span', 'ref': span_of(payload, 'Thursday')},
            'embedding': {'kind': 'enum', 'value': 'direct'}}, payload['focus']['authorized_span_refs'][:1],
            [reading('interpretation.goal.repair_shared_understanding')]))
    record, _, _ = run(V1Double(build, select('give_feedback.acknowledge')),
                       contents=(('user', 'My appointment is on Thursday.'), ('user', text)))
    assert record.pipeline_trace is not None, record.failure
    [move] = record.pipeline_trace.action_selection.plan.moves
    [target] = move.target_refs
    bound = [i for i in _inventory(record) if target.ref in i.represents_refs]
    # Exactly one item (a receipt binds one): the anchored correction, not the generic reference.
    [item] = bound
    assert item.reference_form == ReferenceForm.ANCHORED and item.surface_anchors == ('Friday',)
    assert_replay(record)
    _compiles(record)


# ---------------------------------------------------------------------------
# Registry v1.6: open-world contribution (two_call/contribution.py)
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from app.ai.Cognitive.system.v0.contracts.realization import Certainty, GroundedProposition
from app.ai.Cognitive.system.v0.two_call.contribution import ContributionProposalV1, Take, choose_take, take_scores


def _question(phrase):
    def build(payload):
        return understanding(event('information_request', {
            'topic_ref': {'kind': 'span', 'ref': span_of(payload, phrase)},
            'question_form': {'kind': 'enum', 'value': 'wh'}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
    return build


def _opinion(phrase):
    def build(payload):
        return understanding(event('opinion_request', {
            'topic': {'kind': 'span', 'ref': span_of(payload, phrase)}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
    return build


def _contribution(kind='general_knowledge', claims=(), takes=()):
    return lambda payload: dict(schema_version='contribution-proposal-v1', question_kind=kind,
                                claims=list(claims), takes=list(takes))


LEAVES = [dict(text='Leaves change colour because chlorophyll breaks down in autumn.', confidence='high'),
          dict(text='Other pigments then become visible.', confidence='medium')]
TAKES = [dict(stance='disagree_with_user', tone='serious', confidence='high',
              text='I think small talk is how people find out whether they want to keep talking.'),
         dict(stance='partly_agree_with_user', tone='serious', confidence='medium',
              text="I think you're right when it's scripted, but not when it's curious."),
         dict(stance='partly_agree_with_user', tone='playful', confidence='medium',
              text="I think small talk is the warm-up lap; nobody loves it, but skipping it pulls a muscle.")]


def test_open_question_is_answered_with_labelled_general_knowledge():
    model = V1Double(_question('why do leaves change colour'), select('provide_information.answer'),
                     contribution=_contribution(claims=LEAVES))
    record, _, _ = run(model, text='Why do leaves change colour in autumn?')
    assert record.pipeline_trace is not None, record.failure
    assert [stage for stage, _ in model.calls] == ['call_one', 'contribution', 'call_two']
    contribution = record.pipeline_trace.orchestration.contributions[0]
    # Registry v1.8: follow-ups are answered in context (input v1.1, prompt v1.8).
    assert contribution.provenance['prompt_version'] == 'contribution-v1.8+followup-v1'
    assert contribution.payload['schema_version'] == 'contribution-input-v1.1'
    assert 'questions to Columbina (v1.8)' in prompt('contribution', contribution.payload)
    content = [i for i in requirement_content_of(record.pipeline_trace) if isinstance(i, GroundedProposition)]
    assert [c.source_kind.value for c in content] == ['model_general_knowledge'] * 2
    # High confidence is stated plainly; medium must be hedged.
    certainty = {c.arguments[0].value: c.certainty for c in content}
    assert certainty[LEAVES[0]['text']] == Certainty.EXACT
    assert certainty[LEAVES[1]['text']] == Certainty.APPROXIMATE and content[0].attribution.value == 'columbina'
    assert_replay(record)
    _compiles(record)


@pytest.mark.parametrize('kind', ['about_the_user', 'current_events', 'unanswerable'])
def test_declined_contribution_keeps_the_knowledge_gap(kind):
    model = V1Double(_question('what did I eat'), select('provide_information.answer'),
                     contribution=_contribution(kind))
    record, _, _ = run(model, text='What did I eat yesterday?')
    assert record.pipeline_trace is not None, record.failure
    assert any(isinstance(i, KnowledgeGap) for i in requirement_content_of(record.pipeline_trace))
    assert_replay(record)


def test_dialogue_recall_never_uses_model_knowledge():
    model = V1Double(recall('missing_dialogue_fact'), select('provide_information.answer'))
    record, _, _ = run(model, contents=(('user', 'Maya lent Priya a novel.'), ('user', 'Who owns it?')))
    assert record.pipeline_trace is not None, record.failure
    assert 'contribution' not in [stage for stage, _ in model.calls]


def test_opinion_is_one_personality_selected_take_attributed_to_columbina():
    text = 'Honestly I think small talk is pointless. Change my mind.'
    model = V1Double(_opinion('small talk is pointless'), select('provide_information.answer'),
                     contribution=_contribution('opinion', takes=TAKES))
    record, _, _ = run(model, text=text)
    assert record.pipeline_trace is not None, record.failure
    contribution_payload = model.calls[1][1]
    assert contribution_payload['requested'] == 'opinion' and contribution_payload['personality'] is not None
    [view] = [i for i in requirement_content_of(record.pipeline_trace) if isinstance(i, GroundedProposition)]
    assert view.source_kind.value == 'columbina_own_view' and view.attribution.value == 'columbina'
    assert view.certainty == Certainty.TENTATIVE and view.epistemic_status.value == 'tentative'
    takes = ContributionProposalV1.model_validate(_contribution('opinion', takes=TAKES)(None)).takes
    chosen = takes[choose_take(takes, record.compiled_request)]
    assert view.arguments[0].value == chosen.text and view.predicate == f'columbina.view.{chosen.stance}'
    assert_replay(record)
    _compiles(record)


def _profile(directness, warmth, playfulness, benign=0.5):
    return SimpleNamespace(action_personality=SimpleNamespace(directness=directness, warmth=warmth,
                                                              playfulness=playfulness, curiosity=0.5),
                           interpretation_biases=SimpleNamespace(benign_prior=benign))


def test_personality_selects_different_takes():
    takes = ContributionProposalV1.model_validate(_contribution('opinion', takes=TAKES)(None)).takes
    reserved = _profile(directness=0.92, warmth=0.2, playfulness=0.08, benign=0.4)
    warm = _profile(directness=0.62, warmth=0.8, playfulness=0.58, benign=0.7)
    playful = _profile(directness=0.3, warmth=0.97, playfulness=0.97, benign=0.9)
    picks = [takes[choose_take(takes, profile)] for profile in (reserved, warm, playful)]
    assert [(p.stance, p.tone) for p in picks] == [
        ('disagree_with_user', 'serious'), ('partly_agree_with_user', 'serious'), ('partly_agree_with_user', 'playful')]
    # Deterministic: same inputs, same scores.
    assert take_scores(takes, warm) == take_scores(takes, warm)


def test_contribution_contract_rejects_mixed_or_degenerate_output():
    with pytest.raises(ValueError):
        ContributionProposalV1.model_validate(_contribution('opinion', claims=LEAVES, takes=TAKES)(None))
    with pytest.raises(ValueError):
        ContributionProposalV1.model_validate(_contribution('opinion', takes=TAKES[:1])(None))
    with pytest.raises(ValueError):
        ContributionProposalV1.model_validate(_contribution('about_the_user', claims=LEAVES)(None))
    with pytest.raises(ValueError):
        ContributionProposalV1.model_validate(_contribution('opinion', takes=[TAKES[0], TAKES[0]])(None))


def test_v1_5_open_question_stays_closed_world(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.5')
    model = V1Double(_question('why do leaves change colour'), select('provide_information.answer'),
                     contribution=_contribution(claims=LEAVES))
    record, _, _ = run(model, text='Why do leaves change colour in autumn?')
    assert record.pipeline_trace is not None, record.failure
    assert 'contribution' not in [stage for stage, _ in model.calls]
    assert 'contributions' not in record.pipeline_trace.orchestration.model_dump(mode='json')
    assert_replay(record)
