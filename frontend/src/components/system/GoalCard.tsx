import type { GameGoal } from '../../types/system'
import { PART_BY_ID } from './anatomy'
import { ago, join, span, words } from './format'

const HANDS = PART_BY_ID.hands.color

/** One plan step's mark: done filled, the active one ringed and lit, the rest hollow. */
function StepMark({ state }: { state: string | null }) {
  if (state === 'done') return <span className="mt-[7px] h-2 w-2 shrink-0 rounded-full" style={{ background: HANDS }} />
  if (state === 'active')
    return <span className="mt-[5px] h-3 w-3 shrink-0 rounded-full ring-2" style={{ ['--tw-ring-color' as string]: HANDS, boxShadow: `0 0 10px ${HANDS}` }} />
  return <span className="mt-[7px] h-2 w-2 shrink-0 rounded-full ring-1 ring-[#3A444D]" />
}

/** What her hands are after: the goal the session plays for, the plan's steps, and her effort as code counted it.
 * ``compact`` is the watch log's version: the goal, the current step and the time on it. */
export function GoalCard({ goal, compact = false }: { goal: GameGoal | null; compact?: boolean }) {
  if (!goal) return <p className="text-[13.5px] text-[#8C97A3]">No game played yet, so no goal for her hands.</p>
  const steps = goal.plan?.steps ?? []
  const current = steps.find((s) => s.state === 'active')
  const done = steps.filter((s) => s.state === 'done').length
  const live = !goal.ended_at
  const e = goal.effort
  return (
    <div style={{ ['--part' as string]: HANDS }}>
      <p className="text-[12.5px]" style={{ color: HANDS }}>
        {join(live ? words(goal.state) : `ended ${ago(goal.ended_at)} ago`, goal.driver && live && `hands ${words(goal.driver)}`,
          goal.goal && goal.goal.status !== 'active' && words(goal.goal.status), goal.goal?.owner === 'user' && 'your goal')}
      </p>
      <p className="mt-1 text-[16px] leading-snug font-medium text-[#F1F4F6]">
        {goal.goal?.text ?? goal.plan?.goal ?? 'No goal set'}
      </p>
      {goal.goal?.reason && <p className="mt-1 text-[13px] text-[#8C97A3]">{goal.goal.reason}</p>}
      {goal.plan?.goal && goal.plan.goal !== goal.goal?.text && (
        <p className="mt-1.5 text-[13.5px] text-[#AEB7BF]">Plan: {goal.plan.goal}</p>
      )}
      {e && (
        <p className="mt-1.5 text-[12.5px] text-[#8C97A3] tabular-nums">
          {join(`${span(e.active_s)} playing`, e.paused_s > 0 && `${span(e.paused_s)} paused`, steps.length > 0 && `${done} of ${steps.length} steps done`,
            e.approach_changes > 0 && `plan goal changed ${e.approach_changes}×`)}
        </p>
      )}

      {compact ? (
        current && (
          <p className="mt-2.5 flex gap-2.5 text-[14px] text-[#DDE3E8]">
            <StepMark state="active" />
            <span className="min-w-0">
              {current.do}
              {current.done_when && <span className="block text-[12.5px] text-[#8C97A3]">done when {current.done_when}</span>}
            </span>
          </p>
        )
      ) : (
        <>
          {steps.length > 0 && (
            <ol className="mt-4 space-y-2.5">
              {steps.map((s, i) => (
                <li key={s.id ?? i} className={`flex gap-3 text-[14.5px] ${s.state === 'done' ? 'text-[#8C97A3]' : s.state === 'active' ? 'text-[#F1F4F6]' : 'text-[#C5CDD5]'}`}>
                  <StepMark state={s.state} />
                  <span className="min-w-0">
                    {s.do}
                    {s.done_when && <span className="block text-[12.5px] text-[#7D8790]">done when {s.done_when}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {e && e.rules.length > 0 && (
            <div className="mt-4">
              <p className="text-[12.5px] text-[#8C97A3]">Your rules for it</p>
              <ul className="mt-1 space-y-1 text-[14px] text-[#DDE3E8]">
                {e.rules.map((r) => (
                  <li key={r.ref}>{r.rule}{r.words && <span className="text-[#8C97A3]"> (“{r.words}”)</span>}</li>
                ))}
              </ul>
            </div>
          )}
          {e && e.counted.length > 0 && (
            <div className="mt-4">
              <p className="text-[12.5px] text-[#8C97A3]">Counted by code since the goal was set</p>
              <ul className="mt-1 space-y-0.5 text-[13.5px] text-[#C5CDD5] tabular-nums">
                {e.counted.map((c) => <li key={c}>{c}</li>)}
              </ul>
              {e.spots && <p className="mt-1 text-[13px] text-[#8C97A3]">{e.spots}</p>}
            </div>
          )}
          {e && e.places.length > 0 && (
            <p className="mt-3 text-[13px] text-[#8C97A3]">Places: {e.places.map((p) => (p.where ? `${p.name} (${p.where})` : p.name)).join(', ')}</p>
          )}
        </>
      )}
    </div>
  )
}
