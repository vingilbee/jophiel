import { useEffect, useRef } from 'react'
import type { VoiceState } from '../hooks/useVoiceSession'

/**
 * The voice visualiser: a reactive orb whose motion *is* the state readout.
 *
 * Canvas rather than animated DOM because the whole point is per-frame reaction
 * to audio level, and 60 React renders a second to move 72 bars is the wrong
 * trade. The level arrives through a getter (`getLevel`) so nothing above this
 * component re-renders while the orb moves.
 *
 * Each phase gets its own *kind* of motion, not just its own colour — colour
 * alone is unreadable at a glance (and invisible to some users):
 *
 *   off       still, grey                  — nothing is running
 *   idle      slow breathing               — armed, nobody talking
 *   listening bars driven by the mic       — your voice is being captured
 *   thinking  arc sweeping around the ring — working, no audio either way
 *   speaking  bars + outward ripples       — the assistant's voice
 */

interface Props {
  state: VoiceState
  getLevel: () => number
  /**
   * How settled the assistant is, 0–1, from how long the conversation has been
   * quiet. Only the *breathing rate* reads it: an orb that breathes slower the
   * longer you've been away is the cheapest possible signal that something is
   * keeping time between turns, and it costs one multiply per frame.
   */
  calm?: number
  /** Extra classes for the square container the canvas fills. */
  className?: string
}

interface Palette {
  /** Ring + bar colour as `r, g, b` so alpha can be varied per element. */
  rgb: string
  /** Peak glow strength, 0–1. */
  glow: number
}

const PALETTE: Record<VoiceState, Palette> = {
  off: { rgb: '113, 131, 163', glow: 0.06 }, // muted blue
  idle: { rgb: '143, 199, 232', glow: 0.16 }, // sky blue
  listening: { rgb: '110, 174, 211', glow: 0.32 }, // Columbina blue
  thinking: { rgb: '182, 171, 201', glow: 0.24 }, // lilac
  speaking: { rgb: '217, 154, 174', glow: 0.32 }, // veil pink
}

const BARS = 72
const TAU = Math.PI * 2

/** Stable per-bar weight, so the ring has a fixed silhouette that pumps. */
function barWeight(i: number): number {
  const n = Math.sin(i * 12.9898) * 43758.5453
  return 0.55 + (n - Math.floor(n)) * 0.45
}

