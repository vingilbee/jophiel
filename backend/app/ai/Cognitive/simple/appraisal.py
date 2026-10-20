"""Labels `appraisal-v1` (extract_v5): what they did, what she thinks they meant, her part, what it
changed in her, what she shows (docs/COLUMBINA_INNER_STATE_PLAN.md, step 5.1c; offline).

The frozen teacher's labels (extract_v4) squeezed these together and described how she feels
*now*, so a lingering hurt read as hurt again on every later message, and the extractor copied
feelings from her mood input. Here:

- the extractor never sees her mood (the mood line is removed from its input);
- a change cites the exact words of her thought (checked by code; a change whose quote isn't in
  the thought is dropped and counted) and the message that caused it;
- ``continuing`` changes add nothing; ``reinterpreted`` ones act on the earlier incident
  (app/ai/mind/inner_state.py owns the resulting state, not the reader);
- ``feelings`` (how she feels now) stays for the existing pipeline and students, as a diagnostic.

Thought and reply come from the frozen teacher's prompts (intend_v4, speak_v4), so old
conversations can be relabelled without regenerating them. Old (v4) and new labels are kept apart:
every record carries ``LABELS``.
"""
from __future__ import annotations

import re
from typing import Any

from .contracts import FEELINGS, SCALE_WORDS, Sample
from .intention import IntentionTeacher, extract_schema, split_thought
from .mood import update
from .teacher import View, _enum, _obj, prompt
from .turn import allowed_groups, flags

LABELS = 'appraisal-v1'
PROMPT = 'extract_v5'
MEANT = ('kind', 'neutral', 'joking', 'frustrated', 'dismissive', 'testing', 'unclear')
HER_PART = ('not_applicable', 'no_shortfall', 'she_fell_short', 'partly_hers', 'misunderstanding',
            'unsupported_expectation', 'unclear')
STATUSES = ('new', 'continuing', 'reinterpreted')
_MOOD_LINE = re.compile(r'^Her mood right now:.*\n?', re.M)


def schema(note_ids: list[str]) -> dict[str, Any]:
    base = extract_schema(note_ids)
    understand = base['properties']['understand']
    change = _obj({'feeling': _enum(FEELINGS), 'direction': _enum(['up', 'down']),
                   'amount': _enum([w for w in SCALE_WORDS if w != 'none']), 'status': _enum(STATUSES),
                   'cause_message': {'type': 'integer'}, 'quote': {'type': 'string'}})
    extra = {'toward_her': _enum(['warm', 'neutral', 'cold', 'hostile']), 'dismissive': {'type': 'boolean'},
             'meant': _enum(MEANT), 'meant_sure': _enum(SCALE_WORDS), 'her_part': _enum(HER_PART),
             'her_part_messages': {'type': 'array', 'items': {'type': 'integer'}},
             'changes': {'type': 'array', 'items': change},
             'shows': {'type': 'array', 'items': _enum(FEELINGS)}}
    return _obj({'understand': understand, 'did': base['properties']['did'], **extra})


def extraction_input(view: View, thought: str, reply: str) -> str:
    """Her full view minus her mood, plus the thought and reply. Messages are numbered; the new
    one is ``bundle.turn``."""
    base = _MOOD_LINE.sub('', view.inputs())
    note_ids = [n.id for n in view.bundle.notes]
    return (f'{base}\n\nTHEIR NEW MESSAGE IS NUMBER {view.bundle.turn}.\n'
            f'OPEN NOTE IDS: {", ".join(note_ids) or "none (give an empty note_updates list)"}\n\n'
            f'HER THOUGHT\n{thought}\n\nHER REPLY\n{reply or "(she did not reply)"}')


def _norm(text: str) -> str:
    return ' '.join(re.sub(r'[*_#"“”‘’\']', '', text).lower().split())


def check_changes(changes: list[dict], thought: str, new_number: int) -> tuple[list[dict], list[dict]]:
    """Keep a change only if its quote is in the thought and its cause is a real message
    (``new`` must be this message; the others an earlier one). Returns (kept, dropped with why)."""
    thought_n = _norm(thought)
    kept, dropped = [], []
    for c in changes:
        why = None
        quote = _norm(c.get('quote', ''))
        if not quote or quote not in thought_n:
            why = 'quote not in thought'
        elif c['status'] == 'new' and c['cause_message'] != new_number:
            why = 'new but caused by another message'
        elif c['status'] != 'new' and not 1 <= c['cause_message'] < new_number:
            why = f'{c["status"]} needs an earlier message'
        (dropped if why else kept).append(dict(c, why=why) if why else c)
    return kept, dropped


