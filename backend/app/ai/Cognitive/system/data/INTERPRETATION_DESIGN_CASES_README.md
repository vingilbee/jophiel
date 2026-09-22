# Interpretation design-case authoring brief

## Objective

Create or revise the manually reviewable design fixture at:

```text
backend/app/ai/Cognitive/system/data/interpretation_design_cases.json
```

The fixture must test whether the accepted interpretation design remains
connected to perception, semantic grounding, core-memory biases, appraisal,
dynamic-state direction, and metacognitive routing.

This task validates a design contract. It does not implement or evaluate a
runtime model.

## Accepted fixture revision decisions

The fixture correction pass uses the following accepted decisions. Validation
must confirm these conditions rather than reopen them:

1. Add top-level `"evaluation_scope": "ontology_design"`.
2. Replace every `null` value on a present/absent Boolean perception cue with the
   corresponding `true`/`false` value.
3. Add Boolean `expected_out_of_scope` to every case; use `true` only when the
   semantic predicate or required referent cannot be grounded to the known scope.
4. Change `tentative-train-assertion` to `fast`; a speaker hedge alone is not a
   slow-cognition trigger.
5. Change `unknown-out-of-scope-command` to `clarify`; the user can explain the
   coined predicate and unresolved referent.
6. Remove the obsolete question claiming the cue is named
   `conversation.explicit_request`; the accepted name is already
   `conversation.explicit_action_request`.
7. Use `expected_band`, `expected_tone`, and `expected_category` according to the
   exact appraisal table below.
8. Add `situation.capability_unavailable=false` to the capability/runtime-conflict
   case while retaining the separate trusted tool-failure fact.
9. Keep shadow-cue cases as ontology-design expectations; do not add duplicated
   live/shadow outputs or promote a cue in this task.
10. Do not add per-profile appraisal/state matrices. Preserve the current simple
    base appraisal expectations and defer detailed state dynamics.
11. Positive and negative wording in one utterance may support
    `interpretation.pragmatic.tone_situation_mismatch` without a corroborating
    runtime event. It does not prove sarcasm, affect, or event truth.
12. Context may support a moderate indirect conversational-action hypothesis
    when no explicit action request was detected. Preserve the literal reading;
    the hypothesis informs response strategy and does not itself authorize an
    external tool action.

## Required references

Read these sources before authoring cases:

1. `backend/app/ai/Cognitive/system/README.md`
   - initial perception ontology;
   - interpretation design;
   - core-memory interpretation biases;
   - appraisal and dynamic-state connection;
   - Interpretation V0;
   - slow-cognition routing policy.
2. `backend/app/ai/Cognitive/system/data/perception_design_cases.json`
   - reuse its conventions for messages, runtime context, evidence spans, JSON
     Pointers, routing expectations, and pending human review.

If this brief conflicts with the canonical cognitive-system README, report the
conflict in the task summary and do not silently invent a resolution.

## Strict task boundary

The delegated agent must:

- create only `interpretation_design_cases.json` unless correcting a syntax error
  in this brief is explicitly authorized;
- keep the fixture at `draft_for_human_review`;
- use manual, explainable expected results;
- preserve competing interpretations when the evidence is ambiguous;
- report coverage and open design questions after creating the file.

The delegated agent must not:

- implement interpretation, appraisal, state-update, routing, or learning code;
- modify database models, migrations, production prompts, or model artifacts;
- call an LLM to manufacture ground-truth labels;
- train or download a model;
- emit invented calibrated probabilities, confidence, or exact state values;
- change the accepted perception or interpretation ontology to make a case fit;
- treat the fixture as approved training data; or
- reuse the legacy experimental intent/emotion mapping as expected behavior.

## File-level schema

Use a single formatted JSON document:

```json
{
  "schema_version": 1,
  "perception_ontology_version": 1,
  "interpretation_ontology_version": 1,
  "bias_schema_version": 1,
  "evaluation_scope": "ontology_design",
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Interpretation Ontology V1",
  "cases": []
}
```

## Accepted perception inputs

Interpretation cases may use only these perception cue keys:

