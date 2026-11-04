import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import type { PartId, PartState, PartStatus } from '../../types/system'
import { MAX_MAG, PARTS, SUBS_FROM, SUBS_FULL, WIRES, layoutFor } from './anatomy'
import type { PartAnatomy } from './anatomy'
import { axon, dendrites, reachToward, rng, seedOf, somaPath } from './shapes'
import { FitIcon, MinusIcon, PlusIcon } from './icons'

interface View {
  x: number
  y: number
  k: number
}

const STATE_WORD: Record<PartState, string> = {
  alive: 'alive',
  busy: 'working',
  partial: 'partly down',
  down: 'down',
  failing: 'failing',
  off: 'off',
}

/** How often a cell flashes (a calcium transient) in each state, in seconds; none when it isn't running. */
const PERIOD: Partial<Record<PartState, number>> = { alive: 2.8, busy: 1.05, partial: 4.4, failing: 1.6 }
const FAIL = '#FF4136'

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t))

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** The out-of-focus tissue around the cells: blurred bodies and fibres, painted once, moved with parallax. */
function useTissue(canvas: HTMLCanvasElement | null, key: string) {
  useEffect(() => {
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const W = canvas.width
    const H = canvas.height
    const rand = rng(7)
    const colors = PARTS.map((p) => p.color)
    ctx.clearRect(0, 0, W, H)
    const layer = (blur: number, bodies: number, fibres: number, alpha: number) => {
      ctx.filter = `blur(${blur}px)`
      for (let i = 0; i < fibres; i++) {
        const c = colors[Math.floor(rand() * colors.length)]
        ctx.strokeStyle = c
        ctx.globalAlpha = alpha * (0.5 + rand() * 0.6)
        ctx.lineWidth = 1.5 + rand() * 4
        const x = rand() * W
        const y = rand() * H
        const len = 120 + rand() * 360
        const a = rand() * Math.PI * 2
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.bezierCurveTo(
          x + Math.cos(a) * len * 0.4 + (rand() - 0.5) * 120, y + Math.sin(a) * len * 0.4 + (rand() - 0.5) * 120,
          x + Math.cos(a) * len * 0.7 + (rand() - 0.5) * 120, y + Math.sin(a) * len * 0.7 + (rand() - 0.5) * 120,
          x + Math.cos(a) * len, y + Math.sin(a) * len,
        )
        ctx.stroke()
      }
      for (let i = 0; i < bodies; i++) {
        const c = colors[Math.floor(rand() * colors.length)]
        const x = rand() * W
        const y = rand() * H
        const r = 10 + rand() * 34
        const g = ctx.createRadialGradient(x, y, 0, x, y, r)
        g.addColorStop(0, c)
        g.addColorStop(1, 'transparent')
        ctx.globalAlpha = alpha * (0.6 + rand() * 0.8)
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fill()
      }
    }
    const density = (W * H) / (1440 * 900)
    layer(22, Math.round(40 * density), Math.round(38 * density), 0.16)
    layer(9, Math.round(52 * density), Math.round(44 * density), 0.11)
    ctx.filter = 'none'
    ctx.globalAlpha = 1
  }, [canvas, key])
}

interface Props {
  statuses: Partial<Record<PartId, PartStatus>>
  selected: PartId | null
  onSelect: (id: PartId) => void
  /** The details panel is open beside the field (wide screens): keep the controls clear of it. */
  panelOpen?: boolean
}

