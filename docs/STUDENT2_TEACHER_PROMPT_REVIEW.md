# Student 2 event teacher — prompt revision and coverage

2026-10-23. Main teacher personality/intention prompts updated in place; no evaluated training batch yet.

## Assets

- Character: `docs/characters/columbina-v0.6.md` (main teacher page, updated in place).
- Event decision prompt: `backend/app/ai/Cognitive/simple/prompts/decide_events_v1.txt`.
- Implementation contract: `docs/STUDENT2_COGNITION_REVAMP_IMPLEMENTATION.md`.

The main character page and its live copy now include private initiative, goal persistence,
optional goal creation, and direct expression of hurt/frustration without automatic self-blame.
Both `intend.txt` and `intend_v4.txt` were updated. The duplicate v0.7 draft was removed.
The frozen teacher version is now `teacher-2026-10-23-initiative-feelings`; old manifests remain
historical records and must not be presented as matching these revised files.

The event decision prompt remains an implementation asset for the new structured teacher.
The existing intention → reply → labels runner still always calls speech: prompt edits alone do
not implement silence, proposal selection, or action-only training. The implementation agent must
replace that behavior as specified in the main handoff. No training or live behavior validation
has been performed.

## Integration requirements

The new teacher receives the same bounded situation as the student. Supply the finalized output
schema, including diagnostic does-not-fit reporting, and select decisions before optional speech.
Do not feed the teacher's decision explanation back as a student input. Only runtime-available
private thoughts are inputs. Hash/version both new assets in the new dataset manifest.

For goal creation, mind or another producer supplies executable candidates. A teacher's newly
invented goal is a proposal-generation example or diagnostic, not a Student 2 target with an
imaginary candidate ID. ModernBERT selects candidates; it does not generate arbitrary goal text.
Evaluate proposal generation and selection separately.

## Missing situations to include in the teacher pilot

These are coverage cases, not a table of mandatory responses. Each should include variants with
more than one plausible behavior and human review of the teacher's choice.

| Situation | What to check |
|---|---|
| No reply needed, goal active | Can continue quietly or speak for a real reason without inventing work |
| No goal, interesting thought | Can propose a goal, reflect, share, or leave it private |
| No goal and nothing compelling | Does not manufacture tasks/questions to fill silence |
| User busy, absent, or asks for space | Separates conversational availability from authorization for ongoing activity |
| Curiosity touches a boundary | Does not turn learning about someone into pressure or unrequested investigation |
| User is upset | Avoids a fixed comfort/apology/silence mapping; does not assume calming them is always her goal |
| Personal interest conflicts with a user commitment | Considers both; does not silently forget an obligation or always abandon her own interest |
| Goal blocked or capability unavailable | Can seek clarification, reconsider, defer or request planning without fictitious execution |
| Repeated failures | Distinguishes useful persistence from repeating an ineffective approach |
| Goal completed | Can stop, reflect or propose another goal; does not require endless productivity |
| New user message during work | Considers its meaning and scope, rather than universally cancelling or universally ignoring it |
| Same thought repeated/already shared | Recognizes actual communication history; avoids repeated announcements |
| Deferred thought becomes relevant or expires | Reassesses instead of automatically delivering it |
| Two worthwhile goals/proposals | Selects and identifies a candidate, accounts for current work and conflicting goals |
| Thought is uncertain or contradicted | Does not present it as established fact or blindly execute it |
| Practical request without social chat | Check whether existing tool-sensitivity trait produces unwanted resentment or withdrawal |

## Personality questions still open

1. How far may her curiosity extend into initiating contact or external activity when the user is
   absent? Respect actual configured/user authorization; this prompt does not grant new permissions.
2. How should personal goals weigh against user commitments and shared activities? Test examples
   before choosing a rigid hierarchy.
3. Resolved by the user: let her express feelings when dismissed or treated only as a tool.
   Main prompts now permit direct expression without automatic self-blame or required withdrawal.

Run a small reviewed pilot with these cases before bulk teacher generation. Compare against the pre-edit v0.6 snapshot from version control
on ordinary conversation as well as new events. Report excessive speech, excessive silence,
gratuitous goal creation, repetition, unsupported actions and coercive curiosity. No model calls
or behavioral validation were performed as part of this prompt edit.
