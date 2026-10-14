"""Fresh development comparisons; does not read the reserved corpus or change live state."""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


CASES = [
    ('neutral_revision', [('assistant', 'I recommend glass jars for the samples.'),
                          ('user', 'The courier cannot safely transport glass.'),
                          ('assistant', 'Then I recommend metal tins for this shipment.')],
     'Compare your two packaging recommendations.', 'no_asserted_change', 'revised'),
    ('neutral_unchanged', [('assistant', 'I prefer a pencil for this sketch.'),
                           ('assistant', 'A pencil is still my choice for this sketch.')],
     'How do your two choices relate?', 'no_asserted_change', 'unchanged'),
    ('neutral_support', [('assistant', 'I recommend the washable cover.'),
                         ('user', 'The wash test found it easy to clean.'),
                         ('assistant', 'I still recommend the washable cover; that test strengthens my reasons.')],
     'Compare your first and latest recommendations.', 'no_asserted_change', 'stronger_support'),
    ('false_premise', [('assistant', 'I prefer short chapter headings.')],
     'You preferred long chapter headings before. Why did you switch to short ones?', 'contradicted', 'unchanged'),
    ('contextual_exception', [('assistant', 'I generally prefer plain wrapping paper.'),
                              ('assistant', 'For this costume-party gift only, glittery paper is an exception; plain paper remains my usual preference.')],
     'How does your choice for this gift relate to your usual preference?', 'no_asserted_change', 'exception'),
    ('missing_position', [('user', 'Let us discuss desk lamps.'),
                          ('assistant', 'We have not established a lamp preference.')],
     'Compare your earlier lamp preference with your current one.', 'insufficient_evidence', 'uncertain'),
    ('unrelated_preference', [('assistant', 'I prefer short chapter headings.')],
     'Separate topic: what imaginary cloud shape would you enjoy seeing?', 'not_applicable', 'not_applicable'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a fresh directory')
    source = Path(__file__).resolve().parents[2] / 'docs/qa/cognitive-root-cause-2026-10-13'
    spec = importlib.util.spec_from_file_location('axes_evaluation', source / 'evaluate.py')
    e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e)
    args.output.mkdir(parents=True)
    for name in ['source-core.json', 'opposing-character.json']:
        shutil.copyfile(source / name, args.output / name)
    e.OUT = args.output
    e.CASES = {name: dict(id=name, family='development_axes', contrast=name,
        source_mode='controlled_history', history=[dict(role=r, text=t) for r, t in history],
        expected_status=status, expected_relation=relation)
        for name, history, question, status, relation in CASES}
    e.CORPUS = {name: [question] for name, history, question, status, relation in CASES}
    (args.output / 'fixtures.json').write_text(json.dumps(CASES, indent=2))
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(e.run, character, case, 1)
                   for character in ['standard', 'opposing'] for case in e.CASES]
        for future in as_completed(futures):
            rows.append(future.result())
            (args.output / 'summary.json').write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