export function FieldMap({ statuses, selected, onSelect, panelOpen = false }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [view, setView] = useState<View | null>(null)
  const viewRef = useRef<View | null>(null)
  const atFit = useRef(true)
  const anim = useRef<number | null>(null)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ moved: number; pinch: number | null }>({ moved: 0, pinch: null })
  const dragged = useRef(false)
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null)
  const portrait = size.h > size.w * 1.15
  const layout = useMemo(() => layoutFor(portrait), [portrait])
  const FIELD_W = layout.w
  const FIELD_H = layout.h
  // The tissue canvas covers the field and a wide margin around it, so its edge never shows.
  const EXT = 2.6
  const CW = 1600
  const CH = Math.round((CW * FIELD_H) / FIELD_W)
  useTissue(canvas, portrait ? 'portrait' : 'landscape')

  // "Everything" is the cells with their labels, not the empty field around them, so a phone is filled.
  const content = useMemo(() => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const p of layout.parts) {
      const e = p.r * 2.3
      x0 = Math.min(x0, p.x - e - (p.labelSide === 'left' ? 150 : 0))
      x1 = Math.max(x1, p.x + e + (p.labelSide === 'right' ? 150 : 0))
      y0 = Math.min(y0, p.y - e - (p.labelSide === 'above' ? 40 : 0))
      y1 = Math.max(y1, p.y + e + (p.labelSide === 'below' ? 40 : 0))
    }
    return { x0, y0, w: x1 - x0, h: y1 - y0 }
  }, [layout])
  // Room for the page's own text above and the controls below.
  const padTop = portrait ? 110 : 70
  const padBottom = 70
  const kFit = size.w && size.h ? Math.min(size.w / content.w, (size.h - padTop - padBottom) / content.h) * 0.96 : 1
  const fitView = useCallback(
    (): View => ({
      k: kFit,
      x: (size.w - content.w * kFit) / 2 - content.x0 * kFit,
      y: padTop + (size.h - padTop - padBottom - content.h * kFit) / 2 - content.y0 * kFit,
    }),
    [kFit, size.w, size.h, content, padTop],
  )

  const commit = useCallback((v: View) => {
    viewRef.current = v
    setView(v)
  }, [])

  // Measure the viewport; keep the fitted view on resize unless the visitor has moved it.
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setSize({ w: entry.contentRect.width, h: entry.contentRect.height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    if (!size.w || !size.h) return
    if (atFit.current || !viewRef.current) commit(fitView())
  }, [size.w, size.h, fitView, commit])
  useEffect(() => {
    atFit.current = true
  }, [portrait])

  /** Keep some of the field on screen and the zoom within range. */
  const bound = useCallback(
    (v: View): View => {
      const k = clamp(v.k, kFit, kFit * MAX_MAG)
      const slack = 0.35
      const minX = size.w * slack - FIELD_W * k
      const maxX = size.w * (1 - slack)
      const minY = size.h * slack - FIELD_H * k
      const maxY = size.h * (1 - slack)
      return { k, x: clamp(v.x, minX, maxX), y: clamp(v.y, minY, maxY) }
    },
    [kFit, size.w, size.h, FIELD_W, FIELD_H],
  )

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number, from?: View): View => {
      const v = from ?? viewRef.current ?? fitView()
      const k = clamp(v.k * factor, kFit, kFit * MAX_MAG)
      const f = k / v.k
      return bound({ k, x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f })
    },
    [bound, fitView, kFit],
  )

  const animateTo = useCallback(
    (target: View) => {
      if (anim.current) cancelAnimationFrame(anim.current)
      const start = viewRef.current ?? target
      if (prefersReducedMotion()) {
        commit(target)
        return
      }
      const t0 = performance.now()
      const step = (now: number) => {
        const t = Math.min(1, (now - t0) / 420)
        const e = easeOutExpo(t)
        // Interpolate zoom in log space so it feels even at every magnification.
        const k = Math.exp(Math.log(start.k) + (Math.log(target.k) - Math.log(start.k)) * e)
        commit({ k, x: start.x + (target.x - start.x) * e, y: start.y + (target.y - start.y) * e })
        anim.current = t < 1 ? requestAnimationFrame(step) : null
      }
      anim.current = requestAnimationFrame(step)
    },
    [commit],
  )

  // On a phone the panel is a sheet over the lower field: bring the chosen cell into the strip above it.
  useEffect(() => {
    if (!selected || !panelOpen || size.w >= 640 || !viewRef.current) return
    const p = layout.byId[selected]
    const v = viewRef.current
    atFit.current = false
    animateTo({ k: v.k, x: size.w / 2 - p.x * v.k, y: size.h * 0.16 + 50 - p.y * v.k })
  }, [selected, panelOpen, size.w, size.h, layout, animateTo])

  /** The buttons zoom toward the cell nearest the middle of the screen, so a part stays framed. */
  const zoomButton = (factor: number) => {
    atFit.current = false
    const v = viewRef.current
    if (!v) return
    let cx = size.w / 2
    let cy = size.h / 2
    let best = Infinity
    for (const p of layout.parts) {
      const sx = v.x + p.x * v.k
      const sy = v.y + p.y * v.k
      const d = Math.hypot(sx - size.w / 2, sy - size.h / 2)
      if (d < best && d < Math.min(size.w, size.h) * 0.3) {
        best = d
        cx = sx
        cy = sy
      }
    }
    const target = zoomAt(factor, cx, cy)
    // Then bring that cell to the middle, so its sub-parts open around it in view.
    if (best !== Infinity) {
      const p = layout.parts.find((q) => Math.hypot(v.x + q.x * v.k - cx, v.y + q.y * v.k - cy) < 1)
      if (p) {
        animateTo(bound({ k: target.k, x: size.w / 2 - p.x * target.k, y: size.h / 2 - p.y * target.k }))
        return
      }
    }
    animateTo(target)
  }
  const fit = () => {
    atFit.current = true
    animateTo(fitView())
  }

  // Wheel zoom about the pointer (non-passive, so the page itself never scrolls).
  useEffect(() => {
    const el = box.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      if (anim.current) cancelAnimationFrame(anim.current)
      atFit.current = false
      const rect = el.getBoundingClientRect()
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      commit(zoomAt(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0018)), e.clientX - rect.left, e.clientY - rect.top))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [commit, zoomAt])

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest('[data-chrome]')) return
    if (anim.current) cancelAnimationFrame(anim.current)
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.current.size === 1) {
      gesture.current = { moved: 0, pinch: null }
      dragged.current = false
    } else {
      e.currentTarget.setPointerCapture(e.pointerId)
    }
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const prev = pointers.current.get(e.pointerId)
    if (!prev || !viewRef.current) return
    const next = { x: e.clientX, y: e.clientY }
    pointers.current.set(e.pointerId, next)
    const v = viewRef.current
    if (pointers.current.size === 1) {
      const dx = next.x - prev.x
      const dy = next.y - prev.y
      gesture.current.moved += Math.hypot(dx, dy)
      if (gesture.current.moved > 4) {
        // Capture only once it's a drag, so a plain click still reaches the cell under the pointer.
        if (!dragged.current) e.currentTarget.setPointerCapture(e.pointerId)
        dragged.current = true
        atFit.current = false
        commit(bound({ ...v, x: v.x + dx, y: v.y + dy }))
      }
      return
    }
    // Two fingers: pinch about their midpoint.
    const [a, b] = [...pointers.current.values()]
    const dist = Math.hypot(a.x - b.x, a.y - b.y)
    const rect = box.current!.getBoundingClientRect()
    const mid = { x: (a.x + b.x) / 2 - rect.left, y: (a.y + b.y) / 2 - rect.top }
    if (gesture.current.pinch) {
      dragged.current = true
      atFit.current = false
      commit(zoomAt(dist / gesture.current.pinch, mid.x, mid.y, v))
    }
    gesture.current.pinch = dist
  }

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId)
    if (pointers.current.size < 2) gesture.current.pinch = null
  }

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const v = viewRef.current
    if (!v) return
    const pan: Record<string, [number, number]> = {
      ArrowLeft: [80, 0], ArrowRight: [-80, 0], ArrowUp: [0, 80], ArrowDown: [0, -80],
    }
    if (pan[e.key]) {
      e.preventDefault()
      atFit.current = false
      animateTo(bound({ ...v, x: v.x + pan[e.key][0], y: v.y + pan[e.key][1] }))
    } else if (e.key === '+' || e.key === '=') {
      zoomButton(1.5)
    } else if (e.key === '-' || e.key === '_') {
      zoomButton(1 / 1.5)
    } else if (e.key === '0') {
      fit()
    }
  }

  const select = (id: PartId) => {
    if (dragged.current) return
    onSelect(id)
  }

  // Geometry that never changes: somata, dendrites, axons, sub-cells.
  const cells = useMemo(
    () =>
      layout.parts.map((p) => {
        const seed = seedOf(p.id)
        const exits = WIRES.filter((w) => w.from === p.id || w.to === p.id).map((w) => {
          const other = layout.byId[w.from === p.id ? w.to : w.from]
          return Math.atan2(other.y - p.y, other.x - p.x)
        })
        const subs = p.subs.map((s, i) => {
          const spread = Math.PI * 1.25
          const base = p.labelSide === 'left' ? Math.PI : p.labelSide === 'right' ? 0 : p.labelSide === 'above' ? -Math.PI / 2 : Math.PI / 2
          // Sub-cells fan out away from the label, so neither covers the other.
          const a = base + Math.PI - spread / 2 + (spread * (i + 0.5)) / p.subs.length
          const dist = p.r * 1.8
          const sx = Math.cos(a) * dist
          const sy = Math.sin(a) * dist
          const sr = p.r * 0.2
          return { ...s, x: sx, y: sy, r: sr, path: somaPath(sr, seedOf(s.id)), angle: a }
        })
        const dend = dendrites(p.r, seed, exits)
        const toward = { left: Math.PI, right: 0, above: -Math.PI / 2, below: Math.PI / 2 }[p.labelSide]
        // Labels sit just past whatever the dendrites reach on their side.
        const labelGap = reachToward(dend.tips, toward, p.labelSide === 'left' || p.labelSide === 'right' ? 0.75 : 0.5, p.r * 1.2) + 10
        return { part: p, soma: somaPath(p.r, seed), dendrites: dend.paths, labelGap, subs, delay: (seed % 2000) / 1000 }
      }),
    [layout],
  )
  const wires = useMemo(
    () => WIRES.map((w, i) => ({ ...w, i, ...axon(layout.byId[w.from], layout.byId[w.to], seedOf(`${w.from}>${w.to}`)) })),
    [layout],
  )

  const v = view
  const mag = v ? v.k / kFit : 1
  const subsAlpha = clamp((mag - SUBS_FROM) / (SUBS_FULL - SUBS_FROM), 0, 1)
  const labelSize = v ? (15 * Math.pow(mag, 0.22)) / v.k : 15
  const subLabelSize = v ? (11.5 * Math.pow(mag, 0.12)) / v.k : 11
  const stateOf = (id: PartId): PartState | undefined => statuses[id]?.state

  // The tissue sits deeper than the cells: it moves at half the pan and grows slower than the zoom.
  let tissueTransform = ''
  if (v) {
    const depth = 0.5
    const kb = kFit * Math.pow(mag, 0.45)
    const cw = (size.w / 2 - v.x) / v.k
    const ch = (size.h / 2 - v.y) / v.k
    const bw = FIELD_W / 2 + (cw - FIELD_W / 2) * depth
    const bh = FIELD_H / 2 + (ch - FIELD_H / 2) * depth
    const s = (kb * FIELD_W * EXT) / CW
    const tx = size.w / 2 - (bw + (FIELD_W * (EXT - 1)) / 2) * kb
    const ty = size.h / 2 - (bh + (FIELD_H * (EXT - 1)) / 2) * kb
    tissueTransform = `translate(${tx}px, ${ty}px) scale(${s})`
  }

  return (
    <div
      ref={box}
      className="field relative h-full w-full cursor-grab touch-none overflow-hidden outline-none select-none active:cursor-grabbing"
      tabIndex={0}
      role="application"
      aria-label="Her parts, as a field you can drag and zoom. Arrow keys move, plus and minus zoom, 0 fits everything."
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
    >
      <canvas
        ref={setCanvas}
        width={CW}
        height={CH}
        aria-hidden="true"
        className="pointer-events-none absolute top-0 left-0 origin-top-left"
        style={{ transform: tissueTransform, width: CW, height: CH }}
      />

      {v && (
        <svg width={size.w} height={size.h} className="absolute inset-0">
          <defs>
            {PARTS.map((p) => (
              <g key={p.id}>
                <radialGradient id={`soma-${p.id}`} cx="45%" cy="40%" r="62%">
                  <stop offset="0%" stopColor="#F4FFFE" stopOpacity="0.95" />
                  <stop offset="28%" stopColor={p.color} stopOpacity="0.95" />
                  <stop offset="100%" stopColor={p.color} stopOpacity="0.9" />
                </radialGradient>
                <radialGradient id={`dend-${p.id}`} gradientUnits="userSpaceOnUse" cx="0" cy="0" r={p.r * 2.5}>
                  <stop offset="30%" stopColor={p.color} stopOpacity="0.9" />
                  <stop offset="70%" stopColor={p.color} stopOpacity="0.45" />
                  <stop offset="100%" stopColor={p.color} stopOpacity="0" />
                </radialGradient>
                <radialGradient id={`nuc-${p.id}`}>
                  <stop offset="0%" stopColor="#040506" stopOpacity="0.42" />
                  <stop offset="65%" stopColor="#040506" stopOpacity="0.22" />
                  <stop offset="100%" stopColor="#040506" stopOpacity="0" />
                </radialGradient>
                <radialGradient id={`halo-${p.id}`}>
                  <stop offset="0%" stopColor={p.color} stopOpacity="0.5" />
                  <stop offset="45%" stopColor={p.color} stopOpacity="0.14" />
                  <stop offset="100%" stopColor={p.color} stopOpacity="0" />
                </radialGradient>
              </g>
            ))}
            {wires.map((w) => (
              <linearGradient
                key={w.i}
                id={`wire-${w.i}`}
                gradientUnits="userSpaceOnUse"
                x1={w.start[0]}
                y1={w.start[1]}
                x2={w.end[0]}
                y2={w.end[1]}
              >
                <stop offset="0%" stopColor={layout.byId[w.from].color} />
                <stop offset="100%" stopColor={layout.byId[w.to].color} />
              </linearGradient>
            ))}
            {/* Out of the focal plane, fine processes go soft. */}
            <filter id="soft" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="1.1" />
            </filter>
          </defs>

          <g transform={`translate(${v.x} ${v.y}) scale(${v.k})`}>
            <g aria-hidden="true">
              {wires.map((w) => {
                const a = stateOf(w.from)
                const b = stateOf(w.to)
                const live = (s?: PartState) => s && s !== 'down' && s !== 'off'
                const opacity = live(a) && live(b) ? 0.62 : 0.2
                return (
                  <g key={w.i} opacity={opacity}>
                    <path d={w.d} fill="none" stroke={`url(#wire-${w.i})`} strokeWidth={1.3} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                    {w.beads.map((p, j) => (
                      <circle key={j} cx={p[0]} cy={p[1]} r={(2.2 * Math.pow(mag, 0.35)) / v.k} fill={`url(#wire-${w.i})`} />
                    ))}
                  </g>
                )
              })}
            </g>

            {cells.map(({ part, soma, dendrites: dends, labelGap, subs, delay }) => (
              <Cell
                key={part.id}
                part={part}
                soma={soma}
                dendrites={dends}
                labelGap={labelGap}
                subs={subs}
                delay={delay}
                state={stateOf(part.id)}
                selected={selected === part.id}
                labelSize={labelSize}
                subLabelSize={subLabelSize}
                subsAlpha={subsAlpha}
                onSelect={select}
              />
            ))}
          </g>
        </svg>
      )}

      <div aria-hidden="true" className="field-vignette pointer-events-none absolute inset-0" />

      <div
        data-chrome
        className={`pointer-events-none absolute bottom-0 left-0 flex items-end justify-between gap-4 p-4 transition-[right] duration-300 ease-out sm:p-6 ${panelOpen ? 'right-0 sm:right-[460px]' : 'right-0'}`}
      >
        <Magnification mag={mag} />
        <div className="pointer-events-auto flex items-center gap-1.5">
          <ChromeButton label="Zoom out" onClick={() => zoomButton(1 / 1.6)} disabled={mag <= 1.001}>
            <MinusIcon />
          </ChromeButton>
          <ChromeButton label="Zoom in" onClick={() => zoomButton(1.6)} disabled={mag >= MAX_MAG - 0.001}>
            <PlusIcon />
          </ChromeButton>
          <ChromeButton label="Fit the whole field" onClick={fit}>
            <FitIcon />
          </ChromeButton>
        </div>
      </div>
    </div>
  )
}

