"""Bounded real-model continuity canary, no database writes or live delivery.

Uses the shadow's own responses as synthetic history and chains predecessor
state. This is NOT the WebSocket/persistence/worker evaluation. The common
corpus harness uses a test persona, not a personality/affinity quality test.
"""
import argparse
import json
from pathlib import Path

from cognitive_two_call_corpus_eval import get_settings, run_conversation

CORPUS = {
    'options': [
        "I'm deciding whether to spend Saturday painting or hiking.",
        'I keep leaning toward the first one.',
        'What was the other option I mentioned?',
        'Actually, I meant I keep leaning toward hiking, not painting.',
        'Given that correction, which activity am I leaning toward?',
    ],
    'ambiguity': [
        'Maya lent Priya a novel because she had finished it.',
        'Who had finished it?',
        'I meant Maya had finished it.',
        'Who owns the novel?',
    ],
    'knowledge': ['What causes tides on Earth?', 'How long does it take?',
                  'I mean how long from high tide to low tide.'],
    'perspective': [
        'I feel uncomfortable declining an invitation from my friend.',
        "It's not that I don't like them; I'm just exhausted.",
        'No advice, please. What do you think is making this difficult for me?',
    ],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--conversations', nargs='+', choices=list(CORPUS), default=list(CORPUS))
    args = parser.parse_args()
    settings = get_settings()
    rows = []
    for name in args.conversations:
        result = run_conversation(name, CORPUS[name], settings, 'cognitive-two-call-v1', capture_details=True)
        rows.extend(result)
        Path(args.output).write_text(json.dumps(dict(scope='in_process_real_model_shadow', rows=rows),
                                               indent=2, ensure_ascii=False))
        for row in result:
            print(json.dumps({k: row[k] for k in ('turn', 'status', 'failure', 'plan', 'realization', 'final_text')},
                             ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
