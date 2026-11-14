# Desktop security awareness and defense

Give Jophiel access to security logs so she can notice suspicious changes, investigate what happened, and learn from the outcome. Start with the Mac running the backend. Use the existing pairing and Observation paths so future collectors can report through the same interface. Approved protective actions build on that investigation loop in the next milestone.

## What we can reuse

- [The paired desktop relay](../backend/app/ai/desktop/relay.py) already establishes which controller belongs to which environment. [Desktop action capture](../backend/app/services/desktop_control.py) shows how Hands records an action and its outcome for Observation and Learning.
- [Observation](../backend/app/services/observation.py) already has typed reports, source identity, event and receipt times, deduplication, and source health and progress. Her mind's evaluation ([mind_turn.py](../backend/app/services/mind_turn.py)) is where a compact view of that information reaches her decisions (the shared-state view this plan first pointed to was retired on 2026-11-13).
- [Minecraft moments](../backend/app/services/game_moment.py) show how to capture what was known and check what changed before relying on a conclusion. Reuse that freshness check where it matters. Keep game-specific state in Minecraft and Windows-specific event fields in the Windows source.

## First milestone

1. **Add one Observation source.** Verify which security records a read-only collector can reliably access on the Mac, then choose one source that can support a useful investigation. Candidate evidence includes endpoint detections, firewall decisions, and changes to security settings. Send small validated batches through the paired relay and derive ownership and environment identity from the authenticated connection. Monitoring runs continuously while enabled and healthy, with its own opt-in and stop control, independent of screen sharing and input. Stopping monitoring closes intake and leaves previously collected history queryable until it expires. Leave the disabled interval visible in coverage if monitoring resumes.
2. **Keep place and time in existing records.** Use the paired environment ID as the source instance. Store event and receipt times and only the fields needed for investigation. Track collector health, the active collection filter, verified intervals, and gaps. A successful read can establish coverage even when there are zero matching events. Report zero matches for the full window only when coverage under the active filter is verified across that window. Deduplicate across retries and reconnects using stable event identity, and track log resets as a new stream epoch. After a relay outage, recover events only for intervals when monitoring was enabled and the collector can verify log continuity and the filter in effect. Report any remaining gap. Use Observation’s configurable retention for routine events, which defaults to 14 days. Report expired intervals as unavailable and present any retained evidence with its own timestamps and coverage.
3. **Expose one read-only tool.** Add `get_security_activity` through the existing chat tool registration. Mouth can call it on demand, and Mind can read the same evidence through Observation during an investigation without a user-initiated chat turn. Return a bounded timeline, evidence IDs, and coverage for the requested time window and event types. Each call targets one authorized environment. V1 monitors one Mac. Derive the environment’s available evidence and freshness in shared `situation` from the same source state. Store times in UTC and present them in the user’s local time when known. Treat event text as evidence from the source.

The Windows VM is a follow-on source. Selected [Sysmon](https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon) events can supply process and network evidence through the same interface. When that source is added, keep DNS lookups separate from network events that tie a process instance to an IP address and port. Simultaneous monitoring of multiple machines is outside this milestone.

Derive the current view from Observation. If a later investigation takes long enough for the answer to go stale, carry the relevant evidence IDs and source revision into Mind, then check the premise again before Mouth presents it as current.

## How it connects to the rest of Jophiel

| Component | Responsibility |
| --- | --- |
| Observation | Owns intake, coverage, and evidence. Routine traffic can end here. |
| Mouth | Calls the read tool on demand and explains what happened, where, when, and how much she could see. |
| Mind | Investigates a specific question about a machine and time window using the timeline, source evidence, coverage, and relevant lessons. |
| Cognition | Uses its existing decision path to decide whether and how to tell the owner. The System page shows findings and source health. |
| Memory and Learning | Keep meaningful findings and confirmed outcomes through existing evidence links. Turn reviewed outcomes and user corrections into scoped lessons that Mind can use in later investigations. |
| Hands | Owns any future response that changes the machine. An approved action should target the exact environment and executable, verify its effect, and record the result through the existing execution path. |

## What earns attention

