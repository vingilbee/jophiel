# Cognitive input system

This package is the successor to the experimental
`Cognitive/cognitive_nodes` code. It provides a deterministic baseline:

```text
message -> perception -> interpretation
```

The single-call experiment extends this with proposed action selection. Neither
mode is connected to the chat orchestrator yet. Core-memory influence, emotion,
and orchestration remain outside the current scope.

The authenticated `POST /api/cognitive/inspect` diagnostic endpoint runs this
pipeline without creating a conversation or invoking the chat agent. The Chat
page's **Cognitive test** panel uses it to display perception, interpretation,
signals, and runtime metrics while the integration remains experimental.

## Single-call experiment

The inspector now offers **LLM** and **Rules** modes. LLM mode predicts one
`CognitiveState` defined in `llm.py`: explicit perception, contextual
interpretation, and a proposed `respond`, `clarify`, or `delegate` action.
It uses one structured-output provider request, with retries disabled and a
30-second timeout. Set `COGNITIVE_MODEL` to override the existing `CHAT_MODEL`;
the configured `OPENAI_API_KEY` is shared. No model call occurs during import.

Send `mode: "llm"` to `POST /api/cognitive/inspect`. This mode returns `state`,
runtime attachment counts, and metrics; omitted mode retains the original rules
response. `recent_messages` should contain ordered, role-labelled user and
assistant messages. The UI supplies both roles. Counts are supplied by code;
attachment contents are not sent to the model. Optional `available_capabilities`
are hypothetical diagnostic names, not authorization. The UI supplies no
capabilities yet. Validation rejects delegation to names absent from that list,
inconsistent actions, and incomplete outputs. Failures return HTTP 502 rather
than silently substituting a rule prediction.

From `backend`, compare the reviewed cases:

```bash
.venv/bin/python -m app.ai.Cognitive.system.training.evaluate_llm
.venv/bin/python -m app.ai.Cognitive.system.training.evaluate_llm --llm --output /tmp/cognitive-comparison.json
```

The second command makes one model request per case. Scores cover intent,
clarification for rules, and action/capability selection for the LLM. Review
constraint preservation and resolved goals in the full output manually. These
cases are a regression sample, not a general accuracy estimate. Compare actual
agent outcomes and added latency against direct-agent behavior before integration.

The agent is not connected yet. A future handoff must preserve the original
message/history alongside this advisory state and use runtime capabilities and
authorization for execution.

Structured output follows the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Rules runtime API

`CognitiveInputPipeline.process()` returns a validated `CognitiveInput` with:

- `Perception`: stable question/request/direction, novelty, and ambiguity fields
  plus an expandable map of typed `PerceptionSignal` values.
- `Interpretation`: one of `question`, `request`, `inform`, `clarify`, `feedback`,
  `greeting`, or `unknown`, its confidence, alternatives, and whether more
  information is needed.

Rules processing performs no network or LLM calls. Interpretation lazily loads
an optional local classifier artifact and safely falls back to deterministic
rules when no valid artifact is configured.

## Training

Training data uses reviewed JSONL records:

```json
{"text": "Can you analyze this image?", "intent": "request"}
```

From `backend`, train and evaluate with:

```bash
python -m app.ai.Cognitive.system.training.train_intent DATA.jsonl ARTIFACT.json
python -m app.ai.Cognitive.system.training.evaluate_intent DATA.jsonl
```

The legacy corpus is not migrated automatically. Its three source files have
different lengths, its label set does not match the new contract, and several
minority classes contain too few examples for meaningful evaluation. Review and
relabel examples before creating a deployable artifact.

## Extension rules

- Keep the stable `Perception` fields small.
- Add new observations through `signals`, with a value, confidence, and source.
- Do not overwrite reserved signals produced by the perception engine.
- Keep model training offline and artifact loading lazy.
- Keep action selection separate from perception and interpretation, as a sibling state.
