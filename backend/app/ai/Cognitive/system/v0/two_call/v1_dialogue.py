"""Registry v1.7+: interpreted dialogue relationships in and out of Call 1.

In: earlier relationships (``InterpretedDialogueState``), shown with the exact
user text they came from and a message handle when that message is supplied.
Out: the model's proposed ``dialogue_updates`` for the current message,
validated against the supplied user messages. An invalid update is dropped and
counted, never repaired: it cannot fail the turn, and it never enters memory.

Registry v1.8 adds ``recall_answer``: Call 1 writes the answer to a recall
question ("Bea") instead of only pointing at a passage. It is used only if it
appears word for word in a supplied user message, so it can never be a name
the user did not say.
"""
from __future__ import annotations

import re

from ..contracts.dialogue_interpretation import (
    MAX_SPAN_CHARACTERS, InterpretedDialogueState, InterpretedSpan, InterpretedUpdate,
)
from .continuity import dialogue_span
from .v1_contracts import MAX_RECALL_ANSWER_CHARACTERS


def interpreted_links_view(state: InterpretedDialogueState | None, table) -> dict:
    """The Call 1 view of earlier relationships. Handles only; canonical refs stay in the table."""
    if state is None:
        return dict(interpreted_links=[], interpreted_links_incomplete=False)

    def part(folded) -> dict:
        span = folded.span if hasattr(folded, 'span') else folded
        return dict(message_ref=table.reverse.get(span.message_ref), text=span.text,
                    superseded=getattr(folded, 'superseded', False))

    links = []
    for item in state.option_sets:
        links.append(dict(kind='option_set', options=[part(option) for option in item.options]))
    for item in state.referent_links:
        links.append(dict(kind='same_referent', mention=part(item.mention), referent=part(item.referent)))
    for item in state.corrections:
        links.append(dict(kind='correction', replaced=part(item.replaced), replacement=part(item.replacement)))
    return dict(interpreted_links=links, interpreted_links_incomplete=bool(state.pending_message_refs))


def compile_dialogue_updates(proposal, payload, entries) -> tuple[tuple[InterpretedUpdate, ...], int]:
    """Validated updates for the current message, plus how many were dropped."""
    messages = payload['dialogue_context']['messages']
    focus_ref = payload['focus']['message_ref']
    focus_index = next(i for i, m in enumerate(messages) if m['ref'] == focus_ref)
    supplied = {m['ref']: m for m in messages[:focus_index + 1] if m['speaker'] == 'user'}
    focus_canonical = entries[focus_ref]['canonical_ref']
    kept, dropped = [], 0
    for update in proposal.dialogue_updates or ():
        try:
            spans = []
            for value in update.spans:
                source, entry = supplied.get(value.message_ref), entries.get(value.message_ref, {})
                if source is None or any(entry.get(key) != source[key] for key in ('speaker', 'text')):
                    raise ValueError('dialogue update cites an unsupplied message')
                start, end, text = dialogue_span(value, entries)
                if end - start > MAX_SPAN_CHARACTERS:
                    raise ValueError('dialogue update span too long')
                spans.append(InterpretedSpan(message_ref=entry['canonical_ref'], start=start, end=end, text=text))
            if not any(span.message_ref == focus_canonical for span in spans):
                raise ValueError('a dialogue update cites the current message')
            compiled = InterpretedUpdate(kind=update.kind, spans=tuple(spans))
            if compiled in kept:
                raise ValueError('duplicate dialogue update')
            kept.append(compiled)
        except ValueError:
            dropped += 1
    return tuple(kept), dropped


def written_recall_pointer(answer, payload, entries) -> str | None:
    """A dialogue pointer to the written answer in the latest citable message containing it, else ``None``.

    Citable: the user's messages, and her own earlier replies (marked by the payload builder).
    """
    answer = (answer or '').strip().strip('"\'“”‘’.!?,').strip()
    if not answer or len(answer) > MAX_RECALL_ANSWER_CHARACTERS:
        return None
    messages = payload['dialogue_context']['messages']
    focus_index = next(i for i, m in enumerate(messages) if m['ref'] == payload['focus']['message_ref'])
    pattern = re.compile(rf'(?<!\w){re.escape(answer)}(?!\w)', re.IGNORECASE)
    for message in reversed(messages[:focus_index + 1]):
        entry = entries.get(message['ref'], {})
        if not (message['speaker'] == 'user' or entry.get('citable')) or entry.get('text') != message['text']:
            continue
        match = pattern.search(message['text'])
        if match:
            return f"{entry['canonical_ref']}#dialogue-span:{match.start()}:{match.end()}"
    return None


__all__ = ['compile_dialogue_updates', 'written_recall_pointer', 'interpreted_links_view']
