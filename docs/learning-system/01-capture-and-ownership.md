# Capture and ownership

Status: design decisions accepted 2026-11-01; implementation requested and built 2026-11-01 (uncommitted, branch `feature/learning-01-capture-ownership`): see [Step 1 implementation](01-implementation.md) for the schema, settings and what isn't covered. [Plan index](README.md).

## Outcome

Capture what each learner knew and produced at decision time, and link it to what actually executed. Persistence runs in the background so responding and acting do not wait for storage. Durable reconstruction is available for successfully saved chains; capture loss remains possible and must not be disguised as a complete record.

Dependencies: None.

## Ownership and storage

- Student 1 records its interpretation attempt; Student 2 records its choice; delivery and hands record actual execution, results, and failures.
- A shared capture service owns persistence and linking. Learning reads the records and adds assessments without reconstructing or overwriting component reports. Memory retains ownership of memory writes.
- Reuse observation, delivery, and game records. Add dedicated decision-attempt records where exact learner inputs and outputs are missing. Do not force the chain into legacy `learning_memories`.
- Assign an attempt ID before each learner invocation. Carry it through downstream decisions and execution records. Each actual retry gets a new attempt ID linked to the prior attempt; persistence retries reuse the same record identity.

## Capture contract

Freeze the actual supplied input at decision time, before later outcomes can alter it. Capture the actual output when produced. Include user UUID, conversation or game context, upstream attempt IDs, timestamps, schema/model versions, rendered instructions, retrieved memory and lesson revisions, generation settings, and media references where applicable.

Versioned references can replace embedded content only while the exact immutable content remains retrievable. Exact capture preserves what was supplied; it does not guarantee identical model output on replay. Missing information stays explicitly absent rather than being reconstructed later and presented as original input.

Keep intended actions separate from execution and delivery results, including deviations. Distinguish known failure from an unknown result. Define optional prediction and selection links; missing predictions, alternatives, or selection probabilities remain absent.

## Persistence and failure handling

Actions do not wait for persistence. Freeze and queue capture without a synchronous durable-write requirement, then retry saving in the background within bounded limits. Expose detected capture gaps after failures or exhausted retries. A sudden process crash can lose queued records, and the system cannot promise to identify every lost record.

Exclude incomplete attempts from training that requires their missing information. Recovery must preserve record identity and avoid duplicate evidence. Exact queue limits, retry settings, and storage schema are implementation details to specify before coding; no numeric latency guarantee was agreed.

## Retention, access, and training derivatives

- Raw decision captures have a configurable retention period, initially 30 days. Deleting the originating conversation or memory removes associated raw copies sooner.
- Within enabled learning settings, Columbina may automatically convert conversations and memories into training examples without asking for each conversion.
- Training examples are self-contained: retain exact decision-time input, target, relevant versions, and only the supporting evidence needed to explain the label. They do not require the raw source to remain available or a navigable source link.
- Keep an opaque internal source identifier and derivation associations so explicit corrections can identify affected examples after source deletion. This identifier does not preserve the original source content.
- Deleting a source does not delete training examples already created from it, including any source details retained in those examples. These examples remain eligible under the applicable training rules.
- Provide a separate explicit training-data deletion operation that removes examples from future training uses, including affected dataset exports. It does not undo influence on already-trained models; model-level removal requires a separate strategy.
- Explicitly correcting a source claim is different from deleting it: flag examples derived from the incorrect claim as ineligible for future training until reviewed. Preserve them for inspection unless explicitly deleted.
- Captures and retained training examples stay private to the originating user. Retention does not authorize access or training use for other users. This accepted boundary supersedes the earlier blanket permission for cross-user training; the later shared-training plan must respect it.

## Verified foundations and gaps

Read-only inspection on 2026-11-01; recheck contracts before implementation.

| Existing component | Reuse and remaining gap |
| --- | --- |
| Student 2 teacher path, `backend/app/services/student_two.py` | Already captures detailed snapshot, rendered prompt, system instructions, schema, raw output, versions/settings, and identities in background JSONL. Best-effort writes can fail; effects currently precede logging. Local inference still has summary-only capture. |
| Student 1, `backend/app/services/cognitive_live.py` | Logs selected inputs/outputs after the reply; exact input and attempt/version lineage remain incomplete. |
| Observation, `backend/app/services/observation.py` | Existing source identity, provenance, links, deduplication, and consumer receipts provide the evidence foundation. Persistence participates in the caller transaction. |
| Delivery, `backend/app/db/models.py` and `backend/app/services/observation_adapters.py` | Reuse durable outbox and delivery observations; add attempt linkage and actual settlement timing. |
| Games, `backend/app/services/game_learning.py` and `backend/app/services/desktop_commands.py` | Reuse experience and skill-attempt records. Some command/result evidence is bounded or expires in Redis; general exact learner capture and durable linkage remain incomplete. |
| Memory and legacy records | Preserve `memory_manager` ownership and retry-safe observation derivation. `learning_memories` has a schema but no active capture pipeline found in the inspection. |

## Completion evidence

Demonstrate one conversation chain and one game attempt, including:

- Exact decision-time inputs and actual outputs, with no later information leaking into inputs.
- Upstream interpretation/choice links and separate intended versus executed actions.
- Delivery failure, execution failure, and unknown-result handling.
- Storage outage with continued responses/actions, bounded background retries, and visible detected gaps.
- Restart recovery without duplicate records; lost or incomplete chains remain incomplete.
- Raw retention/source deletion, independent training-example retention/deletion, correction-driven invalidation, and user access boundaries.

This milestone proves capture and ownership contracts, not learning improvement. Later plans own attribution quality, adaptation, training, and model evaluation.
