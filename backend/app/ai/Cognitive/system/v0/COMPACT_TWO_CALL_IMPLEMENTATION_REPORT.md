# Compact context-conditioned two-call implementation

Date: 2026-10-04. Scope: observational Cognitive V0 shadow.
Implements the runtime redesign in `PHASED_MODEL_BACKED_COGNITION_AND_LATENCY_IMPLEMENTATION.md`
and `CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md`. Behavioral acceptance and
live latency acceptance are pending; the full phased completion gate is not claimed.

## Versions and configuration

| Boundary | Version |
| --- | --- |
| Provider proposal protocol | `context-conditioned-two-call-v0.1` |
| Shared context / decision packet | `turn-context-v0.1` / `decision-context-v0.1` |
| Provider schema names | `call_one_compact_v0_1`, `call_two_compact_v0_1` |
| Captured schema/prompt provenance | `call_one-compact-v0.1`, `call_two-compact-v0.1`; exact prompt hashes retained |
| Context-conditioned hypotheses | `interpretation-context-conditioned-readings-v0.1` |
| Bias owner | `interpretation-bias-owner-model-v0.1` |
| Event binding | `compact-event-binding-v0.1` |
| Behavior acceptance | Existing `model_grounded_selection-v0.1` |
| Final language validation | `segmented_response-v0.1` |
| Content alignment | Existing `compiled_move_spans-v0.1` |
| Telemetry summary | `cognitive-latency-summary-v0.3` for compact traces |

`Settings.cognitive_shadow_two_call_protocol` is explicit and defaults to the new
protocol when two-call orchestration is selected. Staged orchestration remains
the shipped default; its topology is unaffected by this setting. The local `.env`
was found to select `staged`; it now explicitly selects `two_call` and the compact
protocol for the already-enabled shadow worker. No capture, sampling, retention,
delivery, tool, or model setting was changed. Restart/reload the worker to apply
cached settings. The bounded canary script now honors the protocol setting.

Observed local provider configuration: `gpt-6-luna`, `reasoning_effort=none`,
20-second per-call timeout, 6,000 maximum output tokens, SDK retries zero,
`store=false`, nonstreaming, one candidate. No live provider call was made in this
implementation session. These settings are configuration, not measured latency.

## Phase 0: inventory, capture compatibility and attribution

Legacy `CallOneOutput`/`CallTwoOutput`, prompts, stage projections, behavior
agreement policies and strict realization decoding remain available. Default
legacy fields are omitted when serialized so historical captures retain their
representation. Completed compact captures contain raw provider proposals,
code-only reference tables, compiled canonical proposals and protocol versions.
Loading a capture recompiles its raw proposal and verifies the compiled digest.
Frozen replay uses the captured protocol and exact prompt/payload digests.

Attribution records Unicode character counts for payload/output sections and
instructions/schema. A characters-divided-by-four estimate is explicitly rough;
there is no measured tokenizer count for the offline fixtures. Provider-reported
input/output/cache/reasoning token counts, provider wall time and parsing time
are recorded when available. Compilation spans cover both proposal conversion
and content compilation. Trace summaries expose only allowlisted numeric fields;
arbitrary metadata keys cannot reveal packet content. Unmeasured first-token,
queue-wait and delivery times remain unavailable.

## Phase 1: shared context and compact understanding

`compact.py` constructs `TurnContext` after normal request validation. It carries
the captured authorized dialogue without another arbitrary truncation; upstream
window truncation is recorded. Actual mapped traits, seven bias values, exposed
relationship, active StateBefore, authorized memories with epistemic provenance,
values, capabilities/permissions, observed tool outcomes, active previous goals,
open inquiries and fallible perception cues are projected with local handles.
Unavailable assertiveness/voice/freshness/context stay unavailable. Explicit test
profile identities are marked synthetic; other profiles do not acquire a fabricated
synthetic classification. Authorized Dialogue Working Memory items/referents are
projected once, preserving ambiguous references and attributed content.

Predicate argument definitions are deduplicated across the vocabulary; definitions
and scales are retained rather than deleting semantic coverage to meet a budget.
Canonical IDs remain in `ReferenceTable`. Call 1 produces `MindProposal`: observations,
registered situated readings, compact eight-value event vectors, existing-goal
impacts, unknowns and conflicts. Spans and references are validated. Null, known
and not-applicable values remain distinct; high-uncertainty known dimensions fail.

Observations compile through the existing semantic compiler. `ContextReadingRules`
removes legacy pragmatic/social predictions, adds accepted model readings as
canonical hypotheses, and passes those to the actual ScenarioBuilder and Goal
engine. Structural act/modality and explicitly expressed request-goal evidence
remain. `ModelBiasOwner` prevents reapplication of deterministic bias. Context
citations remain contextual provenance, not evidence that a world fact is true.
Raw readings and their candidate meanings also reach Call 2. Unknowns/conflicts
enter canonical uncertainty records, with fixed content-free unknown keys.

