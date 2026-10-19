"""bias-application-v1: code applies her bias values to the readings the model said they bear on."""
from app.ai.Cognitive.system.v0.two_call.v1_bias import apply_biases
from app.ai.Cognitive.system.v0.two_call.v1_contracts import UnderstandingProposalV1

JAB, THANKS, GOAL = 'interpretation.pragmatic.indirect', 'interpretation.pragmatic.literal', 'interpretation.goal.express_stance'
NEUTRAL = dict.fromkeys(('benign_prior', 'trust_prior', 'threat_sensitivity', 'confirmation_tendency',
                         'self_attribution_tendency', 'social_affiliation_prior', 'hostile_attribution',
                         'social_cue_sensitivity', 'ambiguity_tolerance'), 0.5)


def reading(key, likelihood=50, fit=(), uncertainty='moderate', **appraisal):
    values = dict(uncertainty='moderate', relevance='moderate', congruence='neutral', novelty='moderate', certainty='moderate',
                  controllability='not_applicable', norm_compatibility='neutral', affiliative_value='neutral', agency='user')
    values.update(appraisal)
    return dict(hypothesis_key=key, uncertainty=uncertainty, context_refs=[], appraisal=values, likelihood=likelihood,
                bias_fit=[dict(bias_ref=f'bias:{b}', direction=d) for b, d in fit])


def proposal(*readings, impacts=(), unknowns=()):
    return UnderstandingProposalV1.model_validate(dict(
        schema_version='understanding-proposal-v1', existing_goal_impacts=list(impacts), unknowns=list(unknowns), conflicts=[],
        events=[dict(predicate_key='statement', polarity='positive', arguments={}, evidence_span_refs=['span:0'],
                     readings=list(readings))]))


def weights(values, *readings, seed='s'):
    _, application = apply_biases(proposal(*readings), NEUTRAL | values, seed)
    return {r.hypothesis_key: r.weight for r in application.readings}


def test_neutral_values_keep_the_models_likelihoods_and_appraisal():
    adjusted, application = apply_biases(proposal(reading(JAB, 70, [('hostile_attribution', 'favours')]),
                                                  reading(THANKS, 30)), NEUTRAL, 's')
    assert {r.hypothesis_key: r.weight for r in application.readings} == {JAB: 0.7, THANKS: 0.3}
    assert all(not s.applied for r in application.readings for s in r.appraisal_shifts)
    assert [r.appraisal for r in adjusted.events[0].readings] == [r.appraisal for r in proposal(
        reading(JAB, 70), reading(THANKS, 30)).events[0].readings]


def test_a_fitting_bias_moves_weight_by_its_value_and_direction():
    readings = (reading(JAB, 50, [('hostile_attribution', 'favours')]), reading(THANKS, 50))
    low, mild, high = (weights({'hostile_attribution': v}, *readings)[JAB] for v in (0.1, 0.68, 0.9))
    assert low < 0.5 < mild < high
    assert round(high, 2) == round(3 ** 0.8 / (3 ** 0.8 + 1), 2)   # full strength triples the odds
    against = weights({'hostile_attribution': 0.9}, reading(JAB, 50, [('hostile_attribution', 'disfavours')]),
                      reading(THANKS, 50))[JAB]
    assert round(against, 4) == round(1 - high, 4)


def test_opposite_biases_cancel_and_unnamed_biases_do_nothing():
    both = weights({'hostile_attribution': 0.9, 'benign_prior': 0.9},
                   reading(JAB, 50, [('hostile_attribution', 'favours')]), reading(THANKS, 50, [('benign_prior', 'favours')]))
    assert both == {JAB: 0.5, THANKS: 0.5}
    assert weights({'threat_sensitivity': 0.95}, reading(JAB, 60), reading(THANKS, 40)) == {JAB: 0.6, THANKS: 0.4}


def test_ambiguity_tolerance_flattens_or_sharpens_the_alternatives():
    readings = (reading(JAB, 80), reading(THANKS, 20))
    assert weights({'ambiguity_tolerance': 0.9}, *readings)[JAB] < 0.8 < weights({'ambiguity_tolerance': 0.1}, *readings)[JAB]


def test_readings_of_different_kinds_are_not_alternatives():
    # A speaker-goal reading and a pragmatic reading of one event are weighed separately.
    w = weights({'hostile_attribution': 0.9}, reading(GOAL, 90), reading(JAB, 50, [('hostile_attribution', 'favours')]),
                reading(THANKS, 50))
    assert w[GOAL] == 1.0 and w[JAB] > 0.5


