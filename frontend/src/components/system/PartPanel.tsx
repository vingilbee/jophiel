import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { systemApi } from '../../api/system'
import type {
  CognitionFeed, HandsFeed, MemoryFeed, MindFeed, MouthFeed, ObservationFeed, PartFeeds, PartStatus, WorkerStatus,
} from '../../types/system'
import type { PartAnatomy } from './anatomy'
import { CloseIcon } from './icons'
import { LearningDetails } from './LearningDetails'

const REFRESH_MS = 10_000

function ago(iso: string | null | undefined): string {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${Math.round(s)} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

function seconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return 'never'
  if (s < 60) return `${Math.round(s)} s ago`
  return `${Math.round(s / 60)} min ago`
}

const WORKER_WORD: Record<WorkerStatus['state'], string> = { alive: 'alive', busy: 'working', down: 'down', failing: 'failing' }

export function PartPanel({ part, status, onClose }: { part: PartAnatomy; status: PartStatus | undefined; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null)

  useEffect(() => {
    panel.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [part.id, onClose])

  return (
    <aside
      ref={panel}
      tabIndex={-1}
      aria-label={`${part.label} details`}
      className="part-panel absolute inset-x-0 bottom-0 z-20 flex max-h-[68vh] flex-col border-t bg-[#07090B] shadow-[-24px_0_48px_rgba(0,0,0,0.55)] outline-none sm:inset-y-0 sm:right-0 sm:left-auto sm:max-h-none sm:w-[460px] sm:border-t-0 sm:border-l"
      style={{ borderColor: `${part.color}55`, ['--part' as string]: part.color }}
    >
      <header className="flex items-start gap-3 px-5 pt-5 pb-4 sm:px-6 sm:pt-6">
        <span className="mt-2 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: part.color, boxShadow: `0 0 14px ${part.color}` }} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[22px] leading-tight font-semibold tracking-[-0.02em] text-[#EEF1F3]">{part.label}</h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-[#9AA5B1]">{part.role}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="-mr-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#AEB7BF] transition-colors hover:bg-[#151A1F] hover:text-white"
        >
          <CloseIcon />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 sm:px-6">
        <Workers status={status} color={part.color} />
        {part.id === 'learning' ? <LearningDetails /> : <Feed part={part.id} />}
      </div>
    </aside>
  )
}

function Workers({ status, color }: { status: PartStatus | undefined; color: string }) {
  if (!status) return null
  if (status.state === 'off') {
    return <p className="mb-6 text-[13px] text-[#8C97A3]">Not running: it needs desktop mode (DESKTOP_AGENT_ENABLED).</p>
  }
  return (
    <section className="mb-7">
      <h3 className="mb-2 text-[13px] font-semibold text-[#DDE3E8]">Running as</h3>
      <ul className="divide-y divide-[#151A1F] border-y border-[#151A1F]">
        {status.workers.map((w) => (
          <li key={w.name} className="flex items-baseline gap-3 py-2 text-[13px]">
            <span
              className="h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full"
              style={{ background: w.state === 'down' ? '#3A434B' : w.state === 'failing' ? '#FF4136' : color }}
            />
            <span className="min-w-0 flex-1 text-[#C5CDD5]">{w.name}</span>
            <span className="text-[#8C97A3] tabular-nums">
              {WORKER_WORD[w.state]}
              {w.state !== 'down' && w.seen_ago !== null ? ` · seen ${seconds(w.seen_ago)}` : ''}
            </span>
          </li>
        ))}
        {status.student_two && (
          <li className="flex items-baseline gap-3 py-2 text-[13px]">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: status.student_two === 'answers' ? color : '#3A434B' }} />
            <span className="min-w-0 flex-1 text-[#C5CDD5]">Student 2 server</span>
            <span className="text-[#8C97A3]">{status.student_two}</span>
          </li>
        )}
      </ul>
      {status.workers.filter((w) => w.error).map((w) => (
        <p key={w.name} className="mt-2 text-[12px] break-words text-[#FF8A80]">
          {w.name}: {w.error}
        </p>
      ))}
      {status.state === 'down' && (
        <p className="mt-2 text-[12px] text-[#8C97A3]">
          Start the workers with <code className="text-[#C5CDD5]">uv run python -m app.workers</code> (in backend/).
        </p>
      )}
    </section>
  )
}

