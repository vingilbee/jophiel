"""Code-owned compact projections and proposal compilation. No provider calls."""
from collections.abc import Mapping
from ..contracts.interpretation import HYPOTHESIS_DIMENSION_BY_KEY
from ..interpretation_v0.model_protocol import SemanticModelOutput, SemanticFrameCandidate
from ..appraisal.context import project_context
from .contracts import CallOneOutput, CallTwoOutput, BehaviorMove
from .compact_contracts import COMPACT_PROTOCOL, TurnContext, MindProposal, DecisionContext, ResponseProposal

_REF_KEYS={'ref','refs','source_refs','evidence_refs','represents_refs','related_refs','event_refs','frame_refs',
           'subject_ids','goal_refs','input_message_ids','source_message_refs','current_turn_ref','known_values'}

def context_handles(payload):
    kinds={'persona','bias','relationship','state','message','memory','value_id','capability_snapshot_id','capability_snapshot_ref'}
    return [r['handle'] for r in payload['reference_scope'] if r['kind'] in kinds]

# Closed, content-free diagnostics. Never put a raw exception, model text or
# unknown reference value into the durable failure envelope.
_RESPONSE_FAILURES={message:message.replace(' ','_') for message in (
    'foreign local reference','duplicate content request','content request coverage mismatch',
    'duplicate response segment','foreign segment content','foreign claim content','foreign claim source',
    'claim source does not support proposed content','foreign or duplicate segment reference',
    'noncontiguous move segments','response move order changed','foreign behavior choice',
    'grounded choice cannot replace targets','duplicate choice parameter','choice parameters changed',
    'foreign behavior template','template parameters require a registered compiler',
    'incompatible shared response segment','unbound response segment','foreign content request',
    'foreign inquiry handle','inquiry metadata disagreement','content inquiry cannot invent a blocking requirement')}

def response_failure_code(error):
    from pydantic import ValidationError
    if isinstance(error,ValidationError):return 'proposal_shape'
    return _RESPONSE_FAILURES.get(str(error),'invalid_reference_contract')

class ReferenceTable:
    """Canonical references stay here/in the capture, never in model packets."""
    def __init__(self, entries=None):
        self.entries={k:dict(v) for k,v in (entries or {}).items()}
        self.reverse={v['canonical_ref']:k for k,v in self.entries.items()}
    def add(self, canonical, kind='reference'):
        if canonical is None:return None
        if canonical not in self.reverse:
            handle=f'r{len(self.entries)}'
            self.entries[handle]={'canonical_ref':canonical,'kind':kind}
            self.reverse[canonical]=handle
        elif kind in {'persona','bias','relationship','state','message','memory','goal','semantic_evidence'}:
            self.entries[self.reverse[canonical]]['kind']=kind
        return self.reverse[canonical]
    def bind(self,handle,canonical,kind,**extra):
        """Protocol v1: an explicit typed handle (``persona:warmth``) for one canonical ref."""
        if handle in self.entries:raise ValueError('duplicate reference handle')
        self.entries[handle]={'canonical_ref':canonical,'kind':kind,**extra}
        self.reverse.setdefault(canonical,handle)
        return handle
    def discover(self,value,key=''):
        if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
        if isinstance(value,Mapping):
            for k,v in value.items():self.discover(v,k)
        elif isinstance(value,(list,tuple)):
            for v in value:self.discover(v,key)
        elif isinstance(value,str) and (key in _REF_KEYS or key.endswith(('_ref','_refs','_id'))):
            self.add(value,key)
    def encode(self,value):
        if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
        self.discover(value)
        def walk(v,key=''):
            if hasattr(v,'model_dump'):v=v.model_dump(mode='json')
            if isinstance(v,Mapping):return {k:walk(x,k) for k,x in v.items()}
            if isinstance(v,(list,tuple)):return [walk(x,key) for x in v]
            if isinstance(v,str) and (key in _REF_KEYS or key.endswith(('_ref','_refs','_id'))):return self.reverse.get(v,v)
            return v
        return walk(value)
    def resolve(self,handle,allowed=None):
        if handle not in self.entries:raise ValueError('foreign local reference')
        canonical=self.entries[handle]['canonical_ref']
        if allowed is not None and canonical not in allowed:raise ValueError('reference outside stage scope')
        return canonical
    def decode(self,value):
        # Decode references, never arbitrary user/generated text. Argument values
        # may contain reference handles; their canonical semantic compiler checks scope.
        def walk(v,key=''):
            if hasattr(v,'model_dump'):v=v.model_dump(mode='json')
            if isinstance(v,Mapping):return {k:walk(x,k) for k,x in v.items()}
            if isinstance(v,(list,tuple)):return [walk(x,key) for x in v]
            if isinstance(v,str) and (key in _REF_KEYS or key.endswith(('_ref','_refs','_id')) or key in {'value','existing_ref','revision_ref'}):
                if v in self.entries:return self.resolve(v)
                if v.startswith('r') and v[1:].isdigit():raise ValueError('foreign local reference')
            return v
        return walk(value)


