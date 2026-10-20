import { useEffect, useState } from 'react'
import { mindApi } from '../api/mind'
import type { InnerState } from '../types'

// Her inner state, for watching it (docs/COLUMBINA_INNER_STATE_PLAN.md step 5.2). A debug strip,
// not part of the conversation: nothing here reaches her replies. Shown only when the backend has
// MIND_INNER_STATE_ENABLED. A value whose two readings disagree reads "content or happy".
const POLL_MS = 15000
const ORDER = ['mood', 'tension', 'interest', 'closeness', 'energy']
const STATUS: Record<string, string> = {
  opened: 'new', escalated: 'more', eased: 'eased', sustained: 'still', lingering: 'lingering',
}

function ago(seconds: number, now: number): string {
  const s = Math.max(0, Math.round(now / 1000 - seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${Math.round(s / 3600)}h`
}

export function MoodStrip() {
  const [state, setState] = useState<InnerState | null>(null)
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    let cancelled = false
    const load = () => mindApi.innerState()
      .then((s) => { if (!cancelled) { setState(s); setNow(Date.now()) } })
      .catch(() => {})
    load()
    const timer = window.setInterval(load, POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])

  if (!state?.enabled) return null
  return (
    <section aria-label="Her mood (debug)" className="mb-2 rounded-2xl border border-dashed border-[#E3DCF0] bg-white/60 px-3 py-1.5 text-[11px] text-[#7A8BA6]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 text-left focus-visible:outline-2 focus-visible:outline-[#8FC7E8]"
      >
        <span className="font-medium">Mood</span>
        {ORDER.map((k) => (
          <span key={k} title={`${k}: ${state.values[k].small.toFixed(2)} / ${state.values[k].clear.toFixed(2)} (baseline ${state.baseline[k]})`}>
            <span className="text-[#A9B5C7]">{k}</span> {state.words[k]}
          </span>
        ))}
        <span className="ml-auto shrink-0">{open ? 'Hide' : state.causes.length ? 'Why' : ''}</span>
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1">
          {state.causes.length === 0 && <li className="italic">Nothing has moved her yet.</li>}
          {state.causes.map((c, i) => (
            <li key={`${c.at}-${i}`} className="flex items-baseline gap-2">
              <span className="shrink-0 rounded-full bg-[#F1EBFB] px-1.5 py-0.5 text-[10px] text-[#6B4A9E]">
                {c.feeling} · {STATUS[c.status] ?? c.status}{c.size !== '-' ? ` · ${c.size}` : ''}
              </span>
              <span className="min-w-0">
                <span className="text-[#5E6F8A]">{c.incident}</span>
                <span className="italic text-[#A9B5C7]"> “{c.quote}”</span>
              </span>
              <span className="ml-auto shrink-0 text-[10px] text-[#A9B5C7]">{ago(c.at, now)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
