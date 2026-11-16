import '@fontsource-variable/schibsted-grotesk'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { systemApi } from '../api/system'
import { FieldMap } from '../components/system/FieldMap'
import { LogDrawer } from '../components/system/LogDrawer'
import { TraceView } from '../components/system/TraceView'
import { TraceContext } from '../components/system/trace'
import type { TraceTarget } from '../components/system/trace'
import { PartPage } from '../components/system/PartPage'
import { PARTS, PART_BY_ID } from '../components/system/anatomy'
import { anchorFromHash } from '../components/system/format'
import { tabsFor } from '../components/tabs'
import { useAuth } from '../hooks/useAuth'
import { useSignals } from '../hooks/useSignals'
import type { PartId, PartStatus, SystemStatus } from '../types/system'

const POLL_MS = 3000
const LOG_KEY = 'system.log.open'

/** The log starts open on a wide screen (it sits beside the field), closed on a narrow one (it covers it). */
function logOpenAtFirst(): boolean {
  try {
    const kept = window.localStorage.getItem(LOG_KEY)
    if (kept !== null) return kept === '1'
  } catch {
    // no storage: fall back to the screen's width
  }
  return window.innerWidth >= 1280
}

/** Her parts as one living field (owner only): who is alive, and what each part did lately. */
export function SystemPage() {
  const { user } = useAuth()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The part whose page is open comes from the address (/system/mind), so back and forward work.
  const params = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const anchor = anchorFromHash(location.hash)
  const [logOpen, setLogOpen] = useState(logOpenAtFirst)
  const [trace, setTrace] = useState<TraceTarget | null>(null)
  const toggleLog = useCallback((open: boolean) => {
    setLogOpen(open)
    try {
      window.localStorage.setItem(LOG_KEY, open ? '1' : '0')
    } catch {
      // remembered only when storage works
    }
  }, [])
  const selected: PartId | null = PARTS.some((p) => p.id === params.part) ? (params.part as PartId) : null
  const [diving, setDiving] = useState(false)
  useEffect(() => {
    if (!selected) setDiving(false)
  }, [selected])
  const { signals, live } = useSignals()
  const last = signals.length ? signals[signals.length - 1] : null

  useEffect(() => {
    let live = true
    const load = () =>
      systemApi.status().then(
        (s) => live && (setStatus(s), setError(null)),
        (e: unknown) => live && setError(e instanceof Error ? e.message : 'Could not read her parts.'),
      )
    void load()
    const t = window.setInterval(load, POLL_MS)
    return () => {
      live = false
      window.clearInterval(t)
    }
  }, [])

  const statuses = useMemo(() => {
    const out: Partial<Record<PartId, PartStatus>> = {}
    for (const p of status?.parts ?? []) out[p.id] = p
    return out
  }, [status])

  const counts = useMemo(() => {
    const parts = status?.parts ?? []
    const running = parts.filter((p) => p.state === 'alive' || p.state === 'busy').length
    const trouble = parts.filter((p) => p.state === 'partial' || p.state === 'failing').length
    const down = parts.filter((p) => p.state === 'down').length
    return { running, trouble, down }
  }, [status])

  const open = useCallback((id: PartId) => navigate(`/system/${id}`), [navigate])
  /** From the log: open a part's page at one of its sections (closing the log on a narrow screen). */
  const openAt = useCallback((id: PartId, at?: string) => {
    if (window.innerWidth < 1024) setLogOpen(false)
    navigate(`/system/${id}${at ? `#${at}` : ''}`)
  }, [navigate])
  const closeTrace = useCallback(() => setTrace(null), [])
  const close = useCallback(() => navigate('/system'), [navigate])
  const nav = tabsFor(user).filter((t) => t.to !== '/system')

  return (
    <TraceContext.Provider value={setTrace}>
    <div className="system-page fixed inset-0 overflow-hidden bg-[#040506] text-[#E9EDF0]">
      <h1 className="sr-only">Jophiel, her parts</h1>
      {/* On a wide screen the open log sits beside the field, so the field fits what's left. */}
      <div className={`absolute inset-y-0 left-0 transition-[right] duration-300 ${logOpen && !selected ? 'right-0 lg:right-[400px]' : 'right-0'}`}>
        <FieldMap statuses={statuses} selected={selected} onSelect={open} signals={signals} onDive={() => setDiving(true)} />
      </div>

      {!selected && !diving && (logOpen ? (
        <LogDrawer signals={signals} onOpen={openAt} onClose={() => toggleLog(false)} />
      ) : (
        <button
          type="button"
          data-chrome
          onClick={() => toggleLog(true)}
          className="absolute top-4 right-4 z-[15] flex items-center gap-2 rounded-full bg-[#0A0D10]/85 px-3.5 py-2 text-[13px] text-[#C5CDD5] ring-1 ring-[#1E252B] backdrop-blur transition-colors hover:text-white sm:top-6 sm:right-6"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <path d="M3 4h10M3 8h10M3 12h6" />
          </svg>
          Log
          {last && <span className="h-1.5 w-1.5 rounded-full" style={{ background: PART_BY_ID[last.to].color }} aria-hidden="true" />}
        </button>
      ))}

      <div
        className={`pointer-events-none absolute top-0 left-0 z-10 max-w-[calc(100vw-2rem)] p-4 transition-opacity duration-300 sm:p-6 ${selected || diving ? 'opacity-0' : 'opacity-100'}`}
        aria-hidden={selected !== null}
      >
        <div className="pointer-events-auto">
          <p className="text-[19px] leading-none font-semibold tracking-[-0.02em]">
            Jophiel <span className="font-normal text-[#8C97A3]">· live field</span>
          </p>
          <p className="mt-2 text-[13px] text-[#AEB7BF] tabular-nums" aria-live="polite">
            {error
              ? <span className="text-[#FF8A80]">{error}</span>
              : !status
                ? 'Listening for her parts…'
                : !status.known
                  ? "Can't read heartbeats: Redis isn't reachable."
                  : `${counts.running} running${counts.trouble ? ` · ${counts.trouble} in trouble` : ''}${counts.down ? ` · ${counts.down} down` : ''}`}
          </p>
          <p className="mt-1 text-[12px] text-[#8C97A3]">
            {last
              ? `Last signal: ${PART_BY_ID[last.from].label} → ${PART_BY_ID[last.to].label}, ${last.kind.replaceAll('_', ' ')}`
              : live ? 'Listening for signals' : 'Connecting to signals…'}
            {last && !live && <span> (reconnecting)</span>}
          </p>
          <nav aria-label="Pages" className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
            <Link to="/dashboard" className="text-[#8C97A3] transition-colors hover:text-white">Voice</Link>
            {nav.map((t) => (
              <Link key={t.to} to={t.to} className="text-[#8C97A3] transition-colors hover:text-white">
                {t.label}
              </Link>
            ))}
          </nav>
        </div>
      </div>

      {selected && <PartPage key={selected} part={PART_BY_ID[selected]} status={statuses[selected]} signals={signals} anchor={anchor} onClose={close} />}
      {trace && <TraceView target={trace} onClose={closeTrace} />}
    </div>
    </TraceContext.Provider>
  )
}
