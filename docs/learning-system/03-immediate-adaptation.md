# Immediate adaptation

Status: design decisions accepted 2026-11-01; built on request the same day ([implementation](03-implementation.md)). [Plan index](README.md).

This plan is authoritative for the immediate-adaptation decisions below. It refines the
[architectural design](../COLUMBINA_LEARNING_SYSTEM_DESIGN.md), including its earlier wording about applying
corrections: feedback prompts reasoning, not automatic obedience.

## Outcome

A supported lesson improves a later relevant decision without changing weights. Lessons inform Columbina's
judgment alongside the current situation, personality, and established values; they are not commands.

Dependencies: [Step 2](02-evidence-and-attribution.md). Existing capture, privacy, retention, and memory-ownership
boundaries continue to apply.

The first proof is conversational adaptation through Student 2. This is sufficient for Step 3; a game example is
not required now. The broader learning architecture still covers games and other actions. Deliberate trials remain
in Step 4, and consolidation remains in later plans.

## From assessment to lessons

Step 2 already determines when to investigate: corrections, complaints, failures, and sampled ordinary interactions.
This step determines what supported findings should change in future behavior. An assessment does not automatically
become a lesson merely because an investigation ran or an evaluator approved it.

- Columbina may accept lessons automatically from supported findings, including failures and experiences where the
  user never explicitly asked her to remember anything. Routine user approval is not required.
- Clear statements establish the user's expressed preferences or intended meaning within their stated scope.
  They do not establish that every requested behavior is a good choice or that an external factual claim is true.
- Inferred lessons require evidence for both the conclusion and its applicability. Guesses remain hypotheses;
  repeated reflection or evaluator agreement does not create independent support.
- An episode correction is not automatically a standing preference. “Yesterday I wanted you to listen” corrects
  that episode; it does not establish “Always listen instead of offering advice.” Broader applicability requires
  an explicit future preference or further supporting evidence.
- Preserve each lesson's scope, exceptions, supporting evidence, counterevidence, origin, status, and revision.
  Preserve uncertainty rather than forcing every experience into a lasting behavioral rule.

## Judgment and ownership

Cognition owns the current choice. Learning reasons about what should persist, and existing memory owners perform
memory writes. Retrieved lessons provide relevant experience and advice for cognition to weigh.

Neither a current instruction nor a stored lesson replaces Columbina's judgment. She considers what the user means,
the evidence, her personality, and her established values when deciding whether and how to act. A complaint or
correction must not create a hardcoded rule requiring compliance. Core values remain fixed; affinity and mood remain
outside V1 under the existing design.

Distinguish what the user currently wants from what Columbina believes is a good choice. If the user no longer prefers
short answers, she should stop representing short answers as their current preference. She may still choose a short
answer because it fits the situation. Similarly, a request to ignore a failed action does not erase evidence of failure.

## Immediate use and durable memory

Clear corrections already present in the conversation inform the next relevant decision without waiting for background
assessment or durable storage. This immediate use still involves judgment; it is not an obedience bypass.

Accepted lessons are stored through existing memory owners for later conversations. Demonstrate both immediate use
and later retrieval when the original correction is no longer in conversational context. Following visible context
alone does not prove persistent learning.

## Scope, conflicts, and revision

- Preserve compatible preferences at different scopes. “Keep answers short” and “Explain coding problems thoroughly”
  can coexist; the more specific lesson is usually more relevant to a coding question.
- Give the current request strong contextual weight while retaining judgment. It does not mechanically override all
  evidence, lessons, or values. This refines the earlier Step 2 wording that a current instruction governs the interaction.
- When applicability is uncertain, favor narrower use. A lesson about listening during venting must not prevent
  practical help when the user is asking to fix a frustrating bug. Clarify when the distinction materially affects the choice.
- Recognize unresolved conflicts between equally relevant lessons rather than inventing certainty or silently picking
  whichever was retrieved first.
- A counterexample prompts reasoning about an exception, changed preference, or mistaken lesson. Keep, narrow, revise,
  or withdraw the lesson according to the evidence; one bad result does not automatically erase it.
