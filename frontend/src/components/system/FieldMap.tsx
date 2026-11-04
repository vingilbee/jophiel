import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import type { PartId, PartState, PartStatus, Signal } from '../../types/system'
import { MAX_MAG, PARTS, SUBS_FROM, SUBS_FULL, WIRES, layoutFor } from './anatomy'
import type { PartAnatomy } from './anatomy'
import { axon, rng, seedOf } from './shapes'
import { useElectricity } from './useElectricity'
import { FitIcon, MinusIcon, PlusIcon } from './icons'
import { FAIL, PERIOD, STATE_WORD } from './nodeStates'

interface View {
  x: number
  y: number
  k: number
}

/** How long a pulse takes to run a fibre, in milliseconds. */
const PULSE_MS = 760

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t))
const easeInCubic = (t: number) => t * t * t
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)

/** How long the jump into a part takes, and the way back out, in milliseconds. */
const DIVE_MS = 900
const SURFACE_MS = 800
/** How far the camera flies in, as a multiple of the fitted zoom: the core fills the screen. */
const DIVE_ZOOM = 22

/** One star of the sky, in the star canvas's own pixels. */
interface Star {
  x: number
  y: number
  r: number
  alpha: number
  color: string
}

/** The jump to lightspeed, made of the sky itself: every star on screen stretches into a streak pulling away
 * from a vanishing point (the node), longer and brighter as the warp builds. "in" stretches the stars from
 * their places; "out" starts stretched and lets them settle back into the same dots. ``offset`` is where the
 * star canvas sits on screen. Resolves when done (the canvas is left clear). */
function runWarp(canvas: HTMLCanvasElement, w: number, h: number, sky: Star[], offset: [number, number],
  from: [number, number], to: [number, number], dir: 'in' | 'out', ms: number): Promise<void> {
  return new Promise((resolve) => {
    const ctx = canvas.getContext('2d')
    if (!ctx) return resolve()
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    // Only the stars you can see (and a margin) take part.
    const stars = sky
      .map((st) => ({ ...st, x: st.x + offset[0], y: st.y + offset[1] }))
      .filter((st) => st.x > -40 && st.x < w + 40 && st.y > -40 && st.y < h + 40)
    const reach = Math.hypot(w, h)
    const t0 = performance.now()
    const frame = (now: number) => {
      const t = Math.min(1, (now - t0) / ms)
      const e = dir === 'in' ? easeInCubic(t) : 1 - easeOutCubic(t)
      // The vanishing point follows the node as the camera brings it to the middle (or back to its place).
      const m = dir === 'in' ? easeOutCubic(t) : t
      const cx = from[0] + (to[0] - from[0]) * m
      const cy = from[1] + (to[1] - from[1]) * m
      ctx.clearRect(0, 0, w, h)
      ctx.lineCap = 'round'
      for (const st of stars) {
        const vx = st.x - cx
        const vy = st.y - cy
        // Farther stars streak longer, as they would rushing past.
        const far = Math.hypot(vx, vy) / reach
        const head = 1 + e * (0.25 + far * 2.6)
        const tail = 1 + e * 0.06
        const x1 = cx + vx * tail
        const y1 = cy + vy * tail
        const x2 = cx + vx * head
        const y2 = cy + vy * head
        ctx.globalAlpha = Math.min(1, st.alpha + (1 - st.alpha) * e * 0.85)
        if (Math.hypot(x2 - x1, y2 - y1) < 0.6) {
          ctx.fillStyle = `rgb(${st.color})`
          ctx.beginPath()
          ctx.arc(x1, y1, st.r, 0, Math.PI * 2)
          ctx.fill()
        } else {
          ctx.strokeStyle = `rgb(${st.color})`
          ctx.lineWidth = st.r * 2 * (1 + e * 0.5)
          ctx.beginPath()
          ctx.moveTo(x1, y1)
          ctx.lineTo(x2, y2)
          ctx.stroke()
        }
      }
      if (t < 1) requestAnimationFrame(frame)
      else {
        ctx.clearRect(0, 0, w, h)
        resolve()
      }
    }
    requestAnimationFrame(frame)
  })
}

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** The star field behind her: white points of many sizes, painted once and moved with parallax. */
function useStars(canvas: HTMLCanvasElement | null, W: number, H: number, sky: { current: Star[] }) {
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
    const stars: Star[] = []
    for (let i = 0; i < count; i++) {
      const x = rand() * W
      const y = rand() * H
      // Most stars are faint pinpoints; a few are bright, with a soft glow of their own.
      const m = Math.pow(rand(), 3.2)
      const r = 0.35 + m * 1.6
      const alpha = 0.18 + m * 0.75
      const tint = rand()
      // About a third of the stars carry one of her parts' colours, so the sky belongs to the same system.
      const color = tint < 0.34 ? STAR_TINTS[Math.floor(rand() * STAR_TINTS.length)] : tint < 0.42 ? '200, 220, 255' : '255, 255, 255'
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
      stars.push({ x, y, r, alpha, color })
    }
    sky.current = stars
  }, [canvas, W, H, sky])
}