Observation checks for changes on a timer. When a newly verified coverage gap overlaps an open investigation, it offers Mind one question about whether the gap changes what she can conclude. An idle outage stays visible on the System page. A sensor or a simple rule can also offer a finding for review. Both routes use the same investigation path. A routine lookup ends with its answer. An investigation stays open while it has an unresolved question about a specific machine and time window.

Group related signals into one investigation. An identical replay leaves the review as it was. Relevant new evidence, such as an upload that fills a gap, updates the existing review. Routine events stay in Observation. A protection tool reporting a threat or a security control changing unexpectedly are examples of reasons to look. Mind checks what the source confirmed and what the evidence leaves uncertain. If Cognition decides to speak, Mouth explains that distinction and names the affected machine and app, service, or control. Traffic dropped at the router would require router evidence.

## Adaptable guidance lives in lessons

Use the existing [Memory Lessons service](../backend/app/services/memory_lessons.py) for guidance that should improve with experience. Lessons can tell Jophiel which evidence to check first, how to recognize expected activity, and when a routine finding is worth mentioning. Mind weighs relevant lessons alongside current evidence, and Cognition uses applicable notification preferences when deciding whether to interrupt the owner.

For example, if the owner confirms that a burst of new network activity came from a scheduled update, Learning can record that pattern for the specific machine and updater with links to the observations and the correction. Mind can use the lesson when a similar pattern appears. A different executable or a new security detection gives her reason to investigate again and can lead Learning to narrow or revise the lesson. Reuse lesson retrieval, revisions, and use records so the System page can show which guidance influenced a decision.

Keep collection consent, environment ownership, coverage reporting, and firewall approval enforced by the existing tool and policy boundaries. A lesson can change how Jophiel interprets a signal and what she recommends. Any change to collection or enforcement goes through its existing controls.

## Next milestone · Approved protective action

