import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { learningApi } from '../../api/learning'
import type { Challenge, LearnedSkill } from '../../types/learning'

const STATUS_STYLES: Record<string, string> = {
  open: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  accepted: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  implemented: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  rejected: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  confirmed: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  on: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  off: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  trial: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  retired: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  active: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  done: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  'gave up': 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  'you changed it': 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  'session ended': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  supported: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  concluded: 'border-[#24503A] bg-[#0E1F16] text-[#7FD6A0]',
  plausible: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  unresolved: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  contradicted: 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  failed: 'border-[#5A2638] bg-[#2A1119] text-[#F08AB0]',
  'no feedback': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  pending: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  running: 'border-[#2B4A5C] bg-[#0E1A22] text-[#7FB8D8]',
  hypothesis: 'border-[#3A2E58] bg-[#1A1528] text-[#B9A2F0]',
  'one episode': 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
  withdrawn: 'border-[#1C2228] bg-[#0F1317] text-[#8C97A3]',
}

const CHALLENGE_LABELS: Record<Challenge['status'], string> = {
  active: 'active',
  done: 'done',
  given_up: 'gave up',
  replaced: 'you changed it',
  expired: 'session ended',
}

function Chip({ label }: { label: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] ${STATUS_STYLES[label] ?? STATUS_STYLES.rejected}`}>
      {label}
    </span>
  )
}

function day(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function LearningDetails() {
  const [challenges, setChallenges] = useState<Challenge[] | null>(null)
  const [skills, setSkills] = useState<LearnedSkill[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([learningApi.challenges(), learningApi.skills()])
      setChallenges(c)
      setSkills(s)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load what she learned.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    }
  }


  return (
    <div className="text-[#E4E8EC]">
      <p className="mb-6 text-[13px] leading-relaxed text-[#9AA5B1]">
        Moves she taught herself in games are hers to use at once, and the challenges she set herself while
        exploring.
      </p>
        {error && (
          <p className="mb-5 rounded-xl border border-[#5A2638] bg-[#2A1119] px-3 py-2 text-xs text-[#F08AB0]">{error}</p>
        )}

        <Section
          title="Challenges she set herself"
          hint="While exploring, she picks her own next challenge after each goal and teaches herself the moves it needs. Newest first."
        >
          {challenges === null && !error && <Loading />}
          {challenges?.length === 0 && <Empty text="No challenges yet. With exploring on, she picks one each time a goal is done." />}
          {challenges?.map((c) => <ChallengeRow key={c.id} challenge={c} />)}
        </Section>

        <Section
          title="Moves she taught herself"
          hint="Programs that worked when she practiced a move. Her hands use them like any skill, in any session, while they're on. Learning one again replaces it."
        >
          {skills === null && !error && <Loading />}
          {skills?.length === 0 && <Empty text="No moves yet. Ask her to learn one while you play (e.g. pillar up by placing blocks)." />}
          {skills?.map((s) => (
            <SkillCard
              key={s.id}
              skill={s}
              onEnabled={(enabled) => act(() => learningApi.setSkillEnabled(s.id, enabled))}
              onDelete={() => act(() => learningApi.deleteSkill(s.id))}
            />
          ))}
        </Section>

    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 mb-3 text-xs text-[#8C97A3]">{hint}</p>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  )
}

function Loading() {
  return <p className="text-[13px] text-[#8C97A3]">Reading…</p>
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-[#2B4A5C] bg-[#0D1115] px-6 py-8 text-center text-sm text-[#8C97A3]">{text}</p>
  )
}

const SMALL_BUTTON =
  'rounded-full border border-[#232A31] bg-[#0D1115] px-3 py-1 text-[11px] text-[#9AA5B1] transition-colors hover:border-[#F08AB0] hover:text-[#F08AB0] disabled:opacity-50'

function ChallengeRow({ challenge: c }: { challenge: Challenge }) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-[#C5CDD5]">{c.challenge}</p>
        {c.why && <p className="mt-1 text-xs text-[#8C97A3]">{c.why}</p>}
        <p className="mt-1 text-[11px] text-[#8C97A3]">
          {c.aim === 'for_us' ? 'for the two of you' : 'something new'} · {c.game} · {day(c.created_at)}
          {c.result && ` · ${c.result}`}
          {c.learned.length > 0 && ` · learned ${c.learned.join(', ')}`}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Chip label={CHALLENGE_LABELS[c.status]} />
      </div>
    </div>
  )
}

function SkillCard({
  skill: s,
  onEnabled,
  onDelete,
}: {
  skill: LearnedSkill
  onEnabled: (enabled: boolean) => void
  onDelete: () => void
}) {
  return (
    <article className="rounded-2xl border border-[#1C2228] bg-[#0D1115] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-medium">{s.task}</h3>
        <span className="font-mono text-[11px] text-[#8C97A3]">{s.name}</span>
        <Chip label={s.enabled ? 'on' : 'off'} />
      </div>
      <p className="mt-2 text-[11px] text-[#8C97A3]">
        {s.game} · learned {day(s.created_at)}
        {s.updated_at !== s.created_at && ` · relearned ${day(s.updated_at)}`} ·{' '}
        {s.uses ? `used ${s.uses}×, worked ${s.successes}` : 'not used yet'}
        {s.enabled && s.misses_in_row > 0 && ` · missed the last ${s.misses_in_row}`}
      </p>
      {!s.enabled && s.off_reason && (
        <p className="mt-2 text-xs text-[#C5CDD5]">Switched itself off: {s.off_reason}</p>
      )}
      {s.tidy_note && (
        <p className="mt-2 text-xs text-[#C5CDD5]">
          {s.original_code ? (s.tidy_proven ? 'Tidied (has worked since): ' : 'Tidied, not tried yet: ') : ''}
          {s.tidy_note}
        </p>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-[#8C97A3]">{s.original_code ? 'Her program (tidied)' : 'Her program'}</summary>
        <pre className="mt-2 overflow-x-auto rounded-xl bg-[#0F1317] px-3 py-2 font-mono text-[11px] text-[#C5CDD5]">{s.code}</pre>
      </details>
      {s.original_code && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[#8C97A3]">As she learned it</summary>
          <pre className="mt-2 overflow-x-auto rounded-xl bg-[#0F1317] px-3 py-2 font-mono text-[11px] text-[#C5CDD5]">{s.original_code}</pre>
        </details>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => onEnabled(!s.enabled)} className={SMALL_BUTTON}>{s.enabled ? 'Switch off' : 'Switch on'}</button>
        <button onClick={onDelete} className={SMALL_BUTTON}>Delete</button>
      </div>
    </article>
  )
}
