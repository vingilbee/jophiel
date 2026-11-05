import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { systemApi } from '../../api/system'
import type { CaptureChain, CapturedAttempt, CapturedExecution, PartId } from '../../types/system'
import { PART_BY_ID } from './anatomy'
import { Facts, Raw } from './log'
import { clock, join, words } from './format'
import type { TraceTarget } from './trace'

/** Which part made each kind of record, so every step of the trace wears its part's colour. */
const LEARNER_PART: Record<string, PartId> = {
  student_1: 'cognition', student_2: 'cognition', hands: 'hands', planner: 'mind', hard_thought: 'mind',
}
const EXECUTION_PART: Record<string, PartId> = {
  reply: 'mouth', heard: 'mouth', channel_delivery: 'mouth', skill_run: 'hands', step_check: 'hands',
  plan_apply: 'mind', thought_route: 'mind', effect: 'cognition',
}
const LEARNER_WORD: Record<string, string> = {
  student_1: 'Student 1 read it', student_2: 'Student 2 decided', hands: 'Her hands decided',
  planner: 'Her planner decided', hard_thought: 'She thought it through',
}
const EXECUTION_WORD: Record<string, string> = {
  reply: 'The mouth said it', heard: 'What was heard of it', channel_delivery: 'Delivered', skill_run: 'The skill ran',
  step_check: 'Code counted the step done', plan_apply: 'The plan was applied', thought_route: 'Where the thought went',
  effect: 'What it changed',
}

type Step = { at: string; kind: 'attempt'; row: CapturedAttempt } | { at: string; kind: 'execution'; row: CapturedExecution }

/** A turn or a decision followed through every part it touched, each step with what it was given and what it
 * produced. Only what was captured: a step that was never saved says so instead of being drawn. */
