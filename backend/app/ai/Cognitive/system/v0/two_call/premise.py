"""Turn-local premise assessment anchored to actual assistant speech, not personality.

The verdict and current-position relation are model judgments. Only their
evidence binding is deterministic; none freezes a preference or forbids change.
"""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts.interpretation import FrozenModel

PREMISE_CONTEXT_VERSION = 'recorded-position-premise-v1'
INDEPENDENT_ASSESSMENT_VERSION = 'recorded-position-premise-v2'

class PremiseAssessment(FrozenModel):
    premise: str | None = Field(default=None, max_length=400)
    status: Literal['no_asserted_change', 'supported', 'contradicted', 'insufficient_evidence', 'not_applicable']
    position_refs: tuple[str, ...] = Field(max_length=6)
    relation: Literal['unchanged', 'stronger_support', 'exception', 'revised', 'uncertain', 'not_applicable']
    reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode='after')
    def grounded_claim(self):
        if len(set(self.position_refs)) != len(self.position_refs):
            raise ValueError('duplicate premise position reference')
        if (self.status in ('supported', 'contradicted') or self.relation in
                ('unchanged', 'stronger_support', 'exception', 'revised')) and not self.position_refs:
            raise ValueError('a claimed relation to a recorded position needs evidence')
        return self

def premise_context(request, followup):
    """Recent speech plus checked older excerpts; never a generated old belief."""
    statements = {}
    for index, message in enumerate(request.messages[-8:], start=max(0, len(request.messages)-8)):
        if message.message_id == request.focus_message_id or message.role.value != 'assistant':
            continue
        if len(message.content) <= 2000:
            ref = f'prior:{index}'
            statements[ref] = dict(ref=ref, message_ref=f'message:{index}', text=message.content,
                                   coverage='full_message')
    for source in (followup.get('followup_context') or {}).get('sources', []):
        if source['speaker'] != 'assistant':
            continue
        index = int(source['message_ref'].split(':')[1])
        full = source.get('source_message_text')
        ref = f'prior:{index}' if full is not None else f'prior:{index}:{source["start"]}:{source["end"]}'
        statements[ref] = dict(ref=ref, message_ref=source['message_ref'], text=full if full is not None else source['text'],
                               coverage='full_message' if full is not None else 'excerpt')
    focus = next(m for m in request.messages if m.message_id == request.focus_message_id)
    return dict(version=PREMISE_CONTEXT_VERSION, user_utterance=focus.content,
                assistant_statements=list(statements.values()))

def assessment_schema(context):
    refs = [s['ref'] for s in context['assistant_statements']]
    properties = {
        'premise': {'type': ['string', 'null'], 'maxLength': 400},
        'status': {'type': 'string', 'enum': list(PremiseAssessment.model_fields['status'].annotation.__args__)},
        'position_refs': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string', 'enum': refs} if refs else {'type': 'string'}},
        'relation': {'type': 'string', 'enum': list(PremiseAssessment.model_fields['relation'].annotation.__args__)},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 400},
    }
    if not refs:
        properties['position_refs']['maxItems'] = 0
    if context.get('version') in (INDEPENDENT_ASSESSMENT_VERSION, 'recorded-position-premise-v3'):
        properties['status']['description'] = ('Assess only the user\'s asserted or presupposed relationship to prior speech. '
            'A neutral comparison has no_asserted_change, regardless of the relation found. '
            'Use not_applicable for requests independent of prior positions.')
        properties['relation']['description'] = ('Independently compare the relevant recorded and current positions, '
            'including a requested comparison of two recorded positions. Do not derive this from status. '
            'A neutral comparison can be revised, unchanged, stronger_support, exception or uncertain. '
            'Use not_applicable only when no position relationship is being evaluated; '
            'missing evidence for a requested comparison is uncertain.')
        properties['reason']['description'] = 'Briefly justify the premise verdict and, separately, the position relation from the supplied evidence.'
    schema = dict(type='object', additionalProperties=False, properties=properties, required=list(properties))
    if context.get('version') == 'recorded-position-premise-v3':
        # Mirror grounded_claim at generation time. Do not constrain which
        # opinion/relation is chosen; require evidence for an asserted relation.
        import copy
        unbound = copy.deepcopy(schema)
        unbound['properties']['position_refs']['maxItems'] = 0
        unbound['properties']['status']['enum'] = ['no_asserted_change', 'insufficient_evidence', 'not_applicable']
        unbound['properties']['relation']['enum'] = ['uncertain', 'not_applicable']
        if not refs:
            return unbound
        bound = copy.deepcopy(schema)
        bound['properties']['position_refs']['minItems'] = 1
        return {'anyOf': [bound, unbound]}
    return schema

def validate_assessment(assessment, context):
    """Record exact supporting speech; reject foreign references, not opinions."""
    statements = {s['ref']: s for s in context['assistant_statements']}
    if any(ref not in statements for ref in assessment.position_refs):
        raise ValueError('premise references speech not supplied to contribution')
    return dict(assessment=assessment.model_dump(mode='json'),
                recorded_speech=[statements[ref] for ref in assessment.position_refs])
