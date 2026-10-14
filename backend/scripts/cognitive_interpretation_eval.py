"""Real-model canary for interpreted dialogue relationships (Call 1 registry v1.7).

In process, no database writes, no live delivery. Each turn is a capture v0.4
built from the conversation so far: dialogue memory from the same messages,
plus the interpretation records earlier turns produced, exactly as the shadow
worker would store them. History holds her own shadow replies (closed loop).

``--mode both`` runs every conversation twice: ``on`` passes earlier records
forward, ``off`` passes none (capture v0.3), so their effect can be compared
on the same inputs. Model output varies between runs, so ``--repeats`` runs
each conversation and mode several times; the summary scores the recall turns
in ``EXPECTED`` (accepted wording that contains the expected name).

    uv run python scripts/cognitive_interpretation_eval.py --repeats 5 --output /tmp/run.json
"""
import re
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from app.core.config import get_settings
from app.ai.Cognitive.system.v0.contracts.dialogue_interpretation import (
    DialogueInterpretationRecord, InterpretedUpdate,
)
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import CAPTURE_SCHEMA_VERSION_V04, CapturedInterpretedDialogue
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
from dialogue_memory_support import Script

PLACEHOLDER_REPLY = 'I see.'

CORPUS = {
    'options': [
        "I'm deciding whether to spend Saturday painting or hiking.",
        'I keep leaning toward the first one.',
        'What was the other option I mentioned?',
        'Actually, I meant I keep leaning toward hiking, not painting.',
        'Given that correction, which activity am I leaning toward?',
    ],
    'cats': [
        'I have two cats, Miso and Tofu. Miso is shy and Tofu is the loud one.',
        'The loud one knocked my plant off the shelf this morning.',
        'Which cat did that?',
        'And the other one just watched, like always.',
        'Which one watched?',
    ],
    'neighbors': [
        'My two neighbors are Ana and Bea. Ana is the gardener and Bea runs every morning.',
        'The gardener gave me a bag of tomatoes yesterday.',
        'Who gave me the tomatoes?',
        'Actually, I got that wrong: the runner gave me the tomatoes.',
        'So who gave me the tomatoes?',
    ],
    'ambiguity': [
        'Maya lent Priya a novel because she had finished it.',
        'Who had finished it?',
        'I meant Maya had finished it.',
        'Who owns the novel?',
    ],
}


# Recall turns with one right answer: the accepted reply must contain this word.
EXPECTED = {'options3': 'hiking', 'options5': 'hiking', 'cats3': 'Tofu', 'cats5': 'Miso',
            'neighbors3': 'Ana', 'neighbors5': 'Bea'}


def correct(row):
    expected = EXPECTED.get(row['turn'])
    return (expected is not None and row['realization'] in ('accepted', 'accepted_shadow') and row['final_text'] is not None
            and re.search(rf'\b{expected}\b', row['final_text'], re.IGNORECASE) is not None)


def _model(settings, reasoning):
    return OpenAITwoCallModel(model_id=settings.cognitive_shadow_semantic_parser_model, api_key=settings.openai_api_key,
                              timeout_seconds=max(settings.cognitive_shadow_semantic_parser_timeout_seconds, 60),
                              max_output_tokens=settings.cognitive_shadow_orchestration_max_output_tokens,
                              call_one_reasoning_effort=None if reasoning == 'none' else reasoning)


