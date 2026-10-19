"""Simple cognition: generate teacher data, train the students, evaluate them.

  uv run python scripts/simple_cognition.py generate --run pilot --episodes 50
  uv run python scripts/simple_cognition.py report --run pilot
  uv run python scripts/simple_cognition.py train --run pilot
  uv run python scripts/simple_cognition.py evaluate --run pilot
  uv run python scripts/simple_cognition.py review --run pilot          # spot-check page
  uv run python scripts/simple_cognition.py import-review --run pilot --votes ~/Downloads/review_votes.json
  uv run python scripts/simple_cognition.py real --run real --conversation <uuid>   # shadow only

Output lives in var/cognitive_simple/<run>/ (gitignored with the rest of var/). Plan:
docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
ROOT = BACKEND.parent
RUNS = BACKEND / 'var' / 'cognitive_simple'


def _generate(args) -> None:
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.episode import Env, run
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.mood import MoodDynamics
    from app.ai.Cognitive.simple.scenario import make_scenarios
    from app.ai.Cognitive.simple.teacher import PROMPTS, Teacher

    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    page_path = ROOT / 'docs' / 'characters' / f'{args.character}.md'
    page = page_path.read_text()
    dynamics = MoodDynamics.load(ROOT / 'docs' / 'characters' / f'{args.character}.mood.json')
    scenarios = make_scenarios(args.episodes, seed=args.seed, min_turns=args.min_turns, max_turns=args.max_turns)
    prompts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(PROMPTS.glob('*.txt'))}
    manifest = dict(character=args.character, page_sha=hashlib.sha256(page.encode()).hexdigest()[:12],
                    model=args.model, seed=args.seed, prompts=prompts, scenarios=[s.to_dict() for s in scenarios])
    # One manifest per batch (seed), so batches made with different prompts stay distinguishable.
    (out / f'manifest-{args.seed}.json').write_text(json.dumps(manifest, indent=1))
    key = dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY')
    llm = LLM(key, model=args.model, concurrency=args.concurrency)
    env = Env(llm=llm, teacher=Teacher(llm, page, dynamics), embedding_model='text-embedding-3-small', out=out)
    asyncio.run(run(env, scenarios, parallel=args.parallel, usage_name=f'usage-{args.seed}.json'))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _experiment(args) -> None:
    """Intention teacher (intention-v1) on chosen scenarios of an earlier run, with their user cards."""
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.episode import Env
    from app.ai.Cognitive.simple.intention import IntentionTeacher, run_experiment
    from app.ai.Cognitive.simple.llm import LLM

    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    page_path = ROOT / 'docs' / 'characters' / f'{args.character}.md'
    page = page_path.read_text()
    world = ROOT / 'docs' / 'characters' / f'{args.character}.world.json'
    ids = [i.strip() for i in args.episodes.split(',') if i.strip()]
    (out / 'manifest.json').write_text(json.dumps(dict(variant=args.variant, source=args.source, episodes=ids,
                                                       character=args.character,
                                                       page_sha=hashlib.sha256(page.encode()).hexdigest()[:12]), indent=1))
    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model, concurrency=args.concurrency)
    env = Env(llm=llm, teacher=None, embedding_model='text-embedding-3-small', out=out)
    asyncio.run(run_experiment(env, IntentionTeacher(llm, page, _dynamics(args), args.variant), RUNS / args.source, ids, world,
                               parallel=args.parallel))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _ablation(args) -> None:
    """2 x 2 x 2 ablation (handoff x page x rhythm) on chosen scenarios of an earlier run."""
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.ablation import ARMS, run
    from app.ai.Cognitive.simple.episode import Env
    from app.ai.Cognitive.simple.llm import LLM

    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    chars = ROOT / 'docs' / 'characters'
    pages = {'v0.2': (chars / 'columbina-v0.2.md').read_text(), 'v0.6': (chars / 'columbina-v0.6.md').read_text()}
    ids = [i.strip() for i in args.episodes.split(',') if i.strip()]
    arms = [a for a in ARMS if not args.arms or a.name in args.arms.split(',')]
    (out / 'manifest.json').write_text(json.dumps(dict(
        design='handoff x page x rhythm', source=args.source, episodes=ids, arms=[a.name for a in arms],
        pages={k: hashlib.sha256(v.encode()).hexdigest()[:12] for k, v in pages.items()}), indent=1))
    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model, concurrency=args.concurrency)
    env = Env(llm=llm, teacher=None, embedding_model='text-embedding-3-small', out=out)
    asyncio.run(run(env, RUNS / args.source, ids, pages, _dynamics(args), chars / 'columbina.world.json', arms=arms,
                    parallel=args.parallel))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _ablation_rate(args) -> None:
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.ablation import rate
    from app.ai.Cognitive.simple.episode import Env
    from app.ai.Cognitive.simple.llm import LLM

    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model, concurrency=args.concurrency)
    asyncio.run(rate(Env(llm=llm, teacher=None, embedding_model='', out=RUNS / args.run), RUNS / args.run))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _ablation_report(args) -> None:
    from app.ai.Cognitive.simple.ablation import report
    result = report(RUNS / args.run)
    (RUNS / args.run / 'report.json').write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


def _batch(args) -> None:
    """A training batch from the frozen teacher (teacher-2026-10-19) on new scenarios."""
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.episode import Env
    from app.ai.Cognitive.simple.frozen import VERSION, fingerprint, generate
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.scenario import make_scenarios

    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    chars = ROOT / 'docs' / 'characters'
    page, world, mood = chars / 'columbina-v0.6.md', chars / 'columbina-teacher-2026-10-19.world.json', chars / 'columbina.mood.json'
    scenarios = make_scenarios(args.episodes, seed=args.seed, min_turns=8, max_turns=12,
                               situations=[x for x in args.situations.split(',') if x] or None,
                               groups=[x for x in args.groups.split(',') if x] or None)
    from app.ai.Cognitive.simple.notes import NOTES_VERSION
    (out / f'manifest-{args.seed}.json').write_text(json.dumps(dict(
        teacher_version=VERSION, notes_version=NOTES_VERSION, focus=dict(situations=args.situations, groups=args.groups), inputs=fingerprint(page, world, mood), seed=args.seed,
        scenarios=[s.to_dict() for s in scenarios]), indent=1))
    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model, concurrency=args.concurrency)
    env = Env(llm=llm, teacher=None, embedding_model='text-embedding-3-small', out=out)
    asyncio.run(generate(env, scenarios, page.read_text(), _dynamics(args), world, parallel=args.parallel))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _tension_probe(args) -> None:
    """Frozen teacher, identical inputs, tension low vs high, on blunt and neutral messages."""
    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.intention import IntentionTeacher
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.probe import pick, run

    paths = [p for src in args.sources.split(',') for p in sorted((RUNS / src).glob(args.glob))]
    items = pick(paths, args.per_class, args.seed)
    page = (ROOT / 'docs' / 'characters' / 'columbina-v0.6.md').read_text()
    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model, concurrency=args.concurrency)
    teacher = IntentionTeacher(llm, page, _dynamics(args), variant='intention-ablation')
    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(run(teacher, items, out / 'probe.json'))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print(json.dumps(llm.usage.to_dict(), indent=1))


def _real(args) -> None:
    """Shadow only: label stored copies of real conversations, read-only."""
    import uuid

    from dotenv import dotenv_values

    from app.ai.Cognitive.simple.episode import Env
    from app.ai.Cognitive.simple.llm import LLM
    from app.ai.Cognitive.simple.real import run_real
    from app.ai.Cognitive.simple.teacher import Teacher

    out = RUNS / args.run
    (out / 'episodes').mkdir(parents=True, exist_ok=True)
    llm = LLM(dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'), model=args.model)
    page = (ROOT / 'docs' / 'characters' / f'{args.character}.md').read_text()
    env = Env(llm=llm, teacher=Teacher(llm, page, _dynamics(args)), embedding_model='text-embedding-3-small', out=out)
    record = asyncio.run(run_real(env, uuid.UUID(args.conversation), limit=args.limit))
    (out / 'episodes' / f'{record["scenario"]["id"]}.json').write_text(json.dumps(record, ensure_ascii=False, indent=1))
    (out / 'usage.json').write_text(json.dumps(llm.usage.to_dict(), indent=1))
    print(f'{len(record["turns"])} turns labelled; {llm.usage.calls} calls')


def _review(args) -> None:
    from app.ai.Cognitive.simple.student.review import build
    print(build(RUNS / args.run, count=args.count, seed=args.seed))


def _import_review(args) -> None:
    from app.ai.Cognitive.simple.student.review import import_votes
    print(f'{import_votes(RUNS / args.run, Path(args.votes))} votes imported; retrain to use them')


def _report(args) -> None:
    from app.ai.Cognitive.simple.student.report import build

    result = build(RUNS / args.run)
    (RUNS / args.run / 'report.json').write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps(result, indent=1, ensure_ascii=False))


def _dynamics(args):
    from app.ai.Cognitive.simple.mood import MoodDynamics
    return MoodDynamics.load(ROOT / 'docs' / 'characters' / f'{args.character}.mood.json')


def _train(args) -> None:
    from app.ai.Cognitive.simple.student.train import train
    print(json.dumps(train(RUNS / args.run, _dynamics(args), seed=args.seed, fraction=args.fraction,
                           models_name=args.models), indent=1))


def _evaluate(args) -> None:
    from app.ai.Cognitive.simple.student.train import evaluate
    print(json.dumps(evaluate(RUNS / args.run, _dynamics(args), seed=args.seed, models_name=args.models), indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    gen = sub.add_parser('generate')
    gen.add_argument('--run', required=True)
    gen.add_argument('--episodes', type=int, default=50)
    gen.add_argument('--seed', type=int, default=1)
    gen.add_argument('--min-turns', type=int, default=8)
    gen.add_argument('--max-turns', type=int, default=12)
    gen.add_argument('--parallel', type=int, default=6, help='episodes at once')
    gen.add_argument('--concurrency', type=int, default=24, help='model calls at once')
    gen.add_argument('--model', default='gpt-6-luna')
    gen.add_argument('--character', default='columbina')
    exp = sub.add_parser('experiment', help='intention teacher on chosen scenarios of an earlier run')
    exp.add_argument('--run', required=True)
    exp.add_argument('--source', required=True, help='run whose scenarios and user cards to reuse')
    exp.add_argument('--episodes', required=True, help='comma-separated scenario ids')
    exp.add_argument('--parallel', type=int, default=10)
    exp.add_argument('--concurrency', type=int, default=40)
    exp.add_argument('--model', default='gpt-6-luna')
    exp.add_argument('--character', default='columbina')
    exp.add_argument('--variant', default='intention-v4',
                     choices=['intention-v1', 'intention-v2', 'intention-v3', 'intention-v4'])
    abl = sub.add_parser('ablation', help='handoff x page x rhythm on chosen scenarios of an earlier run')
    abl.add_argument('--run', required=True)
    abl.add_argument('--source', required=True)
    abl.add_argument('--episodes', required=True)
    abl.add_argument('--arms', default='', help='comma-separated arm names (default: all 8)')
    abl.add_argument('--parallel', type=int, default=12)
    abl.add_argument('--concurrency', type=int, default=40)
    abl.add_argument('--model', default='gpt-6-luna')
    abl.add_argument('--character', default='columbina')
    bat = sub.add_parser('batch', help='training batch from the frozen teacher on new scenarios')
    bat.add_argument('--run', required=True)
    bat.add_argument('--episodes', type=int, default=30)
    bat.add_argument('--seed', type=int, default=21)
    bat.add_argument('--parallel', type=int, default=10)
    bat.add_argument('--concurrency', type=int, default=40)
    bat.add_argument('--model', default='gpt-6-luna')
    bat.add_argument('--character', default='columbina')
    bat.add_argument('--situations', default='', help='comma-separated: focus the batch on these situations')
    bat.add_argument('--groups', default='', help='comma-separated user groups to draw from')
    tp = sub.add_parser('tension-probe', help='frozen teacher: tension low vs high on identical inputs')
    tp.add_argument('--run', required=True)
    tp.add_argument('--sources', default='teacher1,ablation-1', help='runs to draw turns from')
    tp.add_argument('--glob', default='**/*.json')
    tp.add_argument('--per-class', type=int, default=20)
    tp.add_argument('--seed', type=int, default=0)
    tp.add_argument('--model', default='gpt-6-luna')
    tp.add_argument('--concurrency', type=int, default=40)
    tp.add_argument('--character', default='columbina')
    for name in ('ablation-rate', 'ablation-report'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', required=True)
        cmd.add_argument('--model', default='gpt-6-luna')
        cmd.add_argument('--concurrency', type=int, default=40)
    real = sub.add_parser('real', help='shadow only: label a stored real conversation (read-only)')
    real.add_argument('--run', required=True)
    real.add_argument('--conversation', required=True)
    real.add_argument('--limit', type=int, default=20, help='user turns to label')
    real.add_argument('--model', default='gpt-6-luna')
    real.add_argument('--character', default='columbina')
    review = sub.add_parser('review', help='write a local spot-check page of random turns')
    review.add_argument('--run', required=True)
    review.add_argument('--count', type=int, default=40)
    review.add_argument('--seed', type=int, default=0)
    imp = sub.add_parser('import-review', help='bring exported votes into the run')
    imp.add_argument('--run', required=True)
    imp.add_argument('--votes', required=True)
    for name in ('report', 'train', 'evaluate'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', required=True)
        cmd.add_argument('--seed', type=int, default=0)
        cmd.add_argument('--character', default='columbina')
        cmd.add_argument('--models', default='models', help='model folder name inside the run')
        if name == 'train':
            cmd.add_argument('--fraction', type=float, default=1.0, help='share of training conversations (learning curve)')
    args = parser.parse_args()
    {'generate': _generate, 'experiment': _experiment, 'ablation': _ablation, 'batch': _batch, 'tension-probe': _tension_probe,
     'ablation-rate': _ablation_rate, 'ablation-report': _ablation_report, 'real': _real, 'review': _review, 'import-review': _import_review, 'report': _report, 'train': _train, 'evaluate': _evaluate}[args.command](args)


if __name__ == '__main__':
    main()
