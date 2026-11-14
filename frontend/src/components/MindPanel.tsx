import { useEffect, useState, type ReactNode } from 'react'
import { ThoughtStream } from './ThoughtStream'
import type { MindState, Thought } from '../types'

// Her mind, for watching it (backend services/mind_state.py): what she is thinking through in the background, what
// she chose to come back to, the questions, promises and intentions she keeps open, and what her recent wakes came
// to. All of it is read from the stores that hold it between evaluations.
const CARD = 'rounded-2xl border border-[#D9E8F6] bg-white p-4'
const TITLE = 'text-[10px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase'
const KIND: Record<string, string> = { question: 'Open question', commitment: 'Promise', intention: 'Intends to' }
const WOKEN_BY: Record<string, string> = {
  work_result: 'thinking she started', thought: 'a thought of hers', proposal: 'a proposal',
  conflict: 'something memory noticed', finding: 'a security finding', due: 'something she chose to come back to',
  event: 'an event',
}
const DID: Record<string, string> = {
  said: 'she said something', silent: 'she chose to stay silent', no_words: 'she wrote nothing',
  wake: 'about to wake her', superseded: 'moot: the conversation moved on', rejected: 'it no longer held',
}

function when(iso: string | null, now: number): string {
  if (!iso) return ''
  const s = Math.round((new Date(iso).getTime() - now) / 1000)
  const a = Math.abs(s)
  const span = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 172800 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`
  return s >= 0 ? `in ${span}` : `${span} ago`
}

export function MindPanel({ state, thoughts }: { state: MindState | null; thoughts: Thought[] }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="space-y-3">
      {!state && (
        <p className="rounded-2xl border border-dashed border-[#D9E3F0] bg-white/60 p-3 text-[11px] leading-relaxed text-[#7183A3]">
          Couldn’t read her mind’s state just now. It will show here once the backend answers.
        </p>
      )}

      {state && (
        <section className={CARD} aria-label="What she keeps open">
          <h3 className={`${TITLE} mb-3`}>What she keeps open</h3>
          {state.open.length ? (
            <ul className="space-y-2.5">
              {state.open.map((i) => (
                <li key={i.id} className="text-[12px] leading-relaxed">
                  <p className="text-[10px] text-[#A9B5C7]">
                    {KIND[i.kind] ?? i.kind} · {i.owner === 'columbina' ? 'hers' : 'theirs'}
                    {i.due_at && i.kind === 'intention' ? ` · lapses ${when(i.due_at, now)}` : ''}
                  </p>
                  <p className="text-[#23314F]">{i.text}</p>
                </li>
              ))}
            </ul>
          ) : <Empty>Nothing open. Questions, promises and intentions she notes show up here.</Empty>}
        </section>
      )}

      {state && (
        <section className={CARD} aria-label="Coming back to">
          <h3 className={`${TITLE} mb-3`}>Coming back to</h3>
          {state.scheduled.length || state.thinking.length ? (
            <ul className="space-y-2.5">
              {state.thinking.map((w) => (
                <li key={w.id} className="text-[12px] leading-relaxed">
                  <p className="text-[10px] text-[#A9B5C7]">Thinking it through · {w.status.replace(/_/g, ' ')}</p>
                  <p className="text-[#23314F]">{w.about}</p>
                </li>
              ))}
              {state.scheduled.map((w) => (
                <li key={w.id} className="text-[12px] leading-relaxed">
                  <p className="text-[10px] text-[#A9B5C7]">She’ll come back to it {when(w.due_at, now)}</p>
                  <p className="text-[#23314F]">{w.about}</p>
                </li>
              ))}
            </ul>
          ) : <Empty>Nothing under way, and nothing she chose to come back to.</Empty>}
        </section>
      )}

      {state && state.recent.length > 0 && (
        <section className={CARD} aria-label="When she woke on her own">
          <h3 className={`${TITLE} mb-3`}>When she woke on her own</h3>
          <ol className="space-y-2">
            {state.recent.slice(0, 8).map((r, n) => (
              <li key={n} className="flex items-baseline gap-2 text-[11px] leading-relaxed">
                <span className="min-w-0 text-[#5E6F8A]">{WOKEN_BY[r.reason] ?? r.reason}: {DID[r.decision] ?? r.decision}</span>
                <span className="ml-auto shrink-0 text-[10px] text-[#A9B5C7]">{when(r.at, now)}</span>
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

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-[11px] italic text-[#A9B5C7]">{children}</p>
}
