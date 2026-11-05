import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ErrorBoundary } from 'react-error-boundary'
import { systemApi } from '../../api/system'
import type {
  CognitionFeed, HandsFeed, LearningFeed, LessonUse, MemoryFeed, MindFeed, MouthFeed, ObservationFeed, PartFeeds,
  PartId, PartStatus, Signal, Thought, WorkerStatus,
} from '../../types/system'
import { PART_BY_ID } from './anatomy'
import type { PartAnatomy } from './anatomy'
import { GoalCard } from './GoalCard'
import { LearningDetails } from './LearningDetails'
import { Entry, Facts, LinkButton, Timeline } from './log'
import { ago, join, words } from './format'
import { NodeGlyph } from './NodeGlyph'
import { STATE_WORD } from './nodeStates'
import { useTrace } from './trace'
import type { TraceTarget } from './trace'

const REFRESH_MS = 10_000

/** Which sub-parts have a section on their part's page (the rest are named, not linked: no record of their own yet). */
const LINKED: Record<PartId, string[]> = {
  mouth: ['text'],
  cognition: ['memory-check', 'student-1', 'student-2', 'coordinator'],
  mind: ['quick', 'hard', 'planner', 'look-backs', 'mood'],
  hands: ['goal', 'skills', 'pack'],
  memory: ['facts', 'working', 'lessons'],
  observation: ['conversation', 'game', 'deliveries'],
  learning: ['capture'],
}

const WORKER_WORD: Record<WorkerStatus['state'], string> = { alive: 'alive', busy: 'working', down: 'down', failing: 'failing' }

/** A section of the page: what it's called, where the rail's link jumps to, and which sub-part it belongs to. */
interface Section {
  id: string
  title: string
  sub?: string
  hint?: string
  body: ReactNode
}

