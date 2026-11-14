import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { learningApi } from '../../api/learning'
import type {
  AdaptationLesson,
  AdaptationLessonDetail,
  AdaptationListing,
  AssessmentDetail,
  AssessmentListing,
  AssessmentSummary,
  Challenge,
  LearnedSkill,
  TrialDetail,
  TrialListing,
  TrialStatus,
  TrialSummary,
} from '../../types/learning'

const STATUS_STYLES: Record<string, string> = {
  open: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  accepted: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  implemented: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  rejected: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  confirmed: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  on: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  off: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  trial: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  retired: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  active: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  done: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  'gave up': 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  'you changed it': 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  'session ended': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  supported: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  concluded: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  plausible: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  unresolved: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  contradicted: 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  failed: 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  'no feedback': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  pending: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  running: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  hypothesis: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  'one episode': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  withdrawn: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
}

const LESSON_STATUS_LABELS: Record<AdaptationLesson['status'], string> = {
  accepted: 'accepted',
  hypothesis: 'hypothesis',
  episode_only: 'one episode',
  withdrawn: 'withdrawn',
}

const USE_CASE_LABELS: Record<string, string> = {
  not_received: "didn't reach her",
  no_decision: 'no valid decision',
  no_account: 'reached her, not accounted for',
  applied: 'she says she applied it',
  unclear: "she couldn't tell",
  set_aside: 'she chose not to follow it',
}

const TRIGGER_LABELS: Record<AssessmentSummary['trigger_kind'], string> = {
  correction: 'a correction',
  complaint: 'a complaint',
  execution: 'recorded activity or an outcome',
  sample: 'an ordinary exchange (sampled)',
  review: 'a stretch of activity (a review opportunity)',
}

const PART_LABELS: Record<string, string> = {
  mind: 'her mind (how she took it, what she chose, how she said it)',
  // Earlier assessments, before her mind owned all three:
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
  stuck: 'parked: nothing new to look at',
  waiting: 'parked: waiting for more',
  left: 'left: nothing worth examining',
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
    <span className={`rounded-full border px-2 py-0.5 text-[11px] ${STATUS_STYLES[label] ?? STATUS_STYLES.rejected}`}>
      {label}
    </span>
  )
}

