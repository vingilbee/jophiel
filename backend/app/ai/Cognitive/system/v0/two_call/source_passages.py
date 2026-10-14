"""Linear-size, code-owned passage menu for dialogue citations.

Selectors retain the existing token-address wire format but are selected, not
calculated by the model. The compiler still independently checks source bounds.
"""
import re


def passages(text):
    from .v1 import focus_tokens
    tokens = focus_tokens(text)
    if not tokens:
        return []
    ranges = set()
    if tokens[-1][1] - tokens[0][0] <= 600:
        ranges.add((0, len(tokens) - 1))
    # Sentence passages preserve complete contrasts whenever they fit. Do not
    # split on commas/semicolons: these often carry qualifications or negation.
    cursor = 0
    for match in re.finditer(r'[^.!?\n]+(?:[.!?]+|$)', text):
        while cursor < len(tokens) and tokens[cursor][0] < match.start():
            cursor += 1
        first = cursor
        while cursor < len(tokens) and tokens[cursor][1] <= match.end():
            cursor += 1
        if cursor > first and tokens[cursor - 1][1] - tokens[first][0] <= 600:
            ranges.add((first, cursor - 1))
    # Long sentences get overlapping bounded windows, never all O(n²) spans.
    first = 0
    while first < len(tokens):
        last = first
        while last + 1 < len(tokens) and tokens[last + 1][1] - tokens[first][0] <= 600:
            last += 1
        if tokens[last][1] - tokens[first][0] <= 600:
            ranges.add((first, last))
        if last == len(tokens) - 1:
            break
        first = max(first + 1, last - 12)
    return [dict(span_ref=f'span:{a}' if a == b else f'span:{a}-{b}',
                 text=text[tokens[a][0]:tokens[b][1]]) for a, b in sorted(ranges)]


def install_source_schema(payload, definitions):
    """Link source identity, available passages and speaker via shared $defs."""
    from .v1_provider import _obj, _enum
    all_refs, user_refs, assistant_refs = [], [], []
    messages = payload['dialogue_context']['messages']
    focus = next(i for i, m in enumerate(messages) if m['ref'] == payload['focus']['message_ref'])
    for index, message in enumerate(messages[:focus + 1]):
        choices = message.get('citation_passages', ())
        if not choices:
            continue
        name = f'DialogueSource{index}'
        definitions[name] = _obj({'kind': _enum(['dialogue_span']),
            'message_ref': _enum([message['ref']]),
            'span_ref': _enum([p['span_ref'] for p in choices])})
        ref = {'$ref': f'#/$defs/{name}'}
        all_refs.append(ref)
        if message['speaker'] == 'user':
            user_refs.append(ref)
        elif message['speaker'] == 'assistant' and message['ref'] != payload['focus']['message_ref']:
            assistant_refs.append(ref)
    def union(refs):
        return refs[0] if len(refs) == 1 else {'anyOf': refs} if refs else {'type': 'null'}
    for name, refs in [('DialogueCitation', all_refs), ('UserCitation', user_refs),
                       ('AssistantCitation', assistant_refs)]:
        definitions[name] = union(refs)
    return bool(assistant_refs)
