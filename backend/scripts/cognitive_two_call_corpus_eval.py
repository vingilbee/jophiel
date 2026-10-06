"""In-process shadow evaluation of a two-call protocol on the general-conversation corpus.

Runs each conversation turn by turn with predecessor state chained, real
provider calls, and no chat delivery. The assistant turn fed into the next
capture is the shadow's own accepted wording (or a neutral placeholder when
none was accepted); this is NOT the WebSocket -> persistence -> worker path.

    COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1 \
        uv run python scripts/cognitive_two_call_corpus_eval.py --output /tmp/v1.json
"""
import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from app.core.config import get_settings
from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline, ObservationContext, observe
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from cognitive_shadow_support import make_capture
from test_cognitive_shadow_compiler import _as_predecessor

# GENERAL_CONVERSATION_TWO_CALL_EVALUATION_2026-10-05.md, same turns and grouping.
CORPUS = {
    'A': ['Hi Columbina, nice to see you. How has your day been?',
          "I've been feeling scattered lately.",
          'Mostly because I have too many small things on my mind.',
          'Can you help me think about how to make tomorrow feel less overwhelming?',
          "Actually, I don't want a schedule. I just want to talk through what matters most.",
          'The important part is making time to call my sister and finish one difficult email. What do you think?'],
    'B': ['Why do people sometimes remember embarrassing moments more vividly than ordinary ones?',
          'Could you explain that without psychology jargon and give one everyday example?',
          "Small correction: I'm asking why the memory feels vivid, not whether it is accurate."],
    'C': ['Help me brainstorm a name for a quiet neighborhood book club. It should feel warm and curious, not pretentious.',
          "I like ‘The Open Chapter,’ but make it a little more playful without sounding childish."],
    'D': ['What should I do next?'],
    'E': ['A friend keeps canceling plans but apologizes sincerely. Help me interpret a few possibilities without '
          'assuming bad intent, then suggest how I could talk to them.'],
}
PLACEHOLDER_REPLY = 'Okay.'


def run_conversation(key, turns, settings, protocol):
    from app.services.cognitive_shadow import collaboration_engine, realization_engine
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    history, ids, previous, rows = [], [], None, []
    for index, text in enumerate(turns):
        history.append(('user', text)); ids.append(str(uuid.uuid4()))
        capture = make_capture(user_ref=user, conversation_ref=conversation, contents=tuple(history), message_ids=tuple(ids),
                               previous_user_message_id=previous[1].focus_message_id if previous else None)
        model = OpenAITwoCallModel(model_id=settings.cognitive_shadow_semantic_parser_model, api_key=settings.openai_api_key,
                                   timeout_seconds=settings.cognitive_shadow_semantic_parser_timeout_seconds,
                                   max_output_tokens=settings.cognitive_shadow_orchestration_max_output_tokens)
        pipeline = build_pipeline(capture.turn_id)
        pipeline.collaboration_engine = collaboration_engine(settings)
        pipeline = install(pipeline, model, protocol=protocol)
        started = time.perf_counter()
        record = observe(capture, _as_predecessor(*previous) if previous else None,
                         ObservationContext(job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
                                            started_at='2026-10-05T00:00:00+00:00'), pipeline_factory=lambda _: pipeline)
        core_ms = (time.perf_counter() - started) * 1000
        engine = realization_engine(settings) if protocol == 'cognitive-two-call-v1' else RealizationEngineV0(None)
        started = time.perf_counter()
        realized = realize_record(record, engine=engine,
                                  configured_maximum_characters=settings.cognitive_shadow_realization_max_characters)
        realization_ms = (time.perf_counter() - started) * 1000
        spans = pipeline.two_call.recorder.spans
        final = (realized.realization_trace.final_candidate.text
                 if realized.realization_trace is not None and realized.realization_trace.final_candidate is not None else None)
        violations = sorted({v.code.value for a in (realized.realization_trace.attempts if realized.realization_trace else ())
                             if a.validation for v in a.validation.violations})
        rows.append(dict(
            turn=f'{key}{index + 1}', user=text, status=record.status.value,
            failure=record.failure.code if record.failure else None,
            plan=[m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] if record.pipeline_trace else None,
            policy_mode=(record.pipeline_trace.orchestration.calls[1].payload.get('policy_mode')
                         if record.pipeline_trace is not None else None),
            realization=realized.realization_status.value, violations=violations, final_text=final,
            core_ms=round(core_ms, 1), realization_ms=round(realization_ms, 1),
            provider=[dict(stage=s.stage, ms=round(s.elapsed_ms, 1), input=s.input_tokens, output=s.output_tokens)
                      for s in spans if s.component == 'provider_attempt'],
            components={f'{s.stage}.{s.component}': round(s.elapsed_ms, 1) for s in spans if s.component != 'provider_attempt'},
            # Local diagnosis only: raw proposals of failed turns.
            proposals=[c.model_dump(mode='json')['proposal'] for c in pipeline.two_call.captures] if record.failure else None))
        if record.status.value == 'completed':
            previous = (record, capture)
        history.append(('assistant', final or PLACEHOLDER_REPLY)); ids.append(str(uuid.uuid4()))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--conversations', default=''.join(CORPUS), help='Subset of conversation keys, e.g. AB.')
    args = parser.parse_args()
    settings = get_settings()
    protocol = settings.cognitive_shadow_two_call_protocol
    with ThreadPoolExecutor(max_workers=len(CORPUS)) as pool:
        keys = [key for key in CORPUS if key in args.conversations]
        futures = {key: pool.submit(run_conversation, key, CORPUS[key], settings, protocol) for key in keys}
        rows = [row for key in keys for row in futures[key].result()]
    Path(args.output).write_text(json.dumps(dict(protocol=protocol, rows=rows), indent=2, ensure_ascii=False))
    for row in rows:
        calls = '/'.join(str(p['ms']) for p in row['provider'])
        print(f"{row['turn']:4} {row['status']:9} {row['realization']:16} plan={row['plan']} calls={calls} "
              f"fail={row['failure']} viol={row['violations']} text={row['final_text']!r}")


if __name__ == '__main__':
    main()