interface SubCell {
  id: string
  label: string
  x: number
  y: number
  r: number
  path: string
  angle: number
}

function Cell({
  part, soma, dendrites: dends, labelGap, subs, delay, state, selected, labelSize, subLabelSize, subsAlpha, onSelect,
}: {
  part: PartAnatomy
  soma: string
  dendrites: string[]
  labelGap: number
  subs: SubCell[]
  delay: number
  state: PartState | undefined
  selected: boolean
  labelSize: number
  subLabelSize: number
  subsAlpha: number
  onSelect: (id: PartId) => void
}) {
  const period = state ? PERIOD[state] : undefined
  const dim = state === 'down' ? 0.3 : state === 'off' ? 0.16 : state === undefined ? 0.45 : state === 'partial' ? 0.7 : 1
  const word = state ? STATE_WORD[state] : '…'
  const anchor = part.labelSide === 'left' ? 'end' : part.labelSide === 'right' ? 'start' : part.labelAlign ?? 'middle'
  const gap = labelGap
  const lx = part.labelSide === 'left' ? -gap : part.labelSide === 'right' ? gap
    : part.labelAlign === 'start' ? -part.r * 0.9 : part.labelAlign === 'end' ? part.r * 0.9 : 0
  const ly = part.labelSide === 'above' ? -gap - labelSize * 0.6 : part.labelSide === 'below' ? gap + labelSize * 0.4 : -labelSize * 0.15
  const driftClass = `drift drift-${(part.id.length % 3) + 1}`

  return (
    <g transform={`translate(${part.x} ${part.y})`}>
      <g
        className={`cell ${driftClass}`}
        role="button"
        tabIndex={0}
        aria-label={`${part.label}: ${word}. Open details.`}
        aria-pressed={selected}
        onClick={() => onSelect(part.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onSelect(part.id)
          }
        }}
        style={{ animationDelay: `${-delay * 7}s` }}
      >
        <g opacity={dim}>
          <circle r={part.r * 2.7} fill={`url(#halo-${part.id})`} />
          {period && (
            <circle
              className="ca"
              r={part.r * 2.4}
              fill={`url(#halo-${part.id})`}
              style={{ ['--period' as string]: `${period}s`, ['--delay' as string]: `${-delay}s`, ['--peak' as string]: 0.95 }}
            />
          )}
          <g filter="url(#soft)">
            {dends.map((d, i) => (
              <path key={i} d={d} fill={`url(#dend-${part.id})`} />
            ))}
          </g>
          <path d={soma} fill={state === 'off' ? 'none' : `url(#soma-${part.id})`} stroke={part.color} strokeOpacity={0.22} strokeWidth={1} />
          <ellipse rx={part.r * 0.34} ry={part.r * 0.3} cx={part.r * 0.06} cy={part.r * 0.05} fill={`url(#nuc-${part.id})`} />
          {period && (
            <path
              className="ca"
              d={soma}
              fill="#F4FFFE"
              style={{ ['--period' as string]: `${period}s`, ['--delay' as string]: `${-delay}s`, ['--peak' as string]: 0.55 }}
            />
          )}
        </g>
        {/* Failing: the cell's own membrane burns red. Partly down: its membrane is broken. */}
        {state === 'failing' && (
          <path d={soma} fill="none" stroke={FAIL} strokeWidth={2.4} vectorEffect="non-scaling-stroke" className="fail-rim" />
        )}
        {state === 'partial' && (
          <path d={soma} fill="none" stroke={part.color} strokeWidth={1.6} strokeDasharray="10 7" vectorEffect="non-scaling-stroke" opacity={0.9} />
        )}
        {selected && <circle r={part.r * 1.42} fill="none" stroke={part.color} strokeWidth={1.2} opacity={0.9} />}
        {/* A generous invisible target, so the whole cell is easy to hit. */}
        <circle r={part.r * 1.5} fill="transparent" className="cursor-pointer" />

        <g opacity={subsAlpha} style={{ pointerEvents: 'none' }} aria-hidden={subsAlpha < 0.5}>
          {subs.map((s) => {
            const sx = Math.cos(s.angle) * part.r * 0.85
            const sy = Math.sin(s.angle) * part.r * 0.85
            const mx = (sx + s.x) / 2 + Math.sin(s.angle) * part.r * 0.18
            const my = (sy + s.y) / 2 - Math.cos(s.angle) * part.r * 0.18
            const outward = Math.cos(s.angle) >= 0
            return (
              <g key={s.id}>
                <path d={`M${sx},${sy} Q${mx},${my} ${s.x},${s.y}`} fill="none" stroke={part.color} strokeWidth={1.1} opacity={0.55 * dim} />
                <g transform={`translate(${s.x} ${s.y})`} opacity={dim}>
                  <circle r={s.r * 2.4} fill={`url(#halo-${part.id})`} />
                  <path d={s.path} fill={`url(#soma-${part.id})`} />
                </g>
                <text
                  className="cell-label"
                  x={s.x + (outward ? s.r * 1.9 : -s.r * 1.9)}
                  y={s.y + subLabelSize * 0.35}
                  textAnchor={outward ? 'start' : 'end'}
                  fontSize={subLabelSize}
                  fill="#C9D1D8"
                >
                  {s.label}
                </text>
              </g>
            )
          })}
        </g>

        <text className="cell-label" x={lx} y={ly} textAnchor={anchor} fontSize={labelSize} fill="#E9EDF0" fontWeight={selected ? 650 : 560}>
          {part.label}
        </text>
        <text
          className="cell-label"
          x={lx}
          y={ly + labelSize * 1.15}
          textAnchor={anchor}
          fontSize={labelSize * 0.72}
          fill={state === 'failing' ? FAIL : state === 'down' || state === 'off' ? '#8C97A3' : part.color}
        >
          {word}
        </text>
      </g>
    </g>
  )
}

