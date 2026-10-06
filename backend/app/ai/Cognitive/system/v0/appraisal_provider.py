"""Single-attempt OpenAI adapter. Explicit model, strict schema, no fallback."""
from __future__ import annotations

import json
from typing import Any

from .appraisal.model_contracts import AppraisalModelError, AppraisalModelOutput, PROTOCOL_VERSION
from .appraisal.model_engine import PROMPT, PROMPT_HASH, PROMPT_VERSION, canonical
from .realization_provider import capabilities_for, ENDPOINT, PROVIDER_CAPABILITY_MANIFEST_VERSION
from .realization_v0.errors import RealizationProviderConfigurationError


def response_schema():
    schema = AppraisalModelOutput.model_json_schema()
    def strict(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)
    strict(schema)
    return schema


def resolve_provenance(*, model_id, timeout_seconds, max_output_tokens=4000):
    if not isinstance(model_id, str) or not model_id.strip():
        raise AppraisalModelError('configuration_unavailable')
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise AppraisalModelError('configuration_incompatible')
    try:
        cap = capabilities_for(model_id)
    except RealizationProviderConfigurationError as exc:
        raise AppraisalModelError('configuration_incompatible') from exc
    if not (ENDPOINT in cap.endpoints and cap.strict_json_schema and cap.storage_control and cap.token_limit_parameter):
        raise AppraisalModelError('configuration_incompatible')
    return {'provider': 'openai', 'model': model_id, 'adapter': 'openai-appraisal-v0.1',
            'model_family': cap.family, 'endpoint': ENDPOINT, 'protocol': PROTOCOL_VERSION,
            'capability_manifest': PROVIDER_CAPABILITY_MANIFEST_VERSION,
            'prompt_version': PROMPT_VERSION, 'prompt_hash': PROMPT_HASH,
            'schema_version': 'appraisal-proposal-schema-v0.1',
            'temperature': 0 if cap.custom_temperature else None,
            'reasoning_controls': cap.reasoning_effort or 'not_sent',
            'reasoning_effort': cap.reasoning_effort, 'reasoning_controls_supported': cap.reasoning_controls,
            'timeout_seconds': timeout_seconds, 'max_output_tokens': max_output_tokens,
            'token_limit_parameter': cap.token_limit_parameter, 'sdk_retries': 0, 'max_attempts': 1,
            'streaming': False, 'store': False}


class OpenAIAppraisalModel:
    def __init__(self, *, model_id, api_key, timeout_seconds, client: Any = None):
        self.provenance = resolve_provenance(model_id=model_id, timeout_seconds=timeout_seconds)
        if client is None:
            if not api_key:
                raise AppraisalModelError('configuration_unavailable')
            from openai import OpenAI
            client = OpenAI(api_key=api_key, timeout=float(timeout_seconds), max_retries=0)
        self.client = client
        self.usage = {}

    def request_parameters(self, payload):
        p = self.provenance
        params = dict(model=p['model'], messages=[{'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': canonical(payload)}],
            response_format={'type': 'json_schema', 'json_schema': {'name': 'cognitive_appraisal_v0_1',
                'strict': True, 'schema': response_schema()}},
            n=1, stream=False, store=False, timeout=float(p['timeout_seconds']))
        params[p['token_limit_parameter']] = p['max_output_tokens']
        if p['temperature'] is not None:
            params['temperature'] = p['temperature']
        if p['reasoning_effort'] is not None:
            params['reasoning_effort'] = p['reasoning_effort']
        return params

    def generate(self, payload):
        import openai
        self.usage = {}
        try:
            response = self.client.chat.completions.create(**self.request_parameters(payload))
        except openai.APITimeoutError as exc:
            raise AppraisalModelError('timeout') from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError,
                openai.NotFoundError, openai.UnprocessableEntityError) as exc:
            raise AppraisalModelError('configuration_incompatible') from exc
        except openai.APIError as exc:
            raise AppraisalModelError('transport') from exc
        usage = getattr(response, 'usage', None)
        details = getattr(usage, 'prompt_tokens_details', None)
        self.usage = {key: value for key, value in {
            'input_tokens': getattr(usage, 'prompt_tokens', None),
            'output_tokens': getattr(usage, 'completion_tokens', None),
            'cached_input_tokens': getattr(details, 'cached_tokens', None),
        }.items() if isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        choices = getattr(response, 'choices', ()) or ()
        if len(choices) != 1:
            raise AppraisalModelError('output_invalid')
        choice = choices[0]
        message = choice.message
        if getattr(message, 'refusal', None) or choice.finish_reason == 'content_filter':
            raise AppraisalModelError('refused')
        if (choice.finish_reason != 'stop' or not isinstance(message.content, str)
                or getattr(message, 'tool_calls', None) or getattr(message, 'function_call', None)):
            raise AppraisalModelError('output_invalid')
        try:
            return AppraisalModelOutput.model_validate(json.loads(message.content))
        except (ValueError, TypeError) as exc:
            raise AppraisalModelError('output_invalid') from exc
