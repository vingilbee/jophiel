"""Closed, stage-scoped schemas for compact packets."""
from .compact_contracts import MindProposal,ResponseProposal
from .provider import strict_schema
from ..contracts.interpretation import HYPOTHESIS_KEYS_BY_DIMENSION,HypothesisDimension
from ..appraisal.model_contracts import SCALES

def patch_operation_schema(operation, definitions, request_index):
    """Match required compiler inputs; share nested shapes to keep tokens small."""
    from copy import deepcopy
    from ..collaborative_content_v0.compiler import PATCH_REQUIRED_FIELDS
    properties=deepcopy(operation['properties'])
    def nonnull(shape):
        return next(x for x in shape['anyOf'] if x.get('type')!='null')
    for field in ('constraint','dish'):
        nested=nonnull(properties[field])
        for name,shape in nested['properties'].items():
            if shape.get('type')=='string' and 'enum' not in shape:
                shape.update(minLength=1,pattern=r'\S')
        if field=='dish':
            nonnull(nested['properties']['prep_time_minutes']).update(minimum=1,maximum=1440)
        key=f'CompactPatch{request_index}'+field.title()
        definitions[key]=nested
        properties[field]={'anyOf':[{'$ref':f'#/$defs/{key}'},{'type':'null'}]}
    for field in ('element_id','section_id','text','heading'):
        nonnull(properties[field]).update(minLength=1,pattern=r'\S')
    nonnull(properties['integer_value']).update(minimum=1,maximum=10000)
    groups={}
    for op in properties['op']['enum']:
        groups.setdefault(PATCH_REQUIRED_FIELDS[op],[]).append(op)
    branches=[]
    for required,ops in groups.items():
        props={key:deepcopy(value) for key,value in properties.items()}
        props['op']={'type':'string','enum':ops}
        for field in properties:
            if field in {'op','source_refs'}:continue
            props[field]=nonnull(props[field]) if field in required else {'type':'null'}
        branches.append({'type':'object','additionalProperties':False,'required':list(props),'properties':props})
    return {'anyOf':branches}

def compact_schema(stage,payload):
    cls=MindProposal if stage=='call_one' else ResponseProposal
    schema=strict_schema(cls.model_json_schema())
    if stage=='call_one':
        definitions=schema['$defs']
        obs=definitions['Observation']['properties']
        obs['predicate']={'type':'string','enum':[p['predicate'] for p in payload['predicates']]}
        obs['evidence_handles']['items']={'type':'string','enum':[e['handle'] for e in payload['evidence_regions']]}
        definitions['Reading']['properties']['hypothesis_key']={'type':'string','enum':list(
            HYPOTHESIS_KEYS_BY_DIMENSION[HypothesisDimension.SPEAKER_GOAL]+HYPOTHESIS_KEYS_BY_DIMENSION[HypothesisDimension.PRAGMATIC_MEANING])}
        definitions['EventAppraisal']['properties']['values']['items']={'anyOf':[
            {'type':'string','enum':sorted({'not_applicable',*(v for scale in SCALES.values() for v in scale)})}, {'type':'null'}]}
        from .compact import context_handles
        for name in ('Reading','EventAppraisal','DimensionException'):
            for field,handles in (('evidence_handles',[e['handle'] for e in payload['evidence_regions']]),('context_handles',context_handles(payload))):
                prop=definitions[name]['properties'][field]
                if handles:prop['items']={'type':'string','enum':handles}
                else:prop['maxItems']=0
    else:
        from ..collaborative_content_v0.model_protocol import CollaborationModelPayload,response_json_schema,vocabulary_for
        context=payload['decision_context']
        props=schema['$defs']['ResponseMove']['properties']
        for key,collection in (('choice_handle','choices'),('template_handle','templates')):
            handles=[x['handle'] for x in context[collection]]
            props[key]={'anyOf':[{'type':'string','enum':handles},{'type':'null'}]} if handles else {'type':'null'}
        branches=[]
        for i,request in enumerate(context['content_requests']):
            vocabulary=vocabulary_for(request['operation'],request['artifact_type'])
            model_payload=CollaborationModelPayload(operation=request['operation'],artifact_type=request['artifact_type'],
                requested_form=request['requested_form'],target_kind=request['target_kind'],artifact=request['artifact'],
                sources=request['sources'],messages=(),vocabulary=vocabulary,max_inquiries=request['max_inquiries'])
            output=dict(response_json_schema(model_payload))
            op=output['properties'];op['content_items']['minItems']=1
            op['content_items']['items']['properties']['anchors'].update(minItems=1,maxItems=6)
            if context.get('version')=='decision-context-v0.2':
                # The compiler accepts only slug options; say so in the schema.
                op['inquiries']['items']['properties']['answer_options']['items']=dict(type='string',pattern=r'^[a-z0-9][a-z0-9_\-]*$')
            if request['operation'] in {'create','revise'}:
                from ..contracts.workspace import MAX_ITEMS
                op['operations'].update(minItems=1,maxItems=MAX_ITEMS*2)
            else:op['operations']['maxItems']=0
            for key in ('operations','content_items','inquiries'):op[key]['items']['properties']['source_refs']['minItems']=1
            if request['operation'] in {'create','revise'}:
                if request['operation']=='create':
                    selector=op['operations']['items']['properties']['op']
                    selector['enum']=[name for name in selector['enum'] if not name.startswith(('update_','remove_'))]
                op['operations']['items']=patch_operation_schema(op['operations']['items'],schema['$defs'],i)
            branches.append({'type':'object','additionalProperties':False,'required':['request_handle','output'],
                'properties':{'request_handle':{'type':'integer','enum':[i]},'output':output}})
        section=schema['properties']['content_ops']
        section.update(minItems=len(branches),maxItems=len(branches))
        if branches:section['items']=branches[0] if len(branches)==1 else {'anyOf':branches}
        else:section['items']={'type':'object','properties':{},'additionalProperties':False,'required':[]}
        # Both fields refer to generated content, never to the input reference
        # table. With no requests there cannot be a valid claim binding.
        segment=schema['$defs']['Segment']['properties']
        claim=schema['$defs']['ClaimBinding']['properties']
        # Source support is declared once on the generated content item. The
        # legacy field remains present for frozen proposals, but new model
        # outputs cannot independently invent a second source list.
        claim['source_handles']['maxItems']=0
        if not branches:
            segment['content_handles']['maxItems']=0
            segment['claim_bindings']['maxItems']=0
            schema['properties']['inquiries']['maxItems']=0
        else:
            request_indices='|'.join(str(i) for i in range(len(branches)))
            handle_schema={'type':'string','pattern':rf'^content:({request_indices}):(0|[1-9][0-9]*)$'}
            segment['content_handles']['items']=dict(handle_schema)
            claim['content_handle']=dict(handle_schema)
    # Generic collaboration $defs become unreachable after specialization.
    definitions=schema.get('$defs',{});used=set()
    def visit(node):
        if isinstance(node,dict):
            ref=node.get('$ref','')
            if ref.startswith('#/$defs/'):
                key=ref.removeprefix('#/$defs/')
                if key not in used:used.add(key);visit(definitions[key])
            for key,value in node.items():
                if key!='$defs':visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(schema)
    schema['$defs']={k:v for k,v in definitions.items() if k in used}
    return schema