/** The magnification readout over a gauge from ×1 (everything) to the closest look. */
function Magnification({ mag }: { mag: number }) {
  const t = Math.log(mag) / Math.log(MAX_MAG)
  return (
    <div className="flex items-center gap-3 text-[#AEB7BF]" aria-live="polite">
      <span className="w-12 text-[13px] font-medium tabular-nums text-[#E9EDF0]">×{mag.toFixed(1)}</span>
      <span className="relative block h-px w-28 bg-[#2A3138] sm:w-40" aria-hidden="true">
        {[1, 2, 4, 6].map((m) => (
          <span
            key={m}
            className="absolute top-0 h-[5px] w-px bg-[#4A545D]"
            style={{ left: `${(Math.log(m) / Math.log(MAX_MAG)) * 100}%` }}
          />
        ))}
        <span className="absolute top-0 left-0 h-px bg-[#E9EDF0]" style={{ width: `${clamp(t, 0, 1) * 100}%` }} />
        <span className="absolute -top-[4px] h-[9px] w-px bg-[#E9EDF0]" style={{ left: `${clamp(t, 0, 1) * 100}%` }} />
      </span>
      <span className="hidden text-[12px] sm:inline">{mag < SUBS_FROM ? 'zoom in to see inside each part' : 'inside view'}</span>
    </div>
  )
}

function ChromeButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="flex h-9 w-9 items-center justify-center rounded-full border border-[#2A3138] bg-[#0A0D10]/90 text-[#DDE3E8] transition-colors duration-150 hover:border-[#5A6670] hover:text-white disabled:cursor-default disabled:opacity-35 disabled:hover:border-[#2A3138]"
    >
      {children}
    </button>
  )
}