def state_view(snapshot,table):
    return {'snapshot_handle':table.add(snapshot.state_snapshot_id,'state'),
            'availability':'available',
            'values':[{k:v for k,v in entry.model_dump(mode='json').items()
                       if k in {'state_key','scope_type','scope_ref','status','score','band'}}
                      for entry in snapshot.values if entry.active]}


def shared_context(owner,semantic):
    request=owner.request
    ir=owner.interpretation_request
    table=owner.references
    source=project_context(type('Context',(),dict(action_personality=request.action_personality,
        relationship=request.relationship.to_appraisal_relationship() if request.relationship else None,
        previous_state=request.previous_state,core_memory_ref=request.core_memory_ref))())
    dialogue=[];omissions=[]
    if request.context_truncated:omissions.append(dict(component='dialogue',reason='upstream_capture_window_truncated'))
    # Already exposure-authorized. Keep the captured window intact: dropping a
    # decisive correction merely because it is old is not a latency optimization.
    for m in request.messages:
        dialogue.append(dict(handle=table.add(m.message_id,'message'),speaker=m.role.value,text=m.content,
                             authority='current_user_task' if m.message_id==request.focus_message_id else 'context_only'))
    persona={'source_handle':table.add(request.action_personality.profile_id,'persona'),
             'traits':{t.key:dict(value=t.value,availability='unavailable' if t.value is None else 'available',
                                  source_handle=table.add(t.source_snapshot_ref,'persona')) for t in source.traits},
             'values':[table.encode(v) for v in request.values],
             'synthetic':True if request.action_personality.profile_id.startswith('test-') else None,
             'mapping_version':request.action_personality.mapper_version,
             'voice_preferences':{'availability':'unavailable'}}
    biases=table.encode(request.interpretation_biases)
    for k in ('profile_id','compiler_version','source_core_memory_ref'):biases.pop(k,None)
    biases={'source_handle':table.add(request.interpretation_biases.profile_id,'bias'), 'values':biases}
    relationship={'availability':source.relationship_availability,'values':table.encode(source.relationship) if source.relationship else None,
                  'snapshot_handle':table.add(source.relationship_snapshot_ref,'relationship')}
    memories=[]
    for memory in request.memories:
        if not memory.authorized:raise ValueError('unauthorized memory')
        memories.append(dict(handle=table.add(memory.memory_id,'memory'),text=memory.content,
                             epistemic_status=memory.status,source=memory.provenance,version=memory.version,
                             freshness='unavailable'))
    previous=[]
    if request.previous_goal_snapshot:
        for g in request.previous_goal_snapshot.records:
            if g.status.value!='active':continue
            previous.append(dict(handle=table.add(g.goal_id,'goal'),definition=g.definition_ref.definition_key,
                                 priority=g.priority_band.value,targets=table.encode([t for requirement in g.requirements for t in requirement.target_refs])))
    runtime=table.encode(request.runtime.to_appraisal_runtime().model_dump(mode='json',
        exclude={'diagnostics','provided_parameters','last_tool_event'}))
    if request.runtime.last_tool_event is not None:runtime['last_tool_event']=table.encode(request.runtime.last_tool_event)
    dialogue_memory={'availability':'unavailable'}
    if request.dialogue_context is not None:
        dc=request.dialogue_context.interpretation_context
        dialogue_memory=table.encode(dict(availability='available',source_ref=dc.source_snapshot_ref,
            items=[i.model_dump(mode='json') for i in dc.items],referents=[r.model_dump(mode='json') for r in dc.referents]))
    context=TurnContext(dialogue=dialogue,dialogue_memory=dialogue_memory,focus_handle=table.add(request.focus_message_id,'message'),persona=persona,
        biases=biases,relationship=relationship,state_before=table.encode(state_view(request.previous_state,table)),
        memories=memories,runtime=runtime,previous_goals=previous,
        open_inquiries=[table.encode(i) for i in semantic.open_inquiries],
        perception=[dict(key=c.cue_key,status=c.state.value) for c in ir.perception.cue_activations],omissions=omissions)
    owner.shared_context=context
    return context


