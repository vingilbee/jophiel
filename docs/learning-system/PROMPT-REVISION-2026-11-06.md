# Learning prompt revision and exploratory comparison

Changed on user request, 2026-11-06. Reviewer `assessment-v7`; builder `lessons-v7`.
Builds on the status-veto removal documented in
[the investigation](INVESTIGATION-2026-11-06-adaptation-gaps.md).

## Changes

Both prompts now begin with: what, if anything, does this experience change about her understanding, and how
could that matter later? They allow useful changes to attention, expectations, possibilities and choices without
requiring advice or an immediately executable method. These are possibilities, not a mandatory taxonomy.

- Ordinary interactions are judged for what they add. Removed the exclusion of small talk and the assumption
  that most ordinary experience teaches nothing. No change remains valid.
- Observations, people's reports, interpretations and possible explanations are distinguished. Useful hypotheses
  can remain tentative; uncertainty may stay in the statement when removing it would change its meaning.
- Existing understanding is compared by proposition and conditions. Removed “One subject, one lesson” and the
  requirement to fit every counterexample into a narrowed lesson. Several independent beliefs can coexist.
- Lesson use is examined through opportunity, decision, actual execution and outcome. Reported application plus
  success/failure does not by itself decide support or challenge. An unexecuted method is distinguished from an
  ineffective one; missing capability, perception, conditions, other actions and chance are possible explanations.
- The game-method section no longer declares a method contrast inherently more important than other learning.
  Existing schemas, ownership and model configuration were retained. No example-specific production handler was added.

## Checks

Learning assessment, lesson, broaden, product and independent integrity-oracle suites: **37 passed, 52 skipped**.
The skipped cases require a disposable test database. `git diff --check` passes. These are contract/regression
checks and do not establish model learning quality.

An exploratory before/after comparison used the configured reviewer and builder models through their existing
structured-call wrappers. Each call used synthetic evidence, with no database, no action execution and no worker
restart. Exact prompts, inputs, raw answers, checked answers and dropped fields are saved under
`backend/var/learning_prompt_probe_20261106_corrected/` (eight calls, no API errors).

| Case | Previous prompt | Revised prompt |
| --- | --- | --- |
| Ordinary contextual conversation pattern | Found the pattern with valid citations | Found the pattern with valid citations; phrasing remained tied to the reported instances |
| Greeting/farewell with no useful additional evidence | Left unchanged | Left unchanged |
| Reported method use, but execution rejected the method | Recognized the method was not tested; related existing lesson as conditions differing | Explicitly separated execution constraint from effectiveness; related existing lesson as narrowing |
| New information about a person, alongside an existing belief about a different activity | No change | Created a separate reported understanding without modifying the existing belief |

The last case illustrates a potentially useful difference, not a demonstrated improvement rate. There was one
pair per case, no calibrated blind judge, and no downstream task or behavioral outcome. Both prompts handled some
cases well. The revised reviewer still sometimes writes episode-oriented findings. The nonexecution case's
target/choice checks also dropped fields because full decision records were not provided; this is a component
probe with partial records, not production-path coverage.

The initial probe files in `backend/var/learning_prompt_probe_20261106/` are preserved. That first setup omitted
rendered conversation reference labels and an explicit action target. The fixtures were repaired and the paired
comparison rerun; initial reviewer outputs are not used to claim better retention. Builder calls in that first
setup also varied, reinforcing that one comparison is not a stable performance estimate. No prompt tuning was
performed between the initial and corrected comparisons.

## Remaining work and limits

The prompt files are updated; running workers were not restarted and these changes were not tested in a live
conversation/game. Full evidence intake, persistence, recall after context removal, later decisions, execution,
transfer and revision still need production-path evaluation. The larger readiness review and Student 1 delivery
gap remain open. Existing semantic code rules concerning confidence/scope on repeated evidence and reconsideration
were not changed by this prompt revision; their removal is not implied here.

Further evaluation should use varied ordinary experiences with distractors, ambiguous and no-change controls,
followed by later tasks whose answer depends on those experiences. Measure the first failed stage rather than
equating a well-worded finding with successful adaptation. The user's mountain and interpersonal examples remain
illustrations of the general requirement, not fixed strategies or mandatory learning products.
