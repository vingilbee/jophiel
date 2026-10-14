"""Recheck four isolated ten-turn chains; preserve all prior evaluation artifacts."""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--conditions', nargs='+', default=['standard-base-strained', 'opposing-blunt-strained'])
    parser.add_argument('--cases', nargs='+', default=['club_values', 'club_values_conversational'])
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a fresh directory')
    source = Path(__file__).resolve().parents[2] / 'docs/qa/cognitive-long-state-2026-10-13'
    spec = importlib.util.spec_from_file_location('long_fix_evaluation', source / 'evaluate.py')
    e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e)
    args.output.mkdir(parents=True)
    for name in ('source-core.json', 'opposing-character.json', 'fixtures.json', 'fixtures-conversational.json'):
        shutil.copyfile(source / name, args.output / name)
    e.OUT = args.output
    e.CASES['club_values_conversational'] = dict(id='club_values_conversational',
        family='long_closed_loop', source_mode='own_accepted_response')
    e.CORPUS['club_values_conversational'] = json.loads((source / 'fixtures-conversational.json').read_text())['turns']
    if any(c not in e.FIXTURES['conditions'] for c in args.conditions) or any(c not in e.CASES for c in args.cases):
        parser.error('unknown condition or development case')
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(e.run, condition, case, 1)
                   for condition in args.conditions for case in args.cases]
        for future in as_completed(futures):
            rows.append(future.result())
            (args.output / 'summary.json').write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
