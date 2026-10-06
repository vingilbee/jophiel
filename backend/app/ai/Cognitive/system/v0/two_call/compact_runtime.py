"""Accepted compact readings and event appraisal consumed by the state spine."""
from ..interpretation_v0.engine import InterpretationEngine
from ..interpretation_v0.rules import HypothesisRuleEngine,HypothesisCandidate
from ..interpretation_v0.model_grounding import ModelSemanticGrounder,canonical_candidate_arguments
from ..interpretation_v0.semantic_grounding import GroundingResult
from ..contracts.interpretation import HypothesisDimension as D,HypothesisDisposition as P,HypothesisSupport as S, HYPOTHESIS_DIMENSION_BY_KEY,UnknownInput,Contradiction
from ..perception_v0.evidence import stable_id
from ..appraisal.model_contracts import DIMENSIONS,DimensionProposal,FrameProposal,AppraisalModelOutput,AppraisalModelPayload,AppraisalModelTrace,ModelTrait
from ..appraisal.models import AppraisalDimension,AppraisalResult
from ..appraisal.engine import AppraisalEngine
from ..appraisal.context import with_dialogue,project_context
from .provider import digest


def frame_map(owner,frames):
    mapping={}
    for obs,candidate in zip(owner.mind.observations,owner.one.interpretation.frames):
        matches=[f for f in frames if (f.predicate,f.polarity,dict(f.arguments))==
                 (candidate.predicate,candidate.polarity,canonical_candidate_arguments(candidate))]
        if len(matches)!=1:raise ValueError('compact observation did not bind uniquely')
        mapping[obs.handle]=matches[0]
    return mapping

class ContextInterpretationEngine(InterpretationEngine):
    def __init__(self,owner,**kwargs):
        self.owner=owner
        super().__init__(**kwargs)
        owner.interpretation_engine=self
    def interpret(self,request):
        self.owner.interpretation_request=request
        return super().interpret(request)

class ContextGrounder(ModelSemanticGrounder):
    def __init__(self,owner,model):
        self.owner=owner
        super().__init__(model)
    def ground(self,request,evidence):
        result=super().ground(request,evidence)
        if self.owner.mind is None:return result
        mapping=frame_map(self.owner,result.frames)
        unknowns=list(result.unknowns);conflicts=list(result.contradictions)
        def sources_for(handle):
            if handle in mapping:return mapping[handle].evidence_refs
            reading=next((r for r in self.owner.mind.readings if r.handle==handle),None)
            if reading:return tuple(dict.fromkeys(ref for h in reading.event_handles for ref in mapping[h].evidence_refs))
            return tuple(result.frames[0].evidence_refs)
        for item in self.owner.mind.unknowns:
            sources=sources_for(item.subject_handle)
            unknowns.append(UnknownInput(unknown_id=stable_id('compact-unknown',request.cognitive_turn_id,item.subject_handle,item.information),
                key='compact_missing_information',owner='unknown',evidence_refs=sources))
        for item in self.owner.mind.conflicts:
            sources=sources_for(item.subject_handle)
            conflicts.append(Contradiction(contradiction_id=stable_id('compact-conflict',request.cognitive_turn_id,item.subject_handle,item.information),
                status='active',description=item.information,evidence_refs=sources))
        return GroundingResult(result.frames,tuple(unknowns),tuple(conflicts),result.out_of_scope_records)

class ModelBiasOwner:
    version='interpretation-bias-owner-model-v0.1'
    def apply(self,candidates,profile):return candidates