def mind_packet(owner,semantic):
    context=shared_context(owner,semantic)
    table=owner.references
    regions=[]
    for region in semantic.evidence_regions:
        regions.append(dict(handle=table.add(region.evidence_ref,'semantic_evidence'),
            message_handle=table.add(region.message_ref,'message'),start=region.start,end=region.end))
    owner.semantic_input=semantic
    definitions=[];argument_specs=[];argument_index={}
    for predicate in semantic.predicates:
        arguments=[]
        for argument in predicate.arguments:
            spec=argument.model_dump(mode='json')
            spec['allowed_values']=[table.reverse.get(v,v) for v in spec['allowed_values']]
            signature=str(spec)
            if signature not in argument_index:
                handle=f'a{len(argument_specs)}';argument_index[signature]=handle
                argument_specs.append(dict(handle=handle,**spec))
            arguments.append(argument_index[signature])
        definitions.append(dict(predicate=predicate.predicate,description=predicate.description,arguments=arguments))
    return dict(protocol=COMPACT_PROTOCOL,turn_context=context.model_dump(mode='json'),
        evidence_regions=regions,predicates=definitions,argument_specs=argument_specs,
        reference_scope=[dict(handle=k,kind=v['kind']) for k,v in table.entries.items()])


def compile_mind(raw,payload,entries):
    mind=MindProposal.model_validate(raw)
    refs=ReferenceTable(entries)
    observations={o.handle:o for o in mind.observations}
    if len(observations)!=len(mind.observations):raise ValueError('duplicate event handles')
    reading_handles={r.handle for r in mind.readings}
    if len(reading_handles)!=len(mind.readings):raise ValueError('duplicate reading handles')
    regions={r['handle']:r for r in payload['evidence_regions']}
    context_scope=set(context_handles(payload))
    def validate_bundles(item):
        if not set(item.evidence_handles)<=set(regions):raise ValueError('evidence outside supplied event evidence')
        if not set(item.context_handles)<=context_scope:raise ValueError('context reference outside supplied context scope')
        for h in (*item.evidence_handles,*item.context_handles):refs.resolve(h)
    predicates={p['predicate'] for p in payload['predicates']}
    messages={m['handle']:m for m in payload['turn_context']['dialogue']}
    frames=[]
    for obs in mind.observations:
        if obs.predicate not in predicates:raise ValueError('foreign predicate')
        if not set(obs.evidence_handles)<=set(regions):raise ValueError('foreign observation evidence')
        if obs.source_span:
            span=obs.source_span
            message=messages.get(span.message_handle)
            if message is None or not 0<=span.start<span.end<=len(message['text']):raise ValueError('invalid source span')
            if not any(r['message_handle']==span.message_handle and r['start']<=span.start<span.end<=r['end']
                       for h,r in regions.items() if h in obs.evidence_handles):raise ValueError('span outside supplied evidence')
        frames.append(SemanticFrameCandidate(predicate=obs.predicate,polarity=obs.polarity,
            arguments=refs.decode(obs.model_dump(mode='json'))['arguments'],
            evidence_refs=[refs.resolve(h) for h in obs.evidence_handles]))
    for reading in mind.readings:
        if reading.hypothesis_key not in HYPOTHESIS_DIMENSION_BY_KEY:raise ValueError('foreign reading key')
        if not set(reading.event_handles)<=set(observations):raise ValueError('foreign reading event')
        # Literal acts/modality/facts are compiled from observations, not subjective judgments.
        if HYPOTHESIS_DIMENSION_BY_KEY[reading.hypothesis_key].value in {'communicative_act','modality'}:
            raise ValueError('subjective reading cannot overwrite literal act or modality')
        validate_bundles(reading)
    assessed=[]
    for event in mind.event_appraisals:
        if not set(event.event_handles)<=set(observations):raise ValueError('foreign appraisal event')
        if len(set(event.event_handles))!=len(event.event_handles):raise ValueError('duplicate appraisal event handle')
        signature=(tuple(sorted(event.event_handles)),event.reading_handle)
        if signature in assessed:raise ValueError('duplicate event appraisal')
        for previous,_ in assessed:
            if set(previous)&set(event.event_handles) and set(previous)!=set(event.event_handles):
                raise ValueError('overlapping event appraisal bundles')
        assessed.append(signature)
        if event.reading_handle is not None and event.reading_handle not in reading_handles:raise ValueError('foreign appraisal reading')
        if event.reading_handle:
            reading=next(r for r in mind.readings if r.handle==event.reading_handle)
            if not set(event.event_handles)<=set(reading.event_handles):raise ValueError('appraisal reading scope mismatch')
        validate_bundles(event)
        for exception in event.exceptions:
            validate_bundles(exception)
    goals={g['handle'] for g in payload['turn_context']['previous_goals']}
    for impact in mind.existing_goal_impacts:
        if impact.goal_handle not in goals or not set(impact.event_handles)<=set(observations):raise ValueError('foreign goal impact')
    for item in (*mind.unknowns,*mind.conflicts):
        if item.subject_handle not in observations and item.subject_handle not in reading_handles:refs.resolve(item.subject_handle)
    return CallOneOutput(interpretation=SemanticModelOutput(frames=frames),appraisal=[])


