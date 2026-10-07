import { useState } from 'react'
import { Button } from './Button'
import { useDesktopSession } from '../hooks/useDesktopSession'
import type {
  DesktopDecisionReport,
  DesktopSessionEvent,
  DesktopSessionState,
  DesktopTargetState,
} from '../types/desktop'

/**
 * Columbina's game session: who has the controls, the plan, and the buttons
 * to stop her (plan §11). Stage A skeleton: no preview yet, and nothing here
 * drives a real game until the VM controller exists (stage B/C).
 *
 * The copy never claims more than the server confirmed. In particular, after
 * Pause / Take over / End it says input release was *requested* until the VM
 * acknowledges it, because a stuck key the UI calls "stopped" is the worst case.
 */

const STATE_COPY: Record<DesktopSessionState, { label: string; detail: string; tone: string }> = {
  starting: {
    label: 'Starting',
    detail: 'Waiting for the game VM to find the game. Open it in the VM and click into it.',
    tone: 'border-[#DCCFF2] bg-[#F2EFFB] text-[#765E94]',
  },
  running: {
    label: 'Playing',
    detail: 'Columbina has the controls.',
    tone: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  },
  paused: {
    label: 'Paused',
    detail: 'Nobody is pressing anything. Resume hands the controls back.',
    tone: 'border-[#D9E8F6] bg-[#F8FAFF] text-[#7183A3]',
  },
  manual: {
    label: 'You have control',
    detail: "Columbina won't touch the game until you resume.",
    tone: 'border-[#F2D8C9] bg-[#FFF5EE] text-[#9A5B36]',
  },
  disconnected: {
    label: 'Disconnected',
    detail: "Lost the game VM. It won't restart on its own — resume when it's back.",
    tone: 'border-red-300/60 bg-red-50 text-red-700',
  },
  ended: { label: 'Ended', detail: 'This session is over.', tone: 'border-[#D9E8F6] bg-[#F8FAFF] text-[#7183A3]' },
  failed: { label: 'Failed', detail: 'This session stopped on an error.', tone: 'border-red-300/60 bg-red-50 text-red-700' },
}

const CARD = 'rounded-2xl border border-[#D9E8F6] bg-white/90 p-4 shadow-[0_8px_24px_rgba(35,49,79,0.04)] backdrop-blur-sm'
const INPUT =
  'w-full rounded-md border border-[#D9E8F6] bg-white px-3 py-2 text-sm text-[#23314F] outline-none focus-visible:ring-2 focus-visible:ring-accent/60'

export function DesktopSessionPanel({ className = '' }: { className?: string }) {
  const desktop = useDesktopSession()
  if (!desktop.enabled) return null

  return (
    <section className={`${CARD} ${className}`} aria-labelledby="desktop-heading">
      <h2 id="desktop-heading" className="text-[11px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">
        Game session
      </h2>
      {desktop.session ? <SessionView /> : <StartForm />}
      {desktop.error && (
        <p role="alert" className="mt-3 rounded-md border border-red-300/60 bg-red-50 px-3 py-2 text-xs text-red-700">
          {desktop.error}
        </p>
      )}
    </section>
  )
}

const NO_PICTURE: Record<DesktopTargetState, string> = {
  ok: 'Waiting for the first picture…',
  capture_off: 'Turning the preview on…',
  not_found: "The game isn't running in the VM.",
  not_foreground: "The game isn't the active window in the VM, so nothing is captured.",
  minimized: 'The game window is minimized.',
  sensitive: 'A Windows sign-in or permission prompt is open in the VM, so nothing is captured.',
  error: "The VM can't capture its screen. Check the program's window in the VM.",
}

/**
 * What Columbina can see. Off by default. "I'm signing in" stops capture in
 * the VM itself and stays off until turned back on; the line under it says
 * whether the VM has *confirmed* that, not just been asked.
 */
