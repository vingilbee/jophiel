import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AppHeader } from '../components/AppHeader'
import { ColumbinaFeathers } from '../components/ColumbinaFeathers'
import { learningApi } from '../api/learning'
import type {
  AssessmentDetail,
  AssessmentListing,
  AssessmentSummary,
  Challenge,
  LearnedSkill,
  Lesson,
  Proposal,
  ProposalStatus,
  Reflection,
} from '../types/learning'

const STATUS_STYLES: Record<string, string> = {
  open: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  accepted: 'border-[#DCCFF2] bg-[#F2EFFB] text-[#6A4FA0]',
  implemented: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  rejected: 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  confirmed: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  on: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  off: 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  trial: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  retired: 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  active: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  done: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  'gave up': 'border-[#E7B5C5] bg-[#FBEFF3] text-[#8B3158]',
  'you changed it': 'border-[#DCCFF2] bg-[#F2EFFB] text-[#6A4FA0]',
  'session ended': 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  supported: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  concluded: 'border-[#BFE3CF] bg-[#EEF9F2] text-[#3E7A57]',
  plausible: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  unresolved: 'border-[#DCCFF2] bg-[#F2EFFB] text-[#6A4FA0]',
  contradicted: 'border-[#E7B5C5] bg-[#FBEFF3] text-[#8B3158]',
  failed: 'border-[#E7B5C5] bg-[#FBEFF3] text-[#8B3158]',
  'no feedback': 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  pending: 'border-[#EAF0FA] bg-[#F8FAFF] text-[#7183A3]',
  running: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
}

const TRIGGER_LABELS: Record<AssessmentSummary['trigger_kind'], string> = {
  correction: 'a correction',
  complaint: 'a complaint',
  execution: 'something that failed',
  sample: 'an ordinary exchange (sampled)',
}

const PART_LABELS: Record<string, string> = {
  student_1: 'how she understood it',
  student_2: 'what she chose to do',
  mouth: 'her wording',
  delivery: 'delivery',
  timing: 'timing',
  hands: "her hands' choice",
  skill: 'the skill itself',
  planner: 'the planner',
  memory: 'memory',
  environment: 'the world around her',
  user_side: 'nothing on her side',
  unclear: 'unclear',
}

const STOP_LABELS: Record<string, string> = {
  concluded: 'the evidence was enough',
  missing_evidence: 'needs evidence that is missing',
  stuck: 'stopped: nothing new to look at',
}

const CHALLENGE_LABELS: Record<Challenge['status'], string> = {
  active: 'active',
  done: 'done',
  given_up: 'gave up',
  replaced: 'you changed it',
  expired: 'session ended',
}

function Chip({ label }: { label: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] tracking-[0.06em] uppercase ${STATUS_STYLES[label] ?? STATUS_STYLES.rejected}`}>
      {label}
    </span>
  )
}

function day(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** A proposal as a self-contained brief a coding agent can act on. */
function agentBrief(p: Proposal): string {
  const evidence = p.evidence
    .map((e) => {
      const tag = e.after_implemented ? ' (after it was marked implemented)' : ''
      const lines = e.examples.map((x) => `  - \`${x}\``).join('\n')
      return `- Session ${e.session_id.slice(0, 8)} on ${day(e.at)}${tag}\n${lines}`
    })
    .join('\n')
  return [
    `# Proposal from Columbina: ${p.title}`,
    '',
    `Game: ${p.game} · Area: ${p.area} · Seen ${p.occurrences}× · Key: \`${p.normalized_key}\` · Id: ${p.id}`,
    '',
    '## Problem',
    p.problem,
    '',
    '## Suggested change',
    p.suggestion,
    '',
    '## Evidence (skill outcomes recorded by code)',
    evidence,
    ...(p.user_note ? ['', '## Note from the user', p.user_note] : []),
    '',
    '## Context',
    'The Minecraft game pack is in `game_packs/minecraft` (skills in `src/skills`, outcome contract in ' +
      '`src/skills/contract.js`). Test on the test server (`bun run itest`) before it reaches the user\'s ' +
      'world. Raw experiences are in the `game_experiences` table by id. When done, mark this proposal ' +
      'implemented on the Learning page: if the problem comes back, it reopens by itself.',
  ].join('\n')
}

