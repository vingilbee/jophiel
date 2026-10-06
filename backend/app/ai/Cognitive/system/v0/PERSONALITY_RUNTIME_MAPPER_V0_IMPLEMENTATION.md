# Personality Runtime Mapper V0 implementation brief

Status: ready for implementation as a conservative, deterministic,
shadow-only V0 after review of this brief. This status does not imply
behavioral calibration, user-specific personality, learning, realization
style, or live-response approval.

## 1. Objective

Implement one deterministic Personality Runtime Mapper that compiles the
active, server-owned `CoreMemory` record into the three exact profiles required
by the canonical cognitive pipeline:

```text
versioned CoreMemory snapshot
        |
        v
PersonalityRuntimeMapperV0
        |
        +--> InterpretationBiasProfile
        +--> Dynamic State PersonalityProfile
        `--> ActionPersonalityProfile
```

The mapper must replace the live-shadow baseline-file dependency as the normal
runtime source. It must compile the personality projection at turn-capture time
and persist the complete compiled projection inside the immutable capture.

The mapper is an identity-configuration compiler. It is not a learner, a
psychological assessment, a user profile, or permission to infer personality
from conversation text.

## 2. Why V0 is deliberately conservative

The architecture already defines:

- the `CoreMemory` groups;
- the three required pipeline profiles;
- `[0, 1]` bounds for Interpretation and Action personality values;
- the Dynamic State personality-control boundary;
- core-memory/version ownership;
- the rule that personality may affect bounded preferences but never facts,
  evidence, permissions, safety, or action eligibility; and
- live-shadow behavior for a missing projection.

The previous documents did not define every field-to-field mapping. V0 closes
that gap without inventing broad coefficients:

- Interpretation uses direct semantically compatible inputs, one reviewed
  two-field mean, and two explicit neutral policy constants.
- Action uses four direct inputs already present in CoreMemory.
- Dynamic State receives a complete explicit neutral-control profile using its
  existing policy defaults. V0 does not claim that unreviewed core traits
  determine state baselines, decay, or transition magnitude.

Richer state-personality mappings require a separately reviewed V0.2 or V1
manifest and real shadow evidence. Do not add them in this task.

## 3. Required references

Read these sources before implementation:

1. `backend/app/ai/Cognitive/system/v0/README.md`
2. `backend/app/db/models.py` (`CoreMemory`)
3. `backend/alembic/versions/d8f1a2b3c4e5_add_cognitive_memory_architecture.py`
4. `backend/app/ai/Cognitive/system/v0/interpretation_v0/models.py`
5. `backend/app/ai/Cognitive/system/v0/dynamic_state/models.py`
6. `backend/app/ai/Cognitive/system/v0/dynamic_state/policy.py`
7. `backend/app/ai/Cognitive/system/v0/contracts/actions.py`
8. `backend/app/ai/Cognitive/system/v0/live_shadow/models.py`
9. `backend/app/ai/Cognitive/system/v0/live_shadow/compiler.py`
10. `backend/app/services/cognitive_shadow.py`
11. `backend/app/ai/Cognitive/system/v0/DURABLE_LIVE_SHADOW_TRACE_V0_IMPLEMENTATION.md`
12. the Perception/Interpretation fixture correction handoff, if it still
    exists while this work begins.

If an existing canonical contract conflicts with this brief, report the
conflict and preserve the stricter safety/ownership rule. Do not silently
change a downstream contract to make mapping easier.

## 4. Scope

### 4.1 In scope

- a pure, deterministic mapper package;
- a closed source projection copied from the ORM row;
- a versioned mapping manifest;
- finite/range/type validation;
- deterministic IDs and mapping provenance;
- all three canonical output profiles;
- typed failures without raw personality content in logs;
- turn-time live-shadow integration;
- removal of the baseline file as the normal runtime dependency;
- an explicit reviewed-baseline compatibility mode for tests or emergency
  staging use only;
- unit, integration, replay, isolation, and regression tests; and
- canonical README status updates after independent verification.

### 4.2 Out of scope

- learning or changing CoreMemory;
- user-specific personality;
- deriving personality from messages, memories, outcomes, feedback, or model
  output;
- relationship style;
- realization/prompt style compilation;
- adding new Interpretation bias fields;
- adding new Action traits;
- learned coefficients;
- non-neutral Dynamic State personality coefficients;
- Perception priors beyond the one explicitly consumed source field;
- live response influence;
- tool permission, authorization, consent, safety, or policy decisions; and
- migration of legacy experimental cognitive-node personality logic.

## 5. Package placement

Create:

```text
backend/app/ai/Cognitive/system/v0/personality_runtime_v0/
    __init__.py
    models.py
    manifest.py
    mapper.py