async def extract(llm, view: View, thought: str, reply: str) -> dict[str, Any]:
    note_ids = [n.id for n in view.bundle.notes]
    labels = await llm.json('extract_v5', prompt(PROMPT), extraction_input(view, thought, reply), schema(note_ids))
    understand = labels['understand']
    seen = {u['note']: u['update'] for u in understand['note_updates'] if u['note'] in note_ids}
    understand['note_updates'] = [{'note': i, 'update': seen.get(i, 'none')} for i in note_ids]
    kept, dropped = check_changes(labels['changes'], thought, view.bundle.turn)
    appraisal = dict(labels=LABELS, toward_her=labels['toward_her'], dismissive=labels['dismissive'],
                     meant=labels['meant'], meant_sure=labels['meant_sure'], her_part=labels['her_part'],
                     her_part_messages=labels['her_part_messages'], changes=kept, dropped_changes=dropped,
                     shows=labels['shows'])
    return dict(understand=understand, did=labels['did'], appraisal=appraisal)


class AppraisalTeacher(IntentionTeacher):
    """The frozen teacher's thought and reply (intend_v4, speak_v4), labelled with extract_v5."""

    def __init__(self, llm, page: str, dynamics):
        super().__init__(llm, page, dynamics, variant='intention-ablation')

    async def sample(self, view: View, index: int) -> Sample:
        base = view.inputs()
        from .teacher import mood_text
        thought = await self.llm.text('intend', prompt('intend' + self.suffix), base, reasoning='low', max_tokens=2500)
        _, intention = split_thought(thought)
        reply = await self.llm.text('speak', prompt('speak' + self.suffix),
                                    f'{base}\n\nHER MOOD: {mood_text(view.bundle.mood)}\n\nHER THOUGHT\n{thought}',
                                    max_tokens=500)
        got = await extract(self.llm, view, thought, reply)
        understand = got['understand']
        turn_flags = flags(view.bundle.notice, understand, view.bundle.notes)
        return Sample(index=index, attempt=0, thought=thought, understand=understand,
                      mood_after=update(view.bundle.mood, understand, self.dynamics), flags=turn_flags,
                      allowed=allowed_groups(view.bundle.notice, understand, turn_flags),
                      decide={k: got['did'][k] for k in ('main', 'second', 'dials')},
                      critic={'verdict': 'none (intention variants have no critic)'}, accepted=True,
                      extra=dict(intention=intention, reply=reply, did_not_fit=got['did']['did_not_fit'],
                                 appraisal=got['appraisal']))


def to_changes(appraisal: dict[str, Any], *, source: str, activity: str, number: int, at: float):
    """The reading of message ``number`` (at time ``at``) as inner-state ``Change``s."""
    from app.ai.mind.inner_state import AMOUNTS, Change
    # v1 has no incidents: the message that caused a feeling stands in for its incident.
    return [Change(source, activity, f'm{number}', f'm{c["cause_message"]}', c['feeling'],
                   1 if c['direction'] == 'up' else -1, AMOUNTS[c['amount']], c['status'], at, c.get('quote', ''))
            for c in appraisal.get('changes', [])]


# --- appraisal-v2 (extract_v6): effects on incidents --------------------------------------------
#
# v1's "changes" belonged to a message, and the reader marked ongoing pleasantness ("I'm glad they
# asked") as new on every friendly message, so warmth piled up. v2 separates the EVENT (this
# message; it deduplicates delivery) from the INCIDENT (what the feeling is about, across messages;
# it links repeats and repair), and the status says what new evidence did to it: opened / escalated
# / eased / sustained (holds, never raises) / lingering (nothing). Size is the shift compared with
# just before the message, and may be "unclear". "unsure" is split: emotional unease is "uneasy";
# not knowing a fact goes to `sure` and moves nothing.