Keep enforcement in the operating system. Choose the first Hands action based on an investigation that gives the owner a concrete decision. One candidate is applying an approved [macOS firewall](https://support.apple.com/guide/mac-help/block-connections-to-your-mac-with-a-firewall-mh34041/mac) restriction to a specific app on the selected Mac. Hands verifies the change, supports undo, and records the outcome as execution evidence. Learning can then review what the response accomplished alongside the original observations.

## Acceptance check

- The owner manually exercises a routine case and a case worth investigating using whichever Mac source was verified first. Let Jophiel review the records first and explain what the source confirms and where she is unsure. Then tell her what was done and whether it was expected. Compare her timeline and any alert with the Mac’s records.
- Open an investigation from a sensor signal, then let the periodic check find a verified coverage gap that overlaps its window. Check that the two signals reach the same investigation and that the gap prompts Mind to revisit what she can conclude.
- Query an empty window with healthy collection, then query a window that overlaps a collector outage. Jophiel distinguishes zero observed matches under the active filter from incomplete visibility and states which intervals she could see.
- Reconnect and replay the same batch. The timeline stays free of duplicates and the review stays unchanged. Verify recovery after a relay outage with a continuous log, and check that any unrecoverable interval is visible. Stop and restart monitoring and verify that the disabled interval remains outside collected history.
- Check that an unpaired or revoked controller cannot supply events for that environment and that another user cannot query its records.
- After the owner corrects a finding, check that Jophiel uses the relevant lesson on a similar case and records which revision she considered. Give her a later case with conflicting evidence and check that she reconsiders the earlier lesson.

## Implementation (2026-11-10, on `codex/general-desktop-control`)

**Where it runs first.** The owner chose the paired **Windows VM** (her own desktop) as the first source instead of
the Mac: it already has an authenticated, owner-bound link through the relay, and its event logs are richer. The
Mac stays a later source on the same interface. The source reads **whole channels** (the collection filter is a
list of channels, `SECURITY_CHANNELS`), never a list of attacks or event ids; deciding what matters is her mind's,
guided by lessons.

| Piece | Where |
| --- | --- |
| Read-only collector: `wevtutil gli/qe`, bookmarks, continuity by record number, gaps, resets, unreadable channels, ack-gated bookmarks | `backend/desktop_controller/src/desktop_controller/security.py` (controller 0.11.0) |
| Wire messages: `SecurityBatch` / `SecurityAck`, `SecurityConfig` in heartbeat replies (0.11.0+ only) | `desktop_controller/link.py` |
| Relay: batches from the authenticated connection only, stored off the read loop, answered | `backend/app/ai/desktop/relay.py` (`_SessionLink.security_batch`) |
| Owner's switch (epochs), intake, coverage, the read query and its rendering | `backend/app/services/security_activity.py` |
| Coverage over a window, piece by piece (pure) | `backend/app/ai/security/coverage.py` |
| Observation kinds `desktop_security.security_event` / `security_coverage` | `backend/app/ai/observe/registry.py` |
| `get_security_activity` (Mouth) and its prompt section | `backend/app/ai/tools/security/`, `prompts.py` (`_SECURITY_PROMPT`) |
| Investigations: signals, periodic look, gaps, late evidence; what Mind reads | `backend/app/services/mind_security.py` (source `security`, trigger `security`) |
| Delivery fences (its own conversation; the revision must still hold) | `backend/app/services/autonomous_speech.py` |
| Learning: investigations as facts in conversation reviews; Mind's `learn` nominations | `learning_assessment._security`, `learning_review.nominate` |
| Tables: `security_coverage`, `security_investigations`; switch columns on `desktop_environments` | migration `d8b3e6f1a4c7` |
| Owner UI: switch + channel health | `frontend/src/components/DesktopSessionPanel.tsx` (`SecurityMonitor`), `PUT/GET /api/desktop/environments/{id}/security` |

**Flow.** VM collector → `SecurityBatch` (≤ 48 KB) → relay → `security_activity.ingest` (owner and machine from
the connection: environment id + controller generation; refused while monitoring is off or for an old epoch) →
one observation per record (key: channel, record number, record time: a replayed batch stores nothing twice) +
merged coverage rows → `SecurityAck stored` → the VM moves its bookmarks. A lost answer means the same records are
read again and deduplicated.

**Coverage.** Each read reports per channel: `covered` (the bookmark record is still there with the same time, so
nothing in between was missed), `gap` (records after the bookmark were overwritten), `reset` (the log was cleared;
stream number bumped), `unavailable` (with the reason, e.g. "access denied: add the user to Event Log Readers").
Reading a window gives every moment one answer: expired (past retention) > off (owner switched it off) > covered >
gap/reset > unavailable > unverified (no report: the VM or the link was away). "Nothing matched" is complete only
when every channel asked was covered across the window. Times are the VM's clock (UTC), skew measured per batch.

**What earns attention.** `mind_security` sweeps every 30 s: records the log itself marked critical/error/warning
or audit failure (the source's own severity) open or join an investigation (window ± `SECURITY_CONTEXT_MINUTES`);
her periodic look (`SECURITY_REVIEW_MINUTES`, default 30, only when records arrived) joins or opens one; a coverage
gap overlapping an investigation, and records that arrive late inside its window, bump its revision. Each revision
is offered once to her slow thinking (`trigger "security"`), which reads the window (notable records, a sample,
counts by kind, coverage per channel), the conversation, memories and lessons. A question keeps it open; any other
conclusion (or "nothing worth concluding") marks it concluded until something changes; 24 h with no change closes it.
A thought that lands after the revision changed is stale; one proposed for speech is re-checked against the
revision before it is said, and only in the investigation's own conversation (the latest when it opened).

**Learning.** Her thoughts reach memory as usual (`thought_concluded`). A conversation review covering the time she
thought about an investigation gets it as a code-recorded fact (what the logs recorded, what code grouped, her
conclusion; "does not establish" whether it was expected). The owner's correction in that conversation is what a
lesson can rest on; lessons reach her later security thoughts through the same retrieval as her other thoughts, and
`reconsider` lets her challenge one. No security rule is written in code or prompts.

**Not yet.** The Mac as a source; protective actions (Hands, next milestone); a System page view of
investigations (source health shows under observation, errors under the errors list).