```

Recommended tests:

```text
backend/tests/test_personality_runtime_v0_contract.py
backend/tests/test_personality_runtime_v0_mapper.py
backend/tests/test_personality_runtime_v0_shadow_integration.py
```

The pure package must not import SQLAlchemy, database sessions, chat code,
prompts, tools, memories, learners, fixture adapters, or the live agent.

Database selection and ORM-to-source copying remain in
`app.services.cognitive_shadow` or a small application service. The mapper
accepts only its immutable source contract.

## 6. Versions

Use these V0 versions unless an already-installed code-owned version requires a
documented collision change:

```text
PERSONALITY_SOURCE_SCHEMA_VERSION = "core-personality-source-v0.1"
PERSONALITY_MAPPING_MANIFEST_VERSION = "personality-runtime-mapping-v0.1"
PERSONALITY_MAPPER_VERSION = "personality-runtime-mapper-v0.1"
```

The same `PERSONALITY_MAPPER_VERSION` must appear in:

- `InterpretationBiasProfile.compiler_version`;
- Dynamic State `PersonalityProfile.mapper_version`;
- `ActionPersonalityProfile.mapper_version`;
- the outer live-shadow personality projection version; and
- terminal component-version provenance.

Do not reuse fixture, baseline, or draft mapper versions.

## 7. Immutable source contract

Define a frozen source model that contains only the authorized fields consumed
by V0:

```python
class CorePersonalitySourceV0(FrozenModel):
    source_schema_version: Literal["core-personality-source-v0.1"]
    core_memory_ref: str
    core_memory_name: str
    core_memory_version: int
    core_schema_version: int

    optimism_prior: Decimal
    charitable_interpretation: Decimal
    trust_prior: Decimal
    ambiguity_tolerance: Decimal
    threat_sensitivity: Decimal
    affiliation: Decimal

    curiosity: Decimal
    warmth: Decimal
    playfulness: Decimal
    directness: Decimal