function GamePreview() {
  const { environment: env, preview, busy, setCapture, setCaptureScope } = useDesktopSession()
  if (!env || !env.paired_at) return null
  const online = env.status === 'online'
  const gate = env.capture_gate
  const stale = preview !== null && preview.ageMs > 5000

  let body: string | null = null
  if (!online) body = 'Game VM not connected.'
  else if (gate === 'manual_auth') body = 'Capture is off while you sign in.'
  else if (gate === 'blocked') body = 'Preview is off. Columbina sees nothing from the VM.'
  else if (!preview)
    body =
      env.target_state === 'ok' || !env.target_state
        ? 'Waiting for the first picture…'
        : NO_PICTURE[env.target_state]

  return (
    <div className="space-y-2">
      <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-[#D9E8F6] bg-[#EEF3FA]">
        {preview && !body ? (
          <>
            <img
              src={preview.url}
              alt={env.capture_scope === 'desktop' ? "Live view of the VM's screen" : 'Live view of the game window'}
              className="h-full w-full object-contain"
            />
            <span
              className={`absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 font-mono text-[9px] tracking-wider uppercase ${
                stale ? 'bg-amber-100 text-amber-800' : 'bg-white/80 text-[#527998]'
              }`}
            >
              {stale ? `stale · ${Math.round(preview.ageMs / 1000)}s` : 'live'}
            </span>
          </>
        ) : (
          <p className="flex h-full items-center justify-center px-4 text-center text-xs text-[#7183A3]">{body}</p>
        )}
      </div>

      {online && (
        <div className="flex flex-wrap items-center gap-2">
          {gate === 'allowed' ? (
            <>
              <Button variant="outline" disabled={busy} onClick={() => void setCapture('blocked')}>
                Hide preview
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => void setCapture('manual_auth')}>
                I'm signing in
              </Button>
            </>
          ) : (
            <Button variant="outline" disabled={busy} onClick={() => void setCapture('allowed')}>
              {gate === 'manual_auth' ? "I'm done signing in" : 'Show preview'}
            </Button>
          )}
        </div>
      )}
      {online && (
        <div role="group" aria-label="What Columbina can see" className="flex gap-1 text-[11px]">
          {(
            [
              ['desktop', 'Whole VM screen'],
              ['game_window', 'Game window only'],
            ] as const
          ).map(([scope, label]) => (
            <button
              key={scope}
              type="button"
              aria-pressed={env.capture_scope === scope}
              disabled={busy || env.capture_scope === scope}
              onClick={() => void setCaptureScope(scope)}
              className={`rounded-full border px-2.5 py-1 transition-colors ${
                env.capture_scope === scope
                  ? 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]'
                  : 'border-[#D9E8F6] text-[#7183A3] hover:text-[#23314F]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {online && env.capture_scope === 'desktop' && gate === 'allowed' && (
        <p className="text-[11px] text-[#7183A3]">
          Everything on the VM's screen is visible. Press “I'm signing in” before typing a password there.
        </p>
      )}
      {online && gate !== 'allowed' && (
        <p className="text-[11px] text-[#7183A3]" aria-live="polite">
          {env.capture_confirmed ? 'Capture off — confirmed by the VM.' : 'Turning capture off… waiting for the VM to confirm.'}
        </p>
      )}
    </div>
  )
}

// Stage C: hand-written moves for the Minecraft profile (no AI yet).
const MINECRAFT_MOVES: { label: string; decision: Record<string, unknown> }[] = [
  { label: 'Walk forward 2 s', decision: { intent: 'walk forward', hold: ['forward'], valid_for_ms: 2000 } },
  { label: 'Jump', decision: { intent: 'jump', events: [{ type: 'tap', button: 'jump' }], valid_for_ms: 300 } },
  { label: 'Turn left 90°', decision: { intent: 'turn left', axes: { look_yaw: -90 }, valid_for_ms: 1000 } },
  { label: 'Turn right 90°', decision: { intent: 'turn right', axes: { look_yaw: 90 }, valid_for_ms: 1000 } },
  { label: 'Look up', decision: { intent: 'look up', axes: { look_pitch: -45 }, valid_for_ms: 1000 } },
  { label: 'Look down', decision: { intent: 'look down', axes: { look_pitch: 45 }, valid_for_ms: 1000 } },
  {
    label: 'Sprint-jump',
    decision: {
      intent: 'sprint jump',
      hold: ['forward', 'sprint'],
      events: [{ type: 'tap', button: 'jump', at_ms: 500 }],
      valid_for_ms: 2000,
    },
  },
  { label: 'Mine 2 s', decision: { intent: 'mine', hold: ['attack'], valid_for_ms: 2000 } },
  { label: 'Inventory', decision: { intent: 'toggle inventory', events: [{ type: 'tap', button: 'inventory' }], valid_for_ms: 300 } },
  { label: 'Turn 360° (calibrate)', decision: { intent: 'calibrate yaw', axes: { look_yaw: 360 }, valid_for_ms: 1000 } },
]

function TestControls() {
  const { session, decisions, busy, sendDecision } = useDesktopSession()
  const [custom, setCustom] = useState('{ "hold": ["forward"], "valid_for_ms": 1000 }')
  const [customError, setCustomError] = useState<string | null>(null)
  if (!session) return null
  const playing = session.state === 'running'
  const moves = session.profile_id === 'minecraft_java' ? MINECRAFT_MOVES : []

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold text-[#7183A3]">Test controls</p>
      {!playing && <p className="text-[11px] text-[#7183A3]">Available while the session is playing.</p>}
      <div className="flex flex-wrap gap-1.5">
        {moves.map((move) => (
          <button
            key={move.label}
            type="button"
            disabled={busy || !playing}
            onClick={() => void sendDecision(move.decision)}
            className="rounded-md border border-[#D9E8F6] bg-white px-2 py-1 text-[11px] text-[#23314F] hover:border-accent disabled:opacity-40"
          >
            {move.label}
          </button>
        ))}
        <button
          type="button"
          disabled={busy || !playing}
          onClick={() => void sendDecision({ intent: 'stop' })}
          className="rounded-md border border-red-300/60 bg-red-50 px-2 py-1 text-[11px] text-red-700 disabled:opacity-40"
        >
          Stop (let go)
        </button>
      </div>

      <details className="text-[11px] text-[#7183A3]">
        <summary className="cursor-pointer select-none">Custom move (JSON)</summary>
        <textarea
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          rows={3}
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-[#D9E8F6] bg-white p-2 font-mono text-[11px] text-[#23314F]"
        />
        <Button
          variant="outline"
          disabled={busy || !playing}
          onClick={() => {
            try {
              const parsed: unknown = JSON.parse(custom)
              if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error()
              setCustomError(null)
              void sendDecision(parsed as Record<string, unknown>)
            } catch {
              setCustomError('That isn’t a JSON object.')
            }
          }}
        >
          Send
        </Button>
        {customError && <p className="mt-1 text-red-700">{customError}</p>}
      </details>

      {decisions.length > 0 && (
        <ul className="space-y-0.5 font-mono text-[10px] text-[#4C5D7D]" aria-label="Recent moves">
          {latestPerDecision(decisions)
            .slice(0, 6)
            .map((d, i) => (
              <li key={d.decision_id ?? `p${i}`}>{describeDecision(d)}</li>
            ))}
        </ul>
      )}
    </div>
  )
}

/** The server lists "sent" and then the outcome; show one line per decision. */
function latestPerDecision(reports: DesktopDecisionReport[]): DesktopDecisionReport[] {
  const seen = new Set<string>()
  return reports.filter((r) => {
    const key = r.decision_id ?? `problem:${r.at}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function describeDecision(d: DesktopDecisionReport): string {
  if (d.problem) return d.problem === 'target_missing' ? 'VM: game window not found yet' : `VM: ${d.problem}`
  const name = d.intent || d.decision_id || 'move'
  switch (d.state) {
    case 'sent':
      return `${name} · sent…`
    case 'expired':
      return `${name} · done (${Math.round((d.ran_ms ?? 0) / 100) / 10} s)`
    case 'completed':
      return `${name} · done early`
    case 'superseded':
      return `${name} · replaced by the next move`
    case 'cancelled':
      return `${name} · stopped (${d.ended_by ?? 'cancelled'})`
    case 'rejected':
      return `${name} · refused: ${d.rejection_reason ?? d.rejection_code}`
    default:
      return `${name} · ${d.state ?? '?'}`
  }
}

function RegisterForm() {
  const { profiles, busy, registerEnvironment } = useDesktopSession()
  const [name, setName] = useState('Game VM')
  const [profileId, setProfileId] = useState('')
  const chosen = profileId || profiles[0]?.id || ''

  return (
    <form
      className="mt-3 space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (chosen) void registerEnvironment(name.trim(), [chosen])
      }}
    >
      <p className="text-sm text-[#7183A3]">Add the computer Columbina will play on.</p>
      <label className="block text-xs text-[#7183A3]">
        Name
        <input className={`${INPUT} mt-1`} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
      </label>
      <ProfileSelect value={chosen} onChange={setProfileId} ids={profiles.map((p) => p.id)} />
      <Button type="submit" disabled={busy || !name.trim() || !chosen}>
        Add game VM
      </Button>
    </form>
  )
}

function StartForm() {
  const { environments, pairing, busy, start, pair, revokeEnvironment } = useDesktopSession()
  const usable = environments.filter((e) => !e.revoked_at)
  const env = usable[0]
  const [profileId, setProfileId] = useState('')
  if (!env) return <RegisterForm />
  const chosen = profileId || env.allowed_profile_ids[0]
  const online = env.status === 'online'

  return (
    <div className="mt-3 space-y-2">
      <p className="flex items-center gap-2 text-sm" aria-live="polite">
        <span className={`h-2 w-2 rounded-full ${online ? 'bg-accent' : 'bg-[#B8C4D6]'}`} />
        {env.display_name} ·{' '}
        {online ? 'connected' : env.paired_at ? 'not connected' : 'not paired yet'}
      </p>

      {!pairing && <GamePreview />}

      {pairing ? (
        <PairingBox />
      ) : (
        !online && (
          <Button variant={env.paired_at ? 'ghost' : 'primary'} disabled={busy} onClick={() => void pair(env.id)}>
            {env.paired_at ? 'Pair again' : 'Pair game VM'}
          </Button>
        )
      )}

      {env.paired_at && !pairing && (
        <>
          <ProfileSelect value={chosen} onChange={setProfileId} ids={env.allowed_profile_ids} />
          <Button disabled={busy || !chosen} onClick={() => void start(env.id, chosen)}>
            Start session
          </Button>
        </>
      )}

      <button
        type="button"
        disabled={busy}
        onClick={() => {
          if (window.confirm(`Remove ${env.display_name}? Its pairing stops working at once.`)) {
            void revokeEnvironment(env.id)
          }
        }}
        className="block text-[11px] text-[#7183A3] underline-offset-2 hover:text-red-700 hover:underline"
      >
        Remove this game VM
      </button>
    </div>
  )
}

function PairingBox() {
  const { pairing, cancelPairing } = useDesktopSession()
  if (!pairing) return null
  const command = `py -m desktop_controller pair ${pairing.relay_address} ${pairing.code}`
  const expires = new Date(pairing.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="space-y-2 rounded-xl border border-[#DCCFF2] bg-[#F7F4FD] p-3">
      <p className="text-xs text-[#765E94]">In the game VM, run this once (works until {expires}):</p>
      <code className="block rounded-md bg-white px-2 py-1.5 font-mono text-xs break-all text-[#23314F] select-all">
        {command}
      </code>
      <p className="text-xs text-[#765E94]">
        Then keep <span className="font-mono">py -m desktop_controller run</span> going. This card switches to
        “connected” on its own.
      </p>
      <details className="text-[10px] text-[#7183A3]">
        <summary className="cursor-pointer select-none">Relay fingerprint</summary>
        <p className="mt-1 font-mono break-all">{pairing.relay_fingerprint}</p>
        <p className="mt-1">The VM prints the same fingerprint after pairing.</p>
      </details>
      <Button variant="ghost" onClick={cancelPairing}>
        Close
      </Button>
    </div>
  )
}

function ProfileSelect({ value, onChange, ids }: { value: string; onChange: (id: string) => void; ids: string[] }) {
  const { profiles } = useDesktopSession()
  const names = new Map(profiles.map((p) => [p.id, p.display_name]))
  return (
    <label className="block text-xs text-[#7183A3]">
      Game
      <select className={`${INPUT} mt-1`} value={value} onChange={(e) => onChange(e.target.value)}>
        {ids.map((id) => (
          <option key={id} value={id}>
            {names.get(id) ?? id}
          </option>
        ))}
      </select>
    </label>
  )
}

function SessionView() {
  const { session, events, releasePending, busy, pause, resume, takeover, end, dismiss } = useDesktopSession()
  if (!session) return null
  const copy = STATE_COPY[session.state]
  const live = session.state !== 'ended' && session.state !== 'failed'
  const canPause = ['starting', 'running', 'manual', 'disconnected'].includes(session.state)
  const canResume = ['paused', 'manual', 'disconnected'].includes(session.state)
  const canTakeover = ['starting', 'running', 'paused', 'disconnected'].includes(session.state)

  return (
    <div className="mt-3 space-y-3">
      <GamePreview />
      {/* Announced: state changes are the one thing a listener must not miss. */}
      <div aria-live="polite">
        <span className={`inline-block rounded-full border px-3 py-1 font-mono text-[10px] tracking-[0.18em] uppercase ${copy.tone}`}>
          {copy.label}
        </span>
        <p className="mt-2 text-sm text-[#23314F]">{copy.detail}</p>
        {releasePending && live && (
          <p className="mt-1 text-xs text-[#9A5B36]">
            Asked the game VM to let go of all keys — not confirmed yet.
          </p>
        )}
        {session.reason_code && <p className="mt-1 font-mono text-[10px] text-[#7183A3]">reason: {session.reason_code}</p>}
      </div>

      {live && <TestControls />}

      {session.plan && (
        <div>
          <p className="text-xs font-semibold text-[#7183A3]">Goal</p>
          <p className="text-sm">{session.plan.goal}</p>
          <ol className="mt-1 space-y-0.5 text-xs text-[#4C5D7D]">
            {session.plan.steps.map((step) => (
              <li key={step.id} className={step.id === session.current_step_id ? 'font-semibold' : ''}>
                {step.state === 'done' ? '✓' : '·'} {step.do}
              </li>
            ))}
          </ol>
        </div>
      )}

      {live ? (
        <div className="flex flex-wrap gap-2">
          {canPause && (
            <Button variant="outline" disabled={busy} onClick={() => void pause()}>
              Pause
            </Button>
          )}
          {canResume && (
            <Button disabled={busy} onClick={() => void resume()}>
              Resume
            </Button>
          )}
          {canTakeover && (
            <Button variant="outline" disabled={busy} onClick={() => void takeover()}>
              Take over
            </Button>
          )}
          <Button variant="ghost" disabled={busy} onClick={() => void end()}>
            End session
          </Button>
        </div>
      ) : (
        <Button variant="outline" onClick={dismiss}>
          New session
        </Button>
      )}

      {events.length > 0 && (
        <details className="text-xs text-[#7183A3]">
          <summary className="cursor-pointer select-none">Activity</summary>
          <ul className="mt-1 space-y-0.5 font-mono text-[10px]">
            {events.slice(-8).map((e) => (
              <li key={e.id}>{describe(e)}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function describe(event: DesktopSessionEvent): string {
  const time = new Date(event.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const p = event.payload
  switch (event.type) {
    case 'desktop.session_state':
      return `${time}  ${String(p.state)}${p.reason ? ` (${String(p.reason)})` : ''}`
    case 'desktop.plan_set':
      return `${time}  new plan (revision ${String(p.plan_revision)})`
    case 'desktop.plan_appended':
      return `${time}  plan extended`
    default:
      return `${time}  ${event.type}`
  }
}
