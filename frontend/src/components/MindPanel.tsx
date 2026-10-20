import { useEffect, useState, type ReactNode } from 'react'
import { ThoughtStream } from './ThoughtStream'
import type { ConversationThought, InnerState, Thought } from '../types'

// Her mind, for watching it: mood, what she thought about your last messages, what moved her, and
// her thoughts while playing. Nothing here reaches her replies (docs/COLUMBINA_INNER_STATE_PLAN.md).
const CARD = 'rounded-2xl border border-[#D9E8F6] bg-white p-4'
const TITLE = 'text-[10px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase'
const ORDER = ['mood', 'energy', 'interest', 'closeness', 'tension']
// mood and closeness run -1..1, the rest 0..1 (backend Cognitive/simple/mood.py).
const SIGNED = new Set(['mood', 'closeness'])
const STATUS: Record<string, string> = {
  opened: 'new', escalated: 'more', eased: 'eased', sustained: 'still', lingering: 'lingering',
}

function ago(seconds: number, now: number): string {
  const s = Math.max(0, Math.round(now / 1000 - seconds))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

export function MindPanel({ state, thoughts }: { state: InnerState | null; thoughts: Thought[] }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])

  const on = state?.enabled ?? false
  const [latest, ...earlier] = state?.thoughts ?? []

  return (
    <div className="space-y-3">
      {!on && (
        <p className="rounded-2xl border border-dashed border-[#D9E3F0] bg-white/60 p-3 text-[11px] leading-relaxed text-[#7183A3]">
          Mood reading is off. Set <code className="text-[#5E6F8A]">MIND_INNER_STATE_ENABLED=true</code> in the backend’s
          .env to see her mood and her thoughts about your messages.
        </p>
      )}

      {on && state && (
        <section className={CARD} aria-label="Her mood">
          <div className="mb-3 flex items-baseline justify-between">
            <h3 className={TITLE}>Mood</h3>
            {state.updated_at && <span className="text-[10px] text-[#A9B5C7]">moved {ago(state.updated_at, now)}</span>}
          </div>
          <ul className="space-y-2.5">
            {ORDER.map((k) => <MoodRow key={k} name={k} state={state} />)}
          </ul>
        </section>
      )}

      {on && (
        <section className={CARD} aria-label="On her mind">
          <h3 className={`${TITLE} mb-3`}>On her mind</h3>
          {latest ? <>
            <ConversationThoughtView thought={latest} now={now} />
            {earlier.length > 0 && (
              <details className="mt-3 border-t border-[#EEF3FA] pt-2">
                <summary className="cursor-pointer select-none text-[11px] text-[#7183A3]">Earlier ({earlier.length})</summary>
                <ol className="mt-3 space-y-4">
                  {earlier.map((t) => <li key={t.at}><ConversationThoughtView thought={t} now={now} /></li>)}
                </ol>
              </details>
            )}
          </> : <Empty>Send her a message. Her thought about it shows up here a few seconds after she replies.</Empty>}
        </section>
      )}

      {on && state && state.causes.length > 0 && (
        <section className={CARD} aria-label="What moved her">
          <h3 className={`${TITLE} mb-3`}>What moved her</h3>
          <ol className="space-y-2.5">
            {state.causes.map((c, i) => (
              <li key={`${c.at}-${i}`} className="text-[11px] leading-relaxed">
                <div className="flex items-center gap-2">
                  <span className="rounded-full bg-[#F1EBFB] px-2 py-0.5 text-[10px] text-[#6B4A9E]">
                    {c.feeling} · {STATUS[c.status] ?? c.status}{c.size !== '-' ? ` · ${c.size}` : ''}
                  </span>
                  <span className="ml-auto text-[10px] text-[#A9B5C7]">{ago(c.at, now)}</span>
                </div>
                <p className="mt-1 text-[#5E6F8A]">{c.incident}</p>
                <p className="italic text-[#A9B5C7]">“{c.quote}”</p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {thoughts.length > 0 && (
        <section className={CARD} aria-label="Her thoughts while playing">
          <h3 className={`${TITLE} mb-3`}>While playing</h3>
          <ThoughtStream thoughts={thoughts} />
        </section>
      )}
    </div>
  )
}

function MoodRow({ name, state }: { name: string; state: InnerState }) {
  const { small, clear } = state.values[name]
  const signed = SIGNED.has(name)
  const pos = (v: number) => (signed ? (v + 1) / 2 : v) * 100
  const lo = Math.min(pos(small), pos(clear))
  const hi = Math.max(pos(small), pos(clear))
  return (
    <li title={`${small.toFixed(2)} / ${clear.toFixed(2)} (usually ${state.baseline[name]})`}>
      <div className="mb-1 flex items-baseline justify-between text-[11px]">
        <span className="capitalize text-[#7183A3]">{name}</span>
        <span className="font-medium text-[#23314F]">{state.words[name]}</span>
      </div>
      <div className="relative h-1.5 rounded-full bg-[#EEF3FA]">
        {/* Her usual level, then the two readings: a span when an unclear size leaves a range. */}
        <span className="absolute -top-0.5 h-2.5 w-px bg-[#C5D2E3]" style={{ left: `${pos(state.baseline[name])}%` }} />
        <span
          className="absolute inset-y-0 rounded-full bg-[#8B3158]/70"
          style={{ left: `${Math.max(0, lo - 1.5)}%`, width: `${Math.max(3, hi - lo + 3)}%` }}
        />
      </div>
    </li>
  )
}

function ConversationThoughtView({ thought, now }: { thought: ConversationThought; now: number }) {
  return (
    <div className="text-[12px] leading-relaxed">
      <p className="mb-2 flex items-baseline gap-2 text-[10px] text-[#A9B5C7]">
        <span className="min-w-0 truncate">You: “{thought.message}”</span>
        <span className="ml-auto shrink-0">{ago(thought.at, now)}</span>
      </p>
      <p className="italic text-[#5E6F8A]">{thought.moment}</p>
      {thought.intention && (
        <p className="mt-2 rounded-xl bg-[#F6F2FC] px-3 py-2 text-[#5B4A7E]">
          <span className="mr-1 text-[10px] font-semibold tracking-[0.06em] text-[#8B6FB8] uppercase">Wants to</span>
          {thought.intention}
        </p>
      )}
    </div>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-[11px] italic text-[#A9B5C7]">{children}</p>
}