class ContextReadingRules(HypothesisRuleEngine):
    version='interpretation-context-conditioned-readings-v0.1'
    def __init__(self,owner):self.owner=owner
    def evaluate(self,request,evidence,frames,unknowns,contradiction_ids):
        # Canonical act/modality and explicitly expressed request goals remain
        # structural. Legacy pragmatic/social judgments are removed entirely.
        literal=super().evaluate(request,evidence,frames,unknowns,contradiction_ids)
        candidates=[c for c in literal if c.dimension!=D.PRAGMATIC_MEANING and
                    c.hypothesis_key not in {'interpretation.goal.maintain_social_connection','interpretation.goal.seek_acknowledgement'}]
        if self.owner.mind is None:return literal
        mapping=frame_map(self.owner,frames)
        registry={e.evidence_id for e in evidence}
        by_source={e.source_ref:e.evidence_id for e in evidence}
        for reading in self.owner.mind.readings:
            dimension=HYPOTHESIS_DIMENSION_BY_KEY[reading.hypothesis_key]
            source=[]
            for handle in reading.event_handles:source.extend(mapping[handle].evidence_refs)
            for handle in reading.evidence_handles:
                ref=self.owner.references.resolve(handle)
                if ref in registry:source.append(ref)
                elif ref in by_source:source.append(by_source[ref])
                else:raise ValueError('reading evidence is not accepted interpretation evidence')
            disposition=P.REJECTED if reading.support==S.UNSUPPORTED else (P.RETAINED_ALTERNATIVE if reading.uncertainty=='high' else P.LEADING)
            candidates.append(HypothesisCandidate(hypothesis_key=reading.hypothesis_key,dimension=dimension,
                base_support=reading.support,support=reading.support,disposition=disposition,
                semantic_frame_refs=tuple(mapping[h].frame_id for h in reading.event_handles),
                supporting_evidence_refs=tuple(dict.fromkeys(source)),rule_ids=(f'{self.version}:{reading.handle}',),
                ambiguity_gate=0.0))
        return tuple(candidates)