## Phase 2: event binding, readiness and state

`BindingGoalEngine` records actual semantic-frame formation links. Call 1 neither
receives all future goal definitions nor predicts future goal IDs. Event vectors
bind through those links after goal formation; exact active previous-goal impacts
are supported. One event is allocated to at most one eligible goal/scenario pair.
Other adopted pairs remain explicitly unknown. Alternative vectors on identical
event bundles are combined once: agreed dimensions survive with bounded
uncertainty, conflicting dimensions are unknown. Partial overlapping bundles are
rejected to prevent ambiguous double counting.

This is a conservative compatibility adapter to the existing per-goal appraisal
contract. It does not infer goal-specific congruence for a newly formed goal.
Events with no available formation-linked pair remain recorded as unmatched;
they do not fabricate a goal or state effect. Grouped events that contend for the
same pair can remain unmatched. This limitation is explicit in provenance and
needs behavioral review before broader claims about event affect coverage.

The existing updater consumes the resulting canonical appraisal, prior state,
personality mapping, elapsed time, decay/bounds and observed outcomes. Call 2's
StateAfter is checked against the actual updated snapshot. Compact readiness
groups the real requirement records per goal, preserves blockers/missing fields
and known references, exposes capability/permission/grounding status and the
current constructible behaviors. More restrictive blocking states take precedence
in the summary; individual records remain available. Ready independent goals are
not converted into a global missing-input block.

## Phase 3: compact decision and single prose source

`decision_packet` collects actual collaboration requests and directly invokes
candidate construction, hard gates and planning-condition checks. It does not
execute deterministic action/realization previews or serialize their plans.
Current eligible choices have local handles, exact targets, parameters and wording
constraints. Future content-dependent construction templates carry action keys,
target schemas and roles; parameters are supplied by existing code binders, not
invented by the model. Content requests retain actual task content, revision and
source records, without repeated message windows or full vocabulary copies.

Call 2 returns `ResponseProposal`. Content operations remain typed proposals;
ordinary social turns have none. Moves refer to grounded choices or construction
templates and ordered segment handles. Prose appears once in `segments[].text`.
The compiler derives the canonical response text and move excerpts. Foreign
choices, substituted targets, unbound/duplicate/noncontiguous segments, unsupported
parameter mutations, content references and claim sources fail deterministically.
Content inquiry metadata is optional and must match its content proposal. Blocking
request-field questions use actual requirement/action choices; the existing content
inquiry contract does not offer model-created blocking inquiries.

## Phase 4: connected content/action/realization

The existing content compiler creates canonical artifacts/content/inquiries from
the captured proposal. The existing model-selected planner constructs, gates,
checks planning conditions and transitions each requested action sequentially.
Selection need not equal the deterministic scoring argmax. Raw Call 2 digest
provenance binds every model-selected action to its source capture.

Captured Realization derives offsets directly from ordered segments, avoiding
substring ambiguity when text repeats. Actual move content bindings acquire
candidate spans in code; final coverage, anchors, values, epistemic restrictions,
capabilities, safety and outcome validation remain mandatory. Exact shared spans
are allowed only for the explicitly compatible greeting/acknowledgement pair.
Other overlaps remain invalid. Cosmetic style checks remain advisory under the
existing tolerance policy. No third wording call or automatic repair is introduced.

Code-only authorized dialogue snapshots are retained in compact captures so core
replay can restore the exact Dialogue Working Memory context without sending the
full snapshot to the provider. Shadow proposals still do not establish delivery,
execution or accepted workspace heads. Persistence and stale-worker compare-and-set
guards are unchanged; database-specific tests require the disposable database.

## Phase 5: verification status

The related backend regression selection passes **470 tests with 62 skips**.
The compact test file adds foreign/ambiguous reference failures, protocol settings
wiring, synthetic provider JSON decoding, actual state order, no second bias,
event deduplication, context citation, unknown/conflict propagation, compact
privacy-safe summaries, authorized dialogue replay and eight-turn state continuity.
Legacy replay and grounded action/realization checks remain in the selection.

The eight-turn case is a social wiring regression, not the required mixed-task
behavioral evaluation. The paired trust case uses a scripted context-sensitive
double: it proves downstream consumption and invariant observations, not actual
LLM personality sensitivity. Live varied-turn regressions, independently changed
context components, consent/safety invariance, human quality review and configured
provider p50/p95/acceptance measurements remain pending. The latency targets are
not claimed as achieved. See the separate behavioral and latency reports.

## Reproduction

From `backend`, run the compact and legacy tests with:

```sh
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --no-sync python -m pytest -q tests/test_compact_two_call.py tests/test_two_call_orchestration.py
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --no-sync python scripts/cognitive_compact_offline_report.py
```

The report script uses synthetic doubles only, checks core replay and accepted
Realization, and prints structural counts. It never constructs an external provider.

## Response reference repair (2026-10-04)