/** The parts' colours as rgb triples, for tinted stars. */
const STAR_TINTS = PARTS.map((p) => {
  const n = parseInt(p.color.slice(1), 16)
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`
})

/** A few stars that twinkle, placed in the star canvas's own pixels. */
const TWINKLES = (() => {
  const rand = rng(23)
  return Array.from({ length: 34 }, () => ({ x: rand(), y: rand(), d: rand() * 6, p: 3 + rand() * 5, s: 1.2 + rand() * 1.6 }))
})()

interface Props {
  statuses: Partial<Record<PartId, PartStatus>>
  selected: PartId | null
  onSelect: (id: PartId) => void
  /** Hand-offs to draw as pulses; each id fires once. */
  signals?: Signal[]
  /** The jump into a part has begun (the page's own text should step aside). */
  onDive?: () => void
}

export function FieldMap({ statuses, selected, onSelect, signals = [], onDive }: Props) {
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
  const sky = useRef<Star[]>([])
  useStars(canvas, CW, CH, sky)

  const fitView = useCallback(
    (): View => ({
      k: kFit,
      x: (size.w - content.w * kFit) / 2 - content.x0 * kFit,
      y: padTop + (size.h - padTop - padBottom - content.h * kFit) / 2 - content.y0 * kFit,
    }),
    [kFit, size.w, size.h, content, padTop],
  )

  /** Where the star canvas sits on screen for a view. Stars are at infinity: they never grow with the zoom,
   * they only drift by at half the pan. */
  const starOffset = useCallback(
    (v: View): [number, number] => {
      const depth = 0.5
      const cw = (size.w / 2 - v.x) / v.k
      const ch = (size.h / 2 - v.y) / v.k
      const bw = FIELD_W / 2 + (cw - FIELD_W / 2) * depth
      const bh = FIELD_H / 2 + (ch - FIELD_H / 2) * depth
      return [size.w / 2 - (bw + (FIELD_W * (EXT - 1)) / 2) * kFit, size.h / 2 - (bh + (FIELD_H * (EXT - 1)) / 2) * kFit]
    },
    [size.w, size.h, FIELD_W, FIELD_H, kFit],
  )
  // While the sky warps it is drawn on the warp canvas, so the painted one steps aside.
  const [warping, setWarping] = useState(false)

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
    (target: View, ms = 420, ease: (t: number) => number = easeOutExpo, focus?: { x: number; y: number }) => {
      if (anim.current) cancelAnimationFrame(anim.current)
      const start = viewRef.current ?? target
      if (prefersReducedMotion()) {
        commit(target)
        return
      }
      // With a focus (a point in the field), that point glides straight between where it is on screen at the
      // start and at the end while the zoom changes, so the camera flies into or out of it, never past others.
      const f = focus ?? { x: 0, y: 0 }
      const s0 = { x: start.x + f.x * start.k, y: start.y + f.y * start.k }
      const s1 = { x: target.x + f.x * target.k, y: target.y + f.y * target.k }
      const t0 = performance.now()
      const step = (now: number) => {
        const t = Math.min(1, (now - t0) / ms)
        const e = ease(t)
        // Interpolate zoom in log space so it feels even at every magnification.
        const k = Math.exp(Math.log(start.k) + (Math.log(target.k) - Math.log(start.k)) * e)
        if (focus) {
          const sx = s0.x + (s1.x - s0.x) * e
          const sy = s0.y + (s1.y - s0.y) * e
          commit({ k, x: sx - f.x * k, y: sy - f.y * k })
        } else {
          commit({ k, x: start.x + (target.x - start.x) * e, y: start.y + (target.y - start.y) * e })
        }
        anim.current = t < 1 ? requestAnimationFrame(step) : null
      }
      anim.current = requestAnimationFrame(step)
    },
    [commit],
  )

  // The jump into a part and back out. The camera flies into the node, the stars streak out from it, and its
  // light floods the screen; then the part's own page opens over it. Closing the page plays it in reverse.
  const warp = useRef<HTMLCanvasElement>(null)
  const saved = useRef<View | null>(null)
  const diving = useRef(false)
  const [flare, setFlare] = useState<{ x: number; y: number; color: string; phase: 'in' | 'held' | 'out' } | null>(null)
  const divedView = useCallback(
    (id: PartId): View => {
      const p = layout.byId[id]
      const k = kFit * DIVE_ZOOM
      return { k, x: size.w / 2 - p.x * k, y: size.h / 2 - p.y * k }
    },
    [layout, kFit, size.w, size.h],
  )

  const dive = (id: PartId) => {
    const v = viewRef.current
    if (!v || diving.current) return
    onDive?.()
    if (prefersReducedMotion()) {
      saved.current = v
      commit(divedView(id))
      onSelect(id)
      return
    }
    diving.current = true
    saved.current = v
    const p = layout.byId[id]
    const from: [number, number] = [v.x + p.x * v.k, v.y + p.y * v.k]
    animateTo(divedView(id), DIVE_MS, easeInCubic, { x: p.x, y: p.y })
    if (warp.current) {
      setWarping(true)
      void runWarp(warp.current, size.w, size.h, sky.current, starOffset(v), from, [size.w / 2, size.h / 2], 'in', DIVE_MS)
        .then(() => setWarping(false))
    }
    setFlare({ x: size.w / 2, y: size.h / 2, color: p.color, phase: 'in' })
    window.setTimeout(() => {
      diving.current = false
      onSelect(id)
    }, DIVE_MS)
  }

  const previous = useRef<PartId | null>(null)
  const hasView = view !== null
  // What the effect below needs, read fresh; it runs only when the open part changes (or the field first appears).
  const latest = useRef({ divedView, fitView, commit, animateTo, layout, size, starOffset })
  useEffect(() => {
    latest.current = { divedView, fitView, commit, animateTo, layout, size, starOffset }
  })
  useEffect(() => {
    const { divedView, fitView, commit, animateTo, layout, size, starOffset } = latest.current
    const before = previous.current
    previous.current = selected
    if (!viewRef.current) return
    if (selected && !before && !diving.current && !saved.current) {
      // Opened straight onto a part (a link, or forward): be inside it already, so going back still surfaces.
      saved.current = viewRef.current
      commit(divedView(selected))
      setFlare({ x: size.w / 2, y: size.h / 2, color: layout.byId[selected].color, phase: 'held' })
    } else if (selected && !before) {
      setFlare((f) => (f ? { ...f, phase: 'held' } : f))
    } else if (!selected && before) {
      // Back out: the light falls away, the streaks shrink back into stars, the camera returns.
      const back = saved.current ?? fitView()
      saved.current = null
      const p = layout.byId[before]
      setFlare({ x: size.w / 2, y: size.h / 2, color: p.color, phase: 'out' })
      animateTo(back, SURFACE_MS, easeOutCubic, { x: p.x, y: p.y })
      if (warp.current && !prefersReducedMotion()) {
        // The stars settle into the places they'll have once the camera is back.
        setWarping(true)
        void runWarp(warp.current, size.w, size.h, sky.current, starOffset(back), [size.w / 2, size.h / 2],
          [back.x + p.x * back.k, back.y + p.y * back.k], 'out', SURFACE_MS).then(() => setWarping(false))
      }
      window.setTimeout(() => setFlare(null), SURFACE_MS)
    }
  }, [selected, hasView])

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
    dive(id)
  }

  // Geometry that never changes: cores, fibres, sub-cores.
  const cells = useMemo(
    () =>
      layout.parts.map((p) => {
        const seed = seedOf(p.id)
        const base = p.labelSide === 'left' ? Math.PI : p.labelSide === 'right' ? 0 : p.labelSide === 'above' ? -Math.PI / 2 : Math.PI / 2
        const subs = p.subs.map((s, i) => {
          // Sub-cores orbit inside the ring, evenly spaced, starting fanned away from the label.
          const a = base + Math.PI + (Math.PI * 2 * i) / p.subs.length
          return { ...s, angle: a, x: Math.cos(a) * p.r * 0.56, y: Math.sin(a) * p.r * 0.56, r: p.r * 0.085 }
        })
        // Labels sit past where the arcs usually reach (a busy node's longest arcs may cross them, briefly).
        const labelGap = p.r * 1.5 + 8
        return { part: p, labelGap, subs, delay: (seed % 2000) / 1000 }
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

  const tissueTransform = view ? (() => {
    const [tx, ty] = starOffset(view)
    return `translate(${tx}px, ${ty}px)`
  })() : ''

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
        style={{ transform: tissueTransform, width: CW, height: CH, opacity: warping ? 0 : 1 }}
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
        <svg
          width={size.w}
          height={size.h}
          className="absolute inset-0 transition-opacity duration-300"
          style={{ opacity: selected ? 0 : 1 }}
          aria-hidden={selected !== null}
        >
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

            {cells.map(({ part, labelGap, subs, delay }) => (
              <Cell
                key={part.id}
                part={part}
                paused={selected !== null}
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
      <canvas ref={warp} aria-hidden="true" className="pointer-events-none absolute inset-0" style={{ width: size.w, height: size.h }} />
      {flare && (
        <div
          aria-hidden="true"
          className={`flare flare-${flare.phase} pointer-events-none absolute rounded-full`}
          style={{
            left: flare.x, top: flare.y, width: Math.hypot(size.w, size.h) * 2.2, height: Math.hypot(size.w, size.h) * 2.2,
            background: `radial-gradient(circle, #FFFFFF 0%, ${flare.color} 16%, ${flare.color}55 34%, transparent 62%)`,
          }}
        />
      )}

      <div
        data-chrome
        className={`pointer-events-none absolute right-0 bottom-0 left-0 flex items-end justify-between gap-4 p-4 transition-opacity duration-300 sm:p-6 ${selected || flare ? 'opacity-0' : 'opacity-100'}`}
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
  part, paused, hits, labelGap, subs, delay, state, selected, labelSize, subLabelSize, subsAlpha, magnification, onSelect,
}: {
  part: PartAnatomy
  paused: boolean
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
  // Lines thicken a little as you zoom in, so the arcs and ring keep their proportions to the node.
  const line = Math.pow(magnification, 0.5)
  const { arcs, bursts } = useElectricity({ state, color: part.color, r: part.r, ringR, line, delay, hits, paused })
  const still = prefersReducedMotion()
  // The orbit: one slow turn for all of a node's sub-cores together, so they never pass each other.
  const ORBIT = `${60 + (part.r % 7) * 4}s`

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
          {/* Living arcs: a few discharges re-forming around the ring every tick (useElectricity). */}
          <g ref={arcs} />
          {/* The charged ring: it fills with light, then lets it go. Broken when partly down, red when failing. */}
          <circle r={ringR} fill="none" stroke={ringColor} strokeWidth={1.2 * line} strokeOpacity={0.3} vectorEffect="non-scaling-stroke" />
          <circle
            r={ringR}
            fill="none"
            stroke={ringColor}
            strokeWidth={(state === 'failing' ? 2.6 : 2) * line}
            strokeDasharray={state === 'partial' ? `${ringR * 0.5} ${ringR * 0.32}` : state === 'off' ? `${ringR * 0.12} ${ringR * 0.2}` : undefined}
            vectorEffect="non-scaling-stroke"
            className={period ? (state === 'failing' ? 'fail-rim' : 'charge') : undefined}
            style={period ? pace : undefined}
            opacity={period ? undefined : 0.35}
          />
          <circle r={coreR * 1.9} fill={`url(#halo-${part.id})`} />
          {state !== 'off' && <circle r={coreR} fill={`url(#core-${part.id})`} />}
          {period && <circle className="ca" r={coreR * 1.15} fill="#FFFFFF" style={{ ...pace, ['--peak' as string]: 0.7 }} />}
          {/* Bursts: bolts and flying sparks on each discharge and each arriving pulse. */}
          <g ref={bursts} />
        </g>
        {selected && <circle r={part.r * 1.22} fill="none" stroke={part.color} strokeWidth={1} opacity={0.85} vectorEffect="non-scaling-stroke" />}
        {/* A generous invisible target, so the whole node is easy to hit. */}
        <circle r={part.r * 1.4} fill="transparent" className="cursor-pointer" />

        <g opacity={subsAlpha} style={{ pointerEvents: 'none' }} aria-hidden={subsAlpha < 0.5}>
          <g>
            {!still && <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur={ORBIT} repeatCount="indefinite" />}
            {subs.map((s, i) => {
              // Each label rides its sub-core's bearing just outside the ring, turned back so it stays upright.
              const half = s.label.length * subLabelSize * 0.27
              const lr = ringR + part.r * 0.12 + half
              const lx = Math.cos(s.angle) * lr
              const ly = Math.sin(s.angle) * lr
              return (
                <g key={s.id}>
                  <g transform={`translate(${s.x} ${s.y})`} opacity={dim}>
                    <g className={still ? undefined : `drift-${(i % 3) + 1}`}>
                      <circle r={s.r * 2.6} fill={`url(#halo-${part.id})`} />
                      <circle r={s.r} fill={`url(#core-${part.id})`} />
                    </g>
                  </g>
                  <g>
                    {!still && (
                      <animateTransform attributeName="transform" type="rotate" from={`0 ${lx} ${ly}`} to={`-360 ${lx} ${ly}`} dur={ORBIT} repeatCount="indefinite" />
                    )}
                    <text className="cell-label" x={lx} y={ly + subLabelSize * 0.35} textAnchor="middle" fontSize={subLabelSize} fill="#D5DBE0">
                      {s.label}
                    </text>
                  </g>
                </g>
              )
            })}
          </g>
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