def appraise_events(owner,request,turn,*,binding='compact-event-binding-v0.1',context_version='turn-context-v0.1',
                    prompt_version='compact-mind-prompt-v0.1',per_event_goal_impacts=False):
    """Allocate each event once through actual formation links, never per guessed goal.

    Goal-specific congruence is unknown without an explicit existing-goal impact.
    Event relevance applies only to a goal actually formed from that event. Other
    adopted goal/scenario pairs stay unknown. The allocation is recorded explicitly.
    """
    from types import SimpleNamespace
    if owner.mind is None:
        from .orchestrator import TwoCallError
        raise TwoCallError(stage='call_one',subject='empty_focus')
    mapping=frame_map(owner,request.interpretation.semantic_frames)
    contexts=with_dialogue(project_context(SimpleNamespace(action_personality=owner.request.action_personality,
        relationship=request.relationship,previous_state=owner.request.previous_state,core_memory_ref=owner.request.core_memory_ref)),
        owner.request.messages,owner.request.focus_message_id)
    # Use exactly the authorized compact dialogue window for reference checks,
    # including older corrections retained by TurnContext.
    contexts=type(contexts)(**(contexts.model_dump(mode='python')|dict(dialogue_context=tuple(
        dict(ref=m.message_id,speaker=m.role.value,text=m.content,current_turn=m.message_id==owner.request.focus_message_id,
            authority='current_user_task' if m.message_id==owner.request.focus_message_id and m.role.value=='user' else 'context_only')
        for m in owner.request.messages),omitted_dialogue_refs=())))
    biases=owner.request.interpretation_biases
    traits=[type(t)(**(t.model_dump(mode='python')|dict(source_snapshot_ref=t.source_snapshot_ref or owner.request.action_personality.profile_id)))
        for t in contexts.traits]
    for key in ('benign_prior','trust_prior','threat_sensitivity','confirmation_tendency',
                'self_attribution_tendency','ambiguity_tolerance','social_affiliation_prior'):
        traits.append(ModelTrait(key=key,value=getattr(biases,key),source_kind='mapped',
            source_snapshot_ref=biases.profile_id,scale_definition_ref=biases.compiler_version+':unit-interval',
            behavioral_definition='Interpretation context tendency; not a factual source.'))
    contexts=type(contexts)(**(contexts.model_dump(mode='python')|dict(traits=tuple(traits))))
    baseline=AppraisalEngine()
    retained=[s for s in request.interpretation.scenarios if s.disposition in {'leading','retained_alternative'}]
    # Leading first makes alternative scenario count irrelevant to event affect.
    retained.sort(key=lambda s:(s.disposition!='leading',s.scenario_id))
    pairs=[(s,g) for s in retained for g in request.active_goals if g.status=='active']
    allocated={};allocation=[];unmatched=[]
    # v1: one impact per (goal, event); v0.1 keeps one impact per goal.
    impact_lists={}
    for item in owner.mind.existing_goal_impacts:
        impact_lists.setdefault(owner.references.resolve(item.goal_handle),[]).append(item)
    if any(len(items)>1 for items in impact_lists.values()) and not per_event_goal_impacts:
        raise ValueError('duplicate existing goal impacts')
    if any(len({tuple(sorted(i.event_handles)) for i in items})!=len(items) for items in impact_lists.values()):
        raise ValueError('duplicate existing goal impacts')
    def impact_for(goal_id,handles):
        return next((i for i in impact_lists.get(goal_id,()) if set(i.event_handles)==set(handles)),None)
    from .compact_contracts import EventAppraisal,DimensionException
    groups={}
    for ai,event in enumerate(owner.mind.event_appraisals):
        groups.setdefault(tuple(sorted(event.event_handles)),[]).append((ai,event))
    # Alternative readings of one event remain in the raw capture. Agreed
    # dimensions can influence state once; conflicting dimensions are unknown.
    events=[]
    for group in groups.values():
        if len(group)==1:events.append(group[0]);continue
        values=[];exceptions=[]
        for di in range(8):
            assessments=[(e.values[di],next((x.uncertainty for x in e.exceptions if x.index==di),e.uncertainty)) for _,e in group]
            value=assessments[0][0] if len({v for v,_ in assessments})==1 else None
            uncertainty=max((u for _,u in assessments),key={'low':0,'moderate':1,'high':2}.get)
            if uncertainty=='high' and value!='not_applicable':value=None
            values.append(value)
            if uncertainty!='high':exceptions.append(DimensionException(index=di,uncertainty=uncertainty,
                evidence_handles=tuple(dict.fromkeys(h for _,e in group for h in e.evidence_handles)),
                context_handles=tuple(dict.fromkeys(h for _,e in group for h in e.context_handles))))
        events.append((group[0][0],EventAppraisal(event_handles=group[0][1].event_handles,reading_handle=None,
            values=values,uncertainty='high',exceptions=exceptions,
            evidence_handles=tuple(dict.fromkeys(h for _,e in group for h in e.evidence_handles)),
            context_handles=tuple(dict.fromkeys(h for _,e in group for h in e.context_handles)))))
    for ai,event in events:
        event_refs={mapping[h].frame_id for h in event.event_handles}
        matching=[(s,g) for s,g in pairs if event_refs<=set(s.semantic_frame_refs) and
                  event_refs<=set(owner.goal_engine.formation_bindings.get(g.goal_id,())) and (s.scenario_id,g.goal_id) not in allocated]
        if not matching:
            matching=[(s,g) for s,g in pairs if any(event_refs=={mapping[h].frame_id for h in i.event_handles}
                      for i in impact_lists.get(g.goal_id,())) and event_refs<=set(s.semantic_frame_refs)
                      and (s.scenario_id,g.goal_id) not in allocated]
        if not matching:
            unmatched.append(ai);continue
        s,g=matching[0];allocated[(s.scenario_id,g.goal_id)]=event
        allocation.append(dict(event_index=ai,scenario_ref=s.scenario_id,goal_ref=g.goal_id,event_refs=sorted(event_refs)))
    frames=[];proposals=[]
    for scenario,goal in pairs:
        event=allocated.get((scenario.scenario_id,goal.goal_id));dims=[];proposal_dims=[]
        for i,(key,(kind,_)) in enumerate(DIMENSIONS.items()):
            value=event.values[i] if event else None
            # Per-goal congruence cannot be supplied before a goal exists.
            if i==1:value=None
            impact=impact_for(goal.goal_id,event.event_handles) if event is not None else None
            if impact and {mapping[h].frame_id for h in impact.event_handles}<=set(scenario.semantic_frame_refs) and i in {0,1}:
                value=impact.relevance if i==0 else impact.congruence
            uncertainty=event.uncertainty if event else 'high'
            exception=next((e for e in event.exceptions if e.index==i),None) if event else None
            if exception:uncertainty=exception.uncertainty
            if impact and i in {0,1}:uncertainty=impact.uncertainty
            if uncertainty=='high' and value not in {None,'not_applicable'}:raise ValueError('unsupported goal impact certainty')
            status='unknown' if value is None else ('not_applicable' if value=='not_applicable' else 'known')
            refs=[]
            if event:
                refs.extend(mapping[h].frame_id for h in event.event_handles)
                for h in event.event_handles:refs.extend(mapping[h].evidence_refs)
                refs.extend(owner.references.resolve(h) for h in ((exception.evidence_handles+exception.context_handles) if exception else (event.evidence_handles+event.context_handles)))
            if status=='known':refs.append(goal.goal_id)
            refs=tuple(dict.fromkeys(refs))
            # All supplied context refs are closed; canonical validator also
            # ensures applicability to this retained scenario and actual goal.
            proposal=DimensionProposal(dimension_key=key,status=status,value=None if status!='known' else value,
                assessment_basis='insufficient' if status=='unknown' else ('not_applicable' if status=='not_applicable' else 'inferred'),
                uncertainty=uncertainty,evidence_refs=refs,evidence_summary='Compact event binding.',
                missing_input_keys=('compact_unassessed_or_unbound',) if status=='unknown' else (),conflict_refs=())
            proposal_dims.append(proposal)
            dims.append(AppraisalDimension(dimension_key=key,status=status,value_type=kind,value=proposal.value,evidence_refs=refs,
                derivation_strength={'low':'strong','moderate':'moderate','high':'weak'}[uncertainty],
                rule_id=f'{binding}:{key}',required_input_refs=(scenario.scenario_id,goal.goal_id),
                missing_input_keys=proposal.missing_input_keys,conflict_refs=()))
        frames.append(baseline._frame(request,scenario,goal,tuple(dims)))
        proposals.append(FrameProposal(scenario_ref=scenario.scenario_id,goal_ref=goal.goal_id,dimensions=proposal_dims))
    payload=AppraisalModelPayload(focus_message_ref=request.interpretation.focus_message_id,context=contexts,
        pairs=(),evidence=(),runtime={},values=(),supplemental_inputs=())
    provenance=dict(owner.captures[0].provenance) | dict(protocol=owner.protocol,binding=binding,
        source_call=digest(owner.captures[0].proposal),event_allocations=allocation,unmatched_events=unmatched,
        bias_application_owner='model',affect_aggregation='one_allocation_per_event')
    trace=AppraisalModelTrace(prompt_version=prompt_version,prompt_hash=owner.captures[0].prompt_hash,
        payload_digest=digest(payload),provenance=provenance,payload=payload,proposal=AppraisalModelOutput(frames=proposals))
    result=AppraisalResult(appraisal_trace_id='appraisal-compact-'+digest(trace)[:20],cognitive_turn_id=request.cognitive_turn_id,
        interpretation_trace_id=request.interpretation.interpretation_trace_id,frames=frames,aggregate=baseline._aggregate(request,tuple(frames)),
        model_trace=trace,versions=dict(schema='appraisal-schema-v1',dimension_registry='appraisal-dimensions-v1',
            scenario_builder=request.interpretation.versions.scenario_builder,rules=binding,
            protocol=owner.protocol,context=context_version,prompt=prompt_version))
    baseline._validate_result(request,result)
    return result


