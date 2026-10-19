"""Offline checks of the conditioning harness: features and verdicts, no model calls."""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/cognitive_conditioning_eval.py'
spec = importlib.util.spec_from_file_location('conditioning_eval', SCRIPT)
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def _calls(readings, moves=('give_feedback.acknowledge',), predicate='statement'):
    return [
        {'stage': 'call_one', 'proposal': {'events': [{'predicate_key': predicate, 'readings': readings}], 'unknowns': []}},
        {'stage': 'call_two', 'compiled_proposal': {'selected_identities': [[m, [], '{}'] for m in moves]}},
    ]


def _reading(key, affiliative='neutral', refs=()):
    return {'hypothesis_key': key, 'uncertainty': 'moderate', 'context_refs': list(refs),
            'appraisal': {'uncertainty': 'moderate', 'affiliative_value': affiliative, 'controllability': 'not_applicable'}}


def test_features_cover_readings_appraisal_citations_and_moves():
    found = E.features(_calls([_reading('interpretation.pragmatic.indirect', 'negative', ['bias:hostile_attribution', 'state:2']),
                               _reading('interpretation.pragmatic.literal')], moves=('manage_dialogue.repair',)))
    assert {'event:statement', 'lead:interpretation.pragmatic.indirect', 'reading:interpretation.pragmatic.literal',
            'multi_reading', 'appraisal:affiliative_value=negative', 'cites:bias:hostile_attribution', 'cites:state',
            'move:manage_dialogue.repair', 'move_family:manage_dialogue'} <= set(found['set'])
    assert not any('controllability' in f for f in found['set'])  # not_applicable is not a value
    assert E.features([{'stage': 'call_one', 'proposal': None}]) is None


def test_verdicts_are_by_expected_direction():
    assert E._verdict(0.4, '+') == 'pass' and E._verdict(-0.4, '-') == 'pass'
    assert E._verdict(0.4, '-') == 'wrong_direction'
    assert E._verdict(0.2, '+') == 'no_effect' and E._verdict(None, '+') == 'no_data'


def test_analysis_scores_a_bias_contrast_and_a_control(tmp_path):
    fixtures = {
        'axes': {'hostile_attribution': {'core': {}, 'expect': [['appraisal:affiliative_value=negative', '+']],
                                         'messages': ['thanks for nothing.']}},
        'state': {'levels': {}, 'expect': [], 'messages': []},
        'controls': [{'text': 'I meant Tuesday.', 'correct': ['event:correction']}],
    }
    rows = []
    for level, negatives in (('low', 1), ('high', 4)):
        for repeat in range(5):
            affiliative = 'negative' if repeat < negatives else 'neutral'
            rows.append(dict(part='bias', axis='hostile_attribution', message_id='hostile_attribution:0', level=level,
                             status='completed', features=E.features(_calls([_reading('interpretation.goal.express_stance', affiliative)]))))
            rows.append(dict(part='controls', axis='hostile_attribution', message_id='control:0', level=level,
                             status='completed', features=E.features(_calls([_reading('interpretation.goal.repair_shared_understanding')],
                                                                            predicate='correction'))))
    (tmp_path / 'fixtures.json').write_text(json.dumps(fixtures))
    (tmp_path / 'results.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    summary = E.analyze(tmp_path)
    [expectation] = [e for e in summary['expectations'] if e['verdict'] != 'info']
    assert (expectation['low'], expectation['high'], expectation['delta'], expectation['verdict']) == (0.2, 0.8, 0.6, 'pass')
    [control] = summary['controls']
    assert control['stable'] and control['correct_ok']
    assert '| hostile_attribution | 1 | 0 | 0 | 0 |' in (tmp_path / 'SUMMARY.md').read_text()


def test_the_plan_crosses_every_axis_level_and_repeat():
    fixtures = json.loads((E.FIXTURE_DIR / 'fixtures.json').read_text())
    jobs = E.plan(fixtures, {'bias', 'state', 'controls', 'noise'}, None, 2)
    ids = [job['id'] for job in jobs]
    assert len(ids) == len(set(ids))
    axes = len(fixtures['axes'])
    messages = sum(len(a['messages']) for a in fixtures['axes'].values())
    assert sum(j['part'] == 'bias' for j in jobs) == messages * 2 * 2
    assert sum(j['part'] == 'controls' for j in jobs) == len(fixtures['controls']) * (axes + 1) * 2 * 2
    source = json.loads((E.FIXTURE_DIR / 'source-core.json').read_text())
    high = next(j for j in jobs if j['part'] == 'bias' and j['axis'] == 'confirmation_tendency' and j['level'] == 'high')
    # confirmation is one minus contradiction sensitivity, so a high bias sets a low source.
    assert E.core_for(fixtures, source, high)['perception_priors']['contradiction_sensitivity'] == 0.1


def test_guide_v2_features_are_read_from_the_adjusted_readings():
    jab, thanks = _reading('interpretation.pragmatic.indirect'), _reading('interpretation.pragmatic.literal', 'positive')
    calls = _calls([thanks, jab])
    calls[0]['compiled_proposal'] = {'bias_application': {'readings': [
        dict(event_index=0, source_index=0, index=1, hypothesis_key=thanks['hypothesis_key'], weight=0.3,
             uncertainty_after='high', appraisal_shifts=[]),
        dict(event_index=0, source_index=1, index=0, hypothesis_key=jab['hypothesis_key'], weight=0.7,
             uncertainty_after='low', appraisal_shifts=[dict(dimension='affiliative_value', before='neutral',
                                                             after='negative', applied=True)]),
    ]}}
    found = E.features(calls)
    assert {'leads:interpretation.pragmatic.indirect', 'lead:interpretation.pragmatic.indirect',
            'appraisal:affiliative_value=negative', 'uncertainty:high'} <= set(found['set'])
    assert 'leads:interpretation.pragmatic.literal' not in found['set']
    assert found['weights'] == {'interpretation.pragmatic.indirect': 0.7, 'interpretation.pragmatic.literal': 0.3}


def test_a_zero_expectation_passes_only_when_nothing_moves():
    assert E._verdict(0.1, '0') == 'pass' and E._verdict(-0.2, '0') == 'moved'


def test_lead_scoring_reads_appraisal_from_the_leading_readings_only():
    found = E.features(_calls([_reading('interpretation.pragmatic.literal', 'positive'),
                               _reading('interpretation.pragmatic.indirect', 'negative')]))
    assert 'lead_appraisal:affiliative_value=positive' in found['set']
    assert 'lead_appraisal:affiliative_value=negative' not in found['set']
    assert 'appraisal:affiliative_value=negative' in found['set']
    assert E._scored('appraisal:novelty=low', 'lead') == 'lead_appraisal:novelty=low'
    assert E._scored('reading:interpretation.pragmatic.indirect', 'lead') == 'leads:interpretation.pragmatic.indirect'
    assert E._scored('multi_reading', 'lead') == 'multi_reading'
