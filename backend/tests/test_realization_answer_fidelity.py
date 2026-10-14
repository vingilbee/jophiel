"""Answer fidelity (validation policy ``conversational-v3``).

An answer move must not deny, reverse, or (for content from the conversation)
drop the known answer it was planned to deliver. The corpus holds real stored
shadow candidates: each was accepted under ``conversational-v2``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from realization_v0_support import ScriptedModel, excerpt_output

from app.ai.Cognitive.system.v0.contracts.realization import RealizationCandidateV0, RealizationRequestV0
from app.ai.Cognitive.system.v0.realization_v0.engine import build_candidate
from app.ai.Cognitive.system.v0.realization_v0.model_protocol import project_payload
from app.ai.Cognitive.system.v0.realization_v0.validator import RealizationValidatorV0
from app.ai.Cognitive.system.v0.two_call.v1_contracts import (
    REALIZATION_POLICY_V1, VALIDATION_POLICY_BY_REALIZATION_POLICY,
)

CORPUS = Path(__file__).parents[1] / 'app/ai/Cognitive/system/v0/data/answer_fidelity_regression_v0.json'
CASES = json.loads(CORPUS.read_text())['cases']
A3 = next(case for case in CASES if case['note'].startswith('A3'))


def _validate(policy, request, candidate):
    return RealizationValidatorV0(policy=policy).validate(request, candidate)


@pytest.mark.parametrize('case', CASES, ids=[case['note'][:50] for case in CASES])
@pytest.mark.parametrize('policy', ['conversational-v3', 'conversational-v4'])
def test_stored_candidates_under_v2_and_v3(case, policy):
    request = RealizationRequestV0.model_validate(case['request'])
    candidate = RealizationCandidateV0.model_validate(case['candidate'])
    assert _validate('conversational-v2', request, candidate).status == case['expected_v2']
    result = _validate(policy, request, candidate)
    assert result.status == case['expected_v3'], (case['note'], result.violations)
    assert sorted({item.code.value for item in result.violations}) == case['expected_v3_codes']


def test_corpus_covers_both_verdicts():
    verdicts = {case['expected_v3'] for case in CASES}
    assert verdicts == {'rejected', 'accepted_for_shadow_review'}
    assert all(case['expected_v3_codes'] == ['answer_contradicted'] for case in CASES if case['expected_v3'] == 'rejected')


def _scripted(request, text):
    model = ScriptedModel(text)
    output = excerpt_output(project_payload(request), text, whole=True)
    return build_candidate(request, 1, output, model.provenance)


@pytest.mark.parametrize('text,codes', [
    ('You mentioned hiking.', set()),
    ('The other one you mentioned was hiking, as far as I can tell from what you said.', set()),
    ("I don't remember what else you mentioned.", {'answer_dropped'}),
    ("I don't know what the other option was.", {'answer_dropped', 'answer_contradicted'}),
    ("I'm not sure, but you said hiking.", {'answer_contradicted'}),
])
def test_recall_answer_variants(text, codes):
    request = RealizationRequestV0.model_validate(A3['request'])
    candidate = _scripted(request, text)
    v3 = _validate('conversational-v3', request, candidate)
    answer_codes = {item.code.value for item in v3.violations} & {'answer_dropped', 'answer_contradicted'}
    assert answer_codes == codes, v3.violations
    # The two codes exist only under v3: v2 never emits them.
    v2 = _validate('conversational-v2', request, candidate)
    assert not {item.code.value for item in (*v2.violations, *v2.warnings)} & {'answer_dropped', 'answer_contradicted'}


def test_current_realization_policy_words_under_v3_and_old_policies_keep_theirs():
    assert REALIZATION_POLICY_V1 == 'separate_realization-v1.6'
    assert VALIDATION_POLICY_BY_REALIZATION_POLICY[REALIZATION_POLICY_V1] == 'conversational-v4'
    assert VALIDATION_POLICY_BY_REALIZATION_POLICY['separate_realization-v1.3'] == 'conversational-v3'
    assert VALIDATION_POLICY_BY_REALIZATION_POLICY['separate_realization-v1.2'] == 'conversational-v2'


def test_users_own_words_said_back_to_them_are_not_a_dropped_answer():
    from app.ai.Cognitive.system.v0.realization_v0.validator import addressed
    # Real-model canary 2026-10-11 (neighbors5): "You said the runner gave you the tomatoes."
    assert addressed('the runner gave me the tomatoes') == 'the runner gave you the tomatoes'
    assert addressed("I'm leaning toward my first idea") == "you're leaning toward your first idea"
    assert addressed('Ivy and Mia') == 'Ivy and Mia'
