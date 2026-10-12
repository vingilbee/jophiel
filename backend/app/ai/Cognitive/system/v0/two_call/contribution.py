"""Open-world contribution for argument registry v1.6: general knowledge and Columbina's own view.

Until v1.6 the shadow was closed-world: an open question resolved to an
explicit knowledge gap, and there was no content for "what's your take?".
This module adds one recorded model stage, ``contribution``, between Call 1
and Call 2. It runs only when requirement resolution asks for content the
closed-world producers cannot supply.

- **Knowledge** (``information_request``, an unanswered elaboration): the model
  returns up to three short claims, each with a confidence. Code labels them
  ``model_general_knowledge``. Only high confidence is worded without a hedge.
  Questions about the user, current events or anything unanswerable return
  nothing, so the turn keeps its explicit knowledge gap.
- **Opinion** (``opinion_request``): the model proposes two or three distinct
  takes. Code, not the model, picks one by scoring each take against the
  personality projection with a fixed, versioned formula
  (``OPINION_SELECTION_VERSION``), so a different core picks a different take.
  The chosen view is Columbina's (``columbina_own_view``), always tentative.

The model chooses what to say among candidates; code chooses which candidate
is hers and how it is labelled. Personality shapes opinions, never facts.
The stage is recorded on the orchestration trace (``TwoCallTrace.contributions``)
and replay serves the recorded output, so replay never calls a model.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.interpretation import FrozenModel
from ..contracts.realization import ArgumentValueType, ContentAttribution, ContentSourceKind
from ..producers_v0.contracts import AuthorityClass, ProducedFact, ProducerReason, ProducerResult, ProducerStatus
from ..producers_v0.manifest import QUERY_MANIFEST_VERSION

CONTRIBUTION_STAGE = 'contribution'
CONTRIBUTION_INPUT_VERSION = 'contribution-input-v1'
# Registry v1.8: follow-ups are answered in context (``contribution_v1_8.txt``).
CONTRIBUTION_INPUT_VERSION_V1_1 = 'contribution-input-v1.1'
CONTRIBUTION_SCHEMA_VERSION = 'contribution-proposal-v1'
CONTRIBUTION_PRODUCER_VERSION = 'model-contribution-v1'
OPINION_SELECTION_VERSION = 'opinion-selection-v1'
MAX_CLAIM_CHARACTERS = 400

# Query kinds the closed-world producers leave unsupported and v1.6 may answer.
KNOWLEDGE_QUERY_KINDS = frozenset({'reviewed_knowledge.open_question', 'dialogue.delivered_claim'})
OPINION_QUERY_KIND = 'reviewed_knowledge.opinion_request'

Confidence = Literal['high', 'medium', 'low']
Stance = Literal['agree_with_user', 'partly_agree_with_user', 'disagree_with_user', 'own_position', 'unsure']
Tone = Literal['serious', 'playful']
QuestionKind = Literal['general_knowledge', 'opinion', 'about_the_user', 'current_events', 'unanswerable']


class Claim(FrozenModel):
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARACTERS)
    confidence: Confidence


class Take(FrozenModel):
    stance: Stance
    tone: Tone
    confidence: Confidence
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARACTERS)


class ContributionProposalV1(FrozenModel):
    schema_version: Literal['contribution-proposal-v1']
    question_kind: QuestionKind
    claims: tuple[Claim, ...] = Field(max_length=3)
    takes: tuple[Take, ...] = Field(max_length=3)

    @model_validator(mode='after')
    def consistent(self) -> ContributionProposalV1:
        if self.question_kind == 'general_knowledge' and (not self.claims or self.takes):
            raise ValueError('general knowledge carries claims and no takes')
        if self.question_kind == 'opinion':
            if self.claims or len(self.takes) < 2:
                raise ValueError('an opinion carries two or three takes and no claims')
            if len({take.text.strip().casefold() for take in self.takes}) != len(self.takes):
                raise ValueError('takes must be distinct')
        if self.question_kind not in ('general_knowledge', 'opinion') and (self.claims or self.takes):
            raise ValueError('a question Columbina cannot answer carries no content')
        return self


def _obj(properties: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}


def contribution_schema() -> dict:
    text = {'type': 'string', 'minLength': 1, 'maxLength': MAX_CLAIM_CHARACTERS}
    enum = lambda values: {'type': 'string', 'enum': list(values)}
    return _obj({
        'schema_version': enum(['contribution-proposal-v1']),
        'question_kind': enum(QuestionKind.__args__),
        'claims': {'type': 'array', 'maxItems': 3, 'items': _obj({'text': text, 'confidence': enum(Confidence.__args__)})},
        'takes': {'type': 'array', 'maxItems': 3, 'items': _obj({
            'stance': enum(Stance.__args__), 'tone': enum(Tone.__args__),
            'confidence': enum(Confidence.__args__), 'text': text})},
    })


# ---------------------------------------------------------------------------
# Input payload
# ---------------------------------------------------------------------------

def _band(value) -> str:
    if value is None:
        return 'unknown'
    value = float(value)
    return 'very low' if value < 0.2 else 'low' if value < 0.4 else 'moderate' if value < 0.6 else 'high' if value < 0.8 else 'very high'


_TRAIT_GUIDANCE = {
    'directness': 'how plainly she states a position, including disagreement',
    'warmth': 'how much she looks for what is right in the other view first',
    'curiosity': 'how much she connects the question to something interesting',
    'playfulness': 'how readily she uses a light, playful angle',
}


def personality_view(request) -> dict:
    personality = request.action_personality
    traits = {key: getattr(personality, key, None) for key in _TRAIT_GUIDANCE}
    return dict(traits=[dict(trait=key, band=_band(value), meaning=_TRAIT_GUIDANCE[key]) for key, value in traits.items()],
                charitable_prior=_band(getattr(request.interpretation_biases, 'benign_prior', None)))


def contribution_input_version(owner) -> str:
    from .v1_contracts import WRITTEN_RECALL_REGISTRIES
    return (CONTRIBUTION_INPUT_VERSION_V1_1 if owner.understanding_registry in WRITTEN_RECALL_REGISTRIES
            else CONTRIBUTION_INPUT_VERSION)


def build_payload(owner, *, kind: str, frame, query) -> dict:
    """A bounded, plain-JSON view: the question, recent dialogue, and her personality."""
    from .v1 import plain_json
    request = owner.request
    window = []
    for message in request.messages[-8:]:
        current = message.message_id == request.focus_message_id
        if not current and len(message.content) > 2000:
            continue
        window.append(dict(speaker=message.role.value, text=message.content, current_turn=current))
    topic = next((value for name, value in sorted(frame.arguments.items())
                  if name in ('topic', 'topic_ref', 'referent_phrase') and isinstance(value, str)), None)
    return plain_json(dict(
        protocol='cognitive-two-call-v1', schema_version=contribution_input_version(owner),
        requested=kind, question_topic=topic, conversation_window=window,
        personality=personality_view(request) if kind == 'opinion' else None,
    ))


# ---------------------------------------------------------------------------
# Personality scoring (opinion-selection-v1)
# ---------------------------------------------------------------------------

def _trait(request, name: str, default: float = 0.5) -> Decimal:
    value = getattr(request.action_personality, name, None)
    return Decimal(str(default if value is None else value))


def take_scores(takes: tuple[Take, ...], request) -> tuple[Decimal, ...]:
    """Deterministic fit of each take to the personality projection.

    directness favours stating disagreement and confident positions; warmth and
    the charitable prior favour finding what is right in the user's view;
    playfulness favours a playful tone. An unsure take keeps a constant
    baseline, so an honest "I'm not sure" wins when nothing else fits.
    """
    d, w, p = _trait(request, 'directness'), _trait(request, 'warmth'), _trait(request, 'playfulness')
    b = Decimal(str(getattr(request.interpretation_biases, 'benign_prior', 0.5)))
    half = Decimal('0.5')
    stance = {
        'disagree_with_user': d + half * (1 - w),
        'partly_agree_with_user': half * w + half * b + Decimal('0.25'),
        'agree_with_user': Decimal('0.6') * w + Decimal('0.6') * b - Decimal('0.4') * d,
        'own_position': half + Decimal('0.25') * d,
        'unsure': Decimal('0.35'),
    }
    confidence = {'high': Decimal('0.4') * d, 'medium': Decimal('0.2'), 'low': Decimal('0.4') * (1 - d)}
    # Only a clearly playful core (playfulness above 0.6) prefers a playful take.
    tone = {'serious': Decimal('0'), 'playful': p - Decimal('0.6')}
    return tuple(stance[t.stance] + confidence[t.confidence] + tone[t.tone] for t in takes)


def choose_take(takes: tuple[Take, ...], request) -> int:
    scores = take_scores(takes, request)
    return max(range(len(takes)), key=lambda i: (scores[i], -i))


# ---------------------------------------------------------------------------
# Compilation to a producer result
# ---------------------------------------------------------------------------

def _key(*parts: str) -> str:
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]


def compile_result(proposal: ContributionProposalV1, *, kind: str, query, request, capture_digest: str) -> ProducerResult:
    common = dict(result_id=f'contribution-result:{_key(query.query_id)}', query_ref=query.query_id,
                  producer_id=CONTRIBUTION_PRODUCER_VERSION, producer_version=CONTRIBUTION_PRODUCER_VERSION,
                  manifest_version=QUERY_MANIFEST_VERSION)
    source = f'contribution:{capture_digest}'
    if kind == 'knowledge' and proposal.question_kind == 'general_knowledge':
        authority = AuthorityClass.MODEL_KNOWLEDGE
        facts = tuple(ProducedFact(
            fact_id=f'contribution-fact:{_key(query.query_id, str(i), claim.text)}', query_ref=query.query_id,
            authority_class=authority, producer_id=CONTRIBUTION_PRODUCER_VERSION,
            producer_version=CONTRIBUTION_PRODUCER_VERSION,
            predicate=f'general_knowledge.{claim.confidence}_confidence', subject_anchor=query.subject_anchor,
            value=claim.text, value_type=ArgumentValueType.STRING, value_anchor=claim.text, owner='application',
            source_kind=ContentSourceKind.MODEL_GENERAL_KNOWLEDGE, source_ref=source,
            revision=CONTRIBUTION_PRODUCER_VERSION, attribution=ContentAttribution.COLUMBINA,
        ) for i, claim in enumerate(proposal.claims))
        return ProducerResult(**common, authority_class=authority, status=ProducerStatus.KNOWN, facts=facts)
    if kind == 'opinion' and proposal.question_kind == 'opinion':
        authority = AuthorityClass.COLUMBINA_VIEW
        take = proposal.takes[choose_take(proposal.takes, request)]
        fact = ProducedFact(
            fact_id=f'contribution-fact:{_key(query.query_id, take.text)}', query_ref=query.query_id,
            authority_class=authority, producer_id=CONTRIBUTION_PRODUCER_VERSION,
            producer_version=f'{CONTRIBUTION_PRODUCER_VERSION}+{OPINION_SELECTION_VERSION}',
            predicate=f'columbina.view.{take.stance}', subject_anchor=query.subject_anchor,
            value=take.text, value_type=ArgumentValueType.STRING, value_anchor=take.text, owner='application',
            source_kind=ContentSourceKind.COLUMBINA_OWN_VIEW, source_ref=source,
            revision=CONTRIBUTION_PRODUCER_VERSION, attribution=ContentAttribution.COLUMBINA,
        )
        return ProducerResult(**common, authority_class=authority, status=ProducerStatus.KNOWN, facts=(fact,))
    # About the user, current events, unanswerable, or a kind mismatch: keep the explicit gap.
    return ProducerResult(**common, authority_class=AuthorityClass.MODEL_KNOWLEDGE, status=ProducerStatus.UNAVAILABLE,
                          reason=ProducerReason.NO_MATCHING_RECORD)


__all__ = [
    'CONTRIBUTION_STAGE', 'ContributionProposalV1', 'KNOWLEDGE_QUERY_KINDS', 'OPINION_QUERY_KIND',
    'OPINION_SELECTION_VERSION', 'build_payload', 'choose_take', 'compile_result', 'contribution_schema',
    'take_scores',
]
