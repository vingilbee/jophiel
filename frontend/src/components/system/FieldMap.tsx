import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import type { PartId, PartState, PartStatus, Signal } from '../../types/system'
import { MAX_MAG, PARTS, SUBS_FROM, SUBS_FULL, WIRES, layoutFor } from './anatomy'
import type { PartAnatomy } from './anatomy'
import { axon, filaments, reachToward, rng, seedOf } from './shapes'
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

/** How often a core charges and discharges in each state, in seconds; none when it isn't running. */
const PERIOD: Partial<Record<PartState, number>> = { alive: 2.8, busy: 1.05, partial: 4.4, failing: 1.6 }
/** How long a pulse takes to run a fibre, in milliseconds. */
const PULSE_MS = 760
const FAIL = '#FF4136'

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t))

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** The star field behind her: white points of many sizes, painted once and moved with parallax. */
function useStars(canvas: HTMLCanvasElement | null, W: number, H: number) {
  useEffect(() => {
    if (!canvas || !W || !H) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(H * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const rand = rng(11)
    ctx.clearRect(0, 0, W, H)
    const count = Math.round((W * H) / 1900)
    for (let i = 0; i < count; i++) {
      const x = rand() * W
      const y = rand() * H
      // Most stars are faint pinpoints; a few are bright, with a soft glow of their own.
      const m = Math.pow(rand(), 3.2)
      const r = 0.35 + m * 1.6
      const alpha = 0.18 + m * 0.75
      const tint = rand()
      const color = tint < 0.12 ? '200, 220, 255' : tint < 0.18 ? '255, 236, 214' : '255, 255, 255'
      if (m > 0.55) {
        const g = ctx.createRadialGradient(x, y, 0, x, y, r * 3.5)
        g.addColorStop(0, `rgba(${color}, ${alpha * 0.25})`)
        g.addColorStop(1, `rgba(${color}, 0)`)
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.arc(x, y, r * 3.5, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.fillStyle = `rgba(${color}, ${alpha})`
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
    }
  }, [canvas, W, H])
}

/** A few stars that twinkle, placed in the star canvas's own pixels. */
const TWINKLES = (() => {
  const rand = rng(23)
  return Array.from({ length: 34 }, () => ({ x: rand(), y: rand(), d: rand() * 6, p: 3 + rand() * 5, s: 1.2 + rand() * 1.6 }))
})()

interface Props {
  statuses: Partial<Record<PartId, PartStatus>>
  selected: PartId | null
  onSelect: (id: PartId) => void
  /** The details panel is open beside the field (wide screens): keep the controls clear of it. */
  panelOpen?: boolean
  /** Hand-offs to draw as pulses; each id fires once. */
  signals?: Signal[]
}

export function FieldMap({ statuses, selected, onSelect, panelOpen = false, signals = [] }: Props) {
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
  // The star canvas covers the field and a wide margin around it at the fitted scale, so its edge never shows.
  const EXT = 2.6

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
  const CW = Math.round(FIELD_W * EXT * kFit)
  const CH = Math.round(FIELD_H * EXT * kFit)
  useStars(canvas, CW, CH)

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
    const headerBottom = 150
    const sheetTop = size.h * 0.32
    animateTo({ k: v.k, x: size.w / 2 - p.x * v.k, y: (headerBottom + sheetTop) / 2 + 20 - p.y * v.k })
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
        animateTo(bound({ k: target.k, x: size.w / 2 - p.x * target.k, y: size.h * 0.44 - p.y * target.k }))
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

  // Geometry that never changes: cores, filaments, fibres, sub-cores.
  const cells = useMemo(
    () =>
      layout.parts.map((p) => {
        const seed = seedOf(p.id)
        const exits = WIRES.filter((w) => w.from === p.id || w.to === p.id).map((w) => {
          const other = layout.byId[w.from === p.id ? w.to : w.from]
          return Math.atan2(other.y - p.y, other.x - p.x)
        })
        const base = p.labelSide === 'left' ? Math.PI : p.labelSide === 'right' ? 0 : p.labelSide === 'above' ? -Math.PI / 2 : Math.PI / 2
        const subs = p.subs.map((s, i) => {
          // Sub-cores sit inside the ring, fanned away from the label so their own labels stay clear of it.
          const spread = Math.PI * 1.3
          const a = base + Math.PI - spread / 2 + (spread * (i + 0.5)) / p.subs.length
          return { ...s, angle: a, x: Math.cos(a) * p.r * 0.56, y: Math.sin(a) * p.r * 0.56, r: p.r * 0.085 }
        })
        const fil = filaments(p.r, seed, exits)
        const toward = { left: Math.PI, right: 0, above: -Math.PI / 2, below: Math.PI / 2 }[p.labelSide]
        // Labels sit just past whatever the filaments reach on their side.
        const labelGap = reachToward(fil.tips, toward, p.labelSide === 'left' || p.labelSide === 'right' ? 0.75 : 0.5, p.r * 1.15) + 10
        return { part: p, filaments: fil.paths, labelGap, subs, delay: (seed % 2000) / 1000 }
      }),
    [layout],
  )
  const wires = useMemo(
    () => WIRES.map((w, i) => ({ ...w, i, ...axon(layout.byId[w.from], layout.byId[w.to], seedOf(`${w.from}>${w.to}`)) })),
    [layout],
  )

  // Pulses: each new signal runs its fibre as a bright spike with a trailing glow, and the core it reaches flashes.
  const wireEls = useRef(new Map<string, SVGPathElement>())
  const pulseLayer = useRef<SVGGElement>(null)
  const fired = useRef(new Set<string>())
  const [hits, setHits] = useState<Partial<Record<PartId, number>>>({})
  useEffect(() => {
    const arrive = (to: PartId) => setHits((h) => ({ ...h, [to]: (h[to] ?? 0) + 1 }))
    for (const sig of signals) {
      if (fired.current.has(sig.id)) continue
      fired.current.add(sig.id)
      const path = wireEls.current.get(`${sig.from}>${sig.to}`)
      const layer = pulseLayer.current
      const k = viewRef.current?.k ?? 1
      if (!path || !layer || prefersReducedMotion()) {
        arrive(sig.to)
        continue
      }
      const L = path.getTotalLength()
      const head = Math.min(L * 0.05, 22)
      const make = (width: number, dash: number, color: string, opacity: number) => {
        const el = document.createElementNS('http://www.w3.org/2000/svg', 'path')
        el.setAttribute('d', path.getAttribute('d') ?? '')
        el.setAttribute('fill', 'none')
        el.setAttribute('stroke', color)
        el.setAttribute('stroke-width', String(width / k))
        el.setAttribute('stroke-linecap', 'round')
        el.setAttribute('stroke-opacity', String(opacity))
        el.setAttribute('stroke-dasharray', `${dash} ${L + dash}`)
        layer.appendChild(el)
        const run = el.animate([{ strokeDashoffset: dash }, { strokeDashoffset: -L }],
          { duration: PULSE_MS, easing: 'cubic-bezier(0.45, 0, 0.25, 1)' })
        return { el, run }
      }
      const color = PARTS.find((p) => p.id === sig.from)?.color ?? '#FFFFFF'
      // The fibre conducts: it brightens along its length while the spike runs.
      const glow = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      glow.setAttribute('d', path.getAttribute('d') ?? '')
      glow.setAttribute('fill', 'none')
      glow.setAttribute('stroke', color)
      glow.setAttribute('stroke-width', String(1.6 / k))
      layer.appendChild(glow)
      glow.animate([{ strokeOpacity: 0 }, { strokeOpacity: 0.55, offset: 0.35 }, { strokeOpacity: 0 }], { duration: PULSE_MS + 200 })
      // The spike tapers into a fading tail: three dashes, each longer, thinner and dimmer than the last.
      const parts = [make(1.2, head * 7, color, 0.25), make(1.8, head * 3, '#F4FFFE', 0.5), make(2.6, head, '#FFFFFF', 1)]
      parts[2].run.onfinish = () => {
        parts.forEach((p) => p.el.remove())
        glow.remove()
        arrive(sig.to)
      }
    }
    if (fired.current.size > 2000) fired.current = new Set(signals.map((s) => s.id))
  }, [signals])

  const v = view
  const mag = v ? v.k / kFit : 1
  const subsAlpha = clamp((mag - SUBS_FROM) / (SUBS_FULL - SUBS_FROM), 0, 1)
  const labelSize = v ? (15 * Math.pow(mag, 0.22)) / v.k : 15
  const subLabelSize = v ? (11.5 * Math.pow(mag, 0.12)) / v.k : 11
  const stateOf = (id: PartId): PartState | undefined => statuses[id]?.state

  // The stars sit deeper than the cells: they move at half the pan and grow slower than the zoom.
  let tissueTransform = ''
  if (v) {
    // Stars are at infinity: they never grow with the zoom, they only drift by at half the pan.
    const depth = 0.5
    const cw = (size.w / 2 - v.x) / v.k
    const ch = (size.h / 2 - v.y) / v.k
    const bw = FIELD_W / 2 + (cw - FIELD_W / 2) * depth
    const bh = FIELD_H / 2 + (ch - FIELD_H / 2) * depth
    const tx = size.w / 2 - (bw + (FIELD_W * (EXT - 1)) / 2) * kFit
    const ty = size.h / 2 - (bh + (FIELD_H * (EXT - 1)) / 2) * kFit
    tissueTransform = `translate(${tx}px, ${ty}px)`
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
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-0 left-0 origin-top-left"
        style={{ transform: tissueTransform, width: CW, height: CH }}
      >
        <canvas ref={setCanvas} className="absolute inset-0" style={{ width: CW, height: CH }} />
        {TWINKLES.map((t, i) => (
          <span
            key={i}
            className="twinkle absolute rounded-full bg-white"
            style={{
              left: t.x * CW, top: t.y * CH, width: t.s, height: t.s,
              ['--period' as string]: `${t.p}s`, ['--delay' as string]: `${-t.d}s`,
            }}
          />
        ))}
      </div>

      {v && (
        <svg width={size.w} height={size.h} className="absolute inset-0">
          <defs>
            {PARTS.map((p) => (
              <g key={p.id}>
                <radialGradient id={`core-${p.id}`}>
                  <stop offset="0%" stopColor="#FFFFFF" />
                  <stop offset="38%" stopColor="#F4FFFE" />
                  <stop offset="62%" stopColor={p.color} />
                  <stop offset="100%" stopColor={p.color} stopOpacity="0.85" />
                </radialGradient>
                <radialGradient id={`halo-${p.id}`}>
                  <stop offset="0%" stopColor={p.color} stopOpacity="0.55" />
                  <stop offset="40%" stopColor={p.color} stopOpacity="0.16" />
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
          </defs>

          <g transform={`translate(${v.x} ${v.y}) scale(${v.k})`}>
            <g aria-hidden="true">
              {wires.map((w) => {
                const a = stateOf(w.from)
                const b = stateOf(w.to)
                const live = (s?: PartState) => s && s !== 'down' && s !== 'off'
                const opacity = live(a) && live(b) ? 0.7 : 0.18
                // A conducting fibre: a fine bright core line inside a faint glow.
                return (
                  <g key={w.i} opacity={opacity}>
                    <path d={w.d} fill="none" stroke={`url(#wire-${w.i})`} strokeWidth={6} strokeOpacity={0.12} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                    <path
                      ref={(el) => {
                        if (el) wireEls.current.set(`${w.from}>${w.to}`, el)
                        else wireEls.current.delete(`${w.from}>${w.to}`)
                      }}
                      d={w.d}
                      fill="none"
                      stroke={`url(#wire-${w.i})`}
                      strokeWidth={1.1}
                      strokeLinecap="round"
                      vectorEffect="non-scaling-stroke"
                    />
                  </g>
                )
              })}
            </g>

            <g ref={pulseLayer} aria-hidden="true" style={{ pointerEvents: 'none' }} />

            {cells.map(({ part, filaments: fils, labelGap, subs, delay }) => (
              <Cell
                key={part.id}
                part={part}
                filaments={fils}
                hits={hits[part.id] ?? 0}
                labelGap={labelGap}
                subs={subs}
                delay={delay}
                state={stateOf(part.id)}
                selected={selected === part.id}
                labelSize={labelSize}
                subLabelSize={subLabelSize}
                subsAlpha={subsAlpha}
                magnification={mag}
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

interface SubCore {
  id: string
  label: string
  x: number
  y: number
  r: number
  angle: number
}

function Cell({
  part, filaments: fils, hits, labelGap, subs, delay, state, selected, labelSize, subLabelSize, subsAlpha, magnification, onSelect,
}: {
  part: PartAnatomy
  filaments: string[]
  hits: number
  labelGap: number
  subs: SubCore[]
  delay: number
  state: PartState | undefined
  selected: boolean
  labelSize: number
  subLabelSize: number
  subsAlpha: number
  magnification: number
  onSelect: (id: PartId) => void
}) {
  const period = state ? PERIOD[state] : undefined
  const dim = state === 'down' ? 0.28 : state === 'off' ? 0.14 : state === undefined ? 0.45 : state === 'partial' ? 0.72 : 1
  const word = state ? STATE_WORD[state] : '…'
  const anchor = part.labelSide === 'left' ? 'end' : part.labelSide === 'right' ? 'start' : part.labelAlign ?? 'middle'
  const gap = labelGap
  const lx = part.labelSide === 'left' ? -gap : part.labelSide === 'right' ? gap
    : part.labelAlign === 'start' ? -part.r * 0.9 : part.labelAlign === 'end' ? part.r * 0.9 : 0
  const ly = part.labelSide === 'above' ? -gap - labelSize * 0.6 : part.labelSide === 'below' ? gap + labelSize * 0.4 : -labelSize * 0.15
  const driftClass = `drift drift-${(part.id.length % 3) + 1}`
  const ringR = part.r * 0.8
  // The core makes room for its sub-cores as they resolve.
  const coreR = part.r * (0.36 - 0.14 * subsAlpha)
  const ringColor = state === 'failing' ? FAIL : part.color
  const pace = { ['--period' as string]: `${period ?? 3}s`, ['--delay' as string]: `${-delay}s` }
  // Up close the filaments draw in toward the ring instead of growing into long zigzags.
  const filamentScale = Math.min(1, Math.pow(2 / Math.max(2, magnification), 0.45))

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
          <circle r={part.r * 2.2} fill={`url(#halo-${part.id})`} />
          {/* Discharge: the glow flares as the ring releases its charge. */}
          {period && <circle className="ca" r={part.r * 2} fill={`url(#halo-${part.id})`} style={{ ...pace, ['--peak' as string]: 1 }} />}
          {/* An arriving pulse flashes the core (one flash per pulse: the key restarts it). */}
          {hits > 0 && <circle key={hits} className="hit" r={part.r * 1.9} fill={`url(#halo-${part.id})`} />}
          <g
            className={state && state !== 'down' && state !== 'off' ? 'flicker' : undefined}
            style={pace}
            transform={`scale(${filamentScale})`}
          >
            {fils.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={part.color} strokeWidth={1.1} strokeOpacity={0.75} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            ))}
          </g>
          {/* The charged ring: it fills with light, then lets it go. Broken when partly down, red when failing. */}
          <circle r={ringR} fill="none" stroke={ringColor} strokeWidth={1.2} strokeOpacity={0.3} vectorEffect="non-scaling-stroke" />
          <circle
            r={ringR}
            fill="none"
            stroke={ringColor}
            strokeWidth={state === 'failing' ? 2.6 : 2}
            strokeDasharray={state === 'partial' ? `${ringR * 0.5} ${ringR * 0.32}` : state === 'off' ? `${ringR * 0.12} ${ringR * 0.2}` : undefined}
            vectorEffect="non-scaling-stroke"
            className={period ? (state === 'failing' ? 'fail-rim' : 'charge') : undefined}
            style={period ? pace : undefined}
            opacity={period ? undefined : 0.35}
          />
          <circle r={coreR * 1.9} fill={`url(#halo-${part.id})`} />
          {state !== 'off' && <circle r={coreR} fill={`url(#core-${part.id})`} />}
          {period && <circle className="ca" r={coreR * 1.15} fill="#FFFFFF" style={{ ...pace, ['--peak' as string]: 0.7 }} />}
        </g>
        {selected && <circle r={part.r * 1.22} fill="none" stroke={part.color} strokeWidth={1} opacity={0.85} vectorEffect="non-scaling-stroke" />}
        {/* A generous invisible target, so the whole node is easy to hit. */}
        <circle r={part.r * 1.4} fill="transparent" className="cursor-pointer" />

        <g opacity={subsAlpha} style={{ pointerEvents: 'none' }} aria-hidden={subsAlpha < 0.5}>
          {subs.map((s) => {
            const outward = Math.cos(s.angle) >= 0
            // Each sub-core's label sits outside the ring on its own bearing.
            const lr = ringR + part.r * 0.18
            return (
              <g key={s.id}>
                <line x1={0} y1={0} x2={s.x} y2={s.y} stroke={part.color} strokeWidth={1} strokeOpacity={0.6 * dim} vectorEffect="non-scaling-stroke" />
                <g transform={`translate(${s.x} ${s.y})`} opacity={dim}>
                  <circle r={s.r * 2.6} fill={`url(#halo-${part.id})`} />
                  <circle r={s.r} fill={`url(#core-${part.id})`} />
                </g>
                <line
                  x1={s.x + Math.cos(s.angle) * s.r * 1.6}
                  y1={s.y + Math.sin(s.angle) * s.r * 1.6}
                  x2={Math.cos(s.angle) * lr}
                  y2={Math.sin(s.angle) * lr}
                  stroke="#C9D1D8"
                  strokeOpacity={0.35}
                  strokeWidth={0.8}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  className="cell-label"
                  x={Math.cos(s.angle) * (lr + 3)}
                  y={Math.sin(s.angle) * (lr + 3) + subLabelSize * 0.35}
                  textAnchor={Math.abs(Math.cos(s.angle)) < 0.25 ? 'middle' : outward ? 'start' : 'end'}
                  fontSize={subLabelSize}
                  fill="#D5DBE0"
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
