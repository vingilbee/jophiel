import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ErrorBoundary } from 'react-error-boundary'
import { systemApi } from '../../api/system'
import type {
  CognitionFeed, HandsFeed, MemoryFeed, MindFeed, MouthFeed, ObservationFeed, PartFeeds, PartId, PartStatus, Signal,
  WorkerStatus,
} from '../../types/system'
import { PART_BY_ID } from './anatomy'
import type { PartAnatomy } from './anatomy'
import { LearningDetails } from './LearningDetails'
import { NodeGlyph } from './NodeGlyph'
import { STATE_WORD } from './nodeStates'

const REFRESH_MS = 10_000

function ago(at: string | number | null | undefined): string {
  if (at === null || at === undefined || at === '') return ''
  const ms = typeof at === 'number' ? at * 1000 : new Date(at).getTime()
  const s = Math.max(0, (Date.now() - ms) / 1000)
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} d`
}

/** Which sub-parts have a section on their part's page (the rest are named, not linked: no record of their own yet). */
const LINKED: Record<PartId, string[]> = {
  mouth: ['text'],
  cognition: ['memory-check', 'student-1', 'student-2', 'coordinator'],
  mind: ['quick', 'hard', 'planner', 'mood'],
  hands: ['skills', 'pack'],
  memory: ['working'],
  observation: ['conversation', 'game', 'deliveries'],
  learning: [],
}

const WORKER_WORD: Record<WorkerStatus['state'], string> = { alive: 'alive', busy: 'working', down: 'down', failing: 'failing' }

/** A section of the page: what it's called, where the rail's link jumps to, and which sub-part it belongs to. */
interface Section {
  id: string
  title: string
  sub?: string
  body: ReactNode
}

/** One part of her, as its own page: the live node and its sub-parts in a rail, its record as a timeline. */
export function PartPage({ part, status, signals, onClose }: {
  part: PartAnatomy
  status: PartStatus | undefined
  signals: Signal[]
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
            {part.id === 'learning' ? (
              <div className="mt-12 lg:mt-0 lg:pt-16"><LearningDetails /></div>
            ) : (
              <Feed part={part.id} />
            )}
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

function Feed({ part }: { part: keyof PartFeeds }) {
  const { data, error } = useFeed(part)
  if (error) return <p className="pt-2 text-[14px] text-[#FF8A80] lg:pt-16">{error}</p>
  if (!data) return <p className="pt-2 text-[14px] text-[#8C97A3] lg:pt-16">Reading…</p>
  const sections = sectionsFor(part, data)
  return (
    <div className="mt-12 lg:mt-0 lg:pt-14">
      {sections.map((s) => (
        <section key={s.id} id={s.sub ?? s.id} className="scroll-mt-8 pb-12">
          <h2 className="mb-1 text-[19px] font-semibold tracking-[-0.015em] text-[#EEF1F3]">{s.title}</h2>
          {s.body}
        </section>
      ))}
    </div>
  )
}

/** The timeline: when on the left, what happened on the right. */
function Timeline({ empty, children }: { empty: string; children: ReactNode[] }) {
  if (children.length === 0) return <p className="mt-3 text-[14px] text-[#8C97A3]">{empty}</p>
  return <ol className="mt-3">{children}</ol>
}

function Entry({ at, meta, children, tone }: { at?: string | null; meta: ReactNode; children?: ReactNode; tone?: 'error' | 'muted' }) {
  return (
    <li className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-4 border-t border-[#12171B] py-4 first:border-t-0 sm:grid-cols-[72px_minmax(0,1fr)]">
      <time className="pt-px text-[12px] text-[#7D8790] tabular-nums" dateTime={at ?? undefined}>{at ? ago(at) : ''}</time>
      <div className="min-w-0">
        <p className={`text-[12.5px] ${tone === 'error' ? 'text-[#FF8A80]' : 'text-[color:var(--part)]'}`}>{meta}</p>
        {children && (
          <div className={`mt-1.5 max-w-[68ch] text-[15px] leading-relaxed break-words ${tone === 'muted' ? 'text-[#8C97A3]' : 'text-[#DDE3E8]'}`}>
            {children}
          </div>
        )}
      </div>
    </li>
  )
}

const join = (...bits: (string | null | undefined | false)[]) => bits.filter(Boolean).join(' · ')

function sectionsFor(part: keyof PartFeeds, data: PartFeeds[keyof PartFeeds]): Section[] {
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
        <Entry key={d.id} at={d.at} tone={d.status === 'ok' ? undefined : 'error'} meta={join(d.trigger.replaceAll('_', ' '), d.seconds !== null && `${d.seconds.toFixed(1)} s`, d.status !== 'ok' && d.status)}>
          {d.decided ?? d.error ?? <span className="text-[#8C97A3]">No output recorded.</span>}
        </Entry>
      )
      return [
        { id: 'student-2', sub: 'student-2', title: 'Student 2 decided', body: <Timeline empty="No captured decisions yet.">{by('student_2').map(decision)}</Timeline> },
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
          body: <p className="mt-3 max-w-[68ch] text-[14px] leading-relaxed text-[#8C97A3]">Every message starts with a memory check. Each one shows on the map as a signal from Memory; what it found is on the Memory page.</p>,
        },
      ]
    }
    case 'mind': {
      const f = data as MindFeed
      const thought = (t: MindFeed['thoughts'][number]) => (
        <Entry key={t.id} at={t.at} tone={t.status === 'dropped' || t.status === 'stale' ? 'muted' : undefined} meta={join(t.conclusion ?? t.kind, t.trigger, t.source, t.status !== 'done' && t.status)}>
          {t.text}
          {t.question && <p className="mt-1.5 text-[#AEB7BF]">She'd ask: {t.question}</p>}
          {t.dropped && <p className="mt-1.5 text-[#8C97A3]">Dropped: {t.dropped}</p>}
          {t.thinking && (
            <details className="mt-2.5 text-[14px]">
              <summary className="cursor-pointer text-[#AEB7BF] hover:text-white">Her reasoning</summary>
              <p className="mt-2 border-l border-[color:var(--part)]/40 pl-4 whitespace-pre-wrap text-[#AEB7BF]">{t.thinking}</p>
            </details>
          )}
        </Entry>
      )
      const hard = f.thoughts.filter((t) => t.level === 'hard' && t.kind !== 'plan')
      const plans = f.thoughts.filter((t) => t.kind === 'plan')
      const quick = f.thoughts.filter((t) => t.level !== 'hard' && t.kind !== 'plan')
      const words = f.mood?.words
      return [
        {
          id: 'mood', sub: 'mood', title: 'Mood',
          body: words ? (
            <dl className="mt-3 grid max-w-xl grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3">
              {Object.entries(words).map(([k, w]) => (
                <div key={k}>
                  <dt className="text-[12.5px] text-[#8C97A3]">{k}</dt>
                  <dd className="text-[15px] text-[#DDE3E8]">{w}</dd>
                </div>
              ))}
            </dl>
          ) : <p className="mt-3 text-[14px] text-[#8C97A3]">No mood read yet.</p>,
        },
        { id: 'hard', sub: 'hard', title: 'Hard thoughts', body: <Timeline empty="No hard thoughts lately.">{hard.map(thought)}</Timeline> },
        { id: 'quick', sub: 'quick', title: 'Quick thoughts', body: <Timeline empty="No quick thoughts lately (they come while she plays).">{quick.map(thought)}</Timeline> },
        { id: 'planner', sub: 'planner', title: 'Planner', body: <Timeline empty="No plans lately (the planner writes her game plans).">{plans.map(thought)}</Timeline> },
      ]
    }
    case 'hands': {
      const f = data as HandsFeed
      return [
        {
          id: 'outcomes', sub: 'pack', title: 'What happened in the game',
          body: (
            <Timeline empty="Nothing played yet.">
              {f.outcomes.map((o) => <Entry key={o.id} at={o.at} meta={join(o.game, o.kind.replaceAll('_', ' '), o.seconds ? `${o.seconds} s` : null)}>{o.text}</Entry>)}
            </Timeline>
          ),
        },
        {
          id: 'picks', sub: 'skills', title: 'Skills she picked',
          body: (
            <Timeline empty="No captured skill choices yet.">
              {f.picks.map((p) => <Entry key={p.id} at={p.at} tone={p.status === 'ok' ? undefined : 'error'} meta={p.status}>{p.picked}</Entry>)}
            </Timeline>
          ),
        },
      ]
    }
    case 'memory': {
      const f = data as MemoryFeed
      return [
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
          id: 'events', title: 'What she noted',
          body: (
            <Timeline empty="No memory events yet.">
              {f.events.map((e) => <Entry key={e.id} at={e.at} meta={join(e.kind.replaceAll('_', ' '), e.epistemic)}>{e.text}</Entry>)}
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
            {f.reports.filter(keep).map((r) => <Entry key={r.id} at={r.at} meta={join(r.source, r.kind.replaceAll('_', ' '), r.salience !== 'routine' && r.salience)}>{r.text}</Entry>)}
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
  }
}