function day(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function LearningDetails() {
  const [challenges, setChallenges] = useState<Challenge[] | null>(null)
  const [skills, setSkills] = useState<LearnedSkill[] | null>(null)
  const [assessments, setAssessments] = useState<AssessmentListing | null>(null)
  const [adaptation, setAdaptation] = useState<AdaptationListing | null>(null)
  const [trials, setTrials] = useState<TrialListing | null>(null)
  const [onlyOpen, setOnlyOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, s, a, ad, tr] = await Promise.all([
        learningApi.challenges(),
        learningApi.skills(),
        learningApi.assessments(onlyOpen ? 'unresolved' : undefined),
        learningApi.adaptationLessons(),
        learningApi.trials(),
      ])
      setChallenges(c)
      setSkills(s)
      setAssessments(a)
      setAdaptation(ad)
      setTrials(tr)
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


  return (
    <div className="text-[#E4E8EC]">
      <p className="mb-6 text-[13px] leading-relaxed text-[#9AA5B1]">
        Moves she taught herself are hers to use at once. After each game session Jophiel looks back on what
        happened. Lessons go straight into how she plays. Proposals are changes to her game code: she never makes
        them herself. Review them here and hand the good ones to a coding agent.
      </p>
        {error && (
          <p className="mb-5 rounded-xl border border-[#5A2638] bg-[#2A1119] px-3 py-2 text-xs text-[#F08AB0]">{error}</p>
        )}

        <Section
          title="How it went"
          hint="After a correction, a complaint, something failing, or a sampled ordinary exchange, she looks back: what it was about, which part of her (if any) was responsible, and how sure the evidence makes that. Unresolved means the evidence doesn't settle it. Add a note to correct her reading."
        >
          <label className="mb-1 flex items-center gap-2 text-xs text-[#8C97A3]">
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
          title="What she carries forward with you"
          hint="Lessons from those assessments, kept by her memory and weighed (never obeyed) in later decisions. A preference is what you said you want, in your words; a scoped game hypothesis is tentative experience she can weigh cautiously, with its cause left unknown when the evidence cannot establish it; one episode corrects that time only. Each decision records which lessons it had and what it made of them: having a lesson isn't proof it shaped anything."
        >
          {adaptation === null && !error && <Loading />}
          {adaptation?.items.length === 0 && <Empty text="No lessons yet. They come from corrections and outcomes once an assessment supports them." />}
          {adaptation?.items.map((l) => (
            <AdaptationCard key={l.id} lesson={l} onError={setError} />
          ))}
        </Section>

        <Section
          title="Experiments she ran"
          hint="When something keeps failing in a game, her learning proposes a small test: a hunch, a few ways to try it, what each should do, and limits (3 tries, 5 minutes, 8 blocks). She decides whether, when and which way; the game's counts decide what it showed. Unresolved means it didn't settle anything, and nothing is kept from it."
        >
          {trials === null && !error && <Loading />}
          {trials?.trials.length === 0 && <Empty text="No experiments yet. They come from a step her hands keep failing at while you play." />}
          {trials?.trials.map((t) => <TrialCard key={t.id} trial={t} onError={setError} />)}
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

    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 mb-3 text-xs text-[#8C97A3]">{hint}</p>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  )
}

function Loading() {
  return <p className="text-[13px] text-[#8C97A3]">Reading…</p>
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-[#2B4A5C] bg-[#0D1115] px-6 py-8 text-center text-sm text-[#8C97A3]">{text}</p>
  )
}

const SMALL_BUTTON =
  'rounded-full border border-[#232A31] bg-[#0D1115] px-3 py-1 text-[11px] text-[#9AA5B1] transition-colors hover:border-[#F08AB0] hover:text-[#F08AB0] disabled:opacity-50'

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
  const inquiry = a.inquiry?.question ? a.inquiry : null
  const current = detail?.revisions[0]
  return (
    <article className="rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">After {TRIGGER_LABELS[a.trigger_kind]}</h3>
        {a.target_status && a.target_status !== 'none' && <span className="text-[11px] text-[#8C97A3]">about it: {a.target_status}</span>}
        {a.cause_status && a.cause_status !== 'none' && <span className="text-[11px] text-[#8C97A3]">· cause: {a.cause_status}</span>}
        <Chip label={status} />
      </div>
      <p className="mt-1 text-[11px] text-[#8C97A3]">
        {day(a.anchor_at)} · {a.trigger.why ?? ''}
        {a.revision > 1 && ` · revised ${a.revision - 1}×`}
        {a.stop_reason && ` · ${STOP_LABELS[a.stop_reason] ?? a.stop_reason}`}
      </p>
      {a.explanation && <p className="mt-2 text-xs text-[#C5CDD5]">{a.explanation}</p>}
      {inquiry && (
        <p className="mt-1 text-xs text-[#B9A2F0]">
          Open question: {inquiry.question}
          {inquiry.provisional && ` · for now: ${inquiry.provisional}`}
          {inquiry.alternatives && inquiry.alternatives.length > 0 && ` · or: ${inquiry.alternatives.join('; ')}`}
          {inquiry.resume_when && ` · resumes on: ${inquiry.resume_when}`}
        </p>
      )}
      {a.state === 'failed' && a.last_error && <p className="mt-2 text-xs text-[#F08AB0]">Couldn't assess it: {a.last_error}</p>}
      <details className="mt-2" onToggle={(e) => void open((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-xs text-[#8C97A3]">Evidence and history</summary>
        {!detail && <Loading />}
        {current && (
          <div className="mt-2 flex flex-col gap-2 text-xs text-[#C5CDD5]">
            {current.result.targets.map((t) => (
              <p key={`t-${t.ref}`}>
                <Chip label={t.status} /> about {current.result.target_labels[t.ref] ?? t.ref}
                {t.also.length > 0 && ` (with ${t.also.map((x) => current.result.target_labels[x] ?? x).join(', ')})`}: {t.why}
                <span className="text-[#8C97A3]"> · cites {t.cites.join(', ') || 'nothing'} · {t.independent} independent</span>
              </p>
            ))}
            {current.result.causes.map((c, i) => (
              <p key={`c-${i}`}>
                <Chip label={c.status} /> cause: {PART_LABELS[c.component] ?? c.component} ({c.target}): {c.why}
                {c.basis === 'missing_capture' && <span className="text-[#B9A2F0]"> · its record is missing</span>}
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
              <p className="text-[#B9A2F0]">Missing: {current.result.missing.map((m) => m.what).join('; ')}</p>
            )}
            {(current.result.findings ?? []).map((f, i) => (
              <p key={`f-${i}`}>
                <Chip label={f.status} /> {f.form || 'finding'}: {f.statement}
                {f.applies_when && ` (applies: ${f.applies_when})`}
                {f.alternatives.length > 0 && <span className="text-[#8C97A3]"> · or: {f.alternatives.join('; ')}</span>}
                {f.relates_to.length > 0 && (
                  <span className="text-[#8C97A3]">
                    {' '}
                    · {f.relates_to.map((r) => `${r.relation.replace('_', ' ')} ${r.target ? `a memory (${r.target.split(':')[0]})` : 'a lesson'}`).join(', ')}
                  </span>
                )}
                {f.details && Object.keys(f.details).length > 0 && (
                  <span className="text-[#8C97A3]"> · {Object.entries(f.details).map(([k, v]) => `${k.replace('_', ' ')}: ${v}`).join(' · ')}</span>
                )}
                <span className="text-[#8C97A3]"> · cites {f.cites.join(', ') || 'nothing'} · {f.independent} independent</span>
              </p>
            ))}
            {current.result.clarification?.propose && (
              <p className="text-[#8C97A3]">A question that would settle it (not asked): {current.result.clarification.question}</p>
            )}
            {current.result.follow_up?.propose && (
              <p className="text-[#8C97A3]">
                Proposed to her ({current.result.follow_up.kind}, hers to take up or not): {current.result.follow_up.text}
                {current.result.follow_up.resolves && ` · would settle: ${current.result.follow_up.resolves}`}
              </p>
            )}
            {current.dropped.length > 0 && (
              <details>
                <summary className="cursor-pointer text-[#8C97A3]">What code set aside ({current.dropped.length})</summary>
                <ul className="mt-1 list-disc pl-5">
                  {current.dropped.map((d, i) => (
                    <li key={i}>{d.what}: {d.why}</li>
                  ))}
                </ul>
              </details>
            )}
            {detail.uses.length > 0 && (
              <p className="text-[#8C97A3]">
                Acted on: {detail.uses.map((u) => `${u.consumer} ${u.ref.slice(0, 16)} (${u.state})`).join(', ')}
              </p>
            )}
            <ul className="divide-y divide-[#1C2228] rounded-xl border border-[#1C2228]">
              {detail.revisions.map((r) => (
                <li key={r.revision} className="px-3 py-2 text-[11px] text-[#9AA5B1]">
                  Revision {r.revision} · {day(r.created_at)} · {r.outcome} · {r.reason} · {r.rounds.length} round
                  {r.rounds.length === 1 ? '' : 's'}
                  {r.superseded_by && ` · superseded by ${r.superseded_by}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        {detail && !current && <p className="mt-2 text-xs text-[#8C97A3]">Not assessed yet.</p>}
        {detail && (
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Correct her reading (e.g. I wasn't upset, I was joking)"
              className="min-w-0 flex-1 rounded-xl border border-[#1C2228] bg-[#0F1317] px-3 py-1.5 text-xs outline-none focus:border-[#2B4A5C]"
            />
            <button onClick={() => void sendNote()} className={SMALL_BUTTON}>Add note</button>
          </div>
        )}
      </details>
    </article>
  )
}

function AdaptationCard({ lesson: l, onError }: { lesson: AdaptationLesson; onError: (message: string) => void }) {
  const [detail, setDetail] = useState<AdaptationLessonDetail | null>(null)

  async function open(isOpen: boolean) {
    if (!isOpen || detail) return
    try {
      setDetail(await learningApi.adaptationLesson(l.id))
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not load the lesson.')
    }
  }

  return (
    <article className="rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{l.statement}</h3>
        <span className="text-[11px] text-[#8C97A3]">
          {l.kind === 'preference' ? 'what you said you want' : l.kind === 'understanding' ? `what she understands${l.form ? ` (${l.form})` : ''}` : 'her own finding'} · {l.scope} · revision {l.revision}
        </span>
        <Chip label={LESSON_STATUS_LABELS[l.status]} />
      </div>
      <p className="mt-1 text-xs text-[#C5CDD5]">
        Applies {l.applies_when}
        {l.exceptions.length > 0 && <> · except {l.exceptions.join('; ')}</>}
      </p>
      {l.user_words.length > 0 && (
        <p className="mt-1 text-xs text-[#9AA5B1]">Your words: {l.user_words.map((w) => `“${w}”`).join(' / ')}</p>
      )}
      {l.details && Object.keys(l.details).length > 0 && (
        <p className="mt-1 text-xs text-[#9AA5B1]">
          {Object.entries(l.details)
            .map(([k, v]) => (k === 'tried' ? (v === 'tried' ? 'tried' : 'not tried yet') : `${k.replace('_', ' ')}: ${v}`))
            .join(' · ')}
        </p>
      )}
      <p className="mt-1 text-[11px] text-[#8C97A3]">
        Supported on {l.support} occasion{l.support === 1 ? '' : 's'} · found against on {l.counter}
        {l.noted ? ` · noted without either on ${l.noted}` : ''}
        {l.conflicts_with.length > 0 && ` · points against ${l.conflicts_with.length} other lesson${l.conflicts_with.length === 1 ? '' : 's'}`}
        {l.status_reason && ` · ${l.status_reason}`}
      </p>
      <details className="mt-2" onToggle={(e) => void open((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-xs text-[#8C97A3]">Evidence, revisions and decisions that had it</summary>
        {!detail && <Loading />}
        {detail && (
          <div className="mt-2 flex flex-col gap-2 text-xs text-[#C5CDD5]">
            {(detail.unsettled ?? []).map((u, i) => (
              <p key={`u-${i}`} className="text-[#F0C08A]">
                Unsettled: {u}
              </p>
            ))}
            {(detail.links ?? []).map((k, i) => (
              <p key={`k-${i}`} className="text-[#9AA5B1]">
                {k.relation.replace('_', ' ')} {k.what}: “{k.then_text}”
                {k.changed ? ` · changed since (${k.now_text === null ? 'gone' : k.now_status})` : ''}
                {k.note && ` · reassessed: ${k.note}`}
              </p>
            ))}
            {detail.evidence.map((e, i) => (
              <p key={`e-${i}`}>
                <Chip label={e.role === 'support' ? 'supported' : e.role === 'noted' ? 'noted' : 'contradicted'} /> {day(e.occasion_at)}
                {e.quote && <> · “{e.quote}”</>}
                {e.why && <> · {e.why}</>}
                <span className="text-[#8C97A3]">
                  {' '}
                  · assessment {e.assessment_id?.slice(0, 8) ?? '?'} rev {e.assessment_revision ?? '?'}
                  {!e.current && ' · not counted until its revised assessment is read'}
                </span>
              </p>
            ))}
            <ul className="divide-y divide-[#1C2228] rounded-xl border border-[#1C2228]">
              {detail.revisions.map((r) => (
                <li key={r.revision} className="px-3 py-2 text-[11px] text-[#9AA5B1]">
                  Revision {r.revision} · {day(r.at)} · {r.op}
                  {r.prompted_by && ` (${r.prompted_by === 'critique' ? 'her mind questioned it' : 'a premise changed'}, same evidence)`} ·{' '}
                  {LESSON_STATUS_LABELS[r.status]} · {r.statement}
                  {r.exceptions.length > 0 && ` (except ${r.exceptions.join('; ')})`} · {r.reason}
                </li>
              ))}
            </ul>
            {detail.uses.length === 0 && <p className="text-[#8C97A3]">No decision has had it yet.</p>}
            {detail.uses.length > 0 && (
              <ul className="divide-y divide-[#1C2228] rounded-xl border border-[#1C2228]">
                {detail.uses.map((u) => (
                  <li key={`${u.attempt_id}-${u.consumer}`} className="px-3 py-2 text-[11px] text-[#9AA5B1]">
                    {day(u.at)} · {u.consumer} · revision {u.revision} · <strong>{USE_CASE_LABELS[u.case] ?? u.case}</strong>:{' '}
                    {u.explanation}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </details>
    </article>
  )
}

const TRIAL_STATUS_LABELS: Record<TrialStatus, string> = {
  proposed: 'waiting for her decision',
  presented: 'being decided',
  deferred: 'put off',
  rejected: 'turned down',
  expired: 'never taken up',
  selected: 'chosen',
  preparing: 'getting ready',
  executing: 'running',
  paused: 'paused',
  observing: 'watching what happens',
  assessing: 'being assessed',
  assessed: 'assessed',
}

function TrialCard({ trial: t, onError }: { trial: TrialSummary; onError: (message: string) => void }) {
  const [detail, setDetail] = useState<TrialDetail | null>(null)

  async function open(isOpen: boolean) {
    if (!isOpen || detail) return
    try {
      setDetail(await learningApi.trial(t.id))
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not load the experiment.')
    }
  }

  const reading = detail?.assessments.find((a) => a.current)
  return (
    <article className="rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{t.hypothesis ?? t.problem}</h3>
        {t.created_at && <span className="text-[11px] text-[#8C97A3]">{day(t.created_at)}</span>}
        <Chip label={TRIAL_STATUS_LABELS[t.status]} />
      </div>
      <p className="mt-1 text-xs text-[#C5CDD5]">
        {t.problem}
        {t.ending && <> · ended: {t.ending}</>}
        {t.stop_reason && <> ({t.stop_reason})</>}
      </p>
      {t.deferral && <p className="mt-1 text-xs text-[#9AA5B1]">Put off: {t.deferral.reason}</p>}
      <details className="mt-2" onToggle={(e) => void open((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-xs text-[#8C97A3]">Predictions, her choice, what happened</summary>
        {!detail && <Loading />}
        {detail && (
          <div className="mt-2 flex flex-col gap-2 text-xs text-[#C5CDD5]">
            {detail.revisions.map((r) => (
              <div key={r.revision} className="rounded-xl border border-[#1C2228] px-3 py-2">
                <p className="text-[11px] text-[#8C97A3]">
                  Proposal revision {r.revision} · {day(r.at)} · {r.reason}
                </p>
                <ul className="mt-1 flex flex-col gap-1">
                  {r.proposal.alternatives.map((a) => (
                    <li key={a.key}>
                      <strong>{a.action}</strong>
                      {a.key === r.proposal.recommendation && ' (recommended)'}
                      {detail.selection?.alternative === a.key && r.revision === detail.revision && ' (her choice)'}: {a.expected} ·{' '}
                      {a.uncertainty} uncertainty
                      {a.prerequisites.length > 0 && <> · needs {a.prerequisites.map((p) => `${p.count} ${p.item}`).join(', ')}</>}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {detail.selection && (
              <p>
                Her choice: {detail.selection.reason || '(no reason given)'}
                {!detail.selection.recommended && ' (not the recommended one)'}
              </p>
            )}
            {detail.deferrals.length > 0 && (
              <p className="text-[#9AA5B1]">Put off before: {detail.deferrals.map((d) => d.reason).join('; ')}</p>
            )}
            {reading && (
              <div className="rounded-xl border border-[#1C2228] px-3 py-2">
                <p>
                  <Chip label={reading.hypothesis} /> {reading.result.why}
                  {!reading.test_started && ' (the test never started)'}
                </p>
                <ul className="mt-1">
                  {reading.prediction_checks.map((c) => (
                    <li key={c.ref} className="text-[11px] text-[#9AA5B1]">
                      {c.prediction}: <strong>{c.status.replace('_', ' ')}</strong> ({c.why})
                    </li>
                  ))}
                </ul>
                {reading.missing.length > 0 && (
                  <p className="text-[11px] text-[#8C97A3]">Not measured: {reading.missing.join(', ')}</p>
                )}
                {reading.result.explanations.map((e, i) => (
                  <p key={i} className="text-[11px] text-[#9AA5B1]">
                    Could be: {e.text} ({e.status})
                  </p>
                ))}
                {reading.result.lesson && (
                  <p className="text-[11px] text-[#9AA5B1]">
                    Lesson (tentative): when {reading.result.lesson.situation}, {reading.result.lesson.advice}
                  </p>
                )}
              </div>
            )}
            <ul className="divide-y divide-[#1C2228] rounded-xl border border-[#1C2228]">
              {detail.events.map((e, i) => (
                <li key={i} className="px-3 py-1.5 text-[11px] text-[#9AA5B1]">
                  {day(e.at)} · {e.kind}
                </li>
              ))}
            </ul>
          </div>
        )}
      </details>
    </article>
  )
}

function ChallengeRow({ challenge: c }: { challenge: Challenge }) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-[#C5CDD5]">{c.challenge}</p>
        {c.why && <p className="mt-1 text-xs text-[#8C97A3]">{c.why}</p>}
        <p className="mt-1 text-[11px] text-[#8C97A3]">
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
    <article className="rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{s.task}</h3>
        <span className="font-mono text-[11px] text-[#8C97A3]">{s.name}</span>
        <Chip label={s.enabled ? 'on' : 'off'} />
      </div>
      <p className="mt-2 text-[11px] text-[#8C97A3]">
        {s.game} · learned {day(s.created_at)}
        {s.updated_at !== s.created_at && ` · relearned ${day(s.updated_at)}`} ·{' '}
        {s.uses ? `used ${s.uses}×, worked ${s.successes}` : 'not used yet'}
        {s.enabled && s.misses_in_row > 0 && ` · missed the last ${s.misses_in_row}`}
      </p>
      {!s.enabled && s.off_reason && (
        <p className="mt-2 text-xs text-[#C5CDD5]">Switched itself off: {s.off_reason}</p>
      )}
      {s.tidy_note && (
        <p className="mt-2 text-xs text-[#C5CDD5]">
          {s.original_code ? (s.tidy_proven ? 'Tidied (has worked since): ' : 'Tidied, not tried yet: ') : ''}
          {s.tidy_note}
        </p>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-[#8C97A3]">{s.original_code ? 'Her program (tidied)' : 'Her program'}</summary>
        <pre className="mt-2 overflow-x-auto rounded-xl bg-[#0F1317] px-3 py-2 font-mono text-[11px] text-[#C5CDD5]">{s.code}</pre>
      </details>
      {s.original_code && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[#8C97A3]">As she learned it</summary>
          <pre className="mt-2 overflow-x-auto rounded-xl bg-[#0F1317] px-3 py-2 font-mono text-[11px] text-[#C5CDD5]">{s.original_code}</pre>
        </details>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => onEnabled(!s.enabled)} className={SMALL_BUTTON}>{s.enabled ? 'Switch off' : 'Switch on'}</button>
        <button onClick={onDelete} className={SMALL_BUTTON}>Delete</button>
      </div>
    </article>
  )
}