def _slug(text):
    import re
    return re.sub(r'[^a-z0-9_\-]+','_',text.strip().casefold()).strip('_-')


def _dietary_value(text):
    """'Dairy-free', 'without dairy', 'no dairy' -> 'dairy_free' when that is a closed value."""
    import re
    from ..contracts.workspace import DietaryValue
    slug=_slug(text).replace('-','_')
    match=re.fullmatch(r'(?:no|without|free_of|avoid|avoiding)_(.+)',slug)
    candidate=f'{match.group(1)}_free' if match else slug
    candidate={'nuts_free':'nut_free','eggs_free':'egg_free','peanuts_free':'peanut_free'}.get(candidate,candidate)
    return candidate if candidate in {v.value for v in DietaryValue} else text


def _element_labels(output):
    """Surface labels of the elements this same output writes, keyed by ID."""
    labels={}
    for op in output.get('operations') or ():
        dish=op.get('dish') or {}
        if dish.get('dish_id') and dish.get('name'):labels[dish['dish_id']]=dish['name']
        if op.get('element_id') and (op.get('heading') or op.get('text')):
            labels[op['element_id']]=op.get('heading') or op.get('text')
    return labels


def _cite_identical_requirements(raw,packet):
    """A user requirement can reach Call 2 under several refs with identical text
    (a request-frame constraint and a standalone frame). Citing one cites the
    same requirement, so add its identical siblings to that citation."""
    for op in raw.get('content_ops') or ():
        handle=op.get('request_handle')
        if not isinstance(handle,int) or not 0<=handle<len(packet['content_requests']):continue
        groups={}
        for source in packet['content_requests'][handle]['sources']:
            if source.get('kind')=='user_requirement':
                groups.setdefault((source.get('speaker'),' '.join(str(source.get('text','')).casefold().split())),[]).append(source['ref'])
        siblings={ref:refs for refs in groups.values() if len(refs)>1 for ref in refs}
        for operation in (op.get('output') or {}).get('operations') or ():
            refs=list(operation.get('source_refs') or ())
            for ref in list(refs):
                refs.extend(r for r in siblings.get(ref,()) if r not in refs)
            operation['source_refs']=refs