LABELS_V2 = 'appraisal-v2'
PROMPT_V2 = 'extract_v6'
EFFECT_FEELINGS = ('happy', 'amused', 'curious', 'touched', 'uneasy', 'hurt', 'annoyed', 'tired')
EFFECT_STATUSES = ('opened', 'escalated', 'eased', 'sustained', 'lingering')
EFFECT_SIZES = ('small', 'clear', 'big', 'unclear')
INCIDENTS_SHOWN = 8


class IncidentBook:
    """The incidents open in one conversation, as the reader sees them (ids are code's)."""

    def __init__(self, incidents: list[dict] | None = None, counter: int = 0):
        self.incidents = incidents or []
        self.counter = counter

    def text(self) -> str:
        if not self.incidents:
            return 'OPEN INCIDENTS: none yet.'
        shown = sorted(self.incidents, key=lambda i: -i['last_message'])[:INCIDENTS_SHOWN]
        return 'OPEN INCIDENTS (most recent first):\n' + '\n'.join(
            f'- {i["id"]}: {i["label"]} (since message {i["opened_message"]}, last touched at {i["last_message"]}; '
            f'{", ".join(f"{f} {s}" for f, s in i["feelings"].items())})'
            + (f'; first evidence: "{i["first"]}"' if i.get('first') else '') for i in shown)

    def ids(self) -> list[str]:
        return [i['id'] for i in self.incidents]

    def record(self, effects: list[dict], number: int) -> None:
        for e in effects:
            if e['incident'] == 'new':
                self.counter += 1
                e['incident'] = f'i{self.counter}'
                self.incidents.append(dict(id=e['incident'], label=e['incident_label'] or e['feeling'],
                                           opened_message=number, last_message=number, feelings={},
                                           first=(e.get('evidence') or e.get('quote') or '')[:120]))
            incident = next(i for i in self.incidents if i['id'] == e['incident'])
            incident['last_message'] = number
            incident['feelings'][e['feeling']] = e['status']

    def to_dict(self) -> dict:
        return dict(incidents=self.incidents, counter=self.counter)


def schema_v2(note_ids: list[str], incident_ids: list[str]) -> dict[str, Any]:
    base = schema(note_ids)
    props = dict(base['properties'])
    props.pop('changes')
    props['shows'] = {'type': 'array', 'items': _enum(EFFECT_FEELINGS)}
    props['effects'] = {'type': 'array', 'items': _obj({
        'feeling': _enum(EFFECT_FEELINGS), 'incident': _enum([*incident_ids, 'new']),
        'incident_label': {'type': 'string'}, 'status': _enum(EFFECT_STATUSES), 'size': _enum(EFFECT_SIZES),
        'evidence': {'type': 'string'}, 'quote': {'type': 'string'}})}
    return _obj(props)


def check_effects(effects: list[dict], thought: str) -> tuple[list[dict], list[dict]]:
    """Structural checks only (they say nothing about whether the reading is right): the quote is in
    the thought, and a new incident is opened while an open one is escalated/eased/sustained/lingering."""
    thought_n = _norm(thought)
    kept, dropped = [], []
    for e in effects:
        quote = _norm(e.get('quote', ''))
        why = None
        if not quote or quote not in thought_n:
            why = 'quote not in thought'
        elif (e['incident'] == 'new') != (e['status'] == 'opened'):
            why = 'opened needs a new incident' if e['status'] == 'opened' else f'{e["status"]} needs an open incident'
        (dropped if why else kept).append(dict(e, why=why) if why else e)
    return kept, dropped


async def extract_v2(llm, view: View, thought: str, reply: str, book: IncidentBook) -> dict[str, Any]:
    note_ids = [n.id for n in view.bundle.notes]
    user = extraction_input(view, thought, reply).replace('\n\nHER THOUGHT\n', f'\n{book.text()}\n\nHER THOUGHT\n', 1)
    labels = await llm.json('extract_v6', prompt(PROMPT_V2), user, schema_v2(note_ids, book.ids()))
    understand = labels['understand']
    seen = {u['note']: u['update'] for u in understand['note_updates'] if u['note'] in note_ids}
    understand['note_updates'] = [{'note': i, 'update': seen.get(i, 'none')} for i in note_ids]
    kept, dropped = check_effects(labels['effects'], thought)
    book.record(kept, view.bundle.turn)
    appraisal = dict(labels=LABELS_V2, toward_her=labels['toward_her'], dismissive=labels['dismissive'],
                     meant=labels['meant'], meant_sure=labels['meant_sure'], her_part=labels['her_part'],
                     her_part_messages=labels['her_part_messages'], effects=kept, dropped_effects=dropped,
                     shows=labels['shows'], incidents_after=[dict(i, feelings=dict(i["feelings"])) for i in book.incidents])
    return dict(understand=understand, did=labels['did'], appraisal=appraisal)


