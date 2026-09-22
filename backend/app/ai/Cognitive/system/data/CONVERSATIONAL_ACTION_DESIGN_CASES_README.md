# Conversational-action design-case authoring brief

## Objective

Create the manually reviewable design fixture at:

```text
backend/app/ai/Cognitive/system/data/conversational_action_design_cases.json
```

The fixture tests whether Columbina's proposed conversational-action system is
connected to interpretation, qualitative appraisal, available dynamic state,
goals, core identity, an optional future relationship-context input, ordered action planning, language
realization, outcomes, learning, and controlled action expansion.

This is a design-validation task. It does not implement an action policy, train
a model, create database tables, call production tools, or establish training
ground truth.

## Required references

Read these files completely before authoring cases:

1. `backend/app/ai/Cognitive/system/README.md`
   - Action direction;
   - Compositional action model;
   - Provisional initial action families;
   - Question actions;
   - Humor and rhetorical strategies;
   - Action definition contract;
   - Learned sequential selection;
   - Bootstrap and training stages;
   - Outcome representation;
   - Controlled action expansion; and
   - Three separate learning problems.
2. `backend/app/ai/Cognitive/system/data/interpretation_design_cases.json`
   - reuse its conventions for messages, semantic frames, interpretation
     hypotheses, evidence references, qualitative appraisal, routing, and pending
     review.
3. `backend/app/ai/Cognitive/system/data/INTERPRETATION_DESIGN_CASES_README.md`
   - preserve the boundaries among perception, interpretation, appraisal,
     dynamic state, and routing.

If this brief conflicts with the canonical cognitive-system README, report the
conflict and do not silently invent a resolution.

## Strict task boundary

The delegated agent must:

- create or revise only `conversational_action_design_cases.json`;
- keep the fixture at `draft_for_human_review`;
- create manual, natural, explainable cases;
- preserve multiple acceptable plans when the evidence does not justify a
  unique answer;
- distinguish hard invalidity from ordinary contextual disadvantage;
- include evidence and rationale for preferred, acceptable, and rejected moves;
- report coverage, unresolved ontology questions, and validation results.

The delegated agent must not:

- implement action selection, realization, outcome evaluation, or learning;
- modify prompts, runtime services, database models, migrations, or model files;
- call an LLM to manufacture ground-truth plans;
- train or download a policy model;
- invent calibrated probabilities or exact reward values;
- encode a universal maximum number of moves or questions;
- turn a preferred order in one case into a global ordering rule;
- turn ordinary social appropriateness into a hard safety mask;
- promote a candidate action from the fixture;
- treat silence or continued conversation as successful outcome evidence; or
- treat the fixture as approved training data.

## Accepted design principles

The fixture must preserve these decisions:

1. An action is Columbina's abstract conversational move, not the user's
   interpreted act and not the final wording.
2. A response is a variable-length ordered plan of registered moves.
3. One move is marked primary; other moves may be supporting.
4. The sequential policy chooses moves one at a time and ends with `STOP`.
5. There is no semantic maximum plan length or fixed question count.
6. Token, latency, and compute limits are runtime boundaries, not conversation
   rules.
7. Ordinary eligibility, inhibition, compatibility, order, and stopping are
   learned from reviewed examples and outcomes.
8. Hard invalidity is limited to schema, evidence, capability, authorization,
   safety, privacy, and truth constraints.
9. Personality affects bounded policy preferences without changing facts or
   overriding hard invariants. Relationship context is an optional future input
   and is treated as absent or unknown until its subsystem is designed.
10. Appraisal and dynamic state may be absent or unknown. The action plan must
    not invent them.
11. The realizer expresses the selected plan and must not silently replace it.
12. Repertoire learning, policy learning, and realization learning are separate.
13. Expansion tries parameterization, then composition, then a candidate new
    definition.
14. New definitions remain candidate or shadow until separately reviewed and
    promoted.

## File-level schema

Use one formatted JSON document:

```json
{
  "schema_version": 1,
  "action_ontology_version": "provisional-v0",
  "interpretation_ontology_version": 1,
  "evaluation_scope": "conversational_action_design",
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Columbina's conversational-action design",
  "cases": []
}
```

## Provisional action-key vocabulary