/** One part of her, as its own page: the live node and its sub-parts in a rail, its record as a timeline. */
export function PartPage({ part, status, signals, anchor, onClose }: {
  part: PartAnatomy
  status: PartStatus | undefined
  signals: Signal[]
  /** A section to scroll to once the page has read its records (from the log, or the address's #hash). */
  anchor?: string | null
  onClose: () => void
}) {
  const main = useRef<HTMLElement>(null)

  useEffect(() => {
    main.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [part.id, onClose])

  return (
    <div
      className="part-page absolute inset-0 z-20 overflow-y-auto"
      style={{ ['--part' as string]: part.color }}
      role="dialog"
      aria-modal="true"
      aria-label={`${part.label}, inside`}
    >
      <div className="mx-auto grid max-w-6xl gap-x-14 px-5 pt-5 pb-24 sm:px-8 lg:grid-cols-[288px_minmax(0,1fr)] lg:pt-8">
        <Rail part={part} status={status} signals={signals} onClose={onClose} />
        <main ref={main} tabIndex={-1} className="min-w-0 outline-none">
          <ErrorBoundary
            resetKeys={[part.id]}
            fallbackRender={({ error }: { error: unknown }) => (
              <p className="mt-10 text-[14px] text-[#FF8A80]">
                This page broke while drawing: {error instanceof Error ? error.message : String(error)}. The map still works: press Esc.
              </p>
            )}
          >
            <Feed part={part.id} anchor={anchor} />
            {part.id === 'learning' && <LearningDetails />}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}

function Rail({ part, status, signals, onClose }: { part: PartAnatomy; status: PartStatus | undefined; signals: Signal[]; onClose: () => void }) {
  const state = status?.state
  const word = state ? STATE_WORD[state] : '…'
  const handoffs = useHandoffs(part.id, signals)
  return (
    <aside className="lg:sticky lg:top-8 lg:self-start">
      <button
        type="button"
        onClick={onClose}
        className="group -ml-2 flex items-center gap-2 rounded-full px-2 py-1.5 text-[13px] text-[#AEB7BF] transition-colors hover:text-white"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M10 3.5 5.5 8 10 12.5" />
        </svg>
        System
        <span className="text-[#5A6670] group-hover:text-[#8C97A3]">Esc</span>
      </button>

      <div className="mt-4 flex items-center gap-5 lg:block">
        <div className="-ml-3 shrink-0 lg:-ml-4">
          <NodeGlyph part={part} state={state} size={132} />
        </div>
        <div className="min-w-0">
          <h1 className="text-[30px] leading-none font-semibold tracking-[-0.025em] text-[#F1F4F6]">{part.label}</h1>
          <p className="mt-2 text-[14px] font-medium" style={{ color: state === 'failing' ? '#FF4136' : state === 'down' || state === 'off' ? '#8C97A3' : part.color }}>
            {word}
          </p>
        </div>
      </div>
      <p className="mt-4 text-[14px] leading-relaxed text-[#AEB7BF] lg:max-w-[260px]">{part.role}</p>

      <RailBlock title="Inside">
        <ul className="flex flex-wrap gap-x-5 gap-y-1.5 lg:block lg:space-y-1.5">
          {part.subs.map((s) => {
            const dot = <span className="h-2 w-2 rounded-full" style={{ background: part.color, boxShadow: `0 0 8px ${part.color}` }} />
            return (
              <li key={s.id}>
                {LINKED[part.id].includes(s.id) ? (
                  <a href={`#${s.id}`} className="flex items-center gap-2.5 text-[14px] text-[#D5DBE0] transition-colors hover:text-white">
                    {dot}
                    {s.label}
                  </a>
                ) : (
                  <span className="flex items-center gap-2.5 text-[14px] text-[#8C97A3]">
                    {dot}
                    {s.label}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </RailBlock>

      {status && status.state !== 'off' && (
        <RailBlock title="Running as">
          <ul className="space-y-1.5">
            {status.workers.map((w) => (
              <li key={w.name} className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="truncate text-[#C5CDD5]">{w.name}</span>
                <span className="shrink-0 text-[#8C97A3]">
                  {WORKER_WORD[w.state]}
                  {w.state !== 'down' && w.seen_ago !== null && <span className="tabular-nums"> · {Math.round(w.seen_ago)} s</span>}
                </span>
              </li>
            ))}
            {status.student_two && (
              <li className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="text-[#C5CDD5]">Student 2 server</span>
                <span className="text-[#8C97A3]">{status.student_two}</span>
              </li>
            )}
          </ul>
          {status.workers.filter((w) => w.error).map((w) => (
            <p key={w.name} className="mt-2 text-[12px] break-words text-[#FF8A80]">{w.name}: {w.error}</p>
          ))}
          {status.state === 'down' && (
            <p className="mt-2 text-[12px] text-[#8C97A3]">Start the workers: <code className="text-[#C5CDD5]">uv run python -m app.workers</code></p>
          )}
        </RailBlock>
      )}
      {status?.state === 'off' && (
        <RailBlock title="Running as"><p className="text-[13px] text-[#8C97A3]">Not running: it needs desktop mode.</p></RailBlock>
      )}

      <RailBlock title="Hand-offs">
        {handoffs.length === 0 ? (
          <p className="text-[13px] text-[#8C97A3]">None lately.</p>
        ) : (
          <ul className="space-y-1.5">
            {handoffs.slice(0, 8).map((s) => {
              const out = s.from === part.id
              const other = PART_BY_ID[out ? s.to : s.from]
              return (
                <li key={s.id} className="flex items-baseline gap-2 text-[13px]">
                  <span className="h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full" style={{ background: other.color }} />
                  <span className="min-w-0 flex-1 truncate text-[#C5CDD5]">
                    {out ? `to ${other.label}` : `from ${other.label}`}
                    <span className="text-[#8C97A3]">, {s.kind.replaceAll('_', ' ')}</span>
                  </span>
                  <span className="shrink-0 text-[#7D8790] tabular-nums">{ago(s.at)}</span>
                </li>
              )
            })}
          </ul>
        )}
      </RailBlock>
    </aside>
  )
}

function RailBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-7 border-t border-[#151A1F] pt-4">
      <h2 className="mb-2.5 text-[13px] font-semibold text-[#DDE3E8]">{title}</h2>
      {children}
    </section>
  )
}

/** Signals into and out of this part: the recent history, then whatever arrives live. */
function useHandoffs(part: PartId, live: Signal[]) {
  const [history, setHistory] = useState<Signal[]>([])
  useEffect(() => {
    let on = true
    systemApi.signals(300).then((s) => on && setHistory(s), () => {})
    return () => {
      on = false
    }
  }, [part])
  const seen = new Set<string>()
  return [...history, ...live]
    .filter((s) => (s.from === part || s.to === part) && !seen.has(s.id) && seen.add(s.id))
    .sort((a, b) => b.at - a.at)
}

function useFeed<P extends keyof PartFeeds>(part: P) {
  const [data, setData] = useState<PartFeeds[P] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let on = true
    const load = () =>
      systemApi.part(part).then(
        (d) => on && (setData(d), setError(null)),
        (e: unknown) => on && setError(e instanceof Error ? e.message : 'Could not load this part.'),
      )
    setData(null)
    void load()
    const t = window.setInterval(load, REFRESH_MS)
    return () => {
      on = false
      window.clearInterval(t)
    }
  }, [part])
  return { data, error }
}

function Feed({ part, anchor }: { part: keyof PartFeeds; anchor?: string | null }) {
  const { data, error } = useFeed(part)
  const trace = useTrace()
  const scrolled = useRef<string | null>(null)
  useEffect(() => {
    if (!data || !anchor || scrolled.current === anchor) return
    scrolled.current = anchor
    const el = document.getElementById(anchor) ?? document.querySelector(`[data-section="${anchor}"]`)
    el?.scrollIntoView({ block: 'start' })
  }, [data, anchor])
  if (error) return <p className="pt-2 text-[14px] text-[#FF8A80] lg:pt-16">{error}</p>
  if (!data) return <p className="pt-2 text-[14px] text-[#8C97A3] lg:pt-16">Reading…</p>
  const sections = sectionsFor(part, data, trace)
  return (
    <div className="mt-12 lg:mt-0 lg:pt-14">
      {sections.map((s) => (
        <section key={s.id} id={s.sub ?? s.id} data-section={s.id} className="scroll-mt-8 pb-12">
          <h2 className="mb-1 text-[19px] font-semibold tracking-[-0.015em] text-[#EEF1F3]">{s.title}</h2>
          {s.hint && <p className="max-w-[68ch] text-[13px] text-[#8C97A3]">{s.hint}</p>}
          {s.body}
        </section>
      ))}
    </div>
  )
}

/** A lesson memory gave a decision: what the decision said it did with it, or why it never got there. */
function lessonUse(u: LessonUse) {
  return (
    <Entry
      key={u.id}
      at={u.at}
      tone={u.received ? undefined : 'muted'}
      meta={join(words(u.consumer), `rev ${u.revision}`, u.lesson_status, u.received ? (u.reported ? `said: ${words(u.reported)}` : 'no account given') : 'not given')}
    >
      {u.lesson}
      {u.why && <p className="mt-1 text-[14px] text-[#AEB7BF]">{u.why}</p>}
      {!u.received && u.reason && <p className="mt-1 text-[13.5px] text-[#8C97A3]">{u.reason}</p>}
    </Entry>
  )
}

const traceButton = (trace: (t: TraceTarget) => void, target: TraceTarget | null) =>
  target && <LinkButton onClick={() => trace(target)}>Trace</LinkButton>

function sectionsFor(part: keyof PartFeeds, data: PartFeeds[keyof PartFeeds], trace: (t: TraceTarget) => void): Section[] {
  switch (part) {
    case 'mouth': {
      const f = data as MouthFeed
      return [{
        id: 'said', sub: 'text', title: 'What she said',
        body: (
          <Timeline empty="No replies yet.">
            {f.replies.map((r) => (
              <Entry key={r.id} at={r.at} meta={join(r.conversation ?? 'Untitled', r.channel !== 'web' && r.channel, r.status !== 'complete' && r.status, r.interrupted && 'heard in part')}>
                {r.text || <span className="text-[#8C97A3]">(nothing said)</span>}
              </Entry>
            ))}
          </Timeline>
        ),
      }]
    }
    case 'cognition': {
      const f = data as CognitionFeed
      const by = (learner: string) => f.decisions.filter((d) => d.learner === learner)
      const decision = (d: CognitionFeed['decisions'][number]) => (
        <Entry
          key={d.id}
          at={d.at}
          tone={d.status === 'ok' ? undefined : 'error'}
          meta={join(words(d.trigger), d.seconds !== null && `${d.seconds.toFixed(1)} s`, d.model, d.status !== 'ok' && d.status)}
          action={traceButton(trace, d.turn_id ? { turn: d.turn_id } : { attempt: d.id })}
        >
          {d.decided ?? d.error ?? <span className="text-[#8C97A3]">No output recorded.</span>}
        </Entry>
      )
      return [
        { id: 'student-2', sub: 'student-2', title: 'Student 2 decided', hint: 'Trace follows the turn through every part: memory check, Student 1, Student 2, the mouth, and any thinking it asked for.', body: <Timeline empty="No captured decisions yet.">{by('student_2').map(decision)}</Timeline> },
        { id: 'student-1', sub: 'student-1', title: 'Student 1 read it', body: <Timeline empty="No captured readings yet.">{by('student_1').map(decision)}</Timeline> },
        {
          id: 'coordinator', sub: 'coordinator', title: 'Background work',
          body: (
            <Timeline empty="No background recall or thinking requested lately.">
              {f.work.map((w) => <Entry key={w.id} at={w.at} tone={w.error ? 'error' : undefined} meta={join(w.kind, w.status, w.error)} />)}
            </Timeline>
          ),
        },
        {
          id: 'memory-check', sub: 'memory-check', title: 'Memory check',
          body: <p className="mt-3 max-w-[68ch] text-[14px] leading-relaxed text-[#8C97A3]">Every message starts with a memory check. Each one shows on the map as a signal from Memory, and so do the lessons memory gives Student 2. What it found is on the Memory page.</p>,
        },
      ]
    }
    case 'mind': {
      const f = data as MindFeed
      const thought = (t: Thought) => (
        <Entry
          key={t.id}
          at={t.at}
          tone={t.status === 'dropped' || t.status === 'stale' ? 'muted' : undefined}
          meta={join(t.conclusion ?? t.kind, words(t.trigger), t.source, t.topic, t.status !== 'done' && t.status, t.said && 'she said it')}
          more={t.thinking ? <p className="whitespace-pre-wrap">{t.thinking}</p> : undefined}
          moreLabel="Her reasoning"
        >
          {t.text}
          {t.judged && <p className="mt-1.5 text-[14px] text-[#AEB7BF]">Her look back: {t.judged}</p>}
          {t.question && <p className="mt-1.5 text-[#AEB7BF]">She'd ask: {t.question}</p>}
          {t.for_hands && <p className="mt-1.5 text-[14px] text-[#AEB7BF]">For her hands: {t.for_hands}</p>}
          {t.follows && <p className="mt-1 text-[12.5px] text-[#7D8790]">carries out {t.follows}</p>}
          {t.dropped && <p className="mt-1.5 text-[#8C97A3]">Dropped: {t.dropped}</p>}
        </Entry>
      )
      const hard = f.thoughts.filter((t) => t.level === 'hard' && t.kind !== 'plan')
      const quick = f.thoughts.filter((t) => t.level !== 'hard' && t.kind !== 'plan')
      const lookBacks = f.thoughts.filter((t) => t.judged)
      const words_ = f.mood?.words
      return [
        { id: 'hard', sub: 'hard', title: 'Hard thoughts', body: <Timeline empty="No hard thoughts lately.">{hard.map(thought)}</Timeline> },
        { id: 'quick', sub: 'quick', title: 'Quick thoughts', body: <Timeline empty="No quick thoughts lately (they come while she plays).">{quick.map(thought)}</Timeline> },
        {
          id: 'planner', sub: 'planner', title: 'Planner',
          hint: 'Each call: what she saw, what she decided and why, and what code then applied (rules it carried over say where they came from).',
          body: (
            <Timeline empty="No plans lately (the planner writes her game plans).">
              {f.plans.map((p) => (
                <Entry
                  key={p.id}
                  at={p.at}
                  tone={p.status !== 'ok' || p.applied?.result === 'failed' ? 'error' : undefined}
                  meta={join(p.decision ?? p.status, p.progress && `getting anywhere: ${p.progress}`, p.seconds !== null && `${p.seconds.toFixed(1)} s`,
                    p.applied && `applied: ${words(p.applied.result)}`, p.reasons?.length && `asked for: ${p.reasons.map(words).join(', ')}`)}
                  action={traceButton(trace, { attempt: p.id })}
                  more={
                    <div className="space-y-2.5">
                      <Facts rows={{ 'saw': p.sees, "couldn't see": p.cannot_see, 'goal': p.goal, 'steps': p.steps.length ? <ol className="list-decimal pl-4">{p.steps.map((st, i) => <li key={i}>{st}</li>)}</ol> : null, 'said': p.say, 'asked': p.ask, 'outcome': p.applied?.outcome }} />
                      {p.applied && p.applied.rules.length > 0 && (
                        <div>
                          <p className="text-[12.5px] text-[#7D8790]">Rules applied</p>
                          <ul className="mt-1 space-y-0.5">
                            {p.applied.rules.map((r, i) => <li key={i}>{r.rule} <span className="text-[#7D8790]">(from {r.from.join(', ') || 'unknown'})</span></li>)}
                          </ul>
                        </div>
                      )}
                    </div>
                  }
                  moreLabel="What it saw and what was applied"
                >
                  {p.why ?? p.error ?? <span className="text-[#8C97A3]">No answer recorded.</span>}
                  {p.applied?.deviation && <p className="mt-1.5 text-[14px] text-[#FFB27A]">{p.applied.deviation}</p>}
                </Entry>
              ))}
            </Timeline>
          ),
        },
        {
          id: 'look-backs', sub: 'look-backs', title: 'Look backs',
          hint: 'When a plan step ends she looks back at her effort and judges it herself: is she getting anywhere, and if not, why.',
          body: <Timeline empty="No look backs yet.">{lookBacks.map(thought)}</Timeline>,
        },
        { id: 'mind-lessons', title: 'Lessons she was given', hint: 'What memory gave her planner and her game mind, and what each said it did with them.', body: <Timeline empty="No lessons given lately.">{f.lessons.map(lessonUse)}</Timeline> },
        {
          id: 'mood', sub: 'mood', title: 'Mood',
          body: words_ ? (
            <dl className="mt-3 grid max-w-xl grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3">
              {Object.entries(words_).map(([k, w]) => (
                <div key={k}>
                  <dt className="text-[12.5px] text-[#8C97A3]">{k}</dt>
                  <dd className="text-[15px] text-[#DDE3E8]">{w}</dd>
                </div>
              ))}
            </dl>
          ) : <p className="mt-3 text-[14px] text-[#8C97A3]">No mood read yet.</p>,
        },
      ]
    }
    case 'hands': {
      const f = data as HandsFeed
      return [
        {
          id: 'goal', sub: 'goal', title: 'Goal and plan',
          body: <div className="mt-3 max-w-[68ch]"><GoalCard goal={f.goal} /></div>,
        },
        {
          id: 'decisions', sub: 'skills', title: 'What her hands did',
          hint: 'Three reports, kept apart: what her hands said, what the game pack reported for the skill, and her state as code read it before and after.',
          body: (
            <Timeline empty="No captured decisions yet.">
              {f.decisions.map((d) => {
                const r = d.run
                const failed = d.status !== 'ok' || r?.result === 'failed'
                return (
                  <Entry
                    key={d.id}
                    at={d.at}
                    tone={failed ? 'error' : undefined}
                    meta={join(d.action ?? (d.status === 'ok' ? 'no action' : d.status), r && words(r.result), r?.seconds && `${r.seconds} s`, d.claim && `she says: ${words(d.claim)}`)}
                    action={traceButton(trace, { attempt: d.id })}
                    more={
                      <Facts rows={{ 'step': d.step, 'pack says': r && join(r.pack_status, r.reason), 'deviation': r?.deviation, 'before': r?.before, 'after': r?.after, 'inventory': r?.inventory, 'observed': r?.observed_note, 'lessons': d.weighed.length ? <ul>{d.weighed.map((w, i) => <li key={i}>{w.lesson}: {words(w.use)}{w.why ? ` — ${w.why}` : ''}</li>)}</ul> : null, 'thinking took': d.seconds !== null && `${d.seconds.toFixed(1)} s`, 'error': d.error }} />
                    }
                    moreLabel="The three reports"
                  >
                    {d.note ?? (d.error ? <span className="text-[#FF8A80]">{d.error}</span> : <span className="text-[#8C97A3]">No note.</span>)}
                    {r?.inventory && r.inventory !== 'no change' && <p className="mt-1 text-[13.5px] text-[#AEB7BF] tabular-nums">Inventory: {r.inventory}</p>}
                  </Entry>
                )
              })}
            </Timeline>
          ),
        },
        {
          id: 'checks', title: 'Steps counted done',
          hint: 'Code counting a plan step done, from what it observed; apart from what her hands claimed.',
          body: (
            <Timeline empty="No steps counted done yet.">
              {f.checks.map((c) => (
                <Entry key={c.id} at={c.at} meta={join('counted done', c.done_when && `when ${c.done_when}`)}>
                  {c.do}
                  {c.counted && <p className="mt-1 text-[14px] text-[#AEB7BF]">{c.counted}</p>}
                </Entry>
              ))}
            </Timeline>
          ),
        },
        { id: 'hands-lessons', title: 'Lessons she was given', hint: 'What memory gave her hands, and what they said they did with each (their own account, not proof).', body: <Timeline empty="No lessons given lately.">{f.lessons.map(lessonUse)}</Timeline> },
        {
          id: 'outcomes', sub: 'pack', title: 'What happened in the game',
          body: (
            <Timeline empty="Nothing played yet.">
              {f.outcomes.map((o) => <Entry key={o.id} at={o.at} tone={o.failed ? 'error' : undefined} meta={join(o.game, words(o.kind), o.seconds ? `${o.seconds} s` : null)}>{o.text}</Entry>)}
            </Timeline>
          ),
        },
      ]
    }
    case 'memory': {
      const f = data as MemoryFeed
      return [
        {
          id: 'facts', sub: 'facts', title: 'Facts she keeps',
          body: (
            <Timeline empty="No facts yet.">
              {f.facts.map((x) => (
                <Entry key={x.id} at={x.at} tone={x.status === 'active' ? undefined : 'muted'} meta={join(words(x.kind), x.status !== 'active' && x.status, x.revision > 1 && `rev ${x.revision}`, x.confidence < 1 && `confidence ${x.confidence}`)}>
                  {x.text}
                </Entry>
              ))}
            </Timeline>
          ),
        },
        {
          id: 'events', title: 'What she noted',
          body: (
            <Timeline empty="No memory events yet.">
              {f.events.map((e) => <Entry key={e.id} at={e.at} meta={join(words(e.kind), e.epistemic)}>{e.text}</Entry>)}
            </Timeline>
          ),
        },
        {
          id: 'working', sub: 'working', title: 'Open between you',
          body: (
            <>
              <p className="mt-1 text-[13px] text-[#8C97A3]">
                {f.pending_jobs === 0 ? 'Nothing waiting to be remembered.' : `${f.pending_jobs} exchange${f.pending_jobs > 1 ? 's' : ''} waiting to be remembered.`}
              </p>
              <Timeline empty="Nothing open.">
                {f.open.map((i) => <Entry key={i.id} at={i.at} meta={join(i.kind, i.owner === 'columbina' ? 'hers' : i.owner === 'user' ? 'yours' : i.owner, i.status)}>{i.text}</Entry>)}
              </Timeline>
            </>
          ),
        },
        {
          id: 'lessons', sub: 'lessons', title: 'Lessons',
          hint: 'Learning writes lessons here; memory owns them and gives them to Student 2, her hands, her planner and her game mind. Tentative ones are given labelled as such.',
          body: (
            <Timeline empty="No lessons yet.">
              {f.lessons.map((l) => (
                <Entry
                  key={l.id}
                  at={l.at}
                  tone={l.status === 'withdrawn' ? 'muted' : undefined}
                  meta={join(l.status, words(l.kind), words(l.scope), `rev ${l.revision}`, l.used > 0 && `given ${l.used}×`, l.origin)}
                  more={<Facts rows={{ 'applies when': l.applies_when, 'status': l.reason }} />}
                  moreLabel="When it applies"
                >
                  {l.statement}
                </Entry>
              ))}
            </Timeline>
          ),
        },
      ]
    }
    case 'observation': {
      const f = data as ObservationFeed
      const reports = (title: string, sub: string, keep: (r: ObservationFeed['reports'][number]) => boolean, empty: string): Section => ({
        id: sub, sub, title,
        body: (
          <Timeline empty={empty}>
            {f.reports.filter(keep).map((r) => <Entry key={r.id} at={r.at} meta={join(r.source, words(r.kind), r.salience !== 'routine' && r.salience)}>{r.text}</Entry>)}
          </Timeline>
        ),
      })
      return [
        reports('From the conversation', 'conversation', (r) => r.source === 'conversation' && r.kind !== 'delivery_status', 'Nothing from the conversation lately.'),
        reports('From the game', 'game', (r) => r.source === 'minecraft_pack', 'Nothing from the game lately.'),
        reports('Deliveries', 'deliveries', (r) => r.kind === 'delivery_status', 'No delivery reports lately.'),
        reports('From her own thinking', 'mind-reports', (r) => r.source === 'mind', 'No concluded thoughts reported lately.'),
        {
          id: 'sources', title: 'Sources',
          body: (
            <Timeline empty="No sources reporting yet.">
              {f.sources.map((s) => (
                <Entry key={`${s.source}:${s.instance}`} at={s.last_received_at} tone={s.error ? 'error' : undefined} meta={join(s.source, s.instance && s.instance !== 'default' && s.instance.slice(0, 8), s.availability)}>
                  {s.error}
                </Entry>
              ))}
            </Timeline>
          ),
        },
      ]
    }
    case 'learning': {
      const f = data as LearningFeed
      return [{
        id: 'capture', sub: 'capture', title: 'Capture',
        hint: 'What each learner was given and produced is captured for learning to look back on. These are records that could not be saved whole.',
        body: (
          <Timeline empty="Nothing missing from the capture.">
            {f.gaps.map((g) => (
              <Entry key={g.id} at={g.at} tone="error" meta={join(words(g.label), words(g.reason), g.kind, g.tries > 1 && `${g.tries} tries`)}>
                {g.detail}
              </Entry>
            ))}
          </Timeline>
        ),
      }]
    }
  }
}
