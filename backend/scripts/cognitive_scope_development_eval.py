"""Run selected DEVELOPMENT fixtures in a fresh isolated output directory.

Reuses the root-cause harness, preserving original artifacts. Never opens the
reserved/unfamiliar fixture file. Not a frontend or persistence evaluation.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', default=['route_watch', 'route_room', 'revision_same', 'revision_absent'])
    parser.add_argument('--characters', nargs='+', choices=['standard', 'opposing'], default=['standard'])
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2] / 'docs/qa/cognitive-root-cause-2026-10-13'
    if args.output.exists():
        parser.error('output must be a new directory')
    spec = importlib.util.spec_from_file_location('root_cause_development', source / 'evaluate.py')
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    if any(case not in harness.CASES for case in args.cases):
        parser.error('only predeclared development cases are permitted')
    args.output.mkdir(parents=True)
    for name in ('source-core.json', 'opposing-character.json'):
        shutil.copyfile(source / name, args.output / name)
    harness.OUT = args.output
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(harness.run, character, case, 1)
                   for character in args.characters for case in args.cases]
        for future in as_completed(futures):
            rows.append(future.result())
            (args.output / 'summary.json').write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