def normalize_response(raw,packet=None):
    """decision-context-v0.2: lossless representation repairs, never new meaning.

    Anchors are the model's own wording constraints and must be substrings of
    their statement. An anchor naming an element this output writes becomes
    that element's label when the statement contains it; other foreign anchors
    are dropped only while at least one real anchor remains. Inquiry answer
    options become the compiler's slug form, and question_kind is reconciled
    with the options actually supplied.
    """
    import json
    # Plain mutable copy: frozen models dump to immutable mappings.
    raw=json.loads(raw.model_dump_json() if hasattr(raw,'model_dump_json') else json.dumps(raw))
    for op in raw.get('content_ops') or ():
        output=op.get('output') or {}
        labels=_element_labels(output)
        for item in output.get('content_items') or ():
            statement=str(item.get('statement','')).casefold()
            anchors=[a for a in item.get('anchors') or () if isinstance(a,str) and a.strip()]
            mapped=[labels[a] if a.casefold() not in statement and a in labels
                    and labels[a].casefold() in statement else a for a in anchors]
            kept=list(dict.fromkeys(a for a in mapped if a.casefold() in statement))
            if kept:item['anchors']=kept
        for operation in output.get('operations') or ():
            constraint=operation.get('constraint') or {}
            if constraint.get('kind')=='dietary' and isinstance(constraint.get('value'),str):
                constraint['value']=_dietary_value(constraint['value'])
        for inquiry in output.get('inquiries') or ():
            options=list(dict.fromkeys(filter(None,(_slug(o) for o in inquiry.get('answer_options') or () if isinstance(o,str)))))
            inquiry['answer_options']=options
            kind=inquiry.get('question_kind')
            if kind=='open_set' and len(options)>=2:inquiry['question_kind']='choice'
            elif kind=='choice' and len(options)<2:inquiry['question_kind']='open_set';inquiry['answer_options']=[]
    if packet is not None:_cite_identical_requirements(raw,packet)
    return raw


