"""bias-application-v1: code applies her biases to Call 1's readings.

Under conditioning guide v2 the model reads the words: for each reading it gives
a likelihood and says which biases bear on it *in this context* (``bias_fit``).
It never sees her bias values. Code then applies them, the same way every time:

* Readings of one event and dimension (speaker goal, pragmatic meaning) are
  alternatives. Their likelihoods become weights, each fitting bias multiplies
  a reading's odds by up to ``ODDS_PER_FULL_BIAS``, and ambiguity tolerance
  flattens (high) or sharpens (low) the result.
* The lead reading of each group is sampled from those weights with a
  deterministic seed (the digest of what the model saw), so replay is exact and
  the same message gets the same draw at every bias level.
* Uncertainty in a group follows the weights. A group with one reading keeps
  the model's uncertainty.
* Appraisal of an open reading (in a group of several, or not low uncertainty)
  moves one step along each dimension a favouring bias leans on, with
  probability ``min(1, |push|)``. Clear readings are never touched.

Strength is ``2 x (value - 0.5)``: 0.5 is neutral and any distance pushes.
State is not applied here; the model reads it.
"""
from __future__ import annotations

import hashlib
import math

from ..contracts.interpretation import HYPOTHESIS_DIMENSION_BY_KEY
from .v1_contracts import AppraisalShift, BiasApplication, ReadingWeight, UnderstandingProposalV1

VERSION = 'bias-application-v1'
ODDS_PER_FULL_BIAS = 3.0
# Biases the model may name in ``bias_fit``. Ambiguity tolerance is applied to
# every open group instead, as the temperature of its weights.
FIT_BIASES = ('benign_prior', 'trust_prior', 'threat_sensitivity', 'confirmation_tendency',
              'self_attribution_tendency', 'social_affiliation_prior', 'hostile_attribution',
              'social_cue_sensitivity')
ALL_BIASES = (*FIT_BIASES, 'ambiguity_tolerance')
# What a high value does to the appraisal of a reading it favours (+1 toward
# high/positive, -1 toward low/negative); a low value does the opposite.
APPRAISAL_LEANS = {
    'benign_prior': {'affiliative_value': 1, 'congruence': 1, 'norm_compatibility': 1},
    'trust_prior': {'certainty': 1},
    'threat_sensitivity': {'relevance': 1, 'affiliative_value': -1, 'norm_compatibility': -1},
    'confirmation_tendency': {'novelty': -1, 'congruence': 1},
    'self_attribution_tendency': {},
    'social_affiliation_prior': {'affiliative_value': 1},
    'hostile_attribution': {'affiliative_value': -1, 'norm_compatibility': -1},
    'social_cue_sensitivity': {},
}
SCALES = {name: ('low', 'moderate', 'high') for name in ('relevance', 'novelty', 'certainty', 'controllability')} | {
    name: ('negative', 'neutral', 'positive') for name in ('congruence', 'norm_compatibility', 'affiliative_value')}
LEAD_LOW_UNCERTAINTY = 0.75     # a lead this likely is read with low uncertainty
ALTERNATIVE_KEPT = 0.35         # an alternative this likely stays moderate, else high


def strength(value) -> float:
    return 0.0 if value is None else round(2 * (float(value) - 0.5), 4)


def _draw(seed: str, *parts) -> float:
    """A uniform number in [0, 1) fixed by the seed and the position."""
    text = ':'.join((seed, *map(str, parts)))
    return int(hashlib.sha256(text.encode()).hexdigest()[:13], 16) / 16 ** 13


def _step(scale, value, direction):
    index = scale.index(value) + direction
    return scale[min(max(index, 0), len(scale) - 1)]