function useFeed<P extends keyof PartFeeds>(part: P) {
  const [data, setData] = useState<PartFeeds[P] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const load = () =>
      systemApi.part(part).then(
        (d) => live && (setData(d), setError(null)),
        (e: unknown) => live && setError(e instanceof Error ? e.message : 'Could not load this part.'),
      )
    setData(null)
    void load()
    const t = window.setInterval(load, REFRESH_MS)
    return () => {
      live = false
      window.clearInterval(t)
    }
  }, [part])
  return { data, error }
}

function Feed({ part }: { part: keyof PartFeeds }) {
  const { data, error } = useFeed(part)
  if (error) return <p className="text-[13px] text-[#FF8A80]">{error}</p>
  if (!data) return <p className="text-[13px] text-[#8C97A3]">Reading…</p>
  switch (part) {
    case 'mouth':
      return <MouthView feed={data as MouthFeed} />
    case 'cognition':
      return <CognitionView feed={data as CognitionFeed} />
    case 'mind':
      return <MindView feed={data as MindFeed} />
    case 'hands':
      return <HandsView feed={data as HandsFeed} />
    case 'memory':
      return <MemoryView feed={data as MemoryFeed} />
    case 'observation':
      return <ObservationView feed={data as ObservationFeed} />
  }
}

function Block({ title, empty, count, children }: { title: string; empty: string; count: number; children: ReactNode }) {
  return (
    <section className="mb-8">
      <h3 className="mb-2 text-[13px] font-semibold text-[#DDE3E8]">{title}</h3>
      {count === 0 ? <p className="text-[13px] text-[#8C97A3]">{empty}</p> : <ol className="flex flex-col">{children}</ol>}
    </section>
  )
}

function Entry({ meta, at, children, tone }: { meta: ReactNode; at?: string | null; children?: ReactNode; tone?: 'error' | 'muted' }) {
  return (
    <li className="border-b border-[#151A1F] py-3 last:border-b-0">
      <div className="flex items-baseline gap-3 text-[12px]">
        <span className={`min-w-0 flex-1 truncate ${tone === 'error' ? 'text-[#FF8A80]' : 'text-[color:var(--part)]'}`}>{meta}</span>
        {at && <time className="shrink-0 text-[#7D8790] tabular-nums" dateTime={at}>{ago(at)}</time>}
      </div>
      {children && (
        <div className={`mt-1 text-[13.5px] leading-relaxed break-words ${tone === 'muted' ? 'text-[#8C97A3]' : 'text-[#D5DBE0]'}`}>{children}</div>
      )}
    </li>
  )
}

function MouthView({ feed }: { feed: MouthFeed }) {
  return (
    <Block title="What she said" empty="No replies yet." count={feed.replies.length}>
      {feed.replies.map((r) => (
        <Entry
          key={r.id}
          at={r.at}
          meta={[r.conversation ?? 'Untitled', r.channel !== 'web' ? r.channel : null, r.status !== 'complete' ? r.status : null, r.interrupted ? 'heard in part' : null]
            .filter(Boolean)
            .join(' · ')}
        >
          {r.text || <span className="text-[#8C97A3]">(nothing said)</span>}
        </Entry>
      ))}
    </Block>
  )
}

const LEARNER: Record<string, string> = { student_1: 'Student 1 read it', student_2: 'Student 2 decided' }

function CognitionView({ feed }: { feed: CognitionFeed }) {
  return (
    <>
      <Block title="Readings and decisions" empty="No captured readings or decisions yet." count={feed.decisions.length}>
        {feed.decisions.map((d) => (
          <Entry
            key={d.id}
            at={d.at}
            tone={d.status === 'ok' ? undefined : 'error'}
            meta={[LEARNER[d.learner] ?? d.learner, d.trigger.replaceAll('_', ' '), d.seconds !== null ? `${d.seconds.toFixed(1)} s` : null, d.status !== 'ok' ? d.status : null]
              .filter(Boolean)
              .join(' · ')}
          >
            {d.decided ?? d.error ?? <span className="text-[#8C97A3]">No output recorded.</span>}
          </Entry>
        ))}
      </Block>
      <Block title="Background work" empty="No background recall or thinking requested lately." count={feed.work.length}>
        {feed.work.map((w) => (
          <Entry key={w.id} at={w.at} tone={w.error ? 'error' : undefined} meta={`${w.kind} · ${w.status}${w.error ? ` · ${w.error}` : ''}`} />
        ))}
      </Block>
    </>
  )
}

