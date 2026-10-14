# Scope/source implementation verification — October 13, 2026

## Scope

Implementation is documented in
[V1.10 implementation](../../../backend/app/ai/Cognitive/system/v0/SCOPE_SOURCE_V1_10_IMPLEMENTATION.md).
These are real-model, isolated in-process shadow evaluations, not frontend or
worker tests. They reuse the Oct 13 **development** corpus. The ten reserved
unfamiliar cases were not opened or executed. No original evaluation output was
overwritten; no database, worker configuration or visible conversation was changed.

## Runs

| Directory | Purpose | Accepted turns | Closed loops |
| --- | --- | --- | --- |
| `smoke/` | Four initial routing/comparison controls | 4/4 | None |
| `expanded/` | Twelve cases under two characters | 44/44 | 4/4, six turns each |
| `coverage-intermediate/` | Source-coverage instruction follow-up | 4/4 | None |
| `final/` | Final source metadata plus closed-loop regression | 28/28 | 4/4, six turns each |

Each gzip artifact contains exact capture, model payloads/proposals, source
references, terminal record, realized reply and boundary evidence. Summary JSONs
list completed runs. One repeat per character is **not** a robust effect estimate.
Both profiles use the harness's same trait projection and different written
character content; this is not a broad dynamic-state experiment.

The first three directories are intermediate development snapshots. Source
metadata and contribution instructions changed during this implementation; these
in-flight v1.10 captures are retained for diagnosis, not advertised as replayable
with final code. Replay verification targets `final/`; existing earlier released
registry behavior is separately covered by automated regressions.

## Observed improvements

- Present hypothetical preference questions now enter the opinion route, rather
  than being suppressed as hypothetical acts or misclassified as actual identity.
- Genuine content generation stays on the artifact route; external changes stay
  on the action route. Missing tools/content remain missing, not invented.
- Source-dependent dialogue comparisons enter `dialogue_reasoning`, with actual
  speaker attribution and bounded citation selectors.
- Both character conditions distinguished unchanged recommendations with stronger
  support from genuine recommendation reversals in the development comparisons.
- Notebook loops expressed different tastes: elaborate naming versus plain
  naming. The opposing character maintained a general preference while allowing
  a personally meaningful exception. No forced identical opinion was used.
- Volunteer loops retained optional participation, incorporated reported benefits,
  and distinguished an urgent-meeting exception from general preference.

## Acceptance is not correctness

1. Expanded opposing long-source case: contribution incorrectly denied an earlier
   view despite receiving a valid assistant excerpt. No compiler/source rejection
   occurred. The coverage instruction corrected this, but the next standard run
   wrongly suggested an additional final qualification was absent. Adding source
   length and prefix/suffix coverage supplied the missing location information.
2. Final standard notebook turn 3 still says “reason to make an exception” although
   the character already preferred that title. It also says its general preference
   is unchanged. The bad premise originates in contribution, not realization.
   Treat this as a remaining semantic defect and an unsuitable positive training
   sample, even though the conversation completes.
3. The bare-reference control asks “What would you call it?” rather than explicitly
   clarifying what “it” denotes. Routing availability is better than the wording.
4. Repeated “tentatively” and internal “runtime” capability wording remain.
5. Real generation functionality and tool execution were not added by this work.

No claim of complete human-like behavior, general opinion consistency, or solved
dynamic-state conditioning follows from these results. Freeze this implementation
before independent held-out testing; do not repeatedly tune on the reserved cases.

## Automated checks

Final run: **376 passed, 1 skipped**, covering scope/source regressions, follow-up
handoff, character opinions, V1 orchestration, continuity, compact orchestration,
and requirement-resolution engines/pipeline. `git diff --check` passed.

## Final real-model verification

All 28 final-revision turns completed and produced accepted shadow responses.
Both long-source cases correctly described optional pauses and their final
qualification; both missing-position cases avoided adopting the user's alleged
past dislike as an established earlier belief. All four closed loops finished.
The remaining standard-notebook “exception” defect is explicitly noted above.
