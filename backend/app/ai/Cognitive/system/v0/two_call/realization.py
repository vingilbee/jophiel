"""Validate second-call wording through ordinary Realization, without an API call."""
from ..realization_provider import resolve_provenance
from ..realization_v0.engine import RealizationEngineV0
from ..realization_v0.validator import RealizationValidatorV0
from ..realization_v0.model_protocol import output_from_excerpts
from ..realization_v0.errors import RealizationProviderError
from ..contracts.realization import ProviderErrorCode, RealizationModelProvenance
from .contracts import CallTwoOutput
from .provider import digest

class CapturedRealizationModel:
    def __init__(self, orchestration):
        capture = orchestration.calls[1]
        self.output = CallTwoOutput.model_validate(capture.compiled_proposal or capture.proposal)
        self.segmented = None
        if capture.protocol=='context-conditioned-two-call-v0.1':
            from .compact_contracts import ResponseProposal
            self.segmented=ResponseProposal.model_validate(capture.proposal)
        self.alignment_policy = orchestration.alignment_policy
        source = capture.provenance
        provenance = resolve_provenance(model_id=source['model_id'], timeout_seconds=source['timeout_seconds'],
                                        max_output_tokens=source['max_output_tokens'])
        self._provenance = RealizationModelProvenance(**(provenance.model_dump(mode='python') |
            dict(generation_source='captured_two_call',source_call_digest=digest(capture.proposal))))
    @property
    def provenance(self):
        return self._provenance
    def generate(self, payload):
        try:
            if len(payload.moves)!=len(self.output.behavior_proposal):
                raise ValueError('move coverage')
            moves = []
            for move, proposal in zip(payload.moves,self.output.behavior_proposal):
                if move.action_key != proposal.action_key:
                    raise ValueError('action disagreement')
                moves.append({'move_ref':move.move_ref,'excerpt':proposal.excerpt})
            if self.segmented is not None:
                from ..realization_v0.model_protocol import RealizationModelOutput
                from ..contracts.realization import TextSpanAlignment
                offsets={};cursor=0
                for segment in self.segmented.segments:
                    offsets[segment.handle]=(cursor,cursor+len(segment.text))
                    cursor+=len(segment.text)+1
                spans=[];content_spans={}
                for move,proposal in zip(payload.moves,self.segmented.moves):
                    start=offsets[proposal.segment_handles[0]][0];end=offsets[proposal.segment_handles[-1]][1]
                    spans.append(TextSpanAlignment(ref=move.move_ref,start=start,end=end))
                    for binding in move.content_refs:
                        content_spans[(binding.content_ref,start,end)]=TextSpanAlignment(ref=binding.content_ref,start=start,end=end)
                return RealizationModelOutput(text=self.output.response_candidate.text,move_alignments=tuple(spans),
                    content_alignments=tuple(content_spans.values()),omitted_move_refs=())
            bindings = []
            seen = set()
            for alignment in self.output.response_candidate.content_alignments:
                key = (alignment.move_handle,alignment.binding_handle)
                if key in seen:
                    raise ValueError('duplicate local content binding')
                seen.add(key)
                move = payload.moves[alignment.move_handle]
                binding = move.content_refs[alignment.binding_handle]
                bindings.append({'content_ref':binding.content_ref,'excerpt':alignment.excerpt})
            expected = {(i,j) for i,m in enumerate(payload.moves) for j,_ in enumerate(m.content_refs)}
            if seen != expected:
                if self.alignment_policy == 'complete_bindings-v0.1':
                    raise ValueError('content binding coverage')
                # The model cannot know final compiler binding positions. Use
                # each actual move's excerpt as a candidate span for its missing
                # bindings. This supplies metadata only: ordinary validation
                # must still check anchors, values and meaning restrictions.
                for i,j in sorted(expected - seen):
                    bindings.append({'content_ref':payload.moves[i].content_refs[j].content_ref,
                                     'excerpt':self.output.behavior_proposal[i].excerpt})
            return output_from_excerpts(dict(text=self.output.response_candidate.text,
                move_alignments=moves,content_alignments=bindings,omitted_move_refs=[]),payload)
        except (ValueError,IndexError,KeyError,TypeError) as exc:
            raise RealizationProviderError(ProviderErrorCode.OUTPUT_INVALID) from exc

def captured_engine(orchestration):
    return RealizationEngineV0(CapturedRealizationModel(orchestration),max_attempts=1,
                               validator=RealizationValidatorV0(policy=orchestration.realization_policy))
