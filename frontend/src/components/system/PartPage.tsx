import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ErrorBoundary } from 'react-error-boundary'
import { systemApi } from '../../api/system'
import type {
  HandsFeed, LearningFeed, LessonUse, MemoryFeed, MindEvaluation, MindFeed, MouthFeed, ObservationFeed, PartFeeds,
  PartId, PartStatus, PhoneFeed, Signal, Thought, WorkerStatus,
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
  mind: ['evaluations', 'continuity', 'hard', 'quick', 'planner', 'look-backs'],
  hands: ['desktop', 'goal', 'skills', 'pack'],
  memory: ['facts', 'working', 'lessons'],
  observation: ['conversation', 'game', 'desktop', 'security', 'deliveries'],
  learning: ['capture'],
  phone: ['senses', 'notifications', 'replies', 'checks', 'people', 'space', 'push'],
}

const WORKER_WORD: Record<WorkerStatus['state'], string> = { alive: 'alive', busy: 'working', down: 'down', failing: 'failing' }

/** Why her mind woke, in words (backend services/coordinator.wake_reason and mind_turn reasons). */
const WAKE_WORD: Record<string, string> = {
  user_message: 'their message', work_result: 'thinking she started', thought: 'a thought of hers',
  proposal: 'a proposal', conflict: 'something memory noticed', finding: 'a security finding', due: 'something she chose to come back to',
  event: 'an event',
}
/** What a wake came to (coordinator_outputs.decision). */
const DID_WORD: Record<string, string> = {
  said: 'She said something.', silent: 'She chose to stay silent.', no_words: 'She wrote nothing.',
  wake: 'Waiting to wake her.', superseded: 'Moot before she was woken: the conversation moved on.',
  rejected: 'Kept from her: it no longer held.',
}
const OPEN_WORD: Record<string, string> = { question: 'open question', commitment: 'promise', intention: 'intention' }

