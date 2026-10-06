"""The requested cognitive stages send explicit zero reasoning to GPT-6 Luna."""
from __future__ import annotations

from app.ai.Cognitive.system.v0.appraisal_provider import OpenAIAppraisalModel
from app.ai.Cognitive.system.v0.appraisal.model_contracts import CognitiveModelContext
from app.ai.Cognitive.system.v0.appraisal.model_engine import payload_for
from app.ai.Cognitive.system.v0.contracts.interpretation import SemanticParserProvenance
from app.ai.Cognitive.system.v0.contracts.realization import RealizationModelProvenance
from app.ai.Cognitive.system.v0.realization_provider import OpenAIRealizationModel
from app.ai.Cognitive.system.v0.realization_v0 import project_payload
from app.ai.Cognitive.system.v0.semantic_parser_provider import OpenAISemanticCandidateModel
from app.core.config import Settings
from test_model_appraisal import appraisal_request, _request
from test_realization_provider_capabilities import ITEMS, RecordingClient
from test_semantic_parser_hardening import _payload
from realization_v0_support import compile_request, case_inputs


def test_all_three_stages_send_and_record_luna_zero_reasoning():
    semantic = OpenAISemanticCandidateModel(model_id='gpt-6-luna', api_key=None,
                                           timeout_seconds=20, client=RecordingClient())
    appraisal = OpenAIAppraisalModel(model_id='gpt-6-luna', api_key=None,
                                    timeout_seconds=20, client=RecordingClient())
    realization = OpenAIRealizationModel(model_id='gpt-6-luna', api_key=None,
                                        timeout_seconds=20, client=RecordingClient())
    payloads = (_payload(), payload_for(appraisal_request(_request()), CognitiveModelContext()),
                project_payload(compile_request(case_inputs('action-selection-prior-failure', ITEMS))))
    for model, payload in zip((semantic, appraisal, realization), payloads):
        parameters = model.request_parameters(payload)
        assert parameters['model'] == 'gpt-6-luna'
        assert parameters['reasoning_effort'] == 'none'
        assert parameters['temperature'] == 0
        assert parameters['response_format']['json_schema']['strict'] is True
        assert parameters['store'] is False and parameters['stream'] is False
        provenance = model.provenance
        raw = provenance if isinstance(provenance, dict) else provenance.model_dump(mode='json')
        assert raw['reasoning_effort'] == 'none'
    assert SemanticParserProvenance.model_validate_json(semantic.provenance.model_dump_json()) == semantic.provenance
    assert RealizationModelProvenance.model_validate_json(realization.provenance.model_dump_json()) == realization.provenance


def test_defaults_select_requested_model_without_reading_developer_environment():
    for stage in ('semantic_parser', 'appraisal', 'realization'):
        assert Settings.model_fields[f'cognitive_shadow_{stage}_model'].default == 'gpt-6-luna'
    assert Settings.model_fields['cognitive_shadow_appraisal_implementation'].default == 'deterministic'
    assert Settings.model_fields['cognitive_shadow_realization_enabled'].default is False


def test_nonreasoning_model_does_not_receive_new_reasoning_parameter():
    semantic = OpenAISemanticCandidateModel(model_id='gpt-4o-mini', api_key=None,
                                           timeout_seconds=20, client=RecordingClient())
    assert 'reasoning_effort' not in semantic.request_parameters(_payload())
    assert 'reasoning_effort' not in semantic.provenance.model_dump(mode='json')