- A request to stop using a preference establishes that it is no longer the user's stated preference. Reason separately
  about any independently supported conclusion or strategy; do not keep attributing the withdrawn preference to the user.
- Once a lesson is revised or withdrawn through that reasoning, preserve the resulting status and revision. Older
  background work must not silently restore the superseded lesson. This consistency rule does not make feedback an
  automatic behavioral command.

Personal lessons remain attached to their originating user and applicable context under the existing privacy boundary.
Do not turn one person's preference into a universal rule. Shared-model training is outside this step.

## Retrieval and Student 2

Recall a bounded selection of relevant accepted lessons from memory, including their applicability and exceptions,
rather than supplying every lesson. Prioritize direct relevance when capacity is limited; do not strip exceptions in
a way that changes a lesson's meaning. The storage representation, ranking method, and numerical retrieval budget
remain implementation design work.

Record which lesson revisions each consumer actually received. Existing ordinary-memory input alone does not prove
that a lesson was understood or used.

The user identifies Luna as the current live Student 2 model. Use Luna as the baseline and test the conversational
loop now; preparing or training a future Qwen model is not a prerequisite. Individual lessons must affect behavior
without retraining. Future model changes should preserve the lesson interface and be evaluated against this behavior.

Distinguish three outcomes when a lesson is not followed:

1. It never reached Student 2: investigate memory, retrieval, or input delivery.
2. It reached Student 2 but was misunderstood or overlooked: investigate cognition, including model capability.
3. It was understood and reasonably not followed: this may be valid judgment, not a learning failure.

Do not claim compatibility or adaptation solely from a model name, a lesson appearing in input, or a change in mouth
wording. Keep concrete input/schema changes coordinated with training work without blocking the Luna demonstration
on a future model migration.

## Inspection

Provide a simple way to inspect the lesson, its scope and exceptions, supporting and contradicting evidence, revisions,
and current status. Show which revisions were available to a particular consumer during a decision, with a short
explanation grounded in recorded evidence. Distinguish receiving a lesson from behavior reflecting it; availability
alone is not proof of influence.

## Implementation work remaining

- Define concrete lesson storage and lifecycle contracts through existing memory owners, including revision and
  withdrawal consistency under asynchronous work.
- Define lesson construction from supported assessments while preserving hypotheses and unresolved cases.
- Specify retrieval ranking, bounded input size, and how scope, exceptions, and conflicts reach cognition.
- Add and capture the Student 2 lesson interface using the current Luna path; make actual consumer support inspectable.
- Build the lesson inspection view and conversational evaluation cases below.

These details remain implementation design work. This accepted plan does not authorize coding, establish numerical
evidence thresholds, settle future-model migration mechanics, or define consolidation and training-data acceptance.

## Completion evidence

Use a fixed conversational walkthrough and held-out related situations to demonstrate:

- A supported correction informs the next relevant choice while durable processing may still be pending.
- In a fresh conversation without the original correction in context, Student 2 receives the accepted lesson and makes
  an appropriate choice in an unseen related situation.
- An unrelated choice remains unaffected, and an explicit request for advice is handled appropriately despite a lesson
  about listening during venting.
- A correction about a single past episode does not become an unsupported permanent preference.
- General and specific preferences coexist; current context and judgment resolve their application.
- Counterevidence leads to an appropriate exception, revision, withdrawal, or reasoned retention.
- A preference the user has disclaimed is no longer represented as their current preference. A lesson actually
  withdrawn from use is no longer supplied as active, and stale background work does not restore it.
- Columbina can reasonably decline feedback that conflicts with her established values. Agreement with the latest
  correction is not the success criterion.
- Inspection distinguishes missing retrieval, misunderstood lessons, and justified decisions not to follow a lesson.

Inspect Student 2's actual choice alongside the reply and captured lesson revisions. Mouth-only adaptation, a lesson
appearing in the input, or Columbina saying “I remember” cannot stand in for demonstrated Student 2 adaptation.

