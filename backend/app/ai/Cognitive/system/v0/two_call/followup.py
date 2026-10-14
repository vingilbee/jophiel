"""Turn-scoped semantic handoff. No retrieval, durable memory or answer authority."""
from .v1_contracts import FOLLOWUP_REGISTRIES, SCOPED_SOURCE_REGISTRIES, UnderstandingProposalV1

CONTEXT_VERSION = 'source-linked-followup-v1'


def contribution_context(owner, frame):
    if owner.understanding_registry not in FOLLOWUP_REGISTRIES:
        return {}
    result = {'followup_context_version': CONTEXT_VERSION, 'followup_context': None}
    capture = owner.captures[0]
    proposal = UnderstandingProposalV1.model_validate(capture.proposal)
    key = 'topic' if frame.predicate == 'opinion_request' else 'referent_phrase'
    matches = [event.followup for event in proposal.events
               if event.predicate_key == frame.predicate and event.followup is not None
               and event.followup.resolved_question == frame.arguments.get(key)]
    # Never attach another event's context or arbitrarily select among competing
    # meanings. Compilation and accepted-observation binding use this same topic.
    if len(matches) != 1:
        return result
    followup = matches[0]
    from .continuity import dialogue_span
    entries = capture.reference_table
    excerpts = []
    for source in followup.sources:
        citation = source.citation
        start, end, text = dialogue_span(citation, entries)
        excerpts.append(dict(role=source.role, message_ref=citation.message_ref,
                             speaker=entries[citation.message_ref]['speaker'],
                             span_ref=citation.span_ref, start=start, end=end, text=text))
        # Include bounded surrounding speech so an extract ending in "but"
        # cannot hide its speaker's actual choice. Never clip a long message
        # into an apparently complete source statement.
        full_text = entries[citation.message_ref]['text']
        if owner.understanding_registry in SCOPED_SOURCE_REGISTRIES:
            # Preserve coverage even when the full source exceeds the budget.
            # Offsets alone cannot show whether an excerpt reaches the ending.
            excerpts[-1]['source_message_characters'] = len(full_text)
            excerpts[-1]['has_omitted_prefix'] = bool(full_text[:start].strip())
            excerpts[-1]['has_omitted_suffix'] = bool(full_text[end:].strip(' \t\r\n.!?'))
        if len(full_text) <= 2000:
            excerpts[-1]['source_message_text'] = full_text
    result['followup_context'] = dict(operation=followup.operation,
                                     resolved_question=followup.resolved_question,
                                     sources=excerpts)
    return result