def apply_biases(proposal: UnderstandingProposalV1, values: dict, seed: str):
    """The adjusted proposal and the record of what changed. Pure."""
    strengths = {key: strength(values.get(key)) for key in ALL_BIASES}
    temperature = round(2 ** strengths['ambiguity_tolerance'], 4)
    log_odds = math.log(ODDS_PER_FULL_BIAS)
    records, events, remaps = [], [], []
    for event_index, event in enumerate(proposal.events):
        groups: dict[str, list[int]] = {}
        for position, reading in enumerate(event.readings):
            groups.setdefault(HYPOTHESIS_DIMENSION_BY_KEY[reading.hypothesis_key].value, []).append(position)
        weights, priors, leads, uncertainty = {}, {}, set(), {}
        for dimension, members in groups.items():
            total = sum(event.readings[i].likelihood for i in members)
            for i in members:
                priors[i] = event.readings[i].likelihood / total
            if len(members) == 1:
                weights[members[0]] = 1.0
                leads.add(members[0])
                uncertainty[members[0]] = event.readings[members[0]].uncertainty
                continue
            raw = {}
            for i in members:
                push = sum((1 if fit.direction == 'favours' else -1) * strengths[fit.bias_ref.split(':', 1)[1]]
                           for fit in event.readings[i].bias_fit)
                raw[i] = (priors[i] * math.exp(log_odds * push)) ** (1 / temperature)
            norm = sum(raw.values())
            for i in members:
                weights[i] = raw[i] / norm
            ranked = sorted(members, key=lambda i: (-weights[i], i))
            draw, cumulative, lead = _draw(seed, event_index, dimension), 0.0, ranked[-1]
            for i in ranked:
                cumulative += weights[i]
                if draw < cumulative:
                    lead = i
                    break
            leads.add(lead)
            for i in members:
                if i == lead:
                    uncertainty[i] = 'low' if weights[i] >= LEAD_LOW_UNCERTAINTY else 'moderate'
                else:
                    uncertainty[i] = 'moderate' if weights[i] >= ALTERNATIVE_KEPT else 'high'
        # Leads first (in their original order), then alternatives by weight.
        order = sorted(range(len(event.readings)), key=lambda i: (i not in leads, -weights[i], i))
        remaps.append({source: index for index, source in enumerate(order)})
        readings = []
        for index, source in enumerate(order):
            reading = event.readings[source]
            open_reading = len(groups[HYPOTHESIS_DIMENSION_BY_KEY[reading.hypothesis_key].value]) > 1 \
                or reading.uncertainty != 'low'
            appraisal = reading.appraisal.model_dump(mode='python')
            shifts = []
            if open_reading:
                favouring = [f.bias_ref.split(':', 1)[1] for f in reading.bias_fit if f.direction == 'favours']
                for dimension, scale in SCALES.items():
                    push = round(sum(strengths[b] * APPRAISAL_LEANS[b].get(dimension, 0) for b in favouring), 4)
                    before = appraisal[dimension]
                    if push == 0 or before not in scale:
                        continue
                    applied = _draw(seed, event_index, source, dimension) < min(1.0, abs(push))
                    after = _step(scale, before, 1 if push > 0 else -1) if applied else before
                    appraisal[dimension] = after
                    shifts.append(AppraisalShift(dimension=dimension, before=before, after=after, push=push,
                                                 applied=applied))
            readings.append(reading.model_copy(update=dict(
                uncertainty=uncertainty[source], appraisal=type(reading.appraisal)(**appraisal))))
            records.append(ReadingWeight(event_index=event_index, source_index=source, index=index,
                                         hypothesis_key=reading.hypothesis_key, prior=round(priors[source], 4),
                                         weight=round(weights[source], 4), lead=source in leads,
                                         uncertainty_before=reading.uncertainty, uncertainty_after=uncertainty[source],
                                         appraisal_shifts=tuple(shifts)))
        events.append(event.model_copy(update=dict(readings=tuple(readings))))
    impacts = tuple(i.model_copy(update=dict(reading_index=remaps[i.event_index][i.reading_index]))
                    if i.reading_index is not None else i for i in proposal.existing_goal_impacts)
    unknowns = tuple(u.model_copy(update=dict(subject=u.subject.model_copy(update=dict(
        reading_index=remaps[u.subject.event_index][u.subject.reading_index]))))
        if u.subject.reading_index is not None else u for u in proposal.unknowns)
    adjusted = UnderstandingProposalV1.model_validate(proposal.model_copy(update=dict(
        events=tuple(events), existing_goal_impacts=impacts, unknowns=unknowns)).model_dump(mode='python'))
    application = BiasApplication(version=VERSION, seed=seed, strengths=strengths, ambiguity_temperature=temperature,
                                  readings=tuple(records))
    return adjusted, application