def compile_response(raw,payload,entries):
    packet=payload['decision_context']
    if packet.get('version','decision-context-v0.1')!='decision-context-v0.1':raw=normalize_response(raw,packet)
    response=ResponseProposal.model_validate(raw)
    table=ReferenceTable(entries)
    request_handles=[op.request_handle for op in response.content_ops]
    if len(request_handles)!=len(set(request_handles)):raise ValueError('duplicate content request')
    if set(request_handles)!=set(range(len(packet['content_requests']))):raise ValueError('content request coverage mismatch')
    choices={c['handle']:c for c in packet['choices']}
    templates={t['handle']:t for t in packet['templates']}
    segments={s.handle:s for s in response.segments}
    if len(segments)!=len(response.segments):raise ValueError('duplicate response segment')
    order={s.handle:i for i,s in enumerate(response.segments)}
    content={f'content:{op.request_handle}:{i}':item for op in response.content_ops for i,item in enumerate(op.output.content_items)}
    for segment in response.segments:
        if not set(segment.content_handles)<=set(content):raise ValueError('foreign segment content')
        for claim in segment.claim_bindings:
            if claim.content_handle not in content:raise ValueError('foreign claim content')
            item=content[claim.content_handle]
            request_handle=int(claim.content_handle.split(':')[1])
            request=packet['content_requests'][request_handle]
            sources={s['ref'] for s in request['sources']}
            # New proposals inherit item.source_refs rather than selecting a
            # duplicate source list here. The content compiler validates those
            # refs and the claim kind before any realization is accepted.
            # Preserve strict checks on nonempty legacy bindings for replay.
            if not set(claim.source_handles)<=sources:raise ValueError('foreign claim source')
            if not set(claim.source_handles)<=set(item.source_refs):raise ValueError('claim source does not support proposed content')
    moves=[];used=[]
    for move in response.moves:
        if len(set(move.segment_handles))!=len(move.segment_handles) or any(h not in segments for h in move.segment_handles):
            raise ValueError('foreign or duplicate segment reference')
        positions=[order[h] for h in move.segment_handles]
        if positions!=list(range(min(positions),max(positions)+1)):raise ValueError('noncontiguous move segments')
        if used and min(positions)<used[-1][0]:raise ValueError('response move order changed')
        used.append((min(positions),max(positions)))
        excerpt=' '.join(segments[h].text for h in move.segment_handles)
        if move.choice_handle is not None:
            if move.choice_handle not in choices:raise ValueError('foreign behavior choice')
            choice=table.decode(choices[move.choice_handle])
            targets=[dict(role=t['role'],target_type=t['target_type'],existing_ref=t['ref'],revision_ref=t.get('revision_ref'),
                content_request_handle=None,object_kind=None,item_handle=None) for t in choice['targets']]
            # A model may echo supplied targets/parameters. Compare both sides
            # in canonical space and retain only the code-owned values.
            supplied_targets=table.decode([t.model_dump(mode='json') for t in move.target_bindings])
            target_key=lambda t:tuple(sorted(t.items()))
            if supplied_targets and (len({target_key(t) for t in supplied_targets})!=len(supplied_targets)
                or {target_key(t) for t in supplied_targets}!={target_key(t) for t in targets}):
                raise ValueError('grounded choice cannot replace targets')
            if len({a.name for a in move.parameters})!=len(move.parameters):raise ValueError('duplicate choice parameter')
            parameters=table.decode({a.name:a.value for a in move.parameters})
            if any(key not in choice['parameters'] or value!=choice['parameters'][key] for key,value in parameters.items()):
                raise ValueError('choice parameters changed')
            goals=[table.resolve(h) for h in move.goal_handles] if move.goal_handles else choice['goal_refs']
            action=choice['action_key']
        else:
            if move.template_handle not in templates:raise ValueError('foreign behavior template')
            template=templates[move.template_handle]
            if move.parameters:raise ValueError('template parameters require a registered compiler')
            targets=table.decode([t.model_dump(mode='json') for t in move.target_bindings])
            goals=[table.resolve(h) for h in move.goal_handles]
            action=template['action_key']
        moves.append(BehaviorMove(action_key=action,goal_refs=goals,target_bindings=targets,excerpt=excerpt))
    for i,(left,right) in enumerate(zip(used,used[1:])):
        if left[1]>=right[0] and not (left==right and {moves[i].action_key,moves[i+1].action_key}=={'manage_dialogue.greet','give_feedback.acknowledge'}):
            raise ValueError('incompatible shared response segment')
    if set(segments)!=set(h for m in response.moves for h in m.segment_handles):raise ValueError('unbound response segment')
    contents=[]
    for op in response.content_ops:
        if op.request_handle>=len(packet['content_requests']):raise ValueError('foreign content request')
        contents.append(dict(request_handle=op.request_handle,output=table.decode(op.output.model_dump(mode='json'))))
    for inquiry in response.inquiries:
        op=next((o for o in response.content_ops if o.request_handle==inquiry.request_handle),None)
        if op is None or inquiry.item_handle>=len(op.output.inquiries):raise ValueError('foreign inquiry handle')
        actual=op.output.inquiries[inquiry.item_handle]
        if (actual.target_id,actual.asked_about)!=(inquiry.target_handle,inquiry.asked_about):raise ValueError('inquiry metadata disagreement')
        # Content inquiry candidates carry purpose, not timing. The existing
        # policy compiles optional inquiry timing; request-field blockers remain
        # requirement/action records and cannot be invented here.
        if inquiry.timing!='optional':raise ValueError('content inquiry cannot invent a blocking requirement')
    return CallTwoOutput(content_proposal=contents,behavior_proposal=moves,
        response_candidate=dict(text=' '.join(s.text for s in response.segments),content_alignments=[]))