export function LearningPage() {
  const [challenges, setChallenges] = useState<Challenge[] | null>(null)
  const [skills, setSkills] = useState<LearnedSkill[] | null>(null)
  const [proposals, setProposals] = useState<Proposal[] | null>(null)
  const [lessons, setLessons] = useState<Lesson[] | null>(null)
  const [reflections, setReflections] = useState<Reflection[] | null>(null)
  const [assessments, setAssessments] = useState<AssessmentListing | null>(null)
  const [onlyOpen, setOnlyOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, s, p, l, r, a] = await Promise.all([
        learningApi.challenges(),
        learningApi.skills(),
        learningApi.proposals(),
        learningApi.lessons(),
        learningApi.reflections(),
        learningApi.assessments(onlyOpen ? 'unresolved' : undefined),
      ])
      setChallenges(c)
      setSkills(s)
      setProposals(p)
      setLessons(l)
      setReflections(r)
      setAssessments(a)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load what she learned.')
    }
  }, [onlyOpen])

  useEffect(() => {
    void load()
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    }
  }

  const active = lessons?.filter((l) => l.status !== 'retired') ?? []
  const retired = lessons?.filter((l) => l.status === 'retired') ?? []

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#F8FAFF] text-[#23314F] md:flex-row">
      <AppHeader />
      <ColumbinaFeathers className="pointer-events-none absolute -top-12 -right-10 w-72 opacity-55" />
      <main className="relative mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-8 lg:px-12">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-[-0.03em]">What she's learned.</h1>
          <p className="mt-2 max-w-xl text-sm text-[#7183A3]">
            Moves she taught herself are hers to use at once. After each game session Columbina looks back on
            what happened. Lessons go straight into how she plays. Proposals are changes to her game code: she never makes them herself. Review them here and
            hand the good ones to a coding agent.
          </p>
        </div>

        {error && (
          <p className="mb-5 rounded-xl border border-[#E7B5C5] bg-[#FBEFF3] px-3 py-2 text-xs text-[#8B3158]">{error}</p>
        )}

        <Section
          title="How it went"
          hint="After a correction, a complaint, something failing, or a sampled ordinary exchange, she looks back: what it was about, which part of her (if any) was responsible, and how sure the evidence makes that. Unresolved means the evidence doesn't settle it. Add a note to correct her reading."
        >
          <label className="mb-1 flex items-center gap-2 text-xs text-[#7183A3]">
            <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
            Only unresolved
          </label>
          {assessments === null && !error && <Loading />}
          {assessments?.items.length === 0 && <Empty text="Nothing assessed yet. Corrections and failures are looked at automatically, a minute after they happen." />}
          {assessments?.items.map((a) => (
            <AssessmentCard key={a.id} summary={a} onChanged={load} onError={setError} />
          ))}
        </Section>

        <Section
          title="Challenges she set herself"
          hint="While exploring, she picks her own next challenge after each goal and teaches herself the moves it needs. Newest first."
        >
          {challenges === null && !error && <Loading />}
          {challenges?.length === 0 && <Empty text="No challenges yet. With exploring on, she picks one each time a goal is done." />}
          {challenges?.map((c) => <ChallengeRow key={c.id} challenge={c} />)}
        </Section>

        <Section
          title="Moves she taught herself"
          hint="Programs that worked when she practiced a move. Her hands use them like any skill, in any session, while they're on. Learning one again replaces it."
        >
          {skills === null && !error && <Loading />}
          {skills?.length === 0 && <Empty text="No moves yet. Ask her to learn one while you play (e.g. pillar up by placing blocks)." />}
          {skills?.map((s) => (
            <SkillCard
              key={s.id}
              skill={s}
              onEnabled={(enabled) => act(() => learningApi.setSkillEnabled(s.id, enabled))}
              onDelete={() => act(() => learningApi.deleteSkill(s.id))}
            />
          ))}
        </Section>

        <Section title="Proposals" hint="Changes to the game code she suggests, with the outcomes behind them.">
          {proposals === null && !error && <Loading />}
          {proposals?.length === 0 && <Empty text="No proposals yet. They appear when a skill keeps failing in a way she can't fix by playing differently." />}
          {proposals?.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              onStatus={(status) => act(() => learningApi.updateProposal(p.id, { status }))}
              onNote={(user_note) => act(() => learningApi.updateProposal(p.id, { user_note }))}
            />
          ))}
        </Section>

        <Section
          title="Lessons"
          hint="Applied automatically: skill lessons go to her hands, planning lessons to Columbina when she sets goals. Seen in two sessions = confirmed; contradicted twice = retired."
        >
          {lessons === null && !error && <Loading />}
          {lessons?.length === 0 && <Empty text="No lessons yet. Play a session with her and end it: she reflects when it ends." />}
          {active.map((l) => (
            <LessonRow
              key={l.id}
              lesson={l}
              onRetire={() => act(() => learningApi.setLessonStatus(l.id, 'retired'))}
              onDelete={() => act(() => learningApi.deleteLesson(l.id))}
            />
          ))}
          {retired.length > 0 && (
            <details className="rounded-2xl border border-[#EAF0FA] bg-white px-4 py-3">
              <summary className="cursor-pointer text-xs text-[#7183A3]">Retired ({retired.length})</summary>
              <div className="mt-3 flex flex-col gap-2">
                {retired.map((l) => (
                  <LessonRow
                    key={l.id}
                    lesson={l}
                    onRestore={() => act(() => learningApi.setLessonStatus(l.id, 'trial'))}
                    onDelete={() => act(() => learningApi.deleteLesson(l.id))}
                  />
                ))}
              </div>
            </details>
          )}
        </Section>

        <Section title="Reflections" hint="One per ended game session.">
          {reflections === null && !error && <Loading />}
          {reflections?.length === 0 && <Empty text="No sessions reflected on yet." />}
          {reflections && reflections.length > 0 && (
            <ul className="divide-y divide-[#EAF0FA] rounded-2xl border border-[#EAF0FA] bg-white">
              {reflections.map((r) => (
                <li key={r.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2.5 text-xs">
                  <span className="w-14 shrink-0 text-[#7183A3]">{day(r.created_at)}</span>
                  <span className="font-medium">{r.game}</span>
                  <span className="text-[#617698]">{reflectionSummary(r)}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </main>
    </div>
  )
}

function reflectionSummary(r: Reflection): string {
  if (r.status === 'pending' || r.status === 'processing') return 'reflecting…'
  if (r.status === 'failed') return `failed: ${r.last_error ?? 'unknown error'}`
  const res = r.result ?? {}
  if (r.status === 'skipped') return `too short to learn from (${res.outcomes ?? 0} outcomes)`
  const parts = [
    `${res.outcomes ?? 0} outcomes`,
    res.lessons?.new.length ? `${res.lessons.new.length} new lesson${res.lessons.new.length > 1 ? 's' : ''}` : '',
    res.lessons?.supported.length ? `${res.lessons.supported.length} confirmed again` : '',
    res.lessons?.contradicted.length ? `${res.lessons.contradicted.length} contradicted` : '',
    res.proposals?.new.length ? `${res.proposals.new.length} new proposal${res.proposals.new.length > 1 ? 's' : ''}` : '',
    res.proposals?.reopened.length ? `${res.proposals.reopened.length} reopened` : '',
    res.episode_id ? 'remembered as an episode' : '',
  ]
  return parts.filter(Boolean).join(' · ')
}

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 mb-3 text-xs text-[#7183A3]">{hint}</p>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  )
}

function Loading() {
  return <p className="text-xs tracking-[0.08em] text-[#7183A3] uppercase">Loading…</p>
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-[#B9DCF4] bg-white px-6 py-8 text-center text-sm text-[#7183A3]">{text}</p>
  )
}

const SMALL_BUTTON =
  'rounded-full border border-[#DCE6F5] bg-white px-3 py-1 text-[11px] text-[#617698] transition-colors hover:border-[#8B3158] hover:text-[#8B3158] disabled:opacity-50'

function ProposalCard({
  proposal: p,
  onStatus,
  onNote,
}: {
  proposal: Proposal
  onStatus: (status: ProposalStatus) => void
  onNote: (note: string) => void
}) {
  const [note, setNote] = useState(p.user_note ?? '')
  const [copied, setCopied] = useState(false)
  const reopened = p.evidence.some((e) => e.after_implemented)

  async function copy() {
    try {
      await navigator.clipboard.writeText(agentBrief(p))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  return (
    <article className="rounded-2xl border border-[#EAF0FA] bg-white px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{p.title}</h3>
        <span className="text-[11px] text-[#7183A3]">{p.area} · seen {p.occurrences}×</span>
        <Chip label={p.status} />
      </div>
      {reopened && p.status === 'open' && (
        <p className="mt-2 text-xs text-[#8B3158]">Came back after it was marked implemented.</p>
      )}
      <p className="mt-3 text-sm text-[#40506F]">{p.problem}</p>
      <p className="mt-2 text-sm text-[#40506F]">
        <span className="font-medium">Suggestion: </span>
        {p.suggestion}
      </p>
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-[#7183A3]">Evidence ({p.evidence.length} session{p.evidence.length > 1 ? 's' : ''})</summary>
        <ul className="mt-2 flex flex-col gap-1">
          {p.evidence.flatMap((e) =>
            e.examples.map((x, i) => (
              <li key={`${e.session_id}-${i}`} className="font-mono text-[11px] break-words text-[#617698]">
                {day(e.at)} · {x}
              </li>
            )),
          )}
        </ul>
      </details>
      <textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        onBlur={() => note !== (p.user_note ?? '') && onNote(note)}
        placeholder="Your note (e.g. the commit that fixed it)"
        rows={1}
        className="mt-3 w-full resize-y rounded-xl border border-[#EAF0FA] bg-[#F8FAFF] px-3 py-2 text-xs outline-none focus:border-[#B9DCF4]"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={copy} className={SMALL_BUTTON}>{copied ? 'Copied' : 'Copy for agent'}</button>
        {p.status !== 'accepted' && p.status !== 'implemented' && (
          <button onClick={() => onStatus('accepted')} className={SMALL_BUTTON}>Accept</button>
        )}
        {p.status !== 'implemented' && (
          <button onClick={() => onStatus('implemented')} className={SMALL_BUTTON}>Mark implemented</button>
        )}
        {p.status !== 'rejected' && (
          <button onClick={() => onStatus('rejected')} className={SMALL_BUTTON}>Reject</button>
        )}
        {p.status !== 'open' && (
          <button onClick={() => onStatus('open')} className={SMALL_BUTTON}>Reopen</button>
        )}
      </div>
    </article>
  )
}

function AssessmentCard({
  summary: a,
  onChanged,
  onError,
}: {
  summary: AssessmentSummary
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const [detail, setDetail] = useState<AssessmentDetail | null>(null)
  const [note, setNote] = useState('')

  async function open(isOpen: boolean) {
    if (!isOpen || detail) return
    try {
      setDetail(await learningApi.assessment(a.id))
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not load the assessment.')
    }
  }

  async function sendNote() {
    if (!note.trim()) return
    try {
      setDetail(await learningApi.noteAssessment(a.id, note.trim()))
      setNote('')
      await onChanged()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not add the note.')
    }
  }

  const status = a.state === 'done' ? (a.outcome === 'no_feedback' ? 'no feedback' : a.outcome ?? 'pending') : a.state
  const current = detail?.revisions[0]
  return (
    <article className="rounded-2xl border border-[#EAF0FA] bg-white px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">After {TRIGGER_LABELS[a.trigger_kind]}</h3>
        {a.target_status && a.target_status !== 'none' && <span className="text-[11px] text-[#7183A3]">about it: {a.target_status}</span>}
        {a.cause_status && a.cause_status !== 'none' && <span className="text-[11px] text-[#7183A3]">· cause: {a.cause_status}</span>}
        <Chip label={status} />
      </div>
      <p className="mt-1 text-[11px] text-[#7183A3]">
        {day(a.anchor_at)} · {a.trigger.why ?? ''}
        {a.revision > 1 && ` · revised ${a.revision - 1}×`}
        {a.stop_reason && ` · ${STOP_LABELS[a.stop_reason] ?? a.stop_reason}`}
      </p>
      {a.explanation && <p className="mt-2 text-xs text-[#40506F]">{a.explanation}</p>}
      {a.state === 'failed' && a.last_error && <p className="mt-2 text-xs text-[#8B3158]">Couldn't assess it: {a.last_error}</p>}
      <details className="mt-2" onToggle={(e) => void open((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-xs text-[#7183A3]">Evidence and history</summary>
        {!detail && <Loading />}
        {current && (
          <div className="mt-2 flex flex-col gap-2 text-xs text-[#40506F]">
            {current.result.targets.map((t) => (
              <p key={`t-${t.ref}`}>
                <Chip label={t.status} /> about {current.result.target_labels[t.ref] ?? t.ref}
                {t.also.length > 0 && ` (with ${t.also.map((x) => current.result.target_labels[x] ?? x).join(', ')})`}: {t.why}
                <span className="text-[#7183A3]"> · cites {t.cites.join(', ') || 'nothing'} · {t.independent} independent</span>
              </p>
            ))}
            {current.result.causes.map((c, i) => (
              <p key={`c-${i}`}>
                <Chip label={c.status} /> cause: {PART_LABELS[c.component] ?? c.component} ({c.target}): {c.why}
                {c.basis === 'missing_capture' && <span className="text-[#6A4FA0]"> · its record is missing</span>}
              </p>
            ))}
            {current.result.user_stated.map((u, i) => (
              <p key={`u-${i}`}>
                They said ({u.kind === 'preference' ? 'what they prefer' : 'what they meant'}): “{u.quote}”
              </p>
            ))}
            {current.result.dimensions.length > 0 && (
              <p>
                {current.result.dimensions
                  .map((d) => `${d.dimension.replace('_', ' ')}: ${d.value} (${d.status})`)
                  .join(' · ')}
              </p>
            )}
            {current.result.choice_vs_outcome.map((c, i) => (
              <p key={`o-${i}`}>
                Choice {c.choice}, outcome {c.outcome} ({c.target}){c.note && `: ${c.note}`}
              </p>
            ))}
            {current.result.missing.length > 0 && (
              <p className="text-[#6A4FA0]">Missing: {current.result.missing.map((m) => m.what).join('; ')}</p>
            )}
            {current.result.clarification.propose && (
              <p className="text-[#7183A3]">A question that would settle it (not asked): {current.result.clarification.question}</p>
            )}
            {current.dropped.length > 0 && (
              <details>
                <summary className="cursor-pointer text-[#7183A3]">What code set aside ({current.dropped.length})</summary>
                <ul className="mt-1 list-disc pl-5">
                  {current.dropped.map((d, i) => (
                    <li key={i}>{d.what}: {d.why}</li>
                  ))}
                </ul>
              </details>
            )}
            {detail.uses.length > 0 && (
              <p className="text-[#7183A3]">
                Acted on: {detail.uses.map((u) => `${u.consumer} ${u.ref.slice(0, 16)} (${u.state})`).join(', ')}
              </p>
            )}
            <ul className="divide-y divide-[#EAF0FA] rounded-xl border border-[#EAF0FA]">
              {detail.revisions.map((r) => (
                <li key={r.revision} className="px-3 py-2 text-[11px] text-[#617698]">
                  Revision {r.revision} · {day(r.created_at)} · {r.outcome} · {r.reason} · {r.rounds.length} round
                  {r.rounds.length === 1 ? '' : 's'}
                  {r.superseded_by && ` · superseded by ${r.superseded_by}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        {detail && !current && <p className="mt-2 text-xs text-[#7183A3]">Not assessed yet.</p>}
        {detail && (
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Correct her reading (e.g. I wasn't upset, I was joking)"
              className="min-w-0 flex-1 rounded-xl border border-[#EAF0FA] px-3 py-1.5 text-xs"
            />
            <button onClick={() => void sendNote()} className={SMALL_BUTTON}>Add note</button>
          </div>
        )}
      </details>
    </article>
  )
}

function ChallengeRow({ challenge: c }: { challenge: Challenge }) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-[#EAF0FA] bg-white px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-[#40506F]">{c.challenge}</p>
        {c.why && <p className="mt-1 text-xs text-[#7183A3]">{c.why}</p>}
        <p className="mt-1 text-[11px] text-[#7183A3]">
          {c.aim === 'for_us' ? 'for the two of you' : 'something new'} · {c.game} · {day(c.created_at)}
          {c.result && ` · ${c.result}`}
          {c.learned.length > 0 && ` · learned ${c.learned.join(', ')}`}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Chip label={CHALLENGE_LABELS[c.status]} />
      </div>
    </div>
  )
}

function SkillCard({
  skill: s,
  onEnabled,
  onDelete,
}: {
  skill: LearnedSkill
  onEnabled: (enabled: boolean) => void
  onDelete: () => void
}) {
  return (
    <article className="rounded-2xl border border-[#EAF0FA] bg-white px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{s.task}</h3>
        <span className="font-mono text-[11px] text-[#7183A3]">{s.name}</span>
        <Chip label={s.enabled ? 'on' : 'off'} />
      </div>
      <p className="mt-2 text-[11px] text-[#7183A3]">
        {s.game} · learned {day(s.created_at)}
        {s.updated_at !== s.created_at && ` · relearned ${day(s.updated_at)}`} ·{' '}
        {s.uses ? `used ${s.uses}×, worked ${s.successes}` : 'not used yet'}
        {s.enabled && s.misses_in_row > 0 && ` · missed the last ${s.misses_in_row}`}
      </p>
      {!s.enabled && s.off_reason && (
        <p className="mt-2 text-xs text-[#40506F]">Switched itself off: {s.off_reason}</p>
      )}
      {s.tidy_note && (
        <p className="mt-2 text-xs text-[#40506F]">
          {s.original_code ? (s.tidy_proven ? 'Tidied (has worked since): ' : 'Tidied, not tried yet: ') : ''}
          {s.tidy_note}
        </p>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-[#7183A3]">{s.original_code ? 'Her program (tidied)' : 'Her program'}</summary>
        <pre className="mt-2 overflow-x-auto rounded-xl bg-[#F8FAFF] px-3 py-2 font-mono text-[11px] text-[#40506F]">{s.code}</pre>
      </details>
      {s.original_code && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[#7183A3]">As she learned it</summary>
          <pre className="mt-2 overflow-x-auto rounded-xl bg-[#F8FAFF] px-3 py-2 font-mono text-[11px] text-[#40506F]">{s.original_code}</pre>
        </details>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => onEnabled(!s.enabled)} className={SMALL_BUTTON}>{s.enabled ? 'Switch off' : 'Switch on'}</button>
        <button onClick={onDelete} className={SMALL_BUTTON}>Delete</button>
      </div>
    </article>
  )
}

function LessonRow({
  lesson: l,
  onRetire,
  onRestore,
  onDelete,
}: {
  lesson: Lesson
  onRetire?: () => void
  onRestore?: () => void
  onDelete: () => void
}) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-[#EAF0FA] bg-white px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-[#40506F]">
          <span className="text-[#7183A3]">When {l.situation}: </span>
          {l.advice}
        </p>
        <p className="mt-1 text-[11px] text-[#7183A3]">
          {l.level === 'plan' ? 'for planning' : 'for her hands'} · {l.game} · +{l.support_count} / −{l.contradict_count} · since {day(l.created_at)}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Chip label={l.status} />
        {onRetire && <button onClick={onRetire} className={SMALL_BUTTON}>Retire</button>}
        {onRestore && <button onClick={onRestore} className={SMALL_BUTTON}>Restore</button>}
        <button onClick={onDelete} className={SMALL_BUTTON}>Delete</button>
      </div>
    </div>
  )
}