export function TraceView({ target, onClose }: { target: TraceTarget; onClose: () => void }) {
  const [chain, setChain] = useState<CaptureChain | null>(null)
  const [error, setError] = useState<string | null>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let on = true
    setChain(null)
    setError(null)
    const load = 'turn' in target ? systemApi.traceTurn(target.turn) : systemApi.traceAttempt(target.attempt)
    load.then(
      (c) => on && setChain(c),
      (e: unknown) => on && setError(e instanceof Error ? e.message : 'Could not read the trace.'),
    )
    return () => {
      on = false
    }
  }, [target])

  useEffect(() => {
    panel.current?.focus({ preventScroll: true })
    // Esc closes the trace only, not the part page under it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  const steps: Step[] = chain
    ? [
        ...chain.attempts.map((row) => ({ at: row.started_at ?? '', kind: 'attempt' as const, row })),
        ...chain.executions.map((row) => ({ at: row.ended_at ?? row.started_at ?? row.captured_at ?? '', kind: 'execution' as const, row })),
      ].sort((a, b) => a.at.localeCompare(b.at))
    : []

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-[#040506]/70" onClick={onClose}>
      <div
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Trace"
        onClick={(e) => e.stopPropagation()}
        className="trace-in h-full w-full max-w-[760px] overflow-y-auto border-l border-[#151A1F] bg-[#06080A] px-5 pt-5 pb-20 outline-none sm:px-8"
      >
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <p className="text-[12.5px] text-[#8C97A3]">{'turn' in target ? 'One turn, through her parts' : 'One decision, and what it did'}</p>
            <h2 className="mt-1 text-[22px] font-semibold tracking-[-0.02em] text-[#F1F4F6]">Trace</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-2 py-1.5 text-[13px] text-[#AEB7BF] transition-colors hover:text-white"
          >
            Close <span className="text-[#5A6670]">Esc</span>
          </button>
        </div>

        {error && <p className="mt-6 text-[14px] text-[#FF8A80]">{error}</p>}
        {!chain && !error && <p className="mt-6 text-[14px] text-[#8C97A3]">Reading…</p>}
        {chain && (
          <>
            <p className={`mt-4 text-[13px] ${chain.complete ? 'text-[#8C97A3]' : 'text-[#FFB27A]'}`}>
              {chain.complete
                ? `${steps.length} step${steps.length === 1 ? '' : 's'}, all captured.`
                : `${steps.length} step${steps.length === 1 ? '' : 's'} captured; some of it is missing (below).`}
            </p>
            <ol className="relative mt-6">
              {steps.map((s) => (s.kind === 'attempt' ? <AttemptStep key={s.row.id} row={s.row} /> : <ExecutionStep key={s.row.id} row={s.row} />))}
            </ol>
            {(chain.missing.length > 0 || chain.gaps.length > 0) && (
              <section className="mt-8 border-t border-[#151A1F] pt-4">
                <h3 className="text-[13px] font-semibold text-[#FFB27A]">Missing from the record</h3>
                <ul className="mt-2 space-y-1.5 text-[13.5px] text-[#C5CDD5]">
                  {chain.missing.map((m, i) => <li key={`m${i}`}>{words(m.what)}: {m.note}</li>)}
                  {chain.gaps.map((g, i) => <li key={`g${i}`}>{words(g.label)} ({words(g.reason)}){g.detail ? `: ${g.detail}` : ''}</li>)}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function StepShell({ part, title, meta, children, tone }: { part: PartId; title: string; meta: string; children: ReactNode; tone?: 'error' }) {
  const p = PART_BY_ID[part]
  return (
    <li className="relative grid grid-cols-[18px_minmax(0,1fr)] gap-x-4 pb-7 last:pb-0" style={{ ['--part' as string]: p.color }}>
      <span className="relative flex justify-center">
        <span className="mt-1.5 h-2.5 w-2.5 rounded-full" style={{ background: p.color, boxShadow: `0 0 10px ${p.color}` }} />
        <span className="absolute top-5 bottom-[-4px] w-px bg-[#1A2026]" aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <p className="text-[12.5px]" style={{ color: tone === 'error' ? '#FF8A80' : p.color }}>{join(p.label, meta)}</p>
        <h3 className="mt-0.5 text-[16px] font-medium text-[#EEF1F3]">{title}</h3>
        <div className="mt-2 space-y-2.5">{children}</div>
      </div>
    </li>
  )
}

/** The decision's answer in a line, from the fields its learner really writes; nothing when it wrote none. */
function answerLine(row: CapturedAttempt): string | null {
  const out = row.output ?? {}
  if (row.learner === 'student_2' && typeof out.raw === 'string') {
    try {
      const d = JSON.parse(out.raw) as Record<string, string | null>
      return join(d.intention, d.communication && `says: ${d.communication}`, d.move && d.move !== 'none' && `move: ${d.move}`,
        d.work && d.work !== 'no_change' && `work: ${d.work}`, d.think_about && `thinks about: ${d.think_about}`) || null
    } catch {
      return out.raw
    }
  }
  if (row.learner === 'hands') {
    const o = (out.output ?? {}) as Record<string, unknown>
    const action = out.action as unknown[] | null | undefined
    return join(typeof o.status === 'string' && `her status: ${o.status}`, typeof o.note === 'string' && o.note,
      Array.isArray(action) && action.length ? `chose ${String(action[0])}` : null) || null
  }
  const parsed = (out.parsed ?? null) as Record<string, unknown> | null
  if (parsed) {
    return join(typeof parsed.decision === 'string' && parsed.decision, typeof parsed.kind === 'string' && parsed.kind,
      typeof parsed.why === 'string' && parsed.why, typeof parsed.text === 'string' && parsed.text) || null
  }
  return null
}

function seconds(a: string | null, b: string | null): string | null {
  if (!a || !b) return null
  return `${((new Date(b).getTime() - new Date(a).getTime()) / 1000).toFixed(1)} s`
}

function AttemptStep({ row }: { row: CapturedAttempt }) {
  const part = LEARNER_PART[row.learner] ?? 'learning'
  const line = answerLine(row)
  const given = Object.keys(row.input ?? {}).filter((k) => row.input[k] !== null && row.input[k] !== undefined)
  return (
    <StepShell
      part={part}
      title={LEARNER_WORD[row.learner] ?? words(row.learner)}
      meta={join(words(row.trigger), clock(row.started_at), seconds(row.started_at, row.produced_at), row.model, row.status !== 'ok' && row.status)}
      tone={row.status === 'ok' ? undefined : 'error'}
    >
      {line && <p className="max-w-[68ch] text-[15px] leading-relaxed break-words text-[#DDE3E8]">{line}</p>}
      {row.error && <p className="text-[13.5px] text-[#FF8A80]">{row.error}</p>}
      {row.absent?.length > 0 && <p className="text-[13px] text-[#FFB27A]">Not given to it: {row.absent.map(String).join(', ')}</p>}
      <details className="text-[14px]">
        <summary className="cursor-pointer text-[#AEB7BF] hover:text-white">What it was given{given.length ? ` (${given.join(', ')})` : ''}</summary>
        <div className="mt-2"><Raw value={row.input} max={420} /></div>
      </details>
      {row.output && (
        <details className="text-[14px]">
          <summary className="cursor-pointer text-[#AEB7BF] hover:text-white">What it answered</summary>
          <div className="mt-2"><Raw value={row.output} /></div>
        </details>
      )}
    </StepShell>
  )
}

function ExecutionStep({ row }: { row: CapturedExecution }) {
  const part = EXECUTION_PART[row.kind] ?? 'cognition'
  const bad = row.result === 'failed'
  return (
    <StepShell
      part={part}
      title={EXECUTION_WORD[row.kind] ?? words(row.kind)}
      meta={join(words(row.result), clock(row.ended_at ?? row.started_at), seconds(row.started_at, row.ended_at), row.executor)}
      tone={bad ? 'error' : undefined}
    >
      {row.deviation && <p className={`text-[14px] ${bad ? 'text-[#FF8A80]' : 'text-[#FFB27A]'}`}>{row.deviation}</p>}
      <Facts rows={{ 'intended': Object.keys(row.intended ?? {}).length ? <Raw value={row.intended} max={200} /> : null, 'executed': Object.keys(row.executed ?? {}).length ? <Raw value={row.executed} max={260} /> : null }} />
    </StepShell>
  )
}