def decision_packet(owner,pipeline,turn,appraisal,update,interpretation,goals,workspace,requirement):
    import hashlib,json
    from .orchestrator import PayloadCollector,CollectingEngine,action_request_for
    from ..action_selection_v0.reference_index import ActionReferenceIndex
    from ..action_selection_v0.construction import PlanView,BINDING_MANIFEST,recipe_for
    from ..action_selection_v0.transition import empty_state
    from ..action_selection_v0.features import FeatureCalculator
    from ..action_selection_v0.planning_conditions import derive_planning_conditions,addresses_any
    from ..contracts.actions import ActionTargetType as T
    from ..realization_v0.action_manifest import entry_for
    request=owner.request;table=owner.references
    collector=PayloadCollector();pipeline.collaboration_engine=CollectingEngine(collector)
    pipeline._collaborate(request,interpretation,goals,workspace,requirement)
    action_request=action_request_for(turn,appraisal,update.snapshot)
    engine=pipeline.cognitive_turn_runner.action_engine.engine
    index=ActionReferenceIndex(action_request)
    canonical=json.dumps(action_request.model_dump(mode='json'),sort_keys=True,separators=(',',':'))
    context_ref='action-context-'+hashlib.sha256(canonical.encode()).hexdigest()[:20]
    plan_ref='action-plan-'+hashlib.sha256(context_ref.encode()).hexdigest()[:20]
    state=empty_state(plan_ref)
    candidates,_=engine.constructor_factory(engine.registry).construct(index,PlanView((),()),context_ref)
    gate=engine.gate_factory(engine.registry)
    eligible=[c for c in candidates if gate.evaluate(c,index).eligible_in_shadow]
    conditions=derive_planning_conditions(action_request)
    open_conditions=tuple(c for c in conditions if c.blocker_refs)
    addressing={c.candidate_id for c in eligible if addresses_any(c,open_conditions,engine.registry,index)} if open_conditions else set()
    calculator=FeatureCalculator(engine.registry,index)
    choices=[]
    for candidate in eligible:
        if addressing and candidate.candidate_id not in addressing:continue
        analysis=calculator.analyze(candidate,state)
        key=candidate.definition_ref.action_key
        manifest=entry_for(key,candidate.definition_ref.definition_version)
        choices.append(table.encode(dict(handle=f'b{len(choices)}',action_key=key,
            goal_refs=sorted({link.goal_ref for link in analysis.goal_links}),
            targets=[t.model_dump(mode='json') for t in candidate.target_refs],parameters=dict(candidate.parameters),
            wording_constraints=list(gate.evaluate(candidate,index).realization_constraints),
            wording=manifest.model_dump(mode='json',include={'required_alignment_kind','required_bindings','mandatory_constraint_codes'}) if manifest else None)))
    templates=[]
    if collector.requests:
        future_types={T.ARTIFACT_REVISION,T.PROPOSITION,T.INQUIRY,T.OPTION,T.EVIDENCE_RECORD}
        for recipe in BINDING_MANIFEST:
            if not set(recipe.target_types)&future_types:continue
            definition=engine.registry.by_key(recipe.action_key)
            if definition is None:continue
            templates.append(dict(handle=f't{len(templates)}',action_key=recipe.action_key,
                target_schema=definition.target_schema.model_dump(mode='json'),
                target_roles={k:[v.value if hasattr(v,'value') else v for v in values] for k,values in definition.effect.target_roles.items()},
                local_objects='artifact, content or inquiry through content request/item handles'))
    content_requests=[]
    for i,entry in enumerate(collector.requests):
        p=entry['payload']
        row=table.encode(dict(handle=i,goal_ref=entry['goal_ref'],operation=p['operation'],
            artifact_type=p['artifact_type'],requested_form=p.get('requested_form'),target_kind=p['target_kind'],
            artifact=p.get('artifact'),sources=p['sources'],max_inquiries=p.get('max_inquiries',1),
            delivery_template=next((t['handle'] for t in templates if t['action_key']=='collaborate.deliver_artifact'),None),
            delivery_target=dict(role='primary',target_type='artifact_revision',existing_ref=None,revision_ref=None,
                content_request_handle=i,object_kind='artifact',item_handle=None)))
        # Every compiled artifact must be delivered (select_choice refuses a
        # STOP that omits it), so hand the model the exact move to copy.
        if row['delivery_template'] is not None:
            row['delivery_move']=dict(required=True,template_handle=row['delivery_template'],
                goal_handles=[row['goal_ref']],target_bindings=[row['delivery_target']])
        content_requests.append(row)
    goal_rows=[]
    for g in turn.active_goals:
        goal_rows.append(table.encode(dict(handle=table.add(g.goal_id,'goal'),definition=g.definition_ref.definition_key,
            priority=g.priority_band,targets=[t.model_dump(mode='json') for req in g.requirements for t in req.target_refs])))
    readiness_by_goal={}
    snapshot=requirement.handoff.snapshot
    for resolution in snapshot.resolutions:
        status=resolution.status.value;reason=resolution.reason_code.value;kind=resolution.kind.value
        category='ready' if status=='satisfied' else (
            'needs_authorization' if 'authorization' in kind or 'consent' in kind else
            'unavailable_capability' if 'capability' in kind else
            'unsafe' if 'safety' in kind else
            'insufficient_grounding' if 'fact' in kind or 'trusted' in kind else
            'failed_dependency' if 'failure' in reason else 'needs_clarification')
        row=readiness_by_goal.setdefault(resolution.goal_ref,dict(goal_ref=resolution.goal_ref,
            readiness_kind='ready',missing_fields=[],known_values=[],blocked_reasons=[],requirements=[],
            capability_status='unavailable',permission_status='unavailable',grounding_status='unavailable',
            permitted_next_behaviors=sorted({c['action_key'] for c in choices if table.reverse.get(resolution.goal_ref) in c['goal_refs']})))
        row['requirements'].append(dict(kind=kind,status=status,blocking=resolution.blocking,reason=reason))
        row['known_values'].extend(resolution.value_refs)
        if status!='satisfied':row['missing_fields'].append(kind)
        if resolution.blocking and status!='satisfied':
            priority={'ready':0,'needs_clarification':1,'failed_dependency':2,'insufficient_grounding':3,
                'unavailable_capability':4,'needs_authorization':5,'unsafe':6}
            if priority[category]>priority[row['readiness_kind']]:row['readiness_kind']=category
            row['blocked_reasons'].append(reason)
        for field,terms in (('capability_status',('capability',)),('permission_status',('authorization','consent')),('grounding_status',('fact','trusted'))):
            if any(term in kind for term in terms):row[field]=status
    readiness=[table.encode(row) for row in readiness_by_goal.values()]
    shared=owner.shared_context.model_dump(mode='json',exclude={'state_before','previous_goals','open_inquiries','perception'})
    owner.prepared_content_requests=collector.requests
    packet=DecisionContext(shared_context=shared,
        understanding={'observations':[o.model_dump(mode='json') for o in owner.mind.observations],
                       'readings':[r.model_dump(mode='json') for r in owner.mind.readings],
                       'unknowns':[u.model_dump(mode='json') for u in owner.mind.unknowns],
                       'conflicts':[c.model_dump(mode='json') for c in owner.mind.conflicts],
                       'event_appraisals':[a.model_dump(mode='json') for a in owner.mind.event_appraisals]},
        state_after=table.encode(state_view(update.snapshot,table)),goals=goal_rows,readiness=readiness,
        content_requests=content_requests,answer_permissions=[table.encode(i) for i in requirement.realization_content],
        choices=choices,templates=templates)
    return dict(protocol=COMPACT_PROTOCOL,decision_context=packet.model_dump(mode='json')),collector.requests
