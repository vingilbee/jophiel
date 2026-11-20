import { useEffect, useRef, useState, type PointerEvent, type WheelEvent } from 'react'
import { Button } from './Button'
import { desktopApi } from '../api/desktop'
import { useDesktopSession } from '../hooks/useDesktopSession'
import { ApiError } from '../api/client'
import type {
  DesktopEnvironment,
  DesktopDecisionReport,
  DesktopRelayStatus,
  DesktopSecurityStatus,
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
    detail: 'Jophiel has the controls.',
    tone: 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]',
  },
  paused: {
    label: 'Paused',
    detail: 'Nobody is pressing anything. Resume hands the controls back.',
    tone: 'border-[#D9E8F6] bg-[#F8FAFF] text-[#7183A3]',
  },
  manual: {
    label: 'You have control',
    detail: "Jophiel won't touch the game until you resume.",
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
type PointerMode = 'left' | 'right' | 'move'

/**
 * Where a pointer event landed in the decision frame (e.g. 640x360), or null
 * outside the picture. The image is letterboxed (object-contain), so the
 * rendered picture is found from its natural aspect ratio; positions are then
 * scaled per axis, exactly as the controller maps frame -> game window.
 */
function toFrame(
  e: { clientX: number; clientY: number },
  img: HTMLImageElement,
  frame: [number, number],
): [number, number] | null {
  const box = img.getBoundingClientRect()
  const ratio = img.naturalWidth / Math.max(img.naturalHeight, 1)
  const width = Math.min(box.width, box.height * ratio)
  const height = width / ratio
  const fx = (e.clientX - (box.left + (box.width - width) / 2)) / width
  const fy = (e.clientY - (box.top + (box.height - height) / 2)) / height
  if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null
  return [Math.min(Math.round(fx * frame[0]), frame[0] - 1), Math.min(Math.round(fy * frame[1]), frame[1] - 1)]
}

/**
 * Security monitoring: Jophiel reads the VM's security event logs (read only) while this is on, and only then.
 * Its own switch, apart from the preview and her hands. The channel list says what she can and can't read, so
 * "access denied" on the Security log is visible here rather than silently missing.
 */
function SecurityMonitor() {
  const { environment: env, busy, setSecurityMonitoring } = useDesktopSession()
  const [status, setStatus] = useState<DesktopSecurityStatus | null>(null)
  const envId = env && env.paired_at && env.hosting_kind !== 'game_pack' ? env.id : null
  const monitoring = env?.security_monitoring ?? false

  useEffect(() => {
    if (!envId) return
    let cancelled = false
    const load = () =>
      desktopApi
        .securityStatus(envId)
        .then((next) => !cancelled && setStatus(next))
        .catch(() => !cancelled && setStatus(null))
    void load()
    const timer = window.setInterval(load, 10_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [envId, monitoring])

  if (!env || !envId) return null
  const unreadable = status?.channels.filter((c) => c.status === 'unavailable') ?? []
  const readable = status?.channels.filter((c) => c.status === 'readable').length ?? 0
  const lastReport = status?.last_report_at ? new Date(status.last_report_at) : null

  return (
    <div className="space-y-1.5 rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3 text-xs text-[#4C5D7D]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium text-[#23314F]">Security monitoring · {monitoring ? 'on' : 'off'}</p>
        <Button
          variant={monitoring ? 'ghost' : 'outline'}
          disabled={busy}
          onClick={() => void setSecurityMonitoring(!monitoring)}
        >
          {monitoring ? 'Stop monitoring' : 'Start monitoring'}
        </Button>
      </div>
      <p className="text-[11px] text-[#7183A3]">
        {monitoring
          ? 'Jophiel reads the VM’s security logs (read only) and looks into anything notable. Nothing from while it was off is collected.'
          : 'Off: nothing is read from the VM’s logs. Records collected earlier stay readable for about two weeks.'}
      </p>
      {monitoring && status && (
        <p className="text-[11px] text-[#7183A3]" aria-live="polite">
          {status.source === 'offline'
            ? 'The VM is offline: nothing new is arriving.'
            : lastReport
              ? `${readable} of ${status.channels.length} channels readable · last report ${lastReport.toLocaleTimeString()}`
              : 'Waiting for the VM’s first report…'}
          {status.skew_seconds !== null && Math.abs(status.skew_seconds) >= 5
            ? ` · the VM’s clock is ${Math.round(Math.abs(status.skew_seconds))} s off`
            : ''}
        </p>
      )}
      {monitoring && unreadable.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-[#7183A3]">
          {unreadable.map((c) => (
            <li key={c.channel}>
              <span className="font-mono">{c.channel}</span>: {c.reason ?? 'can’t be read'}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function GamePreview() {
  const { environment: env, preview, busy, session, profiles, setCapture, setCaptureScope, sendDecision } =
    useDesktopSession()
  const imgRef = useRef<HTMLImageElement>(null)
  const pressed = useRef<[number, number] | null>(null)
  const [mode, setMode] = useState<PointerMode>('left')
  const [shift, setShift] = useState(false)
  const [marker, setMarker] = useState<[number, number] | null>(null)
  if (!env || !env.paired_at) return null
  if (env.hosting_kind === 'game_pack') {
    return (
      <p className="rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3 text-xs text-[#4C5D7D]">
        Jophiel plays as her own player, so there's no picture here: watch from your game, or{' '}
        <span className="font-mono">/spectate</span> her.
      </p>
    )
  }
  const online = env.status === 'online'
  const gate = env.capture_gate
  const stale = preview !== null && preview.ageMs > 5000

  let body: string | null = null
  if (!online) body = 'Game VM not connected.'
  else if (gate === 'manual_auth') body = 'Capture is off while you sign in.'
  else if (gate === 'blocked') body = 'Preview is off. Jophiel sees nothing from the VM.'
  else if (!preview)
    body =
      env.target_state === 'ok' || !env.target_state
        ? 'Waiting for the first picture…'
        : NO_PICTURE[env.target_state]

  // Clicking the picture clicks the game: only when the picture *is* the game
  // window (so its positions are the decision frame's) and the session plays.
  const frame = profiles.find((p) => p.id === session?.profile_id)?.observation_size ?? null
  const pointing =
    session?.state === 'running' && env.capture_scope === 'game_window' && frame !== null && !body && !busy
  const button = mode === 'right' ? 'menu_alt_click' : 'menu_click'
  const hold = shift ? ['sneak'] : []

  const point = (e: { clientX: number; clientY: number }) =>
    imgRef.current && frame ? toFrame(e, imgRef.current, frame) : null
  const showMarker = (xy: [number, number]) => frame && setMarker([xy[0] / frame[0], xy[1] / frame[1]])

  const onPointerDown = (e: PointerEvent<HTMLImageElement>) => {
    if (!pointing) return
    pressed.current = point(e)
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerUp = (e: PointerEvent<HTMLImageElement>) => {
    const start = pressed.current
    pressed.current = null
    const end = point(e)
    if (!pointing || !start || !end) return
    showMarker(end)
    const moved = frame ? Math.hypot(end[0] - start[0], end[1] - start[1]) > frame[0] * 0.02 : false
    if (mode === 'move') {
      void sendDecision({ intent: 'point', events: [{ type: 'move', point: end }], valid_for_ms: 200 })
    } else if (moved) {
      void sendDecision({
        intent: 'drag', hold,
        events: [{ type: 'drag', button, path: [start, end], duration_ms: 300 }],
        valid_for_ms: 700,
      })
    } else {
      // Move first, press a moment later, so the game has registered the position.
      void sendDecision({
        intent: shift ? 'shift-click' : mode === 'right' ? 'right click' : 'click', hold,
        events: [{ type: 'move', point: end }, { type: 'click', at_ms: 40, button, point: end }],
        valid_for_ms: 400,
      })
    }
  }
  const onWheel = (e: WheelEvent<HTMLImageElement>) => {
    const at = pointing ? point(e) : null
    if (!at || e.deltaY === 0) return
    showMarker(at)
    void sendDecision({ intent: 'scroll', events: [{ type: 'scroll', amount: e.deltaY > 0 ? 1 : -1, point: at }], valid_for_ms: 200 })
  }

  return (
    <div className="space-y-2">
      <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-[#D9E8F6] bg-[#EEF3FA]">
        {preview && !body ? (
          <>
            <img
              ref={imgRef}
              src={preview.url}
              alt={env.capture_scope === 'desktop' ? "Live view of the VM's screen" : 'Live view of the game window'}
              className={`h-full w-full object-contain select-none ${pointing ? 'cursor-crosshair' : ''}`}
              draggable={false}
              onPointerDown={onPointerDown}
              onPointerUp={onPointerUp}
              onWheel={onWheel}
            />
            {marker && pointing && (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-accent"
                style={{ left: `${marker[0] * 100}%`, top: `${marker[1] * 100}%` }}
              />
            )}
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

      {session?.state === 'running' && online && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#7183A3]">
          <span className="rounded-full border border-[#D9E8F6] px-2 py-0.5 font-mono">
            game: {env.input_context ?? '…'}
          </span>
          {env.capture_scope === 'game_window' ? (
            <>
              <span>Click the picture to</span>
              {(
                [
                  ['left', 'left-click'],
                  ['right', 'right-click'],
                  ['move', 'move the pointer'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={mode === value}
                  onClick={() => setMode(value)}
                  className={`rounded-full border px-2 py-0.5 ${
                    mode === value ? 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]' : 'border-[#D9E8F6]'
                  }`}
                >
                  {label}
                </button>
              ))}
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={shift} onChange={(e) => setShift(e.target.checked)} /> with Shift
              </label>
              <span>· drag to drag · wheel to scroll</span>
            </>
          ) : (
            <span>Switch to “Game window only” to click in the game from the picture.</span>
          )}
        </div>
      )}

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
        <div role="group" aria-label="What Jophiel can see" className="flex gap-1 text-[11px]">
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

const PAUSE_REASONS: Record<string, string> = {
  plan_complete: 'Jophiel finished every step of the goal. Give her a new one to keep playing.',
  pack_offline: 'Her Minecraft player left the world (the pack stopped, or lost the server).',
  planning_timeout: "She finished her goal but didn't pick a new one in time.",
  driver_stuck: 'Jophiel got stuck and stopped. Change the goal or resume to let her try again.',
  needs_human: 'Jophiel saw something outside the game (a login, a crash, a dialog) and needs you.',
  driver_error: 'The driver model kept failing, so she stopped. Resume to try again.',
  driver_unavailable: 'The driver model is not available (check OPENAI_API_KEY).',
  observation_stale: "She couldn't see the game for 10 seconds. Is the game in front in the VM?",
  capture_off: 'She can only drive while the preview is on (Show preview).',
  budget: 'The step budget for this session ran out.',
  not_foreground: 'The game stopped being the active window in the VM.',
}

function ago(at?: number): string {
  if (!at) return ''
  const s = Math.max(0, Math.round(Date.now() / 1000 - at))
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`
}

/** Keep playing on her own after a goal, exploring (her own player), and how often she speaks up. */
function PlayOptions() {
  const { session, busy, setOptions } = useDesktopSession()
  if (!session) return null
  const chip = (active: boolean) =>
    `rounded-full border px-2 py-0.5 ${active ? 'border-[#B9DCF4] bg-[#EAF6FF] text-[#527998]' : 'border-[#D9E8F6]'}`
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#7183A3]">
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={session.autonomy === 'continue'}
          disabled={busy}
          onChange={(e) => void setOptions({ autonomy: e.target.checked ? 'continue' : 'stop' })}
        />
        Keep playing on her own
      </label>
      {session.profile_id === 'minecraft_pack' && (
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={session.explore}
            disabled={busy}
            onChange={(e) =>
              void setOptions(e.target.checked ? { explore: true, autonomy: 'continue' } : { explore: false })
            }
          />
          Explore and learn
        </label>
      )}
      <span role="group" aria-label="How often she speaks up" className="flex gap-1">
        {(['quiet', 'occasional', 'commentary'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            aria-pressed={session.commentary_mode === mode}
            disabled={busy || session.commentary_mode === mode}
            onClick={() => void setOptions({ commentary_mode: mode })}
            className={chip(session.commentary_mode === mode)}
          >
            {mode}
          </button>
        ))}
      </span>
    </div>
  )
}

/** The plan and what the driver is doing with it (stage D1). */
function DriverPanel() {
  const { session, environment, driver } = useDesktopSession()
  if (!session?.plan) return null
  const playing = session.state === 'running'
  const pack = environment?.hosting_kind === 'game_pack'
  const blind = !pack && environment?.capture_gate !== 'allowed'
  const facts = Object.entries(driver?.facts ?? {})

  return (
    <div className="space-y-1.5 rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3">
      <p className="text-xs font-semibold text-[#7183A3]">
        {playing
          ? blind
            ? 'Jophiel is waiting to see the game'
            : driver?.waiting === 'next_goal'
              ? 'Jophiel is choosing her next goal'
              : 'Jophiel is playing'
          : 'Goal'}
      </p>
      <p className="text-sm">{session.plan.goal}</p>
      <ol className="space-y-0.5 text-xs text-[#4C5D7D]">
        {session.plan.steps.map((step) => (
          <li key={step.id} className={step.state === 'active' ? 'font-semibold text-[#23314F]' : ''}>
            {step.state === 'done' ? '✓' : step.state === 'active' ? '▶' : '·'} {step.do}
            <span className="text-[#7183A3]"> — done when {step.done_when}</span>
          </li>
        ))}
      </ol>
      {playing && blind && (
        <p className="text-[11px] text-[#9A5B36]">Turn on the preview (Show preview) so she can see the game.</p>
      )}
      {!playing && session.reason_code && PAUSE_REASONS[session.reason_code] && (
        <p className="text-[11px] text-[#9A5B36]">{PAUSE_REASONS[session.reason_code]}</p>
      )}
      {driver && pack && (
        <div className="space-y-1 text-[11px] text-[#4C5D7D]">
          {playing && driver.running && (
            <p>
              Doing: {driver.running.skill}(
              {Object.entries(driver.running.params)
                .filter(([k]) => k !== 'time_limit')
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(', ')}
              )
            </p>
          )}
          {driver.notes && <p>Her note: “{driver.notes}”</p>}
          {(driver.outcomes ?? []).slice(-4).map((o, i) => (
            <p key={i}>
              {o.status === 'done' ? '✓' : '•'} {o.skill} → {o.status}
              {o.reason ? `: ${o.reason}` : ''} <span className="text-[#7183A3]">({ago(o.at)})</span>
            </p>
          ))}
          {(driver.notices ?? []).slice(-3).map((n, i) => (
            <p key={`n${i}`}>
              {n.urgency === 'urgent' ? '⚠' : '👁'} {n.text}
            </p>
          ))}
          {driver.decisions !== undefined && (
            <p className="text-[10px] text-[#7183A3]">{driver.decisions} decisions so far</p>
          )}
        </div>
      )}
      {driver && !pack && (
        <div className="space-y-1 text-[11px] text-[#4C5D7D]">
          {driver.last && <p>Last move: “{driver.last.intent}” · step {driver.steps}</p>}
          {driver.model_ms !== undefined && (
            <p className="text-[#7183A3]">
              Thinking took {(driver.model_ms / 1000).toFixed(1)} s; the picture was{' '}
              {((driver.frame_age_ms ?? 0) / 1000).toFixed(1)} s old when the move went out.
            </p>
          )}
          {driver.notes && <p>Her notes: “{driver.notes}”</p>}
          {facts.length > 0 && (
            <p>
              Noticed:{' '}
              {facts
                .slice(0, 8)
                .map(([key, f]) => `${key} = ${f.value} (${ago(f.at)})`)
                .join(' · ')}
            </p>
          )}
          {(driver.reports ?? []).slice(-3).map((r, i) => (
            <p key={i}>
              {r.kind === 'danger' ? '⚠' : '•'} {r.text} <span className="text-[#7183A3]">({ago(r.at)})</span>
            </p>
          ))}
          <p className="text-[10px] text-[#7183A3]">Notes, facts and reports are what she read from the game: unverified.</p>
        </div>
      )}
    </div>
  )
}

/** Give or change the goal (Columbina will write plans herself in stage D2). */
function GoalForm() {
  const { session, busy, setPlan } = useDesktopSession()
  const [goal, setGoal] = useState('')
  const [steps, setSteps] = useState('')
  if (!session) return null
  const lines = steps
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 12)
  const typed = lines.map((line) => {
    const [act, done] = line.split('|').map((part) => part.trim())
    return { do: act.slice(0, 120), done_when: (done || `${act} is done`).slice(0, 120) }
  })
  // No steps written: the goal itself is the one step.
  const goalText = goal.trim()
  const parsed = typed.length
    ? typed
    : goalText
      ? [{ do: goalText.slice(0, 120), done_when: `${goalText} is done`.slice(0, 120) }]
      : []
  const missing = !goalText ? 'Write a goal first.' : busy ? 'Working…' : null

  return (
    <details className="text-[11px] text-[#7183A3]" open={!session.plan}>
      <summary className="cursor-pointer select-none font-semibold">
        {session.plan ? 'Change the goal' : 'Give Jophiel a goal'}
      </summary>
      <form
        className="mt-1 space-y-1.5"
        onSubmit={(e) => {
          e.preventDefault()
          if (goal.trim() && parsed.length) void setPlan({ goal: goal.trim().slice(0, 200), steps: parsed })
        }}
      >
        <input
          className={INPUT}
          placeholder="Goal, e.g. Collect 5 oak logs"
          value={goal}
          maxLength={200}
          onChange={(e) => setGoal(e.target.value)}
        />
        <textarea
          className={`${INPUT} font-mono text-[11px]`}
          rows={4}
          placeholder={'Steps (optional), one per line: what to do | when it is done'}
          value={steps}
          onChange={(e) => setSteps(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={missing !== null}>
            {session.plan ? 'Replace the goal' : 'Start driving'}
          </Button>
          <button
            type="button"
            className="underline-offset-2 hover:underline"
            onClick={() => {
              setGoal('Collect 5 oak logs')
              setSteps('Find an oak tree | a tree trunk fills the crosshair\nBreak the trunk | 5 oak logs in the hotbar')
            }}
          >
            Use example
          </button>
        </div>
        {missing && <p className="text-[#9A5B36]">{missing}</p>}
        <p>
          Steps are optional (one per line, “what to do | when it is done”). She drives while the session plays and
          the preview is on. Pause stops her at once.
        </p>
      </form>
    </details>
  )
}

function RegisterForm({ onDone, onCancel }: { onDone?: () => void; onCancel?: () => void }) {
  const { profiles, busy, registerEnvironment } = useDesktopSession()
  const [name, setName] = useState('Game VM')
  const [profileId, setProfileId] = useState('')
  const chosen = profileId || profiles[0]?.id || ''

  return (
    <form
      className="mt-3 space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (!chosen) return
        void registerEnvironment(name.trim(), [chosen]).then((added) => {
          if (added) onDone?.()
        })
      }}
    >
      <p className="text-sm text-[#7183A3]">Add a computer Jophiel can use. You pair it in the next step.</p>
      <label className="block text-xs text-[#7183A3]">
        Name
        <input className={`${INPUT} mt-1`} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
      </label>
      <ProfileSelect value={chosen} onChange={setProfileId} ids={profiles.map((p) => p.id)} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !name.trim() || !chosen}>
          Add machine
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  )
}

/** Keyboard focus for the card's plain text buttons. */
const FOCUS_RING = 'rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#527998]'

/** VM games that a game pack plays instead (mirrors backend game_pack.REPLACED_BY). */
const RETIRED_BY_PACK = ['minecraft_java']

function StartForm() {
  // The relay card sits outside the switch below, so opening "Add a machine" doesn't rebuild (and refetch) it.
  return (
    <>
      <StartChoices />
      <div className="mt-2">
        <RelayCard />
      </div>
    </>
  )
}

function StartChoices() {
  const { environments, environment, pairing, busy, start, pair, revokeEnvironment } = useDesktopSession()
  const [adding, setAdding] = useState(false)
  const [profileId, setProfileId] = useState('')
  const usable = environments.filter((e) => !e.revoked_at)
  const packs = usable.filter((e) => e.hosting_kind === 'game_pack')
  const machines = usable.filter((e) => e.hosting_kind !== 'game_pack')
  const env = environment && environment.hosting_kind !== 'game_pack' ? environment : machines[0]
  if (!env || adding) {
    return (
      <>
        {packs.length > 0 && <PackRows packs={packs} />}
        <RegisterForm onDone={() => setAdding(false)} onCancel={env ? () => setAdding(false) : undefined} />
      </>
    )
  }
  // With her own Minecraft player (the pack), Minecraft isn't played on the VM any more.
  const vmGames = env.allowed_profile_ids.filter((id) => !(packs.length && RETIRED_BY_PACK.includes(id)))
  const chosen = vmGames.includes(profileId) ? profileId : vmGames[0]
  const online = env.status === 'online'

  return (
    <div className="mt-3 space-y-2">
      {packs.length > 0 && <PackRows packs={packs} />}
      <MachinePicker machines={machines} current={env} onAdd={() => setAdding(true)} />
      <p className="flex items-center gap-2 text-sm" aria-live="polite">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${online ? 'bg-accent' : 'bg-[#B8C4D6]'}`} />
        {env.display_name} ·{' '}
        {online ? 'connected' : env.paired_at ? 'not connected' : 'not paired yet'}
      </p>
      {env.paired_at && <MachineReport env={env} />}

      {!pairing && <GamePreview />}
      {!pairing && <SecurityMonitor />}

      {pairing ? (
        <PairingBox />
      ) : (
        !online && (
          <Button variant={env.paired_at ? 'ghost' : 'primary'} disabled={busy} onClick={() => void pair(env.id)}>
            {env.paired_at ? 'Pair again' : 'Pair this machine'}
          </Button>
        )
      )}

      {env.paired_at && !pairing && <AutoStartSwitch env={env} />}

      {env.paired_at && !pairing && vmGames.length === 0 && (
        <p className="text-xs text-[#7183A3]">Minecraft is played as her own player (above), not on the VM.</p>
      )}
      {env.paired_at && !pairing && vmGames.length > 0 && (
        <>
          <ProfileSelect value={chosen} onChange={setProfileId} ids={vmGames} />
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
        Remove this machine
      </button>
    </div>
  )
}

/** Which paired machine the card is about, by its stable id. Shown once there's more than one. */
function MachinePicker({
  machines,
  current,
  onAdd,
}: {
  machines: DesktopEnvironment[]
  current: DesktopEnvironment
  onAdd: () => void
}) {
  const { busy, selectEnvironment } = useDesktopSession()
  return (
    <div className="flex flex-wrap items-end gap-2">
      {machines.length > 1 && (
        <label className="min-w-0 flex-1 text-xs text-[#7183A3]">
          Machine
          <select
            className={`${INPUT} mt-1`}
            value={current.id}
            disabled={busy}
            onChange={(e) => selectEnvironment(e.target.value)}
          >
            {machines.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name} · {m.status === 'online' ? 'connected' : m.paired_at ? 'not connected' : 'not paired'}
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={onAdd}
        className={`text-[11px] text-[#7183A3] underline-offset-2 hover:text-[#23314F] hover:underline ${FOCUS_RING}`}
      >
        Add a machine
      </button>
    </div>
  )
}

const PLATFORM: Record<string, string> = { windows: 'Windows', macos: 'macOS', linux: 'Linux', other: 'Other' }
const STARTUP_METHOD: Record<string, string> = {
  scheduled_task: 'logon task',
  run_key: 'Run key',
  launch_agent: 'LaunchAgent',
  autostart: 'autostart entry',
}

/** What the machine's controller reports: screen, keyboard and mouse, start at login (and why not). */
function MachineReport({ env }: { env: DesktopEnvironment }) {
  const report = env.controller_status
  if (!report) {
    if (env.status !== 'online') return null
    const old = env.controller_version && olderThan(env.controller_version, [0, 12, 0])
    return (
      <p className="text-[11px] text-[#7183A3]">
        {old
          ? `Its controller (${env.controller_version}) doesn’t report screen, input or login start. Update it to see them here.`
          : 'Waiting for the machine’s report…'}
      </p>
    )
  }
  const asOf = env.status !== 'online' && env.controller_status_at ? new Date(env.controller_status_at) : null
  const login =
    report.startup === 'registered'
      ? `yes${report.startup_method ? ` (${STARTUP_METHOD[report.startup_method] ?? report.startup_method})` : ''}`
      : report.startup === 'not_registered'
        ? 'no'
        : 'unknown'
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 break-words gap-y-0.5 rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3 text-[11px] text-[#4C5D7D]">
      <dt className="text-[#7183A3]">Machine</dt>
      <dd>
        {report.machine} · {report.os || PLATFORM[report.platform]}
        {report.display ? ` (${report.display === 'x11' ? 'X11' : 'Wayland'})` : ''}
      </dd>
      <dt className="text-[#7183A3]">Screen</dt>
      <dd>{report.capture.available ? 'can be seen' : (report.capture.reason ?? 'unavailable')}</dd>
      <dt className="text-[#7183A3]">Keyboard, mouse</dt>
      <dd>{report.input.available ? 'can be used' : (report.input.reason ?? 'unavailable')}</dd>
      <dt className="text-[#7183A3]">Starts at login</dt>
      <dd>{login}</dd>
      {asOf && (
        <dd className="col-span-2 text-[#7183A3]">As last reported {asOf.toLocaleString()}.</dd>
      )}
    </dl>
  )
}

function olderThan(version: string, than: [number, number, number]): boolean {
  const parts = version.split('.').map((n) => Number.parseInt(n, 10) || 0)
  for (let i = 0; i < 3; i++) {
    if ((parts[i] ?? 0) !== than[i]) return (parts[i] ?? 0) < than[i]
  }
  return false
}

/** The owner's switch: may Jophiel start a session on this machine herself? Off for every new pairing. */
function AutoStartSwitch({ env }: { env: DesktopEnvironment }) {
  const { busy, setAutoStart } = useDesktopSession()
  const id = `auto-start-${env.id}`
  return (
    <div className="flex items-start gap-2 rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3">
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 h-4 w-4 accent-[#527998]"
        checked={env.auto_start_allowed}
        disabled={busy}
        onChange={(e) => void setAutoStart(env.id, e.target.checked)}
      />
      <label htmlFor={id} className="text-xs text-[#23314F]">
        Allow Jophiel to start sessions here
        <span className="mt-0.5 block text-[11px] text-[#7183A3]">
          {env.auto_start_allowed
            ? 'She can start a session herself whenever this machine is connected, even with this page closed. You can stop it any time.'
            : 'Off: only you start sessions here. Connecting never starts one by itself.'}
        </span>
      </label>
    </div>
  )
}

const RELAY_STATE: Record<DesktopRelayStatus['state'], { label: string; dot: string }> = {
  open: { label: 'listening', dot: 'bg-accent' },
  closed: { label: 'closed', dot: 'bg-[#B8C4D6]' },
  error: { label: 'can’t listen', dot: 'bg-red-500' },
  stopped: { label: 'not running', dot: 'bg-[#B8C4D6]' },
}

const FIREWALL: Record<DesktopRelayStatus['firewall']['state'], string> = {
  not_needed: 'not needed: only virtual machines on this computer can reach the port',
  present: 'the rule admitting only this network is loaded',
  missing: 'the rule isn’t loaded',
  unverified: 'not verified',
}

/**
 * The relay on this computer (owner only; hidden for anyone else): where it listens, which network the owner
 * approved, the firewall, and what can't be checked from here. Open by itself while something needs doing.
 */
function RelayCard() {
  const { busy } = useDesktopSession()
  const [relay, setRelay] = useState<DesktopRelayStatus | null>(null)
  const [hidden, setHidden] = useState(false)
  const [working, setWorking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  // Polls while shown and the tab is visible. A 404 (not the owner) hides the card and ends the polling for good.
  useEffect(() => {
    if (hidden) return
    let cancelled = false
    const load = () => {
      if (document.hidden) return
      desktopApi
        .relay()
        .then((next) => !cancelled && setRelay(next))
        .catch((err) => {
          if (!cancelled && err instanceof ApiError && err.status === 404) setHidden(true)
        })
    }
    load()
    const timer = window.setInterval(load, 5_000)
    document.addEventListener('visibilitychange', load) // fresh at once when the tab comes back
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', load)
    }
  }, [hidden])

  if (hidden || !relay) return null
  const state = RELAY_STATE[relay.state]
  const act = (fn: () => Promise<DesktopRelayStatus>) => {
    setWorking(true)
    setProblem(null)
    fn()
      .then(setRelay)
      .catch((err) => setProblem(err instanceof ApiError ? err.message : 'Something went wrong. Try again.'))
      .finally(() => setWorking(false))
  }
  const approve = (iface: string, kind: string) => {
    const lan =
      'Other devices on this network can reach the relay port (they still can’t pair without a code). ' +
      'It stays closed on any other network.'
    if (kind === 'lan' && !window.confirm(`Approve the network on ${iface}? ${lan}`)) return
    act(() => desktopApi.approveRelayNetwork(iface))
  }
  const forget = () => {
    const what =
      'The relay closes and every paired machine disconnects until you approve a network again.'
    if (!window.confirm(`Forget the approved network? ${what}`)) return
    act(desktopApi.forgetRelayNetwork)
  }
  const disabled = busy || working

  return (
    <details
      className="rounded-xl border border-[#D9E8F6] bg-[#F8FAFF] p-3 text-xs text-[#4C5D7D]"
      open={relay.state !== 'open' || undefined}
    >
      <summary className="flex cursor-pointer items-center gap-2 select-none">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${state.dot}`} />
        <span className="font-medium text-[#23314F]">Relay on this computer · {state.label}</span>
        {relay.address && (
          <span className="font-mono text-[10px] text-[#7183A3]">
            {relay.address}:{relay.port}
          </span>
        )}
      </summary>
      <div className="mt-2 space-y-2" aria-live="polite">
        <p className="text-[11px]">
          {relay.detail}
          {relay.state === 'open' && !relay.advertising && relay.mode === 'approved'
            ? ' Not announced on the network, so machines use their last address or --relay.'
            : ''}
        </p>
        {relay.mode === 'explicit' ? (
          <p className="text-[11px] text-[#7183A3]">
            Uses the fixed address in <span className="font-mono">backend/.env</span> (
            <span className="font-mono">DESKTOP_CONTROLLER_BIND_HOST</span>). Remove that line and restart the relay to
            have it follow an approved network instead.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {relay.networks.map((n) => {
              const approved = relay.approval?.interface === n.interface
              return (
                <li key={`${n.interface}-${n.address}`} className="flex flex-wrap items-start justify-between gap-2">
                  <span className="min-w-0 flex-1">
                    <span className="font-mono text-[#23314F]">{n.interface}</span> · {n.network}
                    {n.kind === 'vm_only' ? ' · VM-only' : ' · this network'}
                    {n.recommended && !approved ? ' · suggested' : ''}
                    <span className="block text-[10px] text-[#7183A3]">{n.note}</span>
                  </span>
                  {approved ? (
                    <Button variant="ghost" disabled={disabled} onClick={forget}>
                      Approved · forget
                    </Button>
                  ) : (
                    n.approvable && (
                      <Button variant="outline" disabled={disabled} onClick={() => approve(n.interface, n.kind)}>
                        Approve
                      </Button>
                    )
                  )}
                </li>
              )
            })}
            {relay.networks.length === 0 && (
              <li className="text-[11px] text-[#7183A3]">No network the relay could listen on right now.</li>
            )}
          </ul>
        )}
        {relay.approval && !relay.networks.some((n) => n.interface === relay.approval?.interface) && (
          <p className="text-[11px] text-[#7183A3]">
            Approved: <span className="font-mono">{relay.approval.interface}</span> on {relay.approval.network} (not
            here right now).{' '}
            <button
              type="button"
              disabled={disabled}
              className={`underline underline-offset-2 hover:text-[#23314F] ${FOCUS_RING}`}
              onClick={forget}
            >
              Forget it
            </button>
          </p>
        )}
        {relay.kind !== 'vm_only' && (relay.approval || relay.mode === 'explicit') && (
          <p className="text-[11px]">
            Firewall: {FIREWALL[relay.firewall.state]}
            {relay.firewall.checked_at ? ` (checked ${new Date(relay.firewall.checked_at).toLocaleString()})` : ''}.
            {relay.firewall.how && <span className="block text-[10px] text-[#7183A3]">Run {relay.firewall.how}.</span>}
          </p>
        )}
        {relay.kind === 'vm_only' && <p className="text-[11px]">Firewall: {FIREWALL.not_needed}.</p>}
        {relay.cannot_verify.map((item) => (
          <p key={item} className="text-[10px] text-[#7183A3]">
            Can’t be checked from here: {item}.
          </p>
        ))}
        {problem && (
          <p role="alert" className="text-[11px] text-red-700">
            {problem}
          </p>
        )}
      </div>
    </details>
  )
}

/** Her own player in a game (stage G): no pairing, no preview; start it with a goal from the chat. */
function PackRows({ packs }: { packs: DesktopEnvironment[] }) {
  const { busy, start } = useDesktopSession()
  return (
    <div className="mt-3 space-y-2">
      {packs.map((pack) => {
        const online = pack.status === 'online'
        return (
          <div key={pack.id} className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-2 text-sm" aria-live="polite">
              <span aria-hidden="true" className={`h-2 w-2 rounded-full ${online ? 'bg-accent' : 'bg-[#B8C4D6]'}`} />
              Minecraft (her own player) · {online ? 'in the world' : 'not in the world'}
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void start(pack.id, pack.allowed_profile_ids[0])}
            >
              Start
            </Button>
          </div>
        )
      })}
    </div>
  )
}

function PairingBox() {
  const { pairing, cancelPairing } = useDesktopSession()
  if (!pairing) return null
  const windows = `py -m desktop_controller ${pairing.pair_args}`
  const unix = `python3 -m desktop_controller ${pairing.pair_args}`
  const expires = new Date(pairing.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="space-y-2 rounded-xl border border-[#DCCFF2] bg-[#F7F4FD] p-3">
      <p className="text-xs text-[#765E94]">
        On the machine, with the controller installed, run this once (works until {expires}). No address needed: it
        finds this computer’s relay on the network and pairs only with it.
      </p>
      <p className="text-[10px] text-[#7183A3]">Windows</p>
      <code className="block rounded-md bg-white px-2 py-1.5 font-mono text-xs break-all text-[#23314F] select-all">
        {windows}
      </code>
      <p className="text-[10px] text-[#7183A3]">macOS, Linux</p>
      <code className="block rounded-md bg-white px-2 py-1.5 font-mono text-xs break-all text-[#23314F] select-all">
        {unix}
      </code>
      <p className="text-xs text-[#765E94]">
        It starts right away and at every login from then on. This card switches to “connected” on its own.
      </p>
      {pairing.relay_address && (
        <p className="text-[11px] text-[#7183A3]">
          If it can’t find the relay (some networks block that), add{' '}
          <span className="font-mono">--relay {pairing.relay_address}</span>.
        </p>
      )}
      <details className="text-[10px] text-[#7183A3]">
        <summary className="cursor-pointer select-none">Relay fingerprint</summary>
        <p className="mt-1 font-mono break-all">{pairing.relay_fingerprint}</p>
        <p className="mt-1">The machine prints the same fingerprint after pairing.</p>
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
  const { session, environment, events, releasePending, busy, pause, resume, takeover, end, dismiss } =
    useDesktopSession()
  if (!session) return null
  const pack = environment?.hosting_kind === 'game_pack'
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
        {session.started_by && (
          <p className="mt-1 text-[11px] text-[#7183A3]">
            Started by {session.started_by === 'jophiel' ? 'Jophiel' : 'you'}
            {environment && environment.hosting_kind !== 'game_pack' ? ` on ${environment.display_name}` : ''}.
          </p>
        )}
        {session.reason_code && <p className="mt-1 font-mono text-[10px] text-[#7183A3]">reason: {session.reason_code}</p>}
      </div>

      {live && !pack && <TestControls />}

      {live && <DriverPanel />}
      {live && <PlayOptions />}
      {live && <GoalForm />}

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