function MindView({ feed }: { feed: MindFeed }) {
  const words = feed.mood?.words
  return (
    <>
      {words && (
        <section className="mb-8">
          <h3 className="mb-2 text-[13px] font-semibold text-[#DDE3E8]">Mood</h3>
          <p className="text-[14px] leading-relaxed text-[#D5DBE0]">
            {Object.entries(words).map(([k, w], i) => (
              <span key={k}>
                {i > 0 && <span className="text-[#4A535B]"> · </span>}
                <span className="text-[#8C97A3]">{k} </span>
                {w}
              </span>
            ))}
          </p>
        </section>
      )}
      <Block title="Her thoughts" empty="No thoughts recorded lately." count={feed.thoughts.length}>
        {feed.thoughts.map((t) => (
          <Entry
            key={t.id}
            at={t.at}
            tone={t.status === 'dropped' || t.status === 'stale' ? 'muted' : undefined}
            meta={[`${t.level} thought`, t.conclusion ?? t.kind, t.trigger, t.source, t.status !== 'done' ? t.status : null]
              .filter(Boolean)
              .join(' · ')}
          >
            {t.text}
            {t.question && <p className="mt-1 text-[#AEB7BF]">She'd ask: {t.question}</p>}
            {t.dropped && <p className="mt-1 text-[#8C97A3]">Dropped: {t.dropped}</p>}
            {t.thinking && (
              <details className="mt-2 text-[13px]">
                <summary className="cursor-pointer text-[#AEB7BF] hover:text-white">Her reasoning</summary>
                <p className="mt-1.5 whitespace-pre-wrap text-[#AEB7BF]">{t.thinking}</p>
              </details>
            )}
          </Entry>
        ))}
      </Block>
    </>
  )
}

function HandsView({ feed }: { feed: HandsFeed }) {
  return (
    <>
      <Block title="What happened in the game" empty="Nothing played yet." count={feed.outcomes.length}>
        {feed.outcomes.map((o) => (
          <Entry key={o.id} at={o.at} meta={`${o.game} · ${o.kind.replaceAll('_', ' ')}${o.seconds ? ` · ${o.seconds} s` : ''}`}>
            {o.text}
          </Entry>
        ))}
      </Block>
      <Block title="Skills she picked" empty="No captured skill choices yet." count={feed.picks.length}>
        {feed.picks.map((p) => (
          <Entry key={p.id} at={p.at} tone={p.status === 'ok' ? undefined : 'error'} meta={p.status}>
            {p.picked}
          </Entry>
        ))}
      </Block>
    </>
  )
}

function MemoryView({ feed }: { feed: MemoryFeed }) {
  return (
    <>
      <p className="mb-6 text-[13px] text-[#8C97A3]">
        {feed.pending_jobs === 0 ? 'Nothing waiting to be remembered.' : `${feed.pending_jobs} exchange${feed.pending_jobs > 1 ? 's' : ''} waiting to be remembered.`}
      </p>
      <Block title="Open between you" empty="Nothing open." count={feed.open.length}>
        {feed.open.map((i) => (
          <Entry key={i.id} at={i.at} meta={`${i.kind} · ${i.owner === 'columbina' ? 'hers' : i.owner === 'user' ? 'yours' : i.owner} · ${i.status}`}>
            {i.text}
          </Entry>
        ))}
      </Block>
      <Block title="What she noted" empty="No memory events yet." count={feed.events.length}>
        {feed.events.map((e) => (
          <Entry key={e.id} at={e.at} meta={`${e.kind.replaceAll('_', ' ')} · ${e.epistemic}`}>
            {e.text}
          </Entry>
        ))}
      </Block>
    </>
  )
}

function ObservationView({ feed }: { feed: ObservationFeed }) {
  return (
    <>
      <Block title="Sources" empty="No sources reporting yet." count={feed.sources.length}>
        {feed.sources.map((s) => (
          <Entry
            key={`${s.source}:${s.instance}`}
            at={s.last_received_at}
            tone={s.error ? 'error' : undefined}
            meta={`${s.source}${s.instance && s.instance !== 'default' ? ` · ${s.instance.slice(0, 8)}` : ''} · ${s.availability}`}
          >
            {s.error}
          </Entry>
        ))}
      </Block>
      <Block title="Latest reports" empty="Nothing reported yet." count={feed.reports.length}>
        {feed.reports.map((r) => (
          <Entry key={r.id} at={r.at} meta={`${r.source} · ${r.kind.replaceAll('_', ' ')}${r.salience !== 'routine' ? ` · ${r.salience}` : ''}`}>
            {r.text}
          </Entry>
        ))}
      </Block>
    </>
  )
}