/** Who asked for a reply on the phone, and what became of it (phone_outbound.asked_by / status). */
const ASKED_WORD: Record<string, string> = { her: 'she chose to', user: 'you asked' }
const CHECK_WORD: Record<string, string> = { reply: 'her reply', draft: 'a message you asked for', operation: 'a phone request', grant: 'a detail you let her share' }

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
    const el = document.getElementById(anchor) ?? document.querySelector(`[data-section="${CSS.escape(anchor)}"]`)
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
      const evaluation = (e: MindEvaluation) => {
        const did = e.silent !== null ? 'stayed silent' : e.said ? 'spoke' : 'wrote nothing'
        const tools = e.tools.filter((t) => t.tool)
        return (
          <Entry
            key={e.id}
            at={e.at}
            tone={e.status === 'ok' ? undefined : 'error'}
            meta={join(`woken by ${WAKE_WORD[e.reason] ?? words(e.reason)}`, did, e.memory && `memory: ${words(e.memory)}`,
              e.thinking_started > 0 && 'started thinking', e.scheduled > 0 && `scheduled ${e.scheduled}`,
              e.proposal && `proposal: ${e.proposal}`, e.seconds !== null && `${e.seconds.toFixed(1)} s`)}
            action={traceButton(trace, e.turn_id ? { turn: e.turn_id } : { attempt: e.id })}
            more={tools.length || e.wake ? (
              <div className="space-y-2.5">
                {e.wake && <p className="whitespace-pre-wrap">{e.wake}</p>}
                {tools.length > 0 && (
                  <ul className="space-y-0.5">
                    {tools.map((t, i) => <li key={i}>{t.tool}{t.result && <span className="text-[#7D8790]">: {t.result}</span>}</li>)}
                  </ul>
                )}
              </div>
            ) : undefined}
            moreLabel="What woke her and the tools she used"
          >
            {e.error ?? (e.silent !== null ? <span className="text-[#AEB7BF]">Silent: {e.silent}</span> : e.said ?? e.message ?? <span className="text-[#8C97A3]">No words.</span>)}
          </Entry>
        )
      }
      const c = f.continuity
      return [
        {
          id: 'evaluations', sub: 'evaluations', title: 'Evaluations',
          hint: 'Each time something woke her (their message, thinking she started that finished, a thought of hers, a proposal, something she chose to come back to): what memory gave her, the tools she used, and whether she spoke. Trace follows it through every part.',
          body: <Timeline empty="No evaluations recorded yet.">{f.evaluations.map(evaluation)}</Timeline>,
        },
        {
          id: 'continuity', sub: 'continuity', title: 'What she keeps',
          hint: 'What lives between evaluations, read from the stores that hold it: thinking under way, what she chose to come back to, the questions, promises and intentions she keeps open, and what her recent wakes came to.',
          body: (
            <div className="space-y-6">
              <Timeline empty="No thinking under way.">
                {c.thinking.map((w) => <Entry key={w.id} at={w.created_at} meta={join(`background ${w.kind}`, words(w.status))}>{w.about}</Entry>)}
              </Timeline>
              <Timeline empty="Nothing scheduled to come back to.">
                {c.scheduled.map((w) => <Entry key={w.id} at={w.due_at} meta={join('will come back', w.item && 'about an item she keeps')}>{w.about}</Entry>)}
              </Timeline>
              <Timeline empty="No open questions, promises or intentions.">
                {c.open.map((i) => <Entry key={i.id} at={i.due_at} meta={join(OPEN_WORD[i.kind] ?? i.kind, i.owner === 'columbina' ? 'hers' : 'theirs', words(i.status))}>{i.text}</Entry>)}
              </Timeline>
              <Timeline empty="No wakes yet.">
                {c.recent.map((r, n) => <Entry key={n} at={r.at} tone={r.status === 'failed' ? 'error' : r.decision === 'silent' || r.status === 'not_sent' ? 'muted' : undefined} meta={join(`woken by ${WAKE_WORD[r.reason] ?? words(r.reason)}`, words(r.status))}>{DID_WORD[r.decision] ?? words(r.decision)}</Entry>)}
              </Timeline>
            </div>
          ),
        },
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
        { id: 'mind-lessons', title: 'Lessons she was given', hint: 'What memory gave her evaluations, her hard thoughts and her planner, and what each said it did with them (an evaluation gives no account of its own: reaching her is shown, not how she weighed it).', body: <Timeline empty="No lessons given lately.">{f.lessons.map(lessonUse)}</Timeline> },
      ]
    }
    case 'hands': {
      const f = data as HandsFeed
      return [
        {
          id: 'desktop', sub: 'desktop', title: 'On her desktop',
          hint: 'Each action her hands took on her own desktop, as code recorded it: what she meant to do, what came back, and the app in front afterwards.',
          body: (
            <Timeline empty="No desktop actions yet.">
              {f.desktop.map((d) => (
                <Entry
                  key={d.id}
                  at={d.at}
                  tone={d.result === 'failed' ? 'error' : d.result === 'unknown' ? 'muted' : undefined}
                  meta={join(d.tool && words(d.tool), d.status && words(d.status),
                    typeof d.exit_code === 'number' && `exit ${d.exit_code}`, d.app && `in ${d.app}`)}
                  action={traceButton(trace, d.turn_id ? { turn: d.turn_id } : null)}
                  more={d.output
                    ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-[#C5CDD5]">{d.output}</pre>
                    : d.window || d.screen ? <Facts rows={{ 'window': d.window, 'saw': d.screen }} /> : undefined}
                  moreLabel={d.output ? 'What it printed' : 'What was in front'}
                >
                  {d.args ?? <span className="text-[#8C97A3]">{d.tool ? words(d.tool) : 'an action'}</span>}
                  {d.deviation && <p className="mt-1.5 text-[14px] text-[#FFB27A]">{d.deviation}</p>}
                </Entry>
              ))}
            </Timeline>
          ),
        },
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
          hint: 'Learning writes lessons here; memory owns them and gives them to her mind’s evaluations, her hard thoughts, her planner and her hands. Tentative ones are given labelled as such.',
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
        reports('From her desktop', 'desktop', (r) => r.source === 'desktop', 'Nothing from her desktop lately.'),
        reports('From its security logs', 'security', (r) => r.source === 'desktop_security', 'Nothing from the security logs lately (monitoring may be off).'),
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
    case 'phone': {
      const f = data as PhoneFeed
      return [
        {
          id: 'senses', sub: 'senses', title: 'What it reports',
          hint: 'The phone as it last reported itself, each reading with its age. A change here wakes her background mind; a heartbeat alone doesn’t.',
          body: f.senses.length === 0
            ? <p className="mt-3 text-[14px] text-[#8C97A3]">Nothing from the phone lately.</p>
            : <ul className="mt-3 max-w-[68ch] space-y-1 text-[14.5px] text-[#D5DBE0]">{f.senses.map((l, i) => <li key={i}>{l.replace(/^- /, '')}</li>)}</ul>,
        },
        {
          id: 'notifications', sub: 'notifications', title: 'Notifications on it',
          hint: 'What’s on the phone now, by app and conversation. Their words are someone else’s: a turn they cause gets no device action.',
          body: f.notifications === null
            ? <p className="mt-3 text-[14px] text-[#8C97A3]">Unknown: they couldn’t be read just now.</p>
            : (
              <Timeline empty="No notifications on the phone.">
                {f.notifications.map((n) => (
                  <Entry key={n.key} at={n.at} meta={join(n.app, n.group && 'group', n.can_reply && 'can be answered', n.messages > 0 && `${n.messages} message${n.messages > 1 ? 's' : ''}`)}>
                    {n.conversation ?? <span className="text-[#8C97A3]">(no title)</span>}
                  </Entry>
                ))}
              </Timeline>
            ),
        },
        {
          id: 'replies', sub: 'replies', title: 'Her replies',
          hint: 'What she wrote to someone through a notification’s reply button, who asked for it, and whether it came back on the phone as sent.',
          body: (
            <Timeline empty="She hasn’t replied to anyone on the phone yet.">
              {f.replies.map((r) => (
                <Entry
                  key={r.id}
                  at={r.at}
                  tone={r.status === 'failed' ? 'error' : undefined}
                  meta={join(r.to && `to ${r.to}`, r.conversation, ASKED_WORD[r.asked_by] ?? r.asked_by, words(r.status), r.echoed && 'seen on the phone')}
                >
                  {r.text}
                </Entry>
              ))}
            </Timeline>
          ),
        },
        {
          id: 'checks', sub: 'checks', title: 'Checks',
          hint: 'A check model reads each message going out against a written policy and only that conversation, and binds every phone request on your turns to what your message asked. Held means nothing was sent or done.',
          body: (
            <Timeline empty="No checks lately.">
              {f.checks.map((c, i) => (
                <Entry
                  key={`${c.at}:${i}`}
                  at={c.at}
                  tone={c.ok === false ? 'error' : undefined}
                  meta={join(CHECK_WORD[c.check ?? ''] ?? c.check, c.ok === true ? 'passed' : c.ok === false ? 'held' : 'no verdict', c.what)}
                >
                  {c.why ?? <span className="text-[#8C97A3]">{c.ok ? 'Nothing to hold.' : 'No reason recorded.'}</span>}
                  {c.reveals.length > 0 && <p className="mt-1.5 text-[14px] text-[#AEB7BF]">Would have revealed: {c.reveals.join('; ')}</p>}
                </Entry>
              ))}
            </Timeline>
          ),
        },
        {
          id: 'people', sub: 'people', title: 'People',
          hint: 'Who she has met through the phone and her record of each: how familiar, how warm, who starts. Her readings of exchanges below are shadow: nothing she does reads them yet.',
          body: (
            <div className="space-y-6">
              <Timeline empty="No one yet.">
                {f.people.map((p) => (
                  <Entry key={p.id} at={p.at} tone={p.status !== 'active' ? 'muted' : undefined} meta={join(p.contact ? 'contact' : p.contact === false ? 'not a contact' : null, p.familiarity, p.warmth, p.initiative && `initiative: ${p.initiative}`, p.status !== 'active' && p.status)}>
                    {p.name ?? <span className="text-[#8C97A3]">(no name)</span>}
                    {p.note && <p className="mt-1 text-[14px] text-[#AEB7BF]">{p.note}</p>}
                  </Entry>
                ))}
              </Timeline>
              <Timeline empty="No exchanges read yet.">
                {f.exchanges.map((e) => (
                  <Entry
                    key={e.id}
                    at={e.at}
                    meta={join(e.person, e.conversation, `to ${words(e.addressed)}`, e.participated && 'she took part', e.lesson !== 'none' && `lesson: ${words(e.lesson)}`)}
                    more={e.their_words || e.lesson_why ? <Facts rows={{ 'their words': e.their_words, 'lesson': e.lesson_why }} /> : undefined}
                    moreLabel="Their words and the lesson"
                  >
                    {e.observation}
                  </Entry>
                ))}
              </Timeline>
            </div>
          ),
        },
        {
          id: 'space', sub: 'space', title: 'Requests for space',
          hint: 'Someone asked her to back off: active at once, they stop her follow-ups, and the check reads them before any reply.',
          body: (
            <Timeline empty="No one has asked for space.">
              {f.space.map((b) => (
                <Entry key={b.id} at={b.at} meta={join(b.person, b.scope === 'everywhere' ? 'everywhere' : b.conversation, b.topic && `about ${b.topic}`)}>
                  “{b.words}”
                </Entry>
              ))}
            </Timeline>
          ),
        },
        {
          id: 'actions', title: 'What she did on it',
          hint: 'Each action on the phone as the chat records it (Maps, music, alarms, replies), with its outcome.',
          body: <Timeline empty="No phone actions yet.">{f.actions.map((a) => <Entry key={a.id} at={a.at} meta="on the phone">{a.text}</Entry>)}</Timeline>,
        },
        {
          id: 'push', sub: 'push', title: 'Push',
          hint: 'Her messages sent to the phone when you weren’t at any app; dropped when you were there or she said it aloud.',
          body: (
            <Timeline empty="No pushes yet.">
              {f.pushes.map((r) => (
                <Entry key={r.id} at={r.at} tone={r.status === 'failed' ? 'error' : r.status === 'dropped' ? 'muted' : undefined} meta={join(words(r.kind), words(r.status), r.attempts > 1 && `${r.attempts} tries`)}>
                  {r.error ?? (r.status === 'sent' ? 'Sent.' : <span className="text-[#8C97A3]">{words(r.status)}</span>)}
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
