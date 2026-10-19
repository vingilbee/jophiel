"""Read the live simple-cognition log (var/cognitive_live.jsonl) by conversation and arm.

  uv run python scripts/cognitive_live_report.py            # summary per arm + every conversation
  uv run python scripts/cognitive_live_report.py --summary  # summary only

Arms: "reading" (Student 1's reading was in her prompt) and "page_only" (it wasn't); both had her
page and typed memories if COGNITIVE_LIVE_CHARACTER was on, and both tracked notes.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--log', default=str(BACKEND / 'var' / 'cognitive_live.jsonl'))
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.log).read_text().splitlines() if line.strip()]
    convos: dict[str, list] = defaultdict(list)
    for row in rows:
        convos[row['conversation_id']].append(row)
    by_arm: dict[str, dict] = defaultdict(lambda: dict(conversations=0, turns=0, questions=0, words=0, student_ms=0))
    for turns in convos.values():
        arm = turns[0].get('arm') or 'none'
        a = by_arm[arm]
        a['conversations'] += 1
        for t in turns:
            reply = t.get('reply', '')
            a['turns'] += 1
            a['questions'] += '?' in reply
            a['words'] += len(reply.split())
            a['student_ms'] += t.get('student_ms', 0)
    for arm, a in sorted(by_arm.items()):
        n = max(1, a['turns'])
        print(f"{arm:10} conversations {a['conversations']}, turns {a['turns']}, replies with a question "
              f"{a['questions'] / n:.0%}, mean words {a['words'] / n:.1f}, student {a['student_ms'] / n:.0f} ms")
    if args.summary:
        return
    for convo, turns in convos.items():
        print(f'\n===== {convo} (arm: {turns[0].get("arm")})')
        for t in turns:
            reading = t.get('reading') or {}
            acts = [k for k, v in (reading.get('acts') or {}).items() if v >= 0.5]
            tone = max(reading['tone'], key=reading['tone'].get) if reading.get('tone') else '-'
            print(f"  them: {t['message'][:160]}\n        [read: {', '.join(acts) or '-'}; tone {tone}; "
                  f"notes {len(t.get('notes_after_reply', []))}]\n  her:  {t.get('reply', '')[:200]}")


if __name__ == '__main__':
    main()