# ---------------------------------------------------------------------------
# Protocol v1 adapters (typed unknowns/conflicts; derived reading support)
# ---------------------------------------------------------------------------

class UnderstandingGrounder(ContextGrounder):
    """Adds Call 1's registered unknowns and typed conflicts with canonical evidence."""
    def _payload(self,request,evidence):
        payload=super()._payload(request,evidence)
        from ..interpretation_v0.ontology import V1_PREDICATES_BY_REGISTRY
        offered=V1_PREDICATES_BY_REGISTRY.get(getattr(self.owner,'understanding_registry',None),frozenset())
        if not offered:
            return payload  # v1.0 captures replay with exactly their original predicates
        from ..interpretation_v0.model_grounding import predicate_specs
        return type(payload)(**(payload.model_dump(mode='python')|dict(
            predicates=predicate_specs(inquiries=bool(request.open_inquiries),v1_predicates=offered))))
    def ground(self,request,evidence):
        result=super().ground(request,evidence)
        proposal=getattr(self.owner,'understanding',None)
        if proposal is None:return result
        from .v1 import focus_tokens,typed_evidence
        mapping=frame_map(self.owner,result.frames)
        bind=dict(entries=self.owner.references.entries,tokens=focus_tokens(request.focus_message.content),
                  evidence=evidence,turn=request.cognitive_turn_id,messages={m.message_id:m.content for m in request.messages})
        unknowns=list(result.unknowns);conflicts=list(result.contradictions)
        for index,item in enumerate(proposal.unknowns):
            subject=item.subject
            refs=(*typed_evidence(item.evidence_refs,**bind),*mapping[f'e{subject.event_index}'].evidence_refs)
            unknowns.append(UnknownInput(unknown_id=stable_id('understanding-unknown',request.cognitive_turn_id,index,item.unknown_key,
                    subject.event_index,subject.reading_index,subject.argument_key),
                key=item.unknown_key,owner=item.owner,evidence_refs=tuple(dict.fromkeys(refs))))
        for index,item in enumerate(proposal.conflicts):
            refs=typed_evidence((item.left_ref,item.right_ref),**bind)
            # The description is the registered conflict key, never model prose.
            conflicts.append(Contradiction(contradiction_id=stable_id('understanding-conflict',request.cognitive_turn_id,index,
                    item.conflict_key,item.left_ref,item.right_ref),
                status='active',description=item.conflict_key,evidence_refs=refs))
        return GroundingResult(result.frames,tuple(unknowns),tuple(conflicts),result.out_of_scope_records)