```

All ten numeric values are required, finite, and in `[0, 1]`. Booleans must not
validate as numbers. Numeric strings may be rejected; the database JSON must
contain JSON numbers, not prose.

The application-layer source builder reads exactly these paths:

| Source field | CoreMemory JSON path |
| --- | --- |
| `optimism_prior` | `interpretation_biases.optimism_prior` |
| `charitable_interpretation` | `interpretation_biases.charitable_interpretation` |
| `trust_prior` | `interpretation_biases.trust_prior` |
| `ambiguity_tolerance` | `interpretation_biases.ambiguity_tolerance` |
| `threat_sensitivity` | `perception_priors.threat_sensitivity` |
| `affiliation` | `drives.affiliation` |
| `curiosity` | `drives.curiosity` |
| `warmth` | `action_style.warmth` |
| `playfulness` | `action_style.playfulness` |
| `directness` | `action_style.directness` |

Other CoreMemory fields are not copied into the mapper and cannot affect V0.
Extra fields in the extensible database groups remain allowed, but a missing,
null, Boolean, non-finite, nonnumeric, or out-of-range consumed field fails the
source build.

Construct the lineage exactly as:

```text
core_memory_ref = "core_memory:{name}:v{version}"
```

It must equal the `CoreMemoryLineage` captured for the turn.

## 8. Deterministic numeric policy

Use decimal fixed-point arithmetic for mapping. Convert each validated source
number through `Decimal(str(value))`, quantize derived results to six decimal
places using `ROUND_HALF_EVEN`, then convert to the finite float required by
the existing contracts.

```text
PERSONALITY_DECIMAL_QUANTUM = Decimal("0.000001")
```

Direct mappings retain the source decimal value, subject only to canonical
six-place output normalization. Do not introduce binary-float comparisons,
randomness, clocks, UUID4 identifiers, environment-dependent rounding, or
model calls.

## 9. Interpretation mapping

Produce one `InterpretationBiasProfile` with this exact mapping:

| Output | V0 source/rule |
| --- | --- |
| `benign_prior` | mean of `optimism_prior` and `charitable_interpretation` |
| `trust_prior` | direct `trust_prior` |
| `threat_sensitivity` | direct `threat_sensitivity` |
| `confirmation_tendency` | explicit mapping-policy neutral constant `0.500000` |
| `self_attribution_tendency` | explicit mapping-policy neutral constant `0.500000` |
| `ambiguity_tolerance` | direct `ambiguity_tolerance` |
| `social_affiliation_prior` | direct `affiliation` |

The mean is:

```text
benign_prior = quantize((optimism_prior + charitable_interpretation) / 2)
```

The two neutral constants are not claims extracted from CoreMemory. They are
explicitly versioned no-effect controls because the current CoreMemory schema
has no reviewed source for those dimensions. Their provenance must be declared
in the mapping manifest. Do not derive them from unrelated traits.

Interpretation already applies these biases only behind its ambiguity gate and
limits a personality adjustment to one ordinal level. The mapper must not alter
that policy.

For the currently seeded Columbina CoreMemory values, the expected profile is:

```json
{
  "benign_prior": 0.77,
  "trust_prior": 0.64,
  "threat_sensitivity": 0.25,
  "confirmation_tendency": 0.5,
  "self_attribution_tendency": 0.5,
  "ambiguity_tolerance": 0.55,
  "social_affiliation_prior": 0.75
}
```

This is a deterministic compilation test vector, not a behavioral claim.

## 10. Dynamic State mapping

Produce a `PersonalityProfile` containing an explicit control for every
`StateKey` currently registered by Dynamic State.

For conservative V0, every control uses the existing code-owned Dynamic State
default and neutral qualitative bands:

```text
baseline_score    = DEFAULT_BASELINE_SCORES[state_key]
persistence_band  = "moderate"
recovery_band     = "moderate"
sensitivity       = "neutral"
maximum_step_band = "moderate"
```

Set:

```text
interaction_patience_band = "moderate"
```

This is intentionally explicit rather than leaving `state_controls` empty. It
makes the captured profile self-contained and prevents a later change to
Dynamic State defaults from silently changing what an old mapper version meant.

The mapping manifest must snapshot the exact baseline scores used by this
mapper version. It must not read mutable global tables without also pinning
their values in mapper tests.

V0 does not map openness, emotional stability, initiative, competence,
affiliation, or other CoreMemory fields into state transition controls. That
work needs a separately reviewed mapping version because the current design
does not define defensible coefficients.

Personality remains a control, never evidence. The resulting profile cannot:

- change a contribution direction;
- manufacture an appraisal or state contribution;
- change fact, missingness, agency, authorization, consent, capability, or
  safety status; or
- reverse or remove an evidence-grounded state update.

## 11. Action mapping

Produce a known `ActionPersonalityProfile` with direct mappings:

| Action affinity | Core source |
| --- | --- |
| `curiosity` | `drives.curiosity` |
| `warmth` | `action_style.warmth` |
| `playfulness` | `action_style.playfulness` |
| `directness` | `action_style.directness` |

For the currently seeded Columbina CoreMemory values, the expected profile is:

```json
{
  "status": "known",
  "curiosity": 0.88,
  "warmth": 0.80,
  "playfulness": 0.58,
  "directness": 0.62
}
```

Action personality is a small bounded scoring feature after construction,
evidence, lifecycle, capability, permission, and safety gates. The mapper must
not change Action eligibility, targets, facts, required evidence, safety, or
commitment permissions.

Do not construct an `unknown` Action profile when a required source is invalid.
Fail the entire three-profile mapping atomically so the live-shadow turn records
`missing_personality_projection`. The three profiles must always share one
lineage and mapper version.

## 12. Mapping manifest

Define a code-owned immutable manifest that records, for every output:

- output profile and field;
- exact source JSON path or `policy_constant`;
- transform (`direct`, `mean_two`, or `dynamic_policy_snapshot`);
- bounds;
- rounding policy;
- influence boundary; and
- rule ID.

Example rule IDs:

```text
personality.interpretation.benign-prior.mean-v1
personality.interpretation.trust-prior.direct-v1
personality.interpretation.threat-sensitivity.direct-v1
personality.interpretation.confirmation-neutral.v1
personality.interpretation.self-attribution-neutral.v1
personality.interpretation.ambiguity-tolerance.direct-v1
personality.interpretation.social-affiliation.direct-v1
personality.state.defaults-snapshot.v1
personality.action.curiosity.direct-v1
personality.action.warmth.direct-v1
personality.action.playfulness.direct-v1
personality.action.directness.direct-v1
```

The mapper must derive through the manifest or prove with a manifest-completeness
test that implementation and manifest contain the same fields. Do not leave
behavioral mappings in undocumented conditionals.

## 13. Deterministic identity and provenance

Use deterministic, content-independent identifiers tied to versioned lineage:

```text
projection_id = "personality-runtime:{core_memory_ref}:{mapper_version}"
interpretation profile_id = projection_id + ":interpretation"
state profile_id          = projection_id + ":state"
action profile_id         = projection_id + ":action"
```

The outer live-shadow projection must be:

```text
projection_kind = "runtime_mapper"
scope = "core_identity"
projection_version = PERSONALITY_MAPPER_VERSION
review_ref = "PERSONALITY_RUNTIME_MAPPER_V0_IMPLEMENTATION.md"
provenance = PERSONALITY_MAPPING_MANIFEST_VERSION
```

All three output profiles must be built in one call and validated before any is
returned. No partial result may escape.

## 14. Mapper API

Recommended pure API:

```python
class PersonalityMappingError(ValueError):
    code: PersonalityMappingErrorCode
    safe_message: str


