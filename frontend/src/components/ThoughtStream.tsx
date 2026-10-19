import { useEffect, useRef, useState } from 'react'
import type { Thought } from '../types'

// Her mind while playing (docs/COLUMBINA_MIND_DESIGN.md). Deliberately unlike a
// message: no bubble, dim italic text, a level badge. Collapsed it shows only the
// latest thought; open, the recent ones, newest at the bottom.
const KEEP = 40
const BADGE = {
  react: 'bg-[#FFF1E0] text-[#9A5B13]',
  light: 'bg-[#EEF3FA] text-[#5B6F8E]',
  quiet: 'bg-[#EAF6F1] text-[#3F7A63]',
  hard: 'bg-[#F1EBFB] text-[#6B4A9E]',
}
const STATUS = { done: '', stale: 'too late, not acted on', unfinished: 'unfinished' }

function ago(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

export function ThoughtStream({ thoughts }: { thoughts: Thought[] }) {
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const listRef = useRef<HTMLOListElement>(null)
  const shown = thoughts.slice(-KEEP)
  const latest = shown.at(-1)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (open) listRef.current?.lastElementChild?.scrollIntoView({ block: 'nearest' })
  }, [open, shown.length])

  if (!latest) return null
  return (
    <section aria-label="Her thoughts" className="mb-3 rounded-2xl border border-dashed border-[#D9E3F0] bg-white/60 px-3 py-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-[11px] text-[#7A8BA6] focus-visible:outline-2 focus-visible:outline-[#8FC7E8]"
      >
        <span aria-hidden="true">💭</span>
        <span className="font-medium">Thinking</span>
        {!open && <ThoughtLine thought={latest} now={now} compact />}
        <span className="ml-auto shrink-0">{open ? 'Hide' : `${shown.length} recent`}</span>
      </button>
      {open && (
        <ol ref={listRef} className="mt-2 max-h-56 space-y-1.5 overflow-y-auto pr-1">
          {shown.map((thought) => (
            <li key={thought.id}><ThoughtLine thought={thought} now={now} /></li>
          ))}
        </ol>
      )}
    </section>
  )
}

function ThoughtLine({ thought, now, compact = false }: { thought: Thought; now: number; compact?: boolean }) {
  const muted = thought.status !== 'done'
  return (
    <span className={`flex min-w-0 items-baseline gap-2 ${compact ? 'flex-1' : ''}`}>
      <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] not-italic ${BADGE[thought.level]}`}>
        {thought.kind ? `${thought.level} · ${thought.kind}` : thought.level}
      </span>
      <span className={`min-w-0 italic ${compact ? 'truncate' : ''} ${muted ? 'text-[#A9B5C7]' : 'text-[#5E6F8A]'}`}>
        {thought.text}
        {!compact && thought.said && <span className="not-italic text-[#8DA0BA]"> → said “{thought.said}”</span>}
        {!compact && STATUS[thought.status] && <span className="not-italic"> ({STATUS[thought.status]})</span>}
      </span>
      <span className="shrink-0 text-[10px] text-[#A9B5C7]">{ago(thought.created_at, now)}</span>
    </span>
  )
}
