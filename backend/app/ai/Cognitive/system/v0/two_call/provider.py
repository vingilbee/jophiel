"""Exactly one provider request per orchestration stage, no SDK repairs/retries."""
import hashlib
import copy
import json
from pathlib import Path
from pydantic import ValidationError
from ..appraisal.model_contracts import AppraisalModelError
from ..realization_provider import capabilities_for, validate_protocol_capabilities
from .contracts import CallOneOutput, CallTwoOutput, VERSION


def canonical(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def compact_prompt_version(stage, payload):
    """Captured packets select their own prompt, so old captures replay unchanged."""
    if stage=='call_two' and payload['decision_context'].get('version')=='decision-context-v0.2':
        return 'v0_2'
    return 'v0_1'

V1_PROTOCOL = 'cognitive-two-call-v1'

def prompt(stage, payload=None):
    if payload and payload.get('protocol')==V1_PROTOCOL:
        if stage=='contribution':
            text = Path(__file__).with_name('contribution_v1_6.txt').read_text()
            if payload.get('schema_version') in ('contribution-input-v1.1', 'contribution-input-v1.2'):
                text += Path(__file__).with_name('contribution_v1_8.txt').read_text()
            if payload.get('schema_version') == 'contribution-input-v1.2':
                text += Path(__file__).with_name('contribution_v1_9.txt').read_text()
            return text
        # The capture's registry version selects its prompt; v1.0/v1.1 replay unchanged.
        if stage=='call_one':
            registry = (payload.get('ontology') or {}).get('predicate_registry_version')
            if registry in ('argument-kind-registry-v1.4', 'argument-kind-registry-v1.5', 'argument-kind-registry-v1.6',
                            'argument-kind-registry-v1.7', 'argument-kind-registry-v1.8'):
                extensions = {'argument-kind-registry-v1.4': ('call_one_v1_4.txt',),
                              'argument-kind-registry-v1.5': ('call_one_v1_5.txt',),
                              'argument-kind-registry-v1.6': ('call_one_v1_5.txt', 'call_one_v1_6.txt'),
                              'argument-kind-registry-v1.7': ('call_one_v1_5.txt', 'call_one_v1_6.txt',
                                                              'call_one_v1_7.txt'),
                              'argument-kind-registry-v1.8': ('call_one_v1_5.txt', 'call_one_v1_6.txt',
                                                              'call_one_v1_7.txt', 'call_one_v1_8.txt')}[registry]
                return Path(__file__).with_name('call_one_v1_2.txt').read_text() + ''.join(
                    Path(__file__).with_name(name).read_text() for name in extensions)
            current = (payload.get('ontology') or {}).get('predicate_registry_version') in ('argument-kind-registry-v1.2', 'argument-kind-registry-v1.3', 'argument-kind-registry-v1.4')
        else:
            if payload.get('policy_guidance') == 'policy-guidance-v1.5':
                return Path(__file__).with_name('call_two_v1_5.txt').read_text()
            if payload.get('policy_guidance') == 'policy-guidance-v1.3':
                return Path(__file__).with_name('call_two_v1_3.txt').read_text()
            current = payload.get('policy_guidance')=='policy-guidance-v1.2'
        name = f'{stage}_v1_2.txt' if current else f'{stage}_v1.txt'
        return Path(__file__).with_name(name).read_text()
    if payload and payload.get('protocol')=='context-conditioned-two-call-v0.1':
        return Path(__file__).with_name(f'{stage}_compact_{compact_prompt_version(stage,payload)}.txt').read_text()
    version = 'v0_1'
    if stage == 'call_two' and payload:
        version = {'two-call-stage-context-v0.2':'v0_2', 'two-call-stage-context-v0.3':'v0_3'}.get(payload.get('context_projection'), 'v0_1')
        if payload.get('alignment_policy') == 'compiled_move_spans-v0.1':
            version = 'v0_4'
    return Path(__file__).with_name(f'{stage}_{version}.txt').read_text()

def strict_schema(schema):
    """OpenAI's strict object shape, with optional fields represented by null."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items() if k not in {'default'}}
        if out.get('type') == 'object':
            out['additionalProperties'] = False
            out['required'] = list(out.get('properties', {}))
        return out
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema

def call_one_evidence_refs(payload):
    """Exact supplied citation vocabulary shared by the provider and adapter."""
    context = payload.get('appraisal_context', {})
    projection = context.get('context', {})
    evidence_refs = {r['evidence_ref'] for r in payload['semantic_input']['evidence_regions']}
    evidence_refs.update(r['ref'] for r in projection.get('dialogue_context', ()))
    evidence_refs.update(t['source_snapshot_ref'] for t in projection.get('traits', ()) if t.get('source_snapshot_ref'))
    evidence_refs.update(projection.get(k) for k in ('dynamic_state_snapshot_ref', 'relationship_snapshot_ref') if projection.get(k))
    capability = context.get('runtime', {}).get('capability_snapshot', {}).get('snapshot_id')
    if capability:
        evidence_refs.add(capability)
    evidence_refs.update(v['value_id'] for v in context.get('values', ()))
    evidence_refs.update(v['input_id'] for v in context.get('supplemental_inputs', ()))
    return frozenset(evidence_refs)


def response_schema(stage, payload):
    if payload.get('protocol')==V1_PROTOCOL and stage=='contribution':
        from .contribution import contribution_schema
        return contribution_schema(payload)
    if payload.get('protocol')==V1_PROTOCOL:
        from .v1_provider import v1_schema
        return v1_schema(stage,payload)
    if payload.get('protocol')=='context-conditioned-two-call-v0.1':
        from .compact_provider import compact_schema
        return compact_schema(stage,payload)
    cls = CallOneOutput if stage == 'call_one' else CallTwoOutput
    schema = strict_schema(cls.model_json_schema())
    if stage == 'call_one' and payload.get('semantic_input'):
        from ..interpretation_v0.model_protocol import SemanticModelPayload, response_json_schema
        from ..appraisal.model_contracts import DIMENSIONS, SCALES
        schema['properties']['interpretation'] = dict(response_json_schema(SemanticModelPayload.model_validate(payload['semantic_input'])))
        dims = schema['$defs']['DimensionProposal']['properties']
        dims['dimension_key'] = {'type':'string','enum':list(DIMENSIONS)}
        dims['value'] = {'anyOf':[{'type':'string','enum':sorted({v for scale in SCALES.values() for v in scale})},{'type':'null'}]}
        evidence_refs = call_one_evidence_refs(payload)
        for key in ('evidence_refs', 'conflict_refs'):
            dims[key]['items'] = {'type':'string', 'enum':sorted(evidence_refs)}
        definition_keys = [d['definition_key'] for d in payload['goal_definitions']]
        from ..contracts.goals import RESPONSE_OBLIGATION_DEFINITION_KEY
        assessment = schema['$defs']['OutcomeAssessment']
        def branch(keys, existing_refs=None):
            result = copy.deepcopy(assessment)
            props = result['properties']
            props['definition_key'] = {'type':'string', 'enum':keys}
            props['existing_goal_ref'] = {'type':'string', 'enum':existing_refs} if existing_refs else {'type':'null'}
            props['frame_handles'] = {'type':'array', 'items':{'type':'integer', 'minimum':0}}
            if existing_refs or keys == [RESPONSE_OBLIGATION_DEFINITION_KEY]:
                props['frame_handles']['maxItems'] = 0
            else:
                props['frame_handles']['minItems'] = 1
            return result
        branches = [branch([RESPONSE_OBLIGATION_DEFINITION_KEY]),
                    branch([k for k in definition_keys if k != RESPONSE_OBLIGATION_DEFINITION_KEY])]
        existing = payload.get('appraisal_context', {}).get('existing_goals')
        if existing and existing.get('records'):
            branches.append(branch(definition_keys, [g['goal_id'] for g in existing['records']]))
        schema['$defs']['OutcomeAssessment'] = {'anyOf':branches}
    if stage == 'call_two' and payload.get('allowed_action_definitions'):
        schema['$defs']['BehaviorMove']['properties']['action_key'] = {'type':'string','enum':[
            d['definition_ref']['action_key'] for d in payload['allowed_action_definitions']]}
        if payload.get('content_requests'):
            from ..collaborative_content_v0.model_protocol import CollaborationModelPayload, response_json_schema
            schemas = [dict(response_json_schema(CollaborationModelPayload.model_validate(r['payload'])))
                       for r in payload['content_requests']]
            schema['$defs']['ContentProposal']['properties']['output'] = schemas[0] if len(schemas)==1 else {'anyOf':schemas}
    if stage == 'call_two' and payload.get('context_projection') in {'two-call-stage-context-v0.2', 'two-call-stage-context-v0.3'}:
        from ..collaborative_content_v0.model_protocol import CollaborationModelPayload, response_json_schema
        branches = []
        requests = payload.get('content_requests', [])
        for handle, request in enumerate(requests):
            content = dict(response_json_schema(CollaborationModelPayload.model_validate(request['payload'])))
            props = content['properties']
            props['content_items']['minItems'] = 1
            from ..contracts.collaboration import MAX_STATEMENT_CHARACTERS, MAX_LABEL_CHARACTERS
            item = props['content_items']['items']['properties']
            item['statement'].update(minLength=1, maxLength=MAX_STATEMENT_CHARACTERS)
            item['anchors'].update(minItems=1, maxItems=6)
            item['anchors']['items'].update(minLength=1, maxLength=MAX_LABEL_CHARACTERS)
            if request['payload']['operation'] in {'create', 'revise'}:
                props['operations']['minItems'] = 1
            else:
                props['operations']['maxItems'] = 0
            for key in ('operations', 'content_items', 'inquiries'):
                props[key]['items']['properties']['source_refs']['minItems'] = 1
            props['inquiries']['maxItems'] = request['payload'].get('max_inquiries', 1)
            branches.append({'type':'object', 'additionalProperties':False,
                'required':['request_handle', 'output'], 'properties':{
                    'request_handle':{'type':'integer', 'enum':[handle]}, 'output':content}})
        section = schema['properties']['content_proposal']
        section.update(minItems=len(requests), maxItems=len(requests))
        if branches:
            section['items'] = branches[0] if len(branches) == 1 else {'anyOf':branches}
        # Replacing the content branch leaves generic Pydantic definitions
        # unreachable. Remove only unreachable definitions, retaining every
        # transitive reference used by behavior and response sections.
        definitions = schema.get('$defs', {})
        used = set()
        def visit(node):
            if isinstance(node, dict):
                ref = node.get('$ref', '')
                if ref.startswith('#/$defs/'):
                    key = ref.removeprefix('#/$defs/')
                    if key not in used:
                        used.add(key)
                        visit(definitions[key])
                for key, value in node.items():
                    if key != '$defs':
                        visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(schema)
        schema['$defs'] = {key:value for key,value in definitions.items() if key in used}
    return schema

def output_type(stage,payload):
    if payload.get('protocol')==V1_PROTOCOL and stage=='contribution':
        from .contribution import CONTRIBUTION_INPUT_VERSION_V1_2, ContributionProposalV1, ContributionProposalV2
        return ContributionProposalV2 if payload.get('schema_version') == CONTRIBUTION_INPUT_VERSION_V1_2 else ContributionProposalV1
    if payload.get('protocol')==V1_PROTOCOL:
        from .v1_contracts import UnderstandingProposalV1,ActionPolicyProposalV1
        return UnderstandingProposalV1 if stage=='call_one' else ActionPolicyProposalV1
    if payload.get('protocol')=='context-conditioned-two-call-v0.1':
        from .compact_contracts import MindProposal,ResponseProposal
        return MindProposal if stage=='call_one' else ResponseProposal
    return CallOneOutput if stage=='call_one' else CallTwoOutput

# Reasoning the reader (Call 1) may use. Any other stage keeps the manifest setting.
CALL_ONE_REASONING_EFFORTS = ('low', 'medium')


class OpenAITwoCallModel:
    def __init__(self, *, model_id, api_key, timeout_seconds=20, max_output_tokens=4000, client=None,
                 call_one_reasoning_effort=None):
        try:
            self.capabilities = capabilities_for(model_id)
            validate_protocol_capabilities(self.capabilities)
        except Exception as exc:
            raise AppraisalModelError('configuration_incompatible') from exc
        if call_one_reasoning_effort not in (None, *CALL_ONE_REASONING_EFFORTS):
            raise AppraisalModelError('configuration_incompatible')
        if not api_key and client is None:
            raise AppraisalModelError('configuration_unavailable')
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.client = client
        self.model_id = model_id
        self.timeout = timeout_seconds
        self.max_tokens = max_output_tokens
        self.usage = {}
        self.call_one_reasoning_effort = call_one_reasoning_effort
        self.provenance = self.base_provenance = dict(provider='openai', provider_adapter=VERSION, model_id=model_id,
            protocol_version=VERSION, endpoint='chat.completions', reasoning_effort=self.capabilities.reasoning_effort,
            temperature='0.000000', max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds,
            sdk_retries=0, streaming=False, store=False, candidate_count=1)

    def parameters(self, stage, payload, schema=None):
        schema = response_schema(stage,payload) if schema is None else schema
        suffix = {V1_PROTOCOL:'_v1','context-conditioned-two-call-v0.1':'_compact_v0_1'}.get(payload.get('protocol'),'_v0_1')
        params = dict(model=self.model_id, messages=[{'role':'system', 'content':prompt(stage, payload)},
            {'role':'user', 'content':'INPUT_DATA\n' + canonical(payload) + '\nEND_INPUT_DATA'}],
            response_format={'type':'json_schema', 'json_schema':{'name':stage+suffix, 'strict':True, 'schema':schema}},
            temperature=0.0, n=1, stream=False, store=False, timeout=float(self.timeout))
        params[self.capabilities.token_limit_parameter] = self.max_tokens
        if self.capabilities.reasoning_effort is not None:
            params['reasoning_effort'] = self.capabilities.reasoning_effort
        if stage == 'call_one' and self.call_one_reasoning_effort is not None:
            # Reasoning models accept only their default temperature.
            params['reasoning_effort'] = self.call_one_reasoning_effort
            del params['temperature']
        return params

    def provenance_for(self, stage):
        if stage == 'call_one' and self.call_one_reasoning_effort is not None:
            return self.base_provenance | dict(reasoning_effort=self.call_one_reasoning_effort, temperature='default')
        return self.base_provenance

    def generate(self, stage, payload, schema=None):
        import openai,time
        self.usage = {}
        # Read by the orchestrator after the call, so each call records its own settings.
        self.provenance = self.provenance_for(stage)
        started=time.perf_counter()
        try:
            response = self.client.chat.completions.create(**self.parameters(stage, payload, schema))
        except openai.APITimeoutError as exc:
            raise AppraisalModelError('timeout') from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError,
                openai.NotFoundError, openai.UnprocessableEntityError) as exc:
            raise AppraisalModelError('configuration_incompatible') from exc
        except openai.APIError as exc:
            raise AppraisalModelError('transport') from exc
        provider_wall_ms=(time.perf_counter()-started)*1000
        parse_started=time.perf_counter()
        choices = getattr(response, 'choices', ()) or ()
        if len(choices) != 1:
            raise AppraisalModelError('output_invalid')
        choice = choices[0]
        message = choice.message
        if getattr(message, 'refusal', None) or choice.finish_reason == 'content_filter':
            raise AppraisalModelError('refused')
        if choice.finish_reason != 'stop' or not isinstance(message.content, str) or getattr(message,'tool_calls',None):
            raise AppraisalModelError('output_invalid')
        usage = getattr(response, 'usage', None)
        if usage:
            details = getattr(usage, 'prompt_tokens_details', None)
            completion_details=getattr(usage,'completion_tokens_details',None)
            self.usage = dict(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                              cached_input_tokens=getattr(details, 'cached_tokens', None))
            if getattr(completion_details,'reasoning_tokens',None) is not None:
                self.usage['reasoning_tokens']=completion_details.reasoning_tokens
        cls = output_type(stage,payload)
        try:
            output=cls.model_validate_json(message.content)
            if payload.get("protocol") in {"context-conditioned-two-call-v0.1",V1_PROTOCOL}:
                self.usage.update(provider_wall_ms=provider_wall_ms,parse_ms=(time.perf_counter()-parse_started)*1000)
            return output
        except (ValidationError, ValueError, TypeError) as exc:
            raise AppraisalModelError('output_invalid') from exc

class FrozenTwoCallModel:
    def __init__(self, trace):
        self.trace = trace
        self.usage = {}
        self.provenance = trace.calls[0].provenance
        self.cursor = 0

    def generate(self, stage, payload, schema=None):
        if stage == 'contribution':
            # Registry v1.6: recorded contribution calls replay in their own order.
            index = getattr(self, 'contribution_cursor', 0)
            if index >= len(self.trace.contributions):
                raise AppraisalModelError('proposal_rejected')
            capture = self.trace.contributions[index]
            if capture.payload_digest != digest(payload) or capture.prompt_hash != hashlib.sha256(prompt(stage, payload).encode()).hexdigest():
                raise AppraisalModelError('proposal_rejected')
            self.contribution_cursor = index + 1
            return output_type(stage, payload).model_validate(capture.proposal)
        if self.cursor >= len(self.trace.calls):
            raise AppraisalModelError('proposal_rejected')
        capture = self.trace.calls[self.cursor]
        if capture.stage != stage or capture.payload_digest != digest(payload) or capture.prompt_hash != hashlib.sha256(prompt(stage, payload).encode()).hexdigest():
            raise AppraisalModelError('proposal_rejected')
        self.cursor += 1
        cls = output_type(stage,payload)
        return cls.model_validate(capture.proposal)