class UnderstandingReadingRules(ContextReadingRules):
    """Support is derived from the reading's uncertainty (reading-support-from-uncertainty-v1).

    ``reading-conflict-policy-v1``: readings of different events may conflict
    ("don't make a schedule" vs "talk it through"), but a leading scenario
    cannot hold conflicting keys.  A model-only reading that conflicts with a
    leading structural reading, or with a better-supported model reading, is
    retained as an alternative -- never dropped.  Rule-only conflicts are
    untouched.
    """
    version='interpretation-context-conditioned-readings-v1'
    conflict_policy='reading-conflict-policy-v1'

    def evaluate(self,request,evidence,frames,unknowns,contradiction_ids):
        from dataclasses import replace
        from ..contracts.interpretation import HYPOTHESIS_CONFLICTS
        candidates=list(super().evaluate(request,evidence,frames,unknowns,contradiction_ids))
        if self.owner.mind is None:return tuple(candidates)
        prefix=f'{self.version}:'
        rank={S.UNSUPPORTED:0,S.WEAK:1,S.MODERATE:2,S.STRONG:3}
        leading={}
        for item in candidates:
            if item.disposition==P.LEADING:leading.setdefault(item.hypothesis_key,[]).append(item)
        structural=lambda key:any(not rule.startswith(prefix) for item in leading[key] for rule in item.rule_ids)
        order=sorted(leading,key=lambda key:(not structural(key),-max(rank[i.support] for i in leading[key]),key))
        kept,demoted=set(),set()
        for key in order:
            if not structural(key) and set(HYPOTHESIS_CONFLICTS.get(key,()))&kept:
                demoted.add(key)
            else:
                kept.add(key)
        return tuple(replace(item,disposition=P.RETAINED_ALTERNATIVE,rule_ids=(*item.rule_ids,f'{self.conflict_policy}:retained'))
                     if item.hypothesis_key in demoted and item.disposition==P.LEADING else item for item in candidates)