class PersonalityRuntimeMapperV0:
    def compile(
        self,
        source: CorePersonalitySourceV0,
    ) -> ShadowPersonalityProjection:
        ...
```

The pure package may reuse `ShadowPersonalityProjection` as the aggregate
output because that is the existing pipeline/live-shadow boundary. It may not
import the application service to obtain it.

The mapper has no I/O. Given the same source and installed version, it must
produce byte-for-byte equivalent JSON output.

## 15. Typed failure behavior

Define stable internal codes at least for:

```text
core_memory_unavailable
core_memory_inactive
core_memory_schema_unsupported
core_memory_lineage_invalid
personality_source_field_missing
personality_source_type_invalid
personality_source_nonfinite
personality_source_out_of_range
personality_projection_invalid
personality_mapper_internal_contract
```

The application service converts mapping failures into a missing personality
projection with a typed `PersonalityMissingReason`. Extend that closed enum with
mapper-specific reasons as needed, for example:

```text
runtime_mapper_input_invalid
runtime_mapper_schema_unsupported
runtime_mapper_projection_invalid
```

The terminal pipeline result remains:

```text
skipped: missing_personality_projection
```

Capture metadata preserves the more specific missing reason. Logs contain only
turn/core references and stable codes—never raw CoreMemory JSON or mapped
values.

Do not fall back to neutral profiles, fixture profiles, or a baseline file when
runtime mapping was selected but failed.

## 16. Application-layer CoreMemory selection

At capture time:

1. select `CoreMemory` by the configured name;
2. require `is_active == true`;
3. require a supported positive `schema_version` and `version`;
4. build `CoreMemoryLineage`;
5. copy the ten required numeric paths into `CorePersonalitySourceV0`;
6. verify source lineage equals captured lineage;
7. invoke the pure mapper once; and
8. persist its complete output inside `CognitiveShadowCapture`.

The worker must never remap from later database state. Replay uses the exact
compiled projection already persisted in the immutable capture/request.

If the active CoreMemory is edited, its version must be incremented before it
may be captured. This task should add an application-level validation/helper and
tests for that update rule if a CoreMemory mutation service exists. Do not build
a new admin mutation API in this task.

No database migration is required for V0 because every consumed source path is
present in the currently seeded CoreMemory. If inspection proves a deployed
database lacks a required path, fail closed and report the data repair needed;
do not hide it behind a migration default.

## 17. Source mode and reviewed-baseline compatibility

Add an explicit closed setting:

```text
cognitive_live_shadow_personality_source = "runtime_mapper"
    | "reviewed_baseline"