class AppraisalTeacherV2(IntentionTeacher):
    """The frozen teacher's thought and reply, labelled with extract_v6. One per conversation: it
    keeps that conversation's incidents."""

    def __init__(self, llm, page: str, dynamics):
        super().__init__(llm, page, dynamics, variant='intention-ablation')
        self.book = IncidentBook()
        self.extractor = extract_v2

    async def sample(self, view: View, index: int) -> Sample:
        from .teacher import mood_text
        base = view.inputs()
        thought = await self.llm.text('intend', prompt('intend' + self.suffix), base, reasoning='low', max_tokens=2500)
        _, intention = split_thought(thought)
        reply = await self.llm.text('speak', prompt('speak' + self.suffix),
                                    f'{base}\n\nHER MOOD: {mood_text(view.bundle.mood)}\n\nHER THOUGHT\n{thought}',
                                    max_tokens=500)
        got = await self.extractor(self.llm, view, thought, reply, self.book)
        understand = got['understand']
        turn_flags = flags(view.bundle.notice, understand, view.bundle.notes)
        return Sample(index=index, attempt=0, thought=thought, understand=understand,
                      mood_after=update(view.bundle.mood, understand, self.dynamics), flags=turn_flags,
                      allowed=allowed_groups(view.bundle.notice, understand, turn_flags),
                      decide={k: got['did'][k] for k in ('main', 'second', 'dials')},
                      critic={'verdict': 'none (intention variants have no critic)'}, accepted=True,
                      extra=dict(intention=intention, reply=reply, did_not_fit=got['did']['did_not_fit'],
                                 appraisal=got['appraisal']))


def to_changes_v2(appraisal: dict[str, Any], *, source: str, activity: str, number: int, at: float):
    from app.ai.mind.inner_state import SIZES, Change
    return [Change(source, activity, f'm{number}', e['incident'], e['feeling'], -1 if e['status'] == 'eased' else 1,
                   SIZES[e['size']], e['status'], at, e.get('evidence', ''))
            for e in appraisal.get('effects', [])]


# --- appraisal-v3 (extract_v7): incident identity apart from the change ---------------------------
#
# v2 over-corrected: incidents grew as broad as "this conversation", so a new reason for a feeling
# became "sustained" (and a sustained label undoes fading). v3 asks separately which specific
# concern a feeling is about, whether THIS message shifted it (up / down / none: needs new
# evidence), and whether its cause is still active, over, or only recalled. Code derives the
# updater's status: up -> opened (new incident) or escalated; down -> eased; none -> sustained only
# with an active cause AND evidence it's still going on, otherwise lingering. Size is only for a
# shift and may stay "unclear": the updater gets it as unknown (see to_changes_v3), never as small.

LABELS_V3 = 'appraisal-v3'
PROMPT_V3 = 'extract_v7'
SHIFTS = ('up', 'down', 'none')
CAUSES = ('active', 'over', 'recalled')
SHIFT_SIZES = ('small', 'clear', 'big', 'unclear', '-')


def schema_v3(note_ids: list[str], incident_ids: list[str]) -> dict[str, Any]:
    props = dict(schema_v2(note_ids, incident_ids)['properties'])
    props['effects'] = {'type': 'array', 'items': _obj({
        'feeling': _enum(EFFECT_FEELINGS), 'incident': _enum([*incident_ids, 'new']),
        'incident_label': {'type': 'string'}, 'shift': _enum(SHIFTS), 'size': _enum(SHIFT_SIZES),
        'cause': _enum(CAUSES), 'evidence': {'type': 'string'}, 'quote': {'type': 'string'}})}
    return _obj(props)