export function VoiceOrb({ state, getLevel, calm = 0, className = '' }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // The draw loop outlives every render; read all three through refs.
  const stateRef = useRef(state)
  stateRef.current = state
  const getLevelRef = useRef(getLevel)
  getLevelRef.current = getLevel
  const calmRef = useRef(calm)
  calmRef.current = calm

  useEffect(() => {
    const canvas = canvasRef.current
    const parent = canvas?.parentElement
    if (!canvas || !parent) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    let size = 0

    const observer = new ResizeObserver(([entry]) => {
      size = Math.min(entry.contentRect.width, entry.contentRect.height)
      canvas.width = Math.round(size * dpr)
      canvas.height = Math.round(size * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    })
    observer.observe(parent)

    let raf = 0
    let level = 0
    const start = performance.now()
    // Breathing is integrated rather than computed from `t`, because its rate
    // changes with `calm` — driving `sin(t * rate)` directly would snap the
    // phase every time the rate moved.
    let breath = 0
    let previous = start

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      if (size === 0) return

      const phase = stateRef.current
      const { rgb, glow } = PALETTE[phase]
      const t = (now - start) / 1000

      // Fast attack, slow release — the same asymmetry a VU meter uses. Snappy
      // on a syllable, no flicker in the gaps between them.
      const target = reduced ? 0 : getLevelRef.current()
      level += (target - level) * (target > level ? 0.4 : 0.09)

      // Clamped so a backgrounded tab (where rAF stops) doesn't resume with
      // one enormous step.
      const dt = Math.min((now - previous) / 1000, 0.1)
      previous = now

      const cx = size / 2
      const cy = size / 2
      // ~1.5 rad/s in an active conversation, easing to ~0.55 once it's been
      // quiet a while — awake versus resting, not moving versus still.
      breath += dt * (1.5 - calmRef.current * 0.95)
      const breathe = reduced || phase === 'off' ? 0 : Math.sin(breath) * 0.02
      const radius = size * 0.24 * (1 + breathe + level * 0.16)

      ctx.clearRect(0, 0, size, size)

      // Ambient glow, brightening with the voice.
      const halo = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, size * 0.5)
      halo.addColorStop(0, `rgba(${rgb}, ${glow * (0.55 + level * 0.45)})`)
      halo.addColorStop(1, `rgba(${rgb}, 0)`)
      ctx.fillStyle = halo
      ctx.fillRect(0, 0, size, size)

      // Ripples racing outward while the assistant talks.
      if (phase === 'speaking' && !reduced) {
        for (let k = 0; k < 3; k++) {
          const p = (t * 0.55 + k / 3) % 1
          ctx.beginPath()
          ctx.arc(cx, cy, radius * (1.12 + p * 1.1), 0, TAU)
          ctx.strokeStyle = `rgba(${rgb}, ${(1 - p) * 0.22})`
          ctx.lineWidth = 1.5
          ctx.stroke()
        }
      }

      // Level bars around the ring — only where there's real audio to show.
      if ((phase === 'listening' || phase === 'speaking') && !reduced) {
        const inner = radius * 1.14
        ctx.lineCap = 'round'
        ctx.lineWidth = Math.max(1.5, size * 0.008)
        for (let i = 0; i < BARS; i++) {
          const angle = (i / BARS) * TAU - Math.PI / 2
          const wobble = 0.72 + 0.28 * Math.sin(t * 5 + i * 0.55)
          const len = size * 0.035 * (0.28 + level * 2.1 * barWeight(i) * wobble)
          const cos = Math.cos(angle)
          const sin = Math.sin(angle)
          ctx.beginPath()
          ctx.moveTo(cx + cos * inner, cy + sin * inner)
          ctx.lineTo(cx + cos * (inner + len), cy + sin * (inner + len))
          ctx.strokeStyle = `rgba(${rgb}, ${0.35 + level * 0.5})`
          ctx.stroke()
        }
      }

      // Boundary ring, plus the sweeping arc that means "thinking".
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 1.08, 0, TAU)
      ctx.strokeStyle = `rgba(${rgb}, 0.28)`
      ctx.stroke()

      if (phase === 'thinking' && !reduced) {
        const head = t * 2.4
        ctx.beginPath()
        ctx.arc(cx, cy, radius * 1.08, head, head + Math.PI * 0.5)
        ctx.strokeStyle = `rgba(${rgb}, 0.9)`
        ctx.lineWidth = 2.5
        ctx.stroke()
      }

      // Core: a lit sphere when live, a flat disc when off.
      const core = ctx.createRadialGradient(
        cx - radius * 0.25,
        cy - radius * 0.3,
        radius * 0.1,
        cx,
        cy,
        radius,
      )
      core.addColorStop(0, `rgba(${rgb}, ${phase === 'off' ? 0.14 : 0.5 + level * 0.4})`)
      core.addColorStop(0.7, `rgba(${rgb}, ${phase === 'off' ? 0.08 : 0.16})`)
      core.addColorStop(1, 'rgba(248, 250, 255, 0.72)')
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, TAU)
      ctx.fillStyle = core
      ctx.fill()
    }

    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [])

  return (
    <div className={`relative aspect-square w-full ${className}`}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      <div className="absolute inset-0 flex items-center justify-center">
        <MicGlyph state={state} />
      </div>
    </div>
  )
}

/** Mic, or a struck-through mic when voice is off. Kept as SVG for crispness. */
function MicGlyph({ state }: { state: VoiceState }) {
  const off = state === 'off'
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className={`h-[14%] w-[14%] transition-colors duration-300 ${
        off ? 'text-muted' : 'text-fg'
      }`}
    >
      <path
        d="M12 4a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3ZM5.5 11.5a6.5 6.5 0 0 0 13 0M12 18.5V21"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      {off && (
        <path
          d="M4 20 20 4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      )}
    </svg>
  )
}
