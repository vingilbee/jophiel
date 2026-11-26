import { useEffect, useMemo, useRef, useState } from 'react'
import { systemApi } from '../../api/system'
import type { PartId, Signal, SystemError, WatchItem, WatchLog } from '../../types/system'
import { PARTS, PART_BY_ID } from './anatomy'
import { GoalCard } from './GoalCard'
import { ago, clock, words } from './format'
import { useTrace } from './trace'

type Tab = 'watch' | 'signals' | 'errors'
type WatchFilter = 'all' | PartId

const WATCH_POLL_MS = 6000
const ERRORS_POLL_MS = 12_000
/** The watch log reads again soon after a signal touches the parts it follows. */
const WATCHED: PartId[] = ['mind', 'memory', 'hands', 'phone']
const WATCH_FILTERS: { id: WatchFilter; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'mind', label: 'Thinking' }, { id: 'memory', label: 'Memory' }, { id: 'hands', label: 'Hands' },
  { id: 'phone', label: 'Phone' },
]

/** The field's log, beside the map: what she's thinking, what memory captured, what her hands are after and what
 * went out on the phone (the watch log), every hand-off between parts (signals), and what went wrong (errors). */
export function LogDrawer({ signals, onOpen, onClose }: {
  signals: Signal[]
  onOpen: (part: PartId, anchor?: string) => void
  onClose: () => void
}) {
  const [tab, setTab] = useState<Tab>(() => (read('system.log.tab') as Tab) || 'watch')
  useEffect(() => write('system.log.tab', tab), [tab])
  const watch = useWatch(signals)
  const errors = useErrors(tab === 'errors')

  return (
    <aside
      aria-label="Log"
      data-chrome
      className="log-drawer absolute inset-y-0 right-0 z-[15] flex w-full flex-col border-l border-[#151A1F] bg-[#06080A]/92 backdrop-blur-md lg:w-[400px]"
    >
      <header className="flex items-center gap-1 border-b border-[#12171B] px-4 pt-4 pb-3 sm:px-5">
        <div role="tablist" aria-label="Log" className="flex flex-1 gap-1">
          {(['watch', 'signals', 'errors'] as Tab[]).map((t) => (
            <button
              key={t}
              role="tab"
              type="button"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`rounded-full px-3 py-1 text-[13px] transition-colors ${tab === t ? 'bg-[#141A1F] text-white' : 'text-[#8C97A3] hover:text-white'}`}
            >
              {t === 'watch' ? 'Watch' : t === 'signals' ? 'Signals' : 'Errors'}
              {t === 'errors' && errors.recent > 0 && <span className="ml-1.5 text-[#FF8A80] tabular-nums">{errors.recent}</span>}
            </button>
          ))}
        </div>
        <button type="button" onClick={onClose} aria-label="Close the log" className="rounded-full p-1.5 text-[#8C97A3] transition-colors hover:text-white">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-10 sm:px-5" role="tabpanel">
        {tab === 'watch' && <WatchTab state={watch} onOpen={onOpen} />}
        {tab === 'signals' && <SignalsTab live={signals} onOpen={onOpen} />}
        {tab === 'errors' && <ErrorsTab state={errors} onOpen={onOpen} />}
      </div>
    </aside>
  )
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}
function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // a remembered tab is a convenience only
  }
}

// --------------------------------------------------------------------------- Watch

function useWatch(signals: Signal[]) {
  const [log, setLog] = useState<WatchLog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useRef<() => void>(() => {})
  useEffect(() => {
    let on = true
    load.current = () =>
      systemApi.watch().then(
        (l) => on && (setLog(l), setError(null)),
        (e: unknown) => on && setError(e instanceof Error ? e.message : 'Could not read the log.'),
      )
    load.current()
    const t = window.setInterval(() => load.current(), WATCH_POLL_MS)
    return () => {
      on = false
      window.clearInterval(t)
    }
  }, [])
  // A signal into or out of a watched part: read again shortly (records land a moment after the hand-off).
  const last = signals[signals.length - 1]
  useEffect(() => {
    if (!last || !(WATCHED.includes(last.from) || WATCHED.includes(last.to))) return
    const t = window.setTimeout(() => load.current(), 900)
    return () => window.clearTimeout(t)
  }, [last])
  return { log, error }
}