```

Recommended default after mapper acceptance:

```text
runtime_mapper
```

Behavior:

- `runtime_mapper`: use only the active CoreMemory and accepted mapper;
- `reviewed_baseline`: use only the configured reviewed baseline file and its
  existing validation; and
- no automatic fallback between modes.

Retain the baseline path setting only for explicit compatibility and emergency
staging rollback. A configuration file cannot claim
`projection_kind=runtime_mapper`.

The feature remains globally disabled by default. Changing the personality
source default does not enable live-shadow capture or production sampling.

## 18. Live-shadow integration

Refactor the existing `_core_memory` / `_personality` capture path so that one
turn-time read produces both lineage and the source projection. Avoid reading
the CoreMemory row twice.

Required invariants:

- mapping occurs before response generation and before capture persistence;
- the compiled projection is immutable inside the capture;
- a ChatJob retry never remaps;
- the worker reads no CoreMemory row;
- all three profiles share the captured `core_memory_ref`;
- the previous Dynamic State mapper version must match the current state
  profile for continuous lineage;
- mapper-version change begins `lineage_version_change`, never false
  continuity;
- baseline/runtime mode and mapper version survive in trace provenance; and
- mapping failure cannot affect the live response, ChatJob, assistant message,
  tools, memories, or learning.

Once runtime mapping succeeds, remove
`OmittedInput.PERSONALITY_PROJECTION` from that capture. Other currently omitted
inputs remain omitted.

## 19. Trust and influence boundaries

Personality may influence only the already accepted bounded locations:

```text
Interpretation: ambiguity-gated support adjustment, max one ordinal step
Dynamic State: explicit controls; V0 is neutral/default only
Action: small post-gate personality-affinity score
```

Personality must never:

- create or alter message content, cues, semantic arguments, runtime facts, or
  evidence;
- resolve missing, unknown, disputed, or contradictory information;
- override a correction;
- establish user intent, affect, relationship, or preference;
- establish capability, permission, authorization, consent, export control, or
  safety;
- make a blocked or unconstructable Action eligible;
- create a goal or claim that a goal is satisfied;
- invoke a tool, write memory, schedule learning, or influence a live response;
  or
- use user/account identity to vary Columbina's core personality.

Add invariance tests proving these boundaries through the real pipeline.

## 20. Required tests

### 20.1 Source-contract tests

- every required field is mandatory;
- null, Boolean, string, list, object, NaN, infinity, and out-of-range values
  fail with typed codes;
- extra unconsumed CoreMemory fields cannot affect the result;
- lineage format and version are validated; and
- source models are immutable and JSON round-trip safely.

### 20.2 Mapping golden tests

Using the current seeded Columbina values, assert exactly:

```text
Interpretation: 0.77, 0.64, 0.25, 0.5, 0.5, 0.55, 0.75
Action:         0.88, 0.80, 0.58, 0.62
Dynamic State: exact snapshotted defaults and neutral bands for every key
```

Also assert:

- deterministic IDs;
- one mapper version across all outputs;
- exact core-memory ownership;
- exact rule/manifest coverage;
- no omitted output field;
- no partial result on failure; and
- byte-equivalent JSON over repeated runs and subprocesses.

### 20.3 Perturbation tests

Change one consumed source at a time and prove only its declared dependent
output changes. Examples:

- `warmth` changes only Action warmth;
- `curiosity` changes only Action curiosity;
- `trust_prior` changes only Interpretation trust;
- `affiliation` changes only Interpretation social affiliation;
- either benign source changes only Interpretation benign prior; and
- an unrelated value such as `values.privacy` changes no V0 output.

### 20.4 Pipeline-boundary tests

- personality never changes Perception output;
- personality never changes grounded semantic frames or base hypothesis
  support;
- explicit/runtime-grounded Interpretation remains invariant;
- ambiguous Interpretation changes by at most one ordinal level;
- Dynamic State contribution signs remain invariant;
- Action candidate construction and all hard gates remain invariant;
- personality may change only permitted post-gate ordering;
- safety-unavailable remains fail-closed; and
- changing the mapper version resets shadow lineage.

### 20.5 Live-shadow tests

Against real PostgreSQL:

- active seeded CoreMemory produces `projection_kind=runtime_mapper`;
- mapper mode requires no baseline file;
- baseline mode remains explicit and validates provenance;
- modes never silently fall back to each other;
- malformed CoreMemory persists a typed skipped terminal record;
- the exact turn-time projection survives later CoreMemory edits;
- a retry does not remap;
- a mapper failure cannot alter the live response;
- completed captures no longer mark personality omitted;
- replay uses stored projection and matches semantically; and
- account/conversation deletion and retention still remove trace data.

### 20.6 Regression tests

Run focused Personality, Interpretation, Dynamic State, Action, Pipeline, and
live-shadow suites, then the complete backend suite and `git diff --check`.

## 21. Acceptance criteria

Personality Runtime Mapper V0 is implementation-accepted only when:

1. one immutable authorized source produces all three profiles atomically;
2. mapping behavior is fully declared in a versioned manifest;
3. all consumed values are finite, bounded, and required;
4. the seeded CoreMemory golden vector matches exactly;
5. IDs, rounding, JSON, and subprocess results are deterministic;
6. every output cites the same core-memory lineage and mapper version;
7. the two neutral Interpretation controls are explicit policy constants, not
   misrepresented as CoreMemory facts;
8. Dynamic State V0 remains explicit neutral/default rather than inventing
   coefficients;
9. Action affinities use only the four declared direct sources;
10. missing or malformed input fails atomically and becomes a typed shadow
    skip;
11. capture compiles at turn time and retries/workers never remap later state;
12. baseline compatibility is an explicit mode, never an automatic fallback;
13. tests prove facts, evidence, gates, safety, permissions, state directions,
    tools, memory, learning, prompts, and live responses remain uninfluenced;
14. focused real-PostgreSQL tests and the complete backend suite pass;
15. an independent conformance/isolation audit finds no blocker; and
16. the canonical README is updated with the verified status and exact test
    counts.

Passing tests is not enough if mappings are undocumented, if missing data turns
into silent neutrality outside the two declared constants, if a worker remaps
later database state, or if personality can influence a live response.

## 22. Implementation order

Implement in this order:

1. source and error contracts;
2. versioned mapping manifest;
3. deterministic numeric helpers;
4. pure three-profile mapper;
5. golden, malformed-input, perturbation, and invariance tests;
6. explicit source-mode configuration;
7. single-read turn-time CoreMemory integration;
8. typed missing-reason integration;
9. live-shadow capture, retry, lineage, and replay tests;
10. focused and complete regression suites;
11. independent audit; and
12. canonical README status update.

Do not begin by changing live-shadow capture to pass raw CoreMemory JSON into
the pipeline.

## 23. Prohibited shortcuts

Do not:

- copy test or design-case profiles into runtime;
- use the legacy `cognitive_nodes` personality logic;
- parse prose into personality values;
- infer personality from user messages or memories;
- use an LLM, embedding model, random value, clock, or network call;
- silently default a missing consumed source field;
- automatically fall back from the runtime mapper to a baseline;
- make the mapper database-aware;
- map unreviewed core fields into Dynamic State coefficients;
- let personality alter facts, evidence, safety, permission, gates, or action
  construction;
- write CoreMemory, WorkingMemory, Semantic/Episodic Memory, or
  `LearningMemory`;
- add personality to the live prompt or response path; or
- enable production live-shadow sampling as part of this task.

## 24. Handoff result

At completion, report:

- exact files changed;
- mapper and manifest versions;
- exact source-to-output mapping table;
- golden-vector results;
- focused and complete test counts;
- real-PostgreSQL integration results;
- proof that the baseline is no longer required in runtime-mapper mode;
- proof of no live response, tool, memory, or learning influence;
- any remaining rollout blockers; and
- the README status change.

The completed mapper removes the `missing_personality_projection` blocker only
for CoreMemory records that validate under the accepted source schema. It does
not by itself approve production sampling or live cognitive behavior.