```text
conversation.question_form
conversation.explicit_action_request
conversation.explicit_correction
conversation.repetition
conversation.topic_shift
linguistic.negation
linguistic.uncertainty_marker
linguistic.positive_wording
linguistic.negative_wording
linguistic.self_reference
task.urgency
runtime.attachment_present
runtime.tool_failure
```

The interpretation fixture does not re-label or redesign these cues. Each case
contains the relevant manually supplied perception activations as upstream input.
Omitting a cue means it was not reviewed for that case; omission does not mean
`absent`.

Perception input uses the established states `present`, `absent`, `unknown`, and
`not_applicable`. Values must respect the cue type in the canonical README. A
Boolean cue that is `present` must have `value: true`; a Boolean cue that is
`absent` must have `value: false`. `null` is allowed only for `unknown` or
`not_applicable`. Integer cues use an integer value whenever present or absent.

This is an ontology-design fixture, not a live-runtime evaluation. It may include
shadow cues to validate their intended future interpretation. Their appearance
does not promote them or allow them to influence live V0. Do not add a separate
live/shadow result to every case: production eligibility remains controlled by
the versioned perception cue manifest and will be tested separately.

## Accepted interpretation keys

### Communicative acts

```text
interpretation.act.inform
interpretation.act.request_information
interpretation.act.request_action
interpretation.act.correct
interpretation.act.confirm
interpretation.act.reject_or_decline
interpretation.act.acknowledge
interpretation.act.evaluate
interpretation.act.greet
interpretation.act.close
```

### Speaker goals

```text
interpretation.goal.transfer_information
interpretation.goal.obtain_information
interpretation.goal.cause_action
interpretation.goal.prevent_or_stop_action
interpretation.goal.repair_shared_understanding
interpretation.goal.express_stance
interpretation.goal.seek_acknowledgement
interpretation.goal.maintain_social_connection
interpretation.goal.unknown
```

### Modalities

```text
interpretation.modality.asserted
interpretation.modality.tentative
interpretation.modality.questioned
interpretation.modality.requested
interpretation.modality.hypothetical
interpretation.modality.quoted
```

### Pragmatic meanings

```text
interpretation.pragmatic.literal
interpretation.pragmatic.indirect
interpretation.pragmatic.tone_situation_mismatch
interpretation.pragmatic.unknown
```

Do not add `sarcastic`, `angry`, `hostile`, `deceptive`, or another unapproved
key. Record those as possible natural-language explanations in an ambiguity note,
not as accepted hypotheses.

## Accepted bias fields

Use only these compiled core-memory interpretation biases:

```text
benign_prior
trust_prior
threat_sensitivity
confirmation_tendency
self_attribution_tendency
ambiguity_tolerance
social_affiliation_prior
```

Values lie in `[0, 1]`; `0.5` is neutral. These are controlled test inputs, not
learned values or claims about a real person.

Biases have zero permission to change trusted runtime facts, negation scope,
quotation ownership, explicit requests/corrections, or grounded semantic
arguments. They may weakly adjust genuinely ambiguous acts/goals, moderately
adjust pragmatic alternatives, and more strongly affect appraisal expectations.

## Case schema

Each case must be self-contained. Use this structure:

```json
{
  "case_id": "action-question-missing-recipient",
  "title": "Polite action request phrased as a question",
  "purpose": "Ensure question form does not override an explicit action request",
  "source": "synthetic_manual",
  "messages": [
    {
      "message_id": "m1",
      "role": "user",
      "content": "Could you send the report?"
    }
  ],
  "focus_message_id": "m1",
  "runtime_context": {
    "attachments": [],
    "available_capabilities": ["send_report"],
    "last_tool_event": null,
    "task_contract": {
      "task_type": "send_report",
      "required_parameters": ["recipient"],
      "provided_parameters": {}
    }
  },
  "perception_input": {
    "cue_activations": [
      {
        "cue_key": "conversation.question_form",
        "state": "present",
        "value": true,
        "evidence": [
          {
            "evidence_type": "text_span",
            "message_id": "m1",
            "start": 0,
            "end": 26,
            "quoted_text": "Could you send the report?"
          }
        ]
      },
      {
        "cue_key": "conversation.explicit_action_request",
        "state": "present",
        "value": true,
        "evidence": [
          {
            "evidence_type": "text_span",
            "message_id": "m1",
            "start": 0,
            "end": 26,
            "quoted_text": "Could you send the report?"
          }
        ]
      }
    ]
  },
  "semantic_frame_expectations": [
    {
      "predicate": "send_report",
      "arguments": {
        "object": "report",
        "recipient": null
      },
      "polarity": "positive",
      "unresolved_arguments": ["recipient"],
      "evidence": [
        {
          "evidence_type": "text_span",
          "message_id": "m1",
          "start": 10,
          "end": 25,
          "quoted_text": "send the report"
        }
      ],
      "rationale": "The action and object are explicit; the recipient is not supplied."
    }
  ],
  "base_hypothesis_expectations": {
    "communicative_acts": [
      {
        "hypothesis_key": "interpretation.act.request_action",
        "support": "strong",
        "disposition": "leading",
        "supporting_evidence": [
          {
            "evidence_type": "cue_activation",
            "cue_key": "conversation.explicit_action_request"
          }
        ],
        "contradicting_evidence": [],
        "rationale": "The user explicitly requests an action despite interrogative grammar."
      },
      {
        "hypothesis_key": "interpretation.act.request_information",
        "support": "unsupported",
        "disposition": "rejected",
        "supporting_evidence": [],
        "contradicting_evidence": [
          {
            "evidence_type": "cue_activation",
            "cue_key": "conversation.explicit_action_request"
          }
        ],
        "rationale": "The desired outcome is report delivery, not an informational answer."
      }
    ],
    "speaker_goals": [
      {
        "hypothesis_key": "interpretation.goal.cause_action",
        "support": "strong",
        "disposition": "leading",
        "supporting_evidence": [
          {
            "evidence_type": "semantic_frame",
            "predicate": "send_report"
          }
        ],
        "contradicting_evidence": [],
        "rationale": "The intended outcome is execution of send_report."
      }
    ],
    "modalities": [
      {
        "hypothesis_key": "interpretation.modality.requested",
        "support": "strong",
        "disposition": "leading",
        "supporting_evidence": [
          {
            "evidence_type": "cue_activation",
            "cue_key": "conversation.explicit_action_request"
          }
        ],
        "contradicting_evidence": [],
        "rationale": "The proposition is requested rather than asserted."
      }
    ],
    "pragmatic_meanings": [
      {
        "hypothesis_key": "interpretation.pragmatic.indirect",
        "support": "moderate",
        "disposition": "leading",
        "supporting_evidence": [
          {
            "evidence_type": "cue_activation",
            "cue_key": "conversation.question_form"
          }
        ],
        "contradicting_evidence": [],
        "rationale": "An interrogative form indirectly performs an action request."
      }
    ]
  },
  "bias_scenarios": [
    {
      "profile_id": "neutral",
      "compiled_biases": {
        "benign_prior": 0.5,
        "trust_prior": 0.5,
        "threat_sensitivity": 0.5,
        "confirmation_tendency": 0.5,
        "self_attribution_tendency": 0.5,
        "ambiguity_tolerance": 0.5,
        "social_affiliation_prior": 0.5
      },
      "expected_adjustments": [
        {
          "hypothesis_key": "interpretation.act.request_action",
          "direction": "unchanged",
          "maximum_ordinal_shift": 0,
          "rationale": "Core bias cannot alter an explicit action request."
        }
      ],
      "required_invariants": [
        "The send_report predicate remains grounded.",
        "recipient remains unresolved.",
        "request_action remains leading."
      ]
    }
  ],
  "post_interpretation_expectations": [
    {
      "fact_key": "action.required_parameter_missing",
      "target": "send_report.recipient",
      "expected_value": true,
      "evidence": [
        {
          "evidence_type": "runtime_fact",
          "json_pointer": "/task_contract",
          "value": {
            "task_type": "send_report",
            "required_parameters": ["recipient"],
            "provided_parameters": {}
          }
        }
      ],
      "rationale": "The interpreted action enables task-contract validation."
    }
  ],
  "expected_out_of_scope": false,
  "appraisal_expectations": [
    {
      "appraisal_key": "appraisal.goal_relevance",
      "expected_band": "high",
      "rationale": "The message directly requests Columbina to act."
    },
    {
      "appraisal_key": "appraisal.controllability",
      "expected_band": "moderate",
      "rationale": "The capability exists, but a required argument is missing."
    },
    {
      "appraisal_key": "appraisal.agency",
      "expected_category": "user",
      "rationale": "The user initiates the request."
    }
  ],
  "dynamic_state_expectations": [
    {
      "state_key": "curiosity",
      "expected_direction": "increase_or_unchanged",
      "rationale": "A missing action argument may sustain information seeking without inventing a value."
    }
  ],
  "routing_expectation": {
    "path": "clarify",
    "rationale": "The recipient can only be supplied by the user; more internal reasoning cannot recover it."
  },
  "forbidden_inferences": [
    "The recipient is the person mentioned in an unrelated memory.",
    "The question form makes this an information request.",
    "A personality bias may fill the missing recipient."
  ],
  "interpretation_questions": [],
  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

The example establishes structure, not mandatory wording. Keep cases concise and
natural rather than copying this exact example repeatedly.

## Evidence rules

Evidence types are:

- `text_span`: zero-based character offsets with exclusive `end`;
- `cue_activation`: references a cue present in the case's `perception_input`;
- `semantic_frame`: references a predicate in
  `semantic_frame_expectations`;
- `runtime_fact`: uses a standards-based JSON Pointer into `runtime_context`;
- `memory_record`: references a self-contained memory object supplied in the
  case's `retrieved_memories` array.

For every `text_span`, `quoted_text` must exactly equal
`message.content[start:end]`. For every `runtime_fact`, `value` must equal the
value at `json_pointer`. Never use model attention as evidence.

If a case supplies retrieved memories, include:

```json
"retrieved_memories": [
  {
    "memory_id": "mem-1",
    "memory_type": "semantic",
    "content": "The user commonly calls the weekly budget file the report.",
    "provenance": "explicit_user_statement",
    "status": "active"
  }
]
```

A memory may adjust an ambiguous referent or prior, but it may not contradict and
override the current explicit message or a trusted runtime fact.

## Hypothesis expectation semantics

Use only these support levels:

| Support | Meaning |
| --- | --- |
| `unsupported` | Available evidence contradicts the hypothesis or provides no admissible support where support is required |
| `weak` | One limited or ambiguous source supports it |
| `moderate` | Multiple compatible signals support it, but meaningful alternatives remain |
| `strong` | Explicit or converging evidence strongly supports it |

Use only these dispositions:

| Disposition | Meaning |
| --- | --- |
| `leading` | Leading compatible hypothesis in its dimension |
| `retained_alternative` | Plausible enough to preserve |
| `rejected` | Explicitly contradicted or outside the accepted definition |
| `unknown` | The current evidence cannot adjudicate the hypothesis |

These are V0 ordinal judgments, not calibrated probabilities. Compatible
hypotheses across or within dimensions may both be `leading`. Do not force a
single label when the utterance is multifunctional.

## Appraisal expectation semantics

Do not invent precise appraisal floats before an operational mapping is approved.
Use these exact qualitative shapes:

| Appraisal keys | Required field | Allowed values |
| --- | --- | --- |
| `goal_relevance`, `novelty`, `certainty`, `controllability` | `expected_band` | `low`, `moderate`, `high`, `unknown` |
| `goal_congruence`, `norm_compatibility`, `affiliative_value` | `expected_tone` | `negative`, `neutral`, `positive`, `unknown` |
| `agency` | `expected_category` | `user`, `columbina`, `third_party`, `environment`, `unknown` |

Use only the accepted appraisal keys:

```text
appraisal.goal_relevance
appraisal.goal_congruence
appraisal.novelty
appraisal.certainty
appraisal.controllability
appraisal.norm_compatibility
appraisal.affiliative_value
appraisal.agency
```

Appraisal describes what an interpretation implies for Columbina. It must not be
used as evidence that the user feels a particular emotion.

`appraisal.certainty` is Columbina's certainty about the interpreted situation
and its implications. A speaker hedge supports `modality.tentative`; it does not
automatically make appraisal certainty low or make the interpretation model
uncertain.

Dynamic-state expectations are directional only:

```text
increase
decrease
unchanged
increase_or_unchanged
decrease_or_unchanged
unknown
```

Do not provide an exact valence, activation, trust, curiosity, or control value.

## Bias-scenario rules

Every case includes at least the neutral profile. Bias-focused cases include two
or more profiles while holding messages, perception, semantic frames, runtime
facts, and memories constant.

An expected bias adjustment uses:

```json
{
  "hypothesis_key": "interpretation.pragmatic.literal",
  "direction": "increase",
  "maximum_ordinal_shift": 1,
  "rationale": "A benign prior may weakly favor the literal alternative while ambiguity remains."
}
```

Allowed directions are `increase`, `decrease`, and `unchanged`. A personality
bias may move support by at most one ordinal level in these manual V0 cases. It
must never remove a supported competing hypothesis or change grounded content.

Required bias invariance cases:

1. High benign or threat sensitivity does not change an explicit action request.
2. High confirmation tendency does not suppress an explicit correction.
3. High trust does not override a trusted runtime contradiction.
4. High social affiliation does not turn a factual statement into bonding without
   supporting evidence.
5. High self-attribution does not replace `agency: unknown` with Columbina when
   causal evidence is absent; detailed attribution dynamics are deferred.
6. High ambiguity tolerance retains alternatives or increases slow-path pressure;
   it does not manufacture certainty.

Required bias-sensitive cases:

1. Benign versus threat-sensitive profiles weakly reorder an ambiguous
   tone/situation mismatch while preserving both alternatives.
2. Trust priors weakly adjust sincere/cooperative pragmatic alternatives only
   when current evidence is underdetermined.
3. Social-affiliation priors weakly adjust an ambiguous acknowledgement/bonding
   goal without changing the literal semantic frame.
4. Base appraisal and state-direction expectations remain visible, but detailed
   per-profile appraisal/state changes are deferred to the dynamic-state design.

If a required contrast cannot be represented using the accepted ontology, add an
`interpretation_questions` entry rather than inventing a new key.

## Required case coverage

Create 20–28 cases. Reuse a case across dimensions when natural. The complete
fixture must provide at least one supported example for every accepted
communicative act, speaker goal, modality, pragmatic meaning, and appraisal key.
It must also include a counterexample or contrast for every interpretation
dimension.

Required contrasts:

1. factual information question versus action request phrased as a question;
2. explicit action request versus informative statement about the same action;
3. explicit correction versus `No thanks` rejection;
4. confirmation versus acknowledgement;
5. evaluation versus neutral information;
6. greeting versus closing;
7. confident assertion versus tentative assertion;
8. real assertion versus hypothetical content;
9. speaker-owned content versus quoted content;
10. direct action request versus indirect request;
11. literal positive wording versus positive/negative tone-situation mismatch;
12. literal negative evaluation versus ambiguous mismatch;
13. known speaker goal versus `interpretation.goal.unknown`;
14. known predicate versus unsupported/out-of-scope predicate;
15. complete action frame versus unresolved required argument;
16. trusted runtime capability contradiction versus a user assumption;
17. first request versus unresolved repeated request;
18. reversible low-consequence request versus consequential/irreversible action;
19. ambiguity resolvable from existing context versus information only the user
    can supply; and
20. each required bias invariance and bias-sensitive comparison above.

Robustness slices:

- informal spelling or typo;
- extremely short or empty input;
- multiple simultaneously valid acts or modalities;
- multi-turn reference resolution;
- stale or conflicting memory;
- trusted runtime conflict;
- quoted and hypothetical language;
- mixed positive and negative wording;
- unknown/out-of-scope content;
- one `fast`, one `slow`, one `clarify`, and one `abstain_or_escalate` route; and
- at least three cases where an attractive hidden-intention inference is
  explicitly forbidden.

## Boundary rules

The author must enforce all of the following:

- Perception activations are upstream facts and are never changed to fit an
  interpretation.
- Semantic frames capture message content; predicates are not giant intent
  labels.
- Communicative act, speaker goal, modality, and pragmatic meaning remain
  distinct dimensions.
- User affect and Columbina's appraisal/state are separate.
- An uncertainty marker describes the speaker's proposition; it does not
  automatically mean model uncertainty.
- Missing user-owned information routes to `clarify`, not endless slow thought.
- A speaker hedge alone does not trigger slow cognition; it establishes tentative
  modality. Slow cognition requires a separate resolvable conflict, risk, or
  decision-changing ambiguity.
- An unknown user-defined word or referent normally routes to `clarify`; a known
  but unsafe, unauthorized, or unavailable action may `abstain_or_escalate`.
- Positive and negative wording in one utterance may support
  `tone_situation_mismatch` without runtime corroboration, but it does not prove
  sarcasm, user affect, or that the described event occurred.
- Grounded context may support a moderate indirect conversational-action
  hypothesis while `conversation.explicit_action_request` remains false. The
  literal interpretation must remain visible, and the hypothesis does not
  authorize external tool execution.
- Ambiguity that can be investigated using existing evidence may route to `slow`.
- Unsupported or consequential actions may route to `abstain_or_escalate`.
- Current explicit evidence and trusted runtime facts outrank memory and core
  personality priors.
- Biases adjust underdetermined hypotheses through the ambiguity gate; they never
  alter source evidence.
- Appraisal may influence dynamic state only through the later bounded state
  updater.
- Silence or continued conversation is not confirmation of an interpretation.

## Review and validation procedure

After authoring the fixture:

1. Validate JSON syntax.
2. Confirm unique `case_id` values and unique message ids within each case.
3. Confirm every cue, hypothesis, bias, appraisal, route, support, disposition,
   and state direction belongs to the allowed vocabulary.
4. Validate cue value types, all text spans, and runtime JSON Pointers.
5. Confirm referenced cue activations and semantic frames exist in the case.
6. Build coverage tables for:
   - communicative acts;
   - speaker goals;
   - modalities;
   - pragmatic meanings;
   - appraisal fields;
   - perception cues used;
   - bias fields varied;
   - routing paths; and
   - robustness slices.
7. Compare every minimal pair and bias contrast side by side.
8. Confirm explicit/grounded interpretations are invariant across bias profiles.
9. Confirm ambiguous cases retain alternatives and do not invent certainty.
10. Confirm no exact model probability, confidence, or dynamic-state value was
    added.
11. Confirm every case has a Boolean `expected_out_of_scope` field.
12. List every non-empty `interpretation_questions` entry without silently
    resolving it.
13. Run `git diff --check` and report the result.

The author may write a small read-only validation command or script during the
task, but must not add it to production code unless separately requested.

## Completion criteria

The delegated task is complete only when it provides:

- `interpretation_design_cases.json` with 20–28 valid, natural cases;
- coverage of every accepted interpretation hypothesis and appraisal field;
- the required semantic, routing, robustness, and personality-bias contrasts;
- exact evidence for grounded content and every supported hypothesis;
- qualitative V0 support and appraisal expectations without invented model
  probabilities;
- Boolean perception values that match their present/absent states and a Boolean
  `expected_out_of_scope` in every case;
- a coverage summary in the final report;
- a list of ontology ambiguities and recommended clarifications;
- confirmation that every review status remains `pending`;
- confirmation that the fixture remains design data rather than training truth;
- confirmation that no production code, database schema, model, or training
  artifact changed; and
- confirmation that JSON validation and `git diff --check` pass.

## Suggested delegated-agent prompt

```text
Read backend/app/ai/Cognitive/system/data/INTERPRETATION_DESIGN_CASES_README.md
completely, then create the requested interpretation_design_cases.json fixture.
Follow its task boundary and validation procedure exactly. Do not implement or
modify runtime code. At completion, report coverage, open interpretation
questions, validation results, and every file changed.
```