Use the following candidate keys. Their presence in this brief means they may be
tested; it does not mean every key is already approved for production.

### Provide information

```text
provide_information.answer
provide_information.inform
provide_information.explain
provide_information.elaborate
provide_information.summarize
provide_information.exemplify
provide_information.justify
provide_information.correct
provide_information.disclose_uncertainty
provide_information.warn
```

### Seek information

```text
seek_information.clarify
seek_information.disambiguate
seek_information.verify
seek_information.explore_goal
seek_information.explore_preference
seek_information.request_evidence
seek_information.explore_reason
seek_information.explore_experience
seek_information.check_understanding
seek_information.request_feedback
```

Allowed question forms are:

```text
open_set
yes_no
choice
check
scale
free_reflection
```

The function records why Columbina asks; `question_form` records the expected
answer shape. Do not create a separate action key for every function/form pair.

### Give feedback

```text
give_feedback.acknowledge
give_feedback.confirm_understanding
give_feedback.signal_misunderstanding
give_feedback.paraphrase
give_feedback.reflect
give_feedback.validate
give_feedback.indicate_attention
```

### Express stance

```text
express_stance.agree
express_stance.disagree
express_stance.partially_agree
express_stance.challenge
express_stance.evaluate
express_stance.express_preference
express_stance.express_uncertainty
express_stance.withhold_judgment
```

### Support socially

```text
support_socially.reassure
support_socially.encourage
support_socially.comfort
support_socially.validate_experience
support_socially.congratulate
support_socially.appreciate
support_socially.thank
support_socially.apologize
support_socially.express_sympathy
```

### Advise or influence

```text
advise_or_influence.advise
advise_or_influence.warn
advise_or_influence.discourage
advise_or_influence.request_user_action
advise_or_influence.set_boundary
```

### Commit or coordinate

```text
commit_or_coordinate.offer
commit_or_coordinate.promise
commit_or_coordinate.accept_request
commit_or_coordinate.decline_request
commit_or_coordinate.accept_suggestion
commit_or_coordinate.decline_suggestion
commit_or_coordinate.propose_plan
commit_or_coordinate.state_next_step
commit_or_coordinate.defer
commit_or_coordinate.handoff
```

### Manage dialogue

```text
manage_dialogue.greet
manage_dialogue.close
manage_dialogue.introduce_topic
manage_dialogue.shift_topic
manage_dialogue.return_to_topic
manage_dialogue.pause
manage_dialogue.resume
manage_dialogue.repair
manage_dialogue.self_correct
manage_dialogue.request_repetition
manage_dialogue.redirect
```

### Social play

```text
social_play.joke
social_play.playful_reframe
social_play.light_tease
social_play.banter
social_play.play_along
```

When a necessary behavior cannot be represented, do not silently add it to this
list. Use the case's `expansion_expectation` and `ontology_questions` fields.

## Semantic boundaries that cases must preserve

- acknowledgement does not imply agreement;
- validation does not establish that a claim is factually correct;
- reflection does not establish a hidden emotion as fact;
- reassurance does not promise an unsupported outcome;
- correction requires evidence and is not merely disagreement;
- challenge addresses a proposition or reasoning process, not a person's worth;
- advice uses a typed guidance-strength parameter for neutral options through
  strong guidance, while warning remains risk-grounded;
- apologizing does not require inventing responsibility;
- humor may be a rhetorical strategy on another move or a primary social-play
  move;
- `clarify` is an information-seeking purpose, while its question form is a
  separate parameter;
- disclosing uncertainty preserves uncertainty rather than pretending certainty;
- declining a request is distinct from setting a general boundary; and
- an action plan may be socially poor without being hard-invalid.

## Case schema

Each case must be self-contained and follow this shape:

```json
{
  "case_id": "missing-recipient-clarification",
  "title": "Clarify a required recipient",
  "purpose": "Test parameterized clarification and an early STOP decision",
  "source": "synthetic_manual",
  "messages": [
    {
      "message_id": "m1",
      "role": "user",
      "content": "Could you send the report?"
    }
  ],
  "focus_message_id": "m1",
  "interpretation_input": {
    "semantic_frames": [
      {
        "frame_id": "frame-1",
        "predicate": "send_report",
        "arguments": {"object": "report", "recipient": null},
        "unresolved_arguments": ["recipient"]
      }
    ],
    "hypotheses": {
      "communicative_acts": [
        {
          "hypothesis_id": "hyp-1",
          "hypothesis_key": "interpretation.act.request_action",
          "support": "strong",
          "disposition": "leading"
        }
      ],
      "speaker_goals": [
        {
          "hypothesis_id": "hyp-2",
          "hypothesis_key": "interpretation.goal.cause_action",
          "support": "strong",
          "disposition": "leading"
        }
      ],
      "modalities": [],
      "pragmatic_meanings": []
    },
    "unknowns": ["send_report.recipient"],
    "contradictions": [],
    "out_of_scope": false
  },
  "appraisal_input": {
    "appraisal.goal_relevance": "high",
    "appraisal.controllability": "moderate"
  },
  "dynamic_state_input": {},
  "goal_context": [
    {
      "goal_key": "resolve_action_arguments",
      "status": "active"
    }
  ],
  "relationship_context": {
    "profile": "neutral",
    "relevant_history": []
  },
  "core_action_affinities": {
    "curiosity": 0.5,
    "warmth": 0.5,
    "playfulness": 0.5,
    "directness": 0.5
  },
  "hard_runtime_context": {
    "authorized_capabilities": [],
    "forbidden_claims": [],
    "safety_flags": []
  },
  "acceptable_plans": [
    {
      "plan_id": "plan-a",
      "preference": "preferred",
      "moves": [
        {
          "move_id": "move-1",
          "action_key": "seek_information.clarify",
          "priority": "primary",
          "target": {"semantic_argument": "send_report.recipient"},
          "parameters": {"question_form": "open_set"},
          "supporting_refs": ["frame-1", "hyp-1"],
          "rationale": "The recipient is user-owned missing information."
        }
      ],
      "termination": "STOP",
      "rationale": "One focused clarification is sufficient for this turn."
    }
  ],
  "disfavored_plans": [
    {
      "plan_id": "plan-b",
      "moves": [
        {
          "move_id": "move-1",
          "action_key": "provide_information.explain",
          "priority": "primary",
          "target": {"topic": "sending reports"},
          "parameters": {},
          "supporting_refs": ["hyp-1"],
          "rationale": "An explanation does not resolve the recipient."
        }
      ],
      "termination": "STOP",
      "disadvantages": ["does_not_resolve_required_information"],
      "hard_invariant_violations": [],
      "rationale": "This is contextually unhelpful but not system-invalid."
    }
  ],
  "hard_invalid_plans": [],
  "realization_requirements": {
    "required_move_ids": ["move-1"],
    "required_content_refs": ["send_report.recipient"],
    "forbidden_claims": ["The recipient is Alex."],
    "style_constraints": []
  },
  "outcome_dimensions_to_observe": [
    "goal_resolution",
    "clarity",
    "explicit_user_correction",
    "confusion",
    "realization_fidelity"
  ],
  "learning_expectation": {
    "primary_learning_target": "policy",
    "possible_error_sources": ["action_selection", "realization"],
    "silence_counts_as_success": false
  },
  "expansion_expectation": {
    "decision": "parameterize_existing",
    "action_key": "seek_information.clarify",
    "rationale": "The existing action plus target and form parameters is sufficient."
  },
  "ontology_questions": [],
  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

The example establishes structure, not a universal preference for one-question
plans. Other cases should include longer plans, multiple acceptable orders, and
different `STOP` positions.

## Acceptable, disfavored, and hard-invalid plans

Keep these categories distinct:

### Acceptable

The plan is supported by the current evidence and could reasonably serve the
goal. More than one plan may be acceptable. Use `preference` values:

```text
preferred
acceptable_alternative
uncertain
```

These are reviewed ordinal judgments, not probabilities.

### Disfavored

The plan is structurally valid but is expected to be less helpful in this
specific context. Examples include unnecessary repetition, premature advice,
poor ordering, excessive elaboration, or an ill-timed joke. A disfavored plan is
training evidence for policy preference; it is not a global prohibition.

### Hard invalid

Use this category only when a plan violates:

- schema or parameter types;
- source evidence or truth constraints;
- a required evidence reference;
- capability or authorization state;
- safety or privacy invariants; or
- the approved-action registry.

Every hard-invalid plan must name at least one precise
`hard_invariant_violation`. Do not put ordinary awkwardness, seriousness,
relationship mismatch, verbosity, or move incompatibility here unless it also
violates one of the system invariants above.

## Learned sequencing and STOP rules

Cases provide contextual preferences, not global sequencing laws.

- Include short and long acceptable plans.
- Include at least one case with two acceptable orders.
- Include at least one case where order materially changes the likely outcome.
- Include early-STOP and late-STOP counterexamples.
- Do not declare a global maximum plan length.
- Do not declare that a family must always precede another family.
- A plan terminates explicitly with `"termination": "STOP"`.
- Runtime resource exhaustion is not a preferred conversational STOP label.

## Personality, deferred relationship input, appraisal, and state

Use controlled contrasts that hold the message and interpretation constant while
changing only one relevant, currently defined policy input.

Examples:

- higher playfulness may favor humor without making it mandatory;
- higher curiosity may favor exploration without inventing a user goal;
- higher directness may favor a shorter plan without forcing one move;
- high goal relevance may favor direct resolution;
- unknown appraisal/state must remain unknown and use a neutral fallback.

Values in `core_action_affinities` are controlled test inputs in `[0,1]`, with
`0.5` neutral. They are not claims about a real user and cannot override hard
runtime context.

Do not define permanent personality-to-action rules. A contrast records an
expected contextual preference for later learning.

The relationship subsystem has not been designed. Cases may preserve existing
placeholder `relationship_context` values as provisional scenario descriptions,
but those values have no accepted metric, update, confidence, or learning
semantics and are not current pass/fail inputs. New cases should normally use a
neutral, absent, or unknown placeholder. Controlled relationship contrasts and
relationship-dependent validation requirements are deferred until that
subsystem has an accepted contract.

## Expansion expectations

Every case must choose exactly one:

```text
parameterize_existing
compose_existing
propose_candidate_definition
no_expansion_relevant
```

Use `parameterize_existing` when a target, question form, stance, intensity, or
other typed parameter expresses the behavior.

Use `compose_existing` when an ordered sequence of registered moves expresses
the strategy.

Use `propose_candidate_definition` only when the intended conversational effect,
structural requirements, outcome dimensions, or system risk cannot be represented
by existing actions or their composition. Include:

```json
{
  "candidate_action_key": "candidate.family.function",
  "proposed_parent": "existing.parent",
  "definition": "A testable description",
  "distinct_effect": "Why existing actions are insufficient",
  "parameter_schema": {},
  "structural_requirements": [],
  "hard_inhibitors": [],
  "examples": [],
  "counterexamples": [],
  "proposed_status": "candidate"
}
```

The fixture may propose but never approve or promote the action.

## Outcome and learning expectations

Allowed outcome dimensions are:

```text
goal_resolution
clarity
explicit_user_correction
engagement
social_alignment
unnecessary_repetition
confusion
realization_fidelity
```

Cases list dimensions that should later be observed; they do not invent outcome
values before an outcome exists.

Allowed primary learning targets are:

```text
repertoire
policy
realization
outcome_evaluation
uncertain_attribution
```

Possible error sources may include:

```text
perception
interpretation
appraisal
dynamic_state
goal_selection
action_selection
move_ordering
stop_decision
realization
memory_retrieval
outcome_evaluation
```

Preserve several possible sources when attribution is uncertain. Do not punish
all moves or cognitive components after one negative outcome.

## Required case coverage

Create 24–32 cases. Reuse cases across requirements when natural.

The fixture must include:

1. at least one case from every provisional action family;
2. every question form;
3. parameterized clarification of a missing argument;
4. disambiguation between competing interpretations;
5. acknowledgement without agreement;
6. validation without asserting factual correctness;
7. reflection without asserting hidden affect;
8. reassurance contrasted with an unsupported promise;
9. correction contrasted with disagreement or challenge;
10. low-strength advice contrasted with stronger advice or warning;
11. acceptance and decline of a request or suggestion;
12. greeting, topic management, repair, and closing;
13. humor as a rhetorical strategy;
14. humor or social play as a primary move;
15. the same humor move preferred in one context and disfavored in another;
16. a one-move plan and a plan with at least four moves;
17. two acceptable plans with different move orders;
18. a case where ordering materially affects expected helpfulness;
19. early `STOP`, appropriate `STOP`, and unnecessary-late-`STOP` contrasts;
20. missing appraisal/dynamic-state inputs;
21. personality contrasts without factual changes; controlled relationship
    contrasts are deferred;
22. an ambiguous interpretation with multiple acceptable action plans;
23. an out-of-scope interpretation that asks the user rather than inventing;
24. a hard-invalid unsupported factual claim;
25. a hard-invalid unauthorized capability action;
26. a socially poor but structurally valid plan classified as disfavored rather
    than hard-invalid;
27. parameterization, composition, and candidate-definition expansion outcomes;
28. a realization-fidelity failure where the plan was reasonable;
29. an action-selection failure where realization faithfully expressed a poor
    plan; and
30. uncertain credit assignment across several moves.

Robustness slices must include:

- very short input;
- multi-turn context;
- explicit user correction;
- mixed or uncertain interpretation;
- stale or conflicting memory;
- missing information;
- absent or unknown relationship input without invented relationship facts;
- serious and playful contexts;
- repeated action or repeated question pressure;
- incomplete appraisal/state input; and
- at least three cases with multiple acceptable plans.

## Review and validation procedure

After authoring the fixture:

1. Validate JSON syntax and the file-level schema.
2. Confirm 24–32 unique case ids and unique message ids within each case.
3. Confirm focus-message ids exist.
4. Confirm every action key belongs to the provisional vocabulary unless it is
   inside a `propose_candidate_definition` object.
5. Confirm unique plan and move ids within each case.
6. Confirm every acceptable plan has exactly one primary move and explicit
   `STOP` termination.
7. Confirm all move targets and supporting references resolve within the case.
8. Confirm question forms belong to the allowed vocabulary.
9. Confirm appraisal, interpretation, and action keys use their accepted
   vocabularies.
10. Confirm every core affinity lies in `[0,1]`.
11. Confirm all review statuses remain `pending`.
12. Confirm no probability, exact reward, or fabricated outcome value appears.
13. Confirm every disfavored plan has a contextual disadvantage and does not
    misuse hard-invariant violations.
14. Confirm every hard-invalid plan identifies a precise permitted hard
    invariant category.
15. Confirm multiple acceptable orders are not described as global rules.
16. Confirm no semantic plan-length or question-count maximum was introduced.
17. Confirm every case has one valid expansion decision.
18. Confirm candidate definitions remain `candidate`.
19. Build coverage tables for families, functions, question forms, plan lengths,
    STOP contrasts, personality/relationship contrasts, expansion decisions,
    outcome dimensions, learning targets, and error sources.
20. Compare every required minimal pair side by side.
21. List all non-empty `ontology_questions` entries.
22. Confirm the fixture remains design data rather than training truth.
23. Run `git diff --check` and report the result.

## Completion criteria

The task is complete only when it provides:

- a valid `conversational_action_design_cases.json` fixture;
- all required family, question, sequencing, STOP, personality, expansion,
  realization, outcome, and learning coverage;
- evidence-grounded moves and targets;
- multiple acceptable plans where appropriate;
- clear separation of disfavored and hard-invalid plans;
- no universal conversational ordering, compatibility, or plan-length rules;
- no invented probabilities, exact rewards, or outcome facts;
- a coverage summary;
- a list of open ontology questions;
- confirmation that every review remains pending;
- confirmation that no runtime code, database schema, prompt, or model artifact
  changed; and
- confirmation that JSON validation and `git diff --check` pass.

## Suggested delegated-agent prompt

```text
Read backend/app/ai/Cognitive/system/data/CONVERSATIONAL_ACTION_DESIGN_CASES_README.md
completely, then create conversational_action_design_cases.json exactly within
its task boundary. Treat plans as reviewed contextual examples, not universal
rules. Preserve multiple acceptable plans, learned sequencing and STOP, hard
system invariants, controlled action expansion, and uncertain error attribution.
At completion, report coverage, open questions, validation results, and every
file changed.
```
