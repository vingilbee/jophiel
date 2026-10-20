import { useEffect, useRef, useState } from 'react'
import type { Thought } from '../types'

// Her mind while playing (docs/COLUMBINA_MIND_DESIGN.md), in the mind panel. Deliberately
// unlike a message: no bubble, dim italic text, a level badge. Newest at the bottom.
const KEEP = 40
const BADGE = {
  react: 'bg-[#FFF1E0] text-[#9A5B13]',
  light: 'bg-[#EEF3FA] text-[#5B6F8E]',
  quiet: 'bg-[#EAF6F1] text-[#3F7A63]',
  hard: 'bg-[#F1EBFB] text-[#6B4A9E]',
}
const STATUS = { done: '', stale: 'too late, not acted on', unfinished: 'unfinished', dropped: 'dropped' }
// What started a hard thought (backend services/mind_hard.py).
const TRIGGER: Record<string, string> = {
  failing: 'keeps failing',
  surprise: 'surprise',
  big: 'before something big',
  handoff: 'thinking it over',
  quiet: 'quiet time',
}

function ago(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

export function ThoughtStream({ thoughts }: { thoughts: Thought[] }) {
  const [now, setNow] = useState(() => Date.now())
  const listRef = useRef<HTMLOListElement>(null)
  const shown = thoughts.slice(-KEEP)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: 'nearest' })
  }, [shown.length])

  return (
    <ol ref={listRef} className="max-h-80 space-y-2 overflow-y-auto pr-1 text-[11px]">
      {shown.map((thought) => (
        <li key={thought.id}><ThoughtLine thought={thought} now={now} /></li>
      ))}
    </ol>
  )
}

function ThoughtLine({ thought, now }: { thought: Thought; now: number }) {
  const muted = thought.status !== 'done'
  const kind = thought.conclusion_kind ?? thought.kind
  const status = thought.status === 'dropped' && thought.dropped_reason
    ? `dropped: ${thought.dropped_reason}`
    : STATUS[thought.status]
  const line = (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] not-italic ${BADGE[thought.level]}`}>
        {kind ? `${thought.level} · ${kind}` : thought.level}
      </span>
      <span className={`min-w-0 italic ${muted ? 'text-[#A9B5C7]' : 'text-[#5E6F8A]'}`}>
        {thought.text}
        {thought.said && <span className="not-italic text-[#8DA0BA]"> → said “{thought.said}”</span>}
        {status && <span className="not-italic"> ({status})</span>}
      </span>
      <span className="shrink-0 text-[10px] text-[#A9B5C7]">{ago(thought.created_at, now)}</span>
    </span>
  )
  if (thought.level !== 'hard') return line
  return (
    <span className="block">
      {line}
      <span className="mt-0.5 block pl-2 text-[10px] text-[#8DA0BA]">
        {thought.trigger && <span>{TRIGGER[thought.trigger] ?? thought.trigger}</span>}
        {thought.for_hands && <span> · told my hands: “{thought.for_hands}”</span>}
        {thought.question && !thought.said && <span> · want to ask: “{thought.question}”</span>}
      </span>
      {thought.thinking && (
        <details className="pl-2 text-[10px] text-[#8DA0BA]">
          <summary className="cursor-pointer select-none">how I got there</summary>
          <p className="mt-0.5 italic">{thought.thinking}</p>
        </details>
      )}
    </span>
  )
}