function WatchTab({ state, onOpen }: { state: ReturnType<typeof useWatch>; onOpen: (part: PartId, anchor?: string) => void }) {
  const [filter, setFilter] = useState<WatchFilter>(() => (read('system.log.watch') as WatchFilter) || 'all')
  useEffect(() => write('system.log.watch', filter), [filter])
  const { log, error } = state
  const items = (log?.items ?? []).filter((i) => filter === 'all' || i.part === filter)
  return (
    <>
      <section className="mt-4 rounded-lg border border-[#161C21] bg-[#080B0E] p-4">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-[13px] font-semibold text-[#DDE3E8]">Her hands’ goal</h2>
          <button type="button" onClick={() => onOpen('hands', 'goal')} className="text-[12px] text-[#8C97A3] transition-colors hover:text-white">
            Open Hands →
          </button>
        </div>
        {log ? <GoalCard goal={log.goal} compact /> : <p className="text-[13px] text-[#8C97A3]">Reading…</p>}
      </section>

      <div className="sticky top-0 z-[1] -mx-4 mt-4 flex gap-1.5 bg-[#06080A]/95 px-4 py-2 sm:-mx-5 sm:px-5">
        {WATCH_FILTERS.map((f) => {
          const color = f.id === 'all' ? '#E9EDF0' : PART_BY_ID[f.id].color
          return (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] ring-1 transition-colors ${filter === f.id ? 'text-white ring-[#3A444D]' : 'text-[#8C97A3] ring-[#161C21] hover:text-white'}`}
            >
              {f.id !== 'all' && <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
              {f.label}
            </button>
          )
        })}
      </div>

      {error && <p className="mt-3 text-[13px] text-[#FF8A80]">{error}</p>}
      {log && items.length === 0 && <p className="mt-3 text-[13.5px] text-[#8C97A3]">Nothing here yet.</p>}
      <ol className="mt-1">
        {items.map((i) => <WatchRow key={i.id} item={i} onOpen={onOpen} />)}
      </ol>
    </>
  )
}

function WatchRow({ item, onOpen }: { item: WatchItem; onOpen: (part: PartId, anchor?: string) => void }) {
  const part = PART_BY_ID[item.part]
  return (
    <li className="border-t border-[#11161A] py-3 first:border-t-0" style={{ ['--part' as string]: part.color }}>
      <div className="flex items-baseline gap-2">
        <span className="h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full" style={{ background: part.color, boxShadow: `0 0 6px ${part.color}` }} />
        <button
          type="button"
          onClick={() => onOpen(item.part, item.anchor)}
          className={`min-w-0 flex-1 truncate text-left text-[12px] hover:underline ${item.tone === 'error' ? 'text-[#FF8A80]' : ''}`}
          style={item.tone === 'error' ? undefined : { color: part.color }}
          title={`Open ${part.label}`}
        >
          {item.title}
        </button>
        <time className="shrink-0 text-[11.5px] text-[#6B757E] tabular-nums" dateTime={item.at} title={clock(item.at)}>{ago(item.at)}</time>
      </div>
      {item.text && (
        <p className={`mt-1 pl-3.5 text-[14px] leading-snug break-words ${item.tone === 'muted' ? 'text-[#8C97A3]' : 'text-[#DDE3E8]'}`}>{item.text}</p>
      )}
      {item.detail && (
        <details className="mt-1 pl-3.5 text-[13px]">
          <summary className="cursor-pointer text-[#8C97A3] hover:text-white">{item.part === 'mind' ? 'Her reasoning' : 'More'}</summary>
          <p className="mt-1.5 border-l border-[color:var(--part)]/40 pl-3 whitespace-pre-wrap text-[#AEB7BF]">{item.detail}</p>
        </details>
      )}
    </li>
  )
}

// --------------------------------------------------------------------------- Signals

/** Signal kinds whose ref is a conversation turn, so the row can open its trace. */
const TURN_KINDS = new Set(['decision', 'thinking'])

function SignalsTab({ live, onOpen }: { live: Signal[]; onOpen: (part: PartId, anchor?: string) => void }) {
  const trace = useTrace()
  const [history, setHistory] = useState<Signal[]>([])
  const [part, setPart] = useState<PartId | 'all'>('all')
  useEffect(() => {
    let on = true
    systemApi.signals(400).then((s) => on && setHistory(s), () => {})
    return () => {
      on = false
    }
  }, [])
  const all = useMemo(() => {
    const seen = new Set<string>()
    return [...history, ...live].filter((s) => !seen.has(s.id) && seen.add(s.id)).sort((a, b) => b.at - a.at)
  }, [history, live])
  const shown = all.filter((s) => part === 'all' || s.from === part || s.to === part)
  return (
    <>
      <div className="sticky top-0 z-[1] -mx-4 flex flex-wrap gap-1.5 bg-[#06080A]/95 px-4 pt-4 pb-2 sm:-mx-5 sm:px-5">
        {(['all', ...PARTS.map((p) => p.id)] as (PartId | 'all')[]).map((id) => (
          <button
            key={id}
            type="button"
            aria-pressed={part === id}
            onClick={() => setPart(id)}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] ring-1 transition-colors ${part === id ? 'text-white ring-[#3A444D]' : 'text-[#8C97A3] ring-[#161C21] hover:text-white'}`}
          >
            {id !== 'all' && <span className="h-1.5 w-1.5 rounded-full" style={{ background: PART_BY_ID[id].color }} />}
            {id === 'all' ? 'All' : PART_BY_ID[id].label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-[#6B757E]">Every hand-off between her parts, newest first ({shown.length}).</p>
      {shown.length === 0 && <p className="mt-3 text-[13.5px] text-[#8C97A3]">No signals yet.</p>}
      <ol className="mt-2">
        {shown.map((s) => {
          const from = PART_BY_ID[s.from]
          const to = PART_BY_ID[s.to]
          const traceable = s.ref && TURN_KINDS.has(s.kind)
          return (
            <li key={s.id} className="flex items-center gap-2 border-t border-[#11161A] py-2 text-[13px] first:border-t-0">
              <time className="w-[54px] shrink-0 text-[11.5px] text-[#6B757E] tabular-nums" title={clock(s.at)}>{clock(s.at).slice(0, 8)}</time>
              <button type="button" onClick={() => onOpen(s.from)} className="shrink-0 hover:underline" style={{ color: from.color }}>{from.label}</button>
              <span className="text-[#4A545D]" aria-label="to">→</span>
              <button type="button" onClick={() => onOpen(s.to)} className="shrink-0 hover:underline" style={{ color: to.color }}>{to.label}</button>
              <span className="min-w-0 flex-1 truncate text-[#AEB7BF]">{words(s.kind)}</span>
              {traceable && (
                <button
                  type="button"
                  onClick={() => trace(TURN_KINDS.has(s.kind) ? { turn: s.ref! } : { attempt: s.ref! })}
                  className="shrink-0 rounded-full px-2 py-0.5 text-[11.5px] text-[#AEB7BF] ring-1 ring-[#1E252B] hover:text-white"
                >
                  Trace
                </button>
              )}
            </li>
          )
        })}
      </ol>
    </>
  )
}

// --------------------------------------------------------------------------- Errors

function useErrors(active: boolean) {
  const [list, setList] = useState<SystemError[] | null>(null)
  const [recent, setRecent] = useState(0)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let on = true
    const load = () =>
      systemApi.errors().then(
        (l) => {
          if (!on) return
          setList(l)
          setError(null)
          // The tab's count: errors in the last ten minutes.
          const now = Date.now()
          setRecent(l.filter((e) => now - new Date(e.at).getTime() < 10 * 60_000).length)
        },
        (e: unknown) => on && setError(e instanceof Error ? e.message : 'Could not read the errors.'),
      )
    void load()
    const t = window.setInterval(load, active ? ERRORS_POLL_MS : ERRORS_POLL_MS * 4)
    return () => {
      on = false
      window.clearInterval(t)
    }
  }, [active])
  return { list, error, recent }
}

function ErrorsTab({ state, onOpen }: { state: ReturnType<typeof useErrors>; onOpen: (part: PartId, anchor?: string) => void }) {
  const trace = useTrace()
  const { list, error } = state
  if (error) return <p className="mt-4 text-[13px] text-[#FF8A80]">{error}</p>
  if (!list) return <p className="mt-4 text-[13px] text-[#8C97A3]">Reading…</p>
  if (list.length === 0) return <p className="mt-4 text-[13.5px] text-[#8C97A3]">Nothing went wrong lately.</p>
  return (
    <ol className="mt-3">
      {list.map((e) => {
        const part = PART_BY_ID[e.part] ?? PART_BY_ID.mind
        return (
          <li key={e.id} className="border-t border-[#11161A] py-3 first:border-t-0">
            <div className="flex items-baseline gap-2">
              <span className="h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full" style={{ background: part.color }} />
              <button type="button" onClick={() => onOpen(part.id)} className="shrink-0 text-[12px] hover:underline" style={{ color: part.color }}>{part.label}</button>
              <span className="min-w-0 flex-1 truncate text-[12px] text-[#FF8A80]">{e.what}</span>
              <time className="shrink-0 text-[11.5px] text-[#6B757E] tabular-nums" title={clock(e.at)}>{ago(e.at)}</time>
            </div>
            {e.text && <p className="mt-1 pl-3.5 text-[13.5px] leading-snug break-words text-[#C5CDD5]">{e.text}</p>}
            {(e.turn_id || e.attempt_id) && (
              <button
                type="button"
                onClick={() => trace(e.turn_id ? { turn: e.turn_id } : { attempt: e.attempt_id! })}
                className="mt-1.5 ml-3.5 rounded-full px-2 py-0.5 text-[11.5px] text-[#AEB7BF] ring-1 ring-[#1E252B] hover:text-white"
              >
                Trace
              </button>
            )}
          </li>
        )
      })}
    </ol>
  )
}