def test_the_lead_is_drawn_from_the_weights_and_leads_the_event():
    readings = (reading(THANKS, 50), reading(JAB, 50, [('hostile_attribution', 'favours')]))
    leads = []
    for seed in map(str, range(200)):
        adjusted, application = apply_biases(proposal(*readings), NEUTRAL | {'hostile_attribution': 0.9}, seed)
        lead = next(r for r in application.readings if r.lead)
        assert adjusted.events[0].readings[0].hypothesis_key == lead.hypothesis_key and lead.index == 0
        leads.append(lead.hypothesis_key)
    # 3^0.8 / (3^0.8 + 1) = 0.71 of draws pick the jab.
    assert 0.6 < leads.count(JAB) / len(leads) < 0.82
    again = apply_biases(proposal(*readings), NEUTRAL | {'hostile_attribution': 0.9}, '7')
    assert again == apply_biases(proposal(*readings), NEUTRAL | {'hostile_attribution': 0.9}, '7')


def test_uncertainty_follows_the_weights_in_an_open_group():
    readings = proposal(reading(JAB, 95, uncertainty='high'), reading(THANKS, 5, uncertainty='low'))
    seen = set()
    for seed in map(str, range(50)):
        _, application = apply_biases(readings, NEUTRAL | {'ambiguity_tolerance': 0.1}, seed)
        by_key = {r.hypothesis_key: r for r in application.readings}
        lead = JAB if by_key[JAB].lead else THANKS
        seen.add((lead, by_key[JAB].uncertainty_after, by_key[THANKS].uncertainty_after))
    # A likely lead is low; a rare one drawn as lead is moderate, and the likely alternative stays moderate.
    assert (JAB, 'low', 'high') in seen and seen <= {(JAB, 'low', 'high'), (THANKS, 'moderate', 'moderate')}


def test_appraisal_moves_one_step_on_open_readings_only():
    full = NEUTRAL | {'hostile_attribution': 1.0}
    adjusted, application = apply_biases(proposal(reading(JAB, 50, [('hostile_attribution', 'favours')]), reading(THANKS, 50)),
                                         full, 's')
    jab = next(r for r in adjusted.events[0].readings if r.hypothesis_key == JAB)
    assert (jab.appraisal.affiliative_value, jab.appraisal.norm_compatibility) == ('negative', 'negative')
    shifts = next(r for r in application.readings if r.hypothesis_key == JAB).appraisal_shifts
    assert {(s.dimension, s.push, s.applied) for s in shifts} == {('affiliative_value', -1.0, True),
                                                                  ('norm_compatibility', -1.0, True)}
    # A clear reading (alone, low uncertainty) is never touched, whatever the model named.
    clear, application = apply_biases(proposal(reading(THANKS, 90, [('trust_prior', 'favours')], uncertainty='low')),
                                      NEUTRAL | {'trust_prior': 0.0}, 's')
    assert clear.events[0].readings[0].appraisal.certainty == 'moderate'
    assert application.readings[0].appraisal_shifts == ()
    # A single open reading may still move.
    single, _ = apply_biases(proposal(reading(THANKS, 90, [('trust_prior', 'favours')])), NEUTRAL | {'trust_prior': 1.0}, 's')
    assert single.events[0].readings[0].appraisal.certainty == 'high'


def test_reading_indexes_follow_the_new_order():
    impact = dict(goal_ref='goal:0', event_index=0, reading_index=1, relevance='high', congruence='negative',
                  uncertainty='moderate')
    unknown = dict(subject=dict(event_index=0, reading_index=1, argument_key=None),
                   unknown_key='interpretation.unclear_intent', owner='user', evidence_refs=[])
    readings = (reading(THANKS, 10), reading(JAB, 90))
    adjusted, application = apply_biases(proposal(*readings, impacts=[impact], unknowns=[unknown]),
                                         NEUTRAL | {'ambiguity_tolerance': 0.0}, 's')
    moved = next(r.index for r in application.readings if r.hypothesis_key == JAB)
    assert adjusted.existing_goal_impacts[0].reading_index == moved
    assert adjusted.unknowns[0].subject.reading_index == moved
    assert adjusted.events[0].readings[moved].hypothesis_key == JAB