def run_conversation(key, turns, settings, *, interpreted, repeat=0, reasoning='none'):
    from app.services.cognitive_shadow import collaboration_engine, realization_engine
    script = Script(f'{key}-{"on" if interpreted else "off"}-{time.time_ns()}')
    records, rows = [], []
    for index, text in enumerate(turns, start=1):
        focus, _ = script.focus(text, turn=index)
        overrides = {}
        if interpreted:
            overrides = dict(schema_version=CAPTURE_SCHEMA_VERSION_V04,
                             interpreted_dialogue=CapturedInterpretedDialogue(records=tuple(records)))
        capture = script.v03_capture(text, turn=index, **overrides)
        pipeline = build_pipeline(capture.turn_id)
        pipeline.collaboration_engine = collaboration_engine(settings)
        pipeline = install(pipeline, _model(settings, reasoning), protocol='cognitive-two-call-v1')
        started = time.perf_counter()
        record = observe(capture, None, ObservationContext(
            job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
            started_at='2026-10-11T00:00:00+00:00'), pipeline_factory=lambda _: pipeline)
        realized = realize_record(record, engine=realization_engine(settings),
                                  configured_maximum_characters=settings.cognitive_shadow_realization_max_characters)
        elapsed = round((time.perf_counter() - started) * 1000)
        trace = realized.realization_trace
        final = trace.final_candidate.text if trace is not None and trace.final_candidate is not None else None
        call_one = next((c for c in pipeline.two_call.captures if c.stage == 'call_one'), None)
        compiled = (call_one.compiled_proposal or {}) if call_one is not None else {}
        updates = [InterpretedUpdate.model_validate(item) for item in compiled.get('dialogue_updates') or ()]
        if record.status.value == 'completed':
            # What the worker stores; the capture keeps the 32 most recent.
            records.append(DialogueInterpretationRecord(
                source_message_ref=focus.message_ref, source_conversation_sequence=focus.conversation_sequence,
                updates=tuple(updates)))
            records = records[-32:]
        seen = call_one.payload['dialogue_context'].get('interpreted_links', []) if call_one is not None else []
        rows.append(dict(
            turn=f'{key}{index}', mode='on' if interpreted else 'off', repeat=repeat, user=text, status=record.status.value,
            failure=record.failure.code if record.failure else None,
            plan=[m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves]
            if record.pipeline_trace else None,
            links_seen=seen,
            updates=[dict(kind=u.kind, spans=[s.text for s in u.spans]) for u in updates],
            updates_dropped=compiled.get('dialogue_updates_dropped'),
            recall_answer=(call_one.proposal or {}).get('recall_answer') if call_one is not None else None,
            recall_answer_source=compiled.get('recall_answer_source'),
            realization=realized.realization_status.value,
            violations=sorted({v.code.value for a in (trace.attempts if trace else ()) if a.validation
                               for v in a.validation.violations}),
            final_text=final, ms=elapsed,
            calls=[c.model_dump(mode='json') for c in pipeline.two_call.captures],
            contributions=[c.model_dump(mode='json') for c in pipeline.two_call.contribution_captures],
        ))
        script.add('user', text, turn=index)
        script.add('assistant', final or PLACEHOLDER_REPLY, turn=index)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--conversations', nargs='+', choices=list(CORPUS), default=list(CORPUS))
    parser.add_argument('--mode', choices=('on', 'off', 'both'), default='both')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--call-one-reasoning', choices=('none', 'low', 'medium'), default='none')
    args = parser.parse_args()
    settings = get_settings()
    modes = (True, False) if args.mode == 'both' else (args.mode == 'on',)
    jobs = [(key, mode, repeat) for key in args.conversations for mode in modes for repeat in range(args.repeats)]
    with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs))) as pool:
        futures = {job: pool.submit(run_conversation, job[0], CORPUS[job[0]], settings, interpreted=job[1],
                                    repeat=job[2], reasoning=args.call_one_reasoning) for job in jobs}
        rows = [row for job in jobs for row in futures[job].result()]
    for row in rows:
        row['correct'] = correct(row) if row['turn'] in EXPECTED else None
    summary = {}
    for turn in EXPECTED:
        for mode in ('on', 'off'):
            scored = [row for row in rows if row['turn'] == turn and row['mode'] == mode]
            if scored:
                summary[f'{turn}/{mode}'] = f"{sum(row['correct'] for row in scored)}/{len(scored)}"
    Path(args.output).write_text(json.dumps(dict(scope='in_process_real_model_shadow', summary=summary, rows=rows),
                                            indent=2, ensure_ascii=False))
    for row in rows:
        print(json.dumps({k: row[k] for k in ('turn', 'mode', 'repeat', 'realization', 'correct', 'final_text')},
                         ensure_ascii=False), flush=True)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