def derive_status(e: dict) -> tuple[str | None, str | None]:
    """(status for the updater, a note when code had to decide) from the reader's separate answers."""
    new = e['incident'] == 'new'
    if e['shift'] == 'up':
        return ('opened' if new else 'escalated'), None
    if e['shift'] == 'down':
        return (None, 'down on a new incident') if new else ('eased', None)
    if e['cause'] == 'active' and e['evidence'].strip():
        return 'sustained', None
    if e['cause'] == 'active':
        return 'lingering', 'active without evidence: lingering'
    return 'lingering', None


def check_effects_v3(effects: list[dict], thought: str) -> tuple[list[dict], list[dict]]:
    thought_n = _norm(thought)
    kept, dropped = [], []
    for e in effects:
        quote = _norm(e.get('quote', ''))
        if not quote or quote not in thought_n:
            dropped.append(dict(e, why='quote not in thought'))
            continue
        status, note = derive_status(e)
        if status is None:
            dropped.append(dict(e, why=note))
            continue
        if e['shift'] == 'none':
            e = dict(e, size='-')
        elif e['size'] == '-':
            e = dict(e, size='unclear')
        kept.append(dict(e, status=status, **({'code_note': note} if note else {})))
    return kept, dropped


async def extract_v3(llm, view: View, thought: str, reply: str, book: IncidentBook, *,
                     prompt_name: str = PROMPT_V3, labels_name: str = LABELS_V3) -> dict[str, Any]:
    note_ids = [n.id for n in view.bundle.notes]
    user = extraction_input(view, thought, reply).replace('\n\nHER THOUGHT\n', f'\n{book.text()}\n\nHER THOUGHT\n', 1)
    labels = await llm.json(prompt_name, prompt(prompt_name), user, schema_v3(note_ids, book.ids()))
    understand = labels['understand']
    seen = {u['note']: u['update'] for u in understand['note_updates'] if u['note'] in note_ids}
    understand['note_updates'] = [{'note': i, 'update': seen.get(i, 'none')} for i in note_ids]
    kept, dropped = check_effects_v3(labels['effects'], thought)
    book.record(kept, view.bundle.turn)
    appraisal = dict(labels=labels_name, toward_her=labels['toward_her'], dismissive=labels['dismissive'],
                     meant=labels['meant'], meant_sure=labels['meant_sure'], her_part=labels['her_part'],
                     her_part_messages=labels['her_part_messages'], effects=kept, dropped_effects=dropped,
                     shows=labels['shows'],
                     incidents_after=[dict(i, feelings=dict(i['feelings'])) for i in book.incidents])
    return dict(understand=understand, did=labels['did'], appraisal=appraisal)


class AppraisalTeacherV3(AppraisalTeacherV2):
    def __init__(self, llm, page: str, dynamics):
        super().__init__(llm, page, dynamics)
        self.extractor = extract_v3


def to_changes_v3(appraisal: dict[str, Any], *, source: str, activity: str, number: int, at: float,
                  unclear_as: float | None = 0.25):
    """``unclear_as``: the amount to assume for a shift of unclear size (None skips those shifts).
    Kept explicit so every result can be shown under several assumptions."""
    from app.ai.mind.inner_state import SIZES, Change
    out = []
    for e in appraisal.get('effects', []):
        if e['shift'] == 'none':
            amount = 0.0
        elif e['size'] == 'unclear':
            if unclear_as is None:
                continue
            amount = unclear_as
        else:
            amount = SIZES[e['size']]
        out.append(Change(source, activity, f'm{number}', e['incident'], e['feeling'],
                          -1 if e['status'] == 'eased' else 1, amount, e['status'], at, e.get('evidence', '')))
    return out


# --- appraisal-v3.1 (extract_v8): same fields, three rules tightened -----------------------------
# A size only when her thought shows how much (else unclear); an open incident only for the very same
# concern (a different thing that happened is a new incident, even with the same feeling; the reader
# sees each incident's first evidence); "active" only when the cause is still going on in this message.

LABELS_V31 = 'appraisal-v3.1'
PROMPT_V31 = 'extract_v8'


async def extract_v31(llm, view: View, thought: str, reply: str, book: IncidentBook) -> dict[str, Any]:
    return await extract_v3(llm, view, thought, reply, book, prompt_name=PROMPT_V31, labels_name=LABELS_V31)


class AppraisalTeacherV31(AppraisalTeacherV2):
    def __init__(self, llm, page: str, dynamics):
        super().__init__(llm, page, dynamics)
        self.extractor = extract_v31