After a reported live `response_reference_compilation` failure, inspection found
two independently reproducible compiler defects. Supplied grounded-choice targets
were rejected even when identical to the provided targets. Reference-valued choice
parameters were compared before expanding the model's local handles, while the
choice parameters had already been expanded to canonical IDs.

The compiler now expands both sides before comparison, accepts only unchanged
read-only target/parameter echoes (including a supplied parameter subset), and
retains the code-owned targets and parameters. Target order is immaterial; duplicate,
foreign, substituted or altered bindings remain rejected. Content-request coverage
and duplicates are checked before claims dereference request slots. Closed failure
subcodes identify the failing reference rule without exposing raw values or text.
Prompts, schema, call count, live authority and retry policy were not changed.

`tests/test_compact_response_references.py` passes 14 cases. The related regression
selection passes 483 tests with 62 skips, followed by the additional isolated
parameter-reference regression. An echoed-target fixture completes both calls,
accepted shadow Realization and frozen replay. The reported real E2E proposal
could not be recovered from the local test file/database; no live retest was run.
These fixes are verified reproductions, not proof of the exact reported turn's
root cause. Its raw Call 2 capture is still needed for that determination.

## Generated claim reference schema repair (2026-10-04)

The subsequent live failure identifies `foreign_claim_content`. The compiler
correctly requires a claim's content handle to name an item generated by this
response's `content_ops`; the provider schema previously allowed any string,
including claims when the decision context contained no content requests.

Call 2 now prohibits segment content references, claim bindings and inquiries
when there are no prepared content requests. With requests, both segment content
handles and claim content handles use the same request-scoped
`content:<request_handle>:<zero-based item index>` pattern, and claim source
handles are restricted to supplied request sources. The prompt explicitly
distinguishes generated items from input references and gives the empty-request
case. Deterministic compilation still verifies actual item existence and that
each claim source belongs to its referenced item. No references are guessed,
discarded or silently rebound; no extra model call or retry was introduced.

Validation: compact/reference suites pass 47 tests; surrounding orchestration
and realization suites pass 203 tests with 22 database-dependent skips.
New cases validate the provider schema's empty-request restrictions and exercise
generated claim bindings through two calls, accepted shadow realization and
frozen replay. Foreign input references, request indices and item indices remain
rejected. The reported live raw proposal has not been supplied, and no new live
provider test was run, so these results do not establish live E2E success.

## Single owner for generated claim sources (2026-10-04)

The next live failure was `claim_source_does_not_support_proposed_content`:
the claim selected a request-authorized source absent from its referenced item's
`source_refs`. Requiring the model to repeat a source selection at the segment
level introduced a second, redundant declaration of content support.

New provider output now keeps `claim_bindings[].source_handles` empty using
`maxItems: 0`. The prompt explicitly makes the content item's `source_refs` the
single source declaration. The claim still references an actual generated item;
the content compiler validates that item's references, source classes, subjects,
numbers and claim kind before realization. Empty segment-level source lists
already meant no extra source assertion and do not remove content grounding.
Nonempty legacy lists retain their strict subset checks, preserving prior frozen
replay and rejecting unsupported or foreign legacy sources. No extra call or
repair retry was added.

The compact/reference suites pass 52 tests. A regression reproduces exactly the
reported mismatch class and verifies the new single-owner form preserves the
original item sources. New and legacy-valid bindings complete accepted shadow
realization and replay. Empty/foreign item sources and unsupported verified claims
remain rejected. No new live provider test was run; the exact failed raw proposal
has not been supplied.

## Patch contract and rejection diagnostics (2026-10-04)

The latest live failure reaches content compilation and reports `patch_rejected`.
The compiler already knew the rejected operation or workspace rule, but the
two-call terminal envelope discarded its subject. Terminal codes now preserve
only closed operation, field and validation-rule tokens. Missing required
payloads and typed validation failures identify fields (for example
`set_main_dish.dish.missing` or `set_servings.value.greater_than_equal`);
workspace errors preserve their enum reason such as `conflicting_operations`.
Raw values, validation messages and unknown subject strings are excluded.

The compact provider schema now uses operation-specific branches: required
payloads cannot be null, unused fields must be null, IDs/text must be nonempty,
and integer/time bounds match typed contracts. Create requests exclude updates
and removals of nonexistent elements. Patch length is bounded by the canonical
contract. Nested dish/constraint definitions are shared per request to limit
schema repetition. Sequence-dependent duplicate IDs, constraint conflicts and
unknown update targets still require deterministic workspace validation; the
prompt documents those rules. The compiler's acceptance gates remain enforced.

New regressions exercise malformed payloads and bounds through both calls into
terminal field diagnostics, validate provider schemas, preserve workspace
rejection reasons and check diagnostic privacy. Valid synthetic creation and
frozen replay continue to complete. The exact rejected live patch is unavailable,
and no live provider call was made: this repairs demonstrated schema gaps and
provides precise diagnostics for a further rejection, without claiming the
reported live turn's root cause or E2E success.
