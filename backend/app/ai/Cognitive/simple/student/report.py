"""Dataset report for a teacher run: coverage, acceptance, distributions, gaps, the history gap."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..contracts import ACTS, FEELINGS, GROUP_LABELS, SCALE
from .data import examples_from, load_episodes

_CHECKS = ('in_character', 'human_like', 'follows_inputs', 'older_messages_complete')


def _accepted(attempts):
    return attempts[-1] if attempts and attempts[-1].get('accepted') else None


def build(run_dir: Path) -> dict[str, Any]:
    episodes = load_episodes(run_dir)
    examples = examples_from(episodes)
    turns = [t for ep in episodes for t in ep['turns']]
    attempts = [a for t in turns for sample in t['samples'] for a in sample]
    samples = [s for t in turns for s in t['samples']]
    accepted = [_accepted(s) for s in samples]
    rejected = [a for a in attempts if not a.get('accepted')]
    failed_checks = Counter(c for a in rejected for c in _CHECKS if a.get('critic', {}).get(c) is False)
    errors = sum(1 for a in rejected if a.get('critic', {}).get('verdict') == 'error')

    main_moves = Counter(e.decide['main'] for e in examples if e.kind == 'main')
    second_moves = Counter(e.decide['second'] for e in examples if e.kind == 'main')
    acts = Counter(a for e in examples if e.kind == 'main' for a in ACTS if e.understand['acts'][a])
    tones = Counter(e.understand['tone'] for e in examples if e.kind == 'main')
    feelings = {f: round(sum(SCALE[e.understand['feelings'][f]] for e in examples if e.kind == 'main')
                         / max(1, sum(e.kind == 'main' for e in examples)), 3) for f in FEELINGS}
    wanted = Counter(w.strip().lower() for t in turns for s in t['samples'] for a in s
                     if a.get('accepted') for w in a['decide'].get('wanted_not_available', []))

    older_samples = [e for e in examples if 'older' in e.tags]
    uncovered = [e for e in examples if 'uncovered' in e.tags]
    turns_with_older_history = sum(1 for t in turns if t['bundle']['window'] and t['bundle']['window'][0]['turn'] > 1)

    # The real gap: bounded teacher vs full teacher, on the turns where it ran.
    gap = Counter()
    for t in turns:
        if not t.get('bounded'):
            continue
        full = [s['decide']['main'] for s in map(_accepted, t['samples']) if s]
        bounded = [s['decide']['main'] for s in map(_accepted, t['bounded']) if s]
        if not full or not bounded:
            continue
        older = bool(t['bundle']['window']) and t['bundle']['window'][0]['turn'] > 1
        key = 'older_history' if older else 'all_in_window'
        for b in bounded:
            gap[f'{key}_bounded_matches_full'] += sum(m == b for m in full) / len(full)
            gap[f'{key}_n'] += 1
        for i, m in enumerate(full):
            others = full[:i] + full[i + 1:]
            if others:
                gap[f'{key}_full_pairwise'] += sum(o == m for o in others) / len(others)
                gap[f'{key}_full_n'] += 1
    gap_summary = {}
    for key in ('older_history', 'all_in_window'):
        if gap[f'{key}_n']:
            gap_summary[key] = dict(
                bounded_vs_full=round(gap[f'{key}_bounded_matches_full'] / gap[f'{key}_n'], 3),
                full_vs_full=round(gap[f'{key}_full_pairwise'] / max(1, gap[f'{key}_full_n']), 3),
                bounded_samples=gap[f'{key}_n'])

    replies = [len(re.findall(r'\w+', t.get('reply') or '')) for t in turns if t.get('reply')]
    usage = {}
    for path in sorted(run_dir.glob('usage*.json')):
        data = json.loads(path.read_text())
        for key in ('calls', 'failures', 'prompt_tokens', 'completion_tokens', 'seconds'):
            usage[key] = round(usage.get(key, 0) + data.get(key, 0), 1)
    batches = Counter(ep['scenario']['id'].split('-')[0] for ep in episodes)
    return dict(
        episodes=len(episodes), turns=len(turns),
        stopped_early=sum(1 for ep in episodes if ep.get('stopped')),
        families=len({ep['scenario']['family'] for ep in episodes}),
        user_types=len({ep['scenario']['user_type'] for ep in episodes}),
        samples=len(samples), accepted=sum(1 for a in accepted if a), attempts=len(attempts),
        first_try_accept=round(sum(1 for s in samples if s and s[0].get('accepted')) / max(1, len(samples)), 3),
        dropped_samples=sum(1 for a in accepted if not a), noisy_turns=sum(1 for t in turns if t.get('noisy')),
        rejections_by_check=dict(failed_checks), call_errors=errors,
        training_examples=dict(total=len(examples), main=sum(e.kind == 'main' for e in examples),
                               replay=sum(e.kind == 'replay' for e in examples)),
        main_moves={GROUP_LABELS[k]: v for k, v in main_moves.most_common()},
        second_moves={(GROUP_LABELS.get(k, k)): v for k, v in second_moves.most_common()},
        acts=dict(acts.most_common()), tones=dict(tones.most_common()), mean_feeling=feelings,
        wanted_not_available=dict(wanted.most_common(20)),
        history=dict(turns_with_history_beyond_window=turns_with_older_history,
                     samples_reporting_older_use=len(older_samples), samples_older_use_not_in_notes=len(uncovered),
                     bounded_teacher=gap_summary),
        reply_words=dict(mean=round(sum(replies) / max(1, len(replies)), 1), max=max(replies, default=0)),
        usage=usage or None, batches=dict(batches),
    )
