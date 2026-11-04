import '@fontsource-variable/schibsted-grotesk'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { systemApi } from '../api/system'
import { FieldMap } from '../components/system/FieldMap'
import { PartPanel } from '../components/system/PartPanel'
import { PART_BY_ID } from '../components/system/anatomy'
import { tabsFor } from '../components/tabs'
import { useAuth } from '../hooks/useAuth'
import type { PartId, PartStatus, SystemStatus } from '../types/system'

const POLL_MS = 3000

/** Her parts as one living field (owner only): who is alive, and what each part did lately. */
export function SystemPage() {
  const { user } = useAuth()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<PartId | null>(null)

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

  const close = useCallback(() => setSelected(null), [])
  const nav = tabsFor(user).filter((t) => t.to !== '/system')

  return (
    <div className="system-page fixed inset-0 overflow-hidden bg-[#040506] text-[#E9EDF0]">
      <h1 className="sr-only">Jophiel, her parts</h1>
      <FieldMap statuses={statuses} selected={selected} onSelect={setSelected} panelOpen={selected !== null} />

      <div className="pointer-events-none absolute top-0 left-0 z-10 max-w-[calc(100vw-2rem)] p-4 sm:p-6">
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

      {selected && <PartPanel part={PART_BY_ID[selected]} status={statuses[selected]} onClose={close} />}
    </div>
  )
}
