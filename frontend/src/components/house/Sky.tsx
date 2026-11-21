import { useEffect, useMemo, useRef } from 'react'

/**
 * The golden-dusk heaven behind the village: gradient sky and low sun (CSS), a few early stars,
 * slow cloud banks at three depths, and sparse snow. Decoration only; nothing here carries meaning.
 */

function seeded(seed: number) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296
    return s / 4294967296
  }
}

/**
 * A bank of puffs along a baseline, drawn twice side by side so it can drift in a seamless loop.
 * One gradient spans the whole strip (not each shape), and a solid base continues it to the bottom
 * of the sky, so no bank ever shows a flat edge or a band.
 */
function bankPuffs(seed: number, count: number, minR: number, maxR: number) {
  const rand = seeded(seed)
  return Array.from({ length: count }, (_, i) => {
    const r = minR + rand() * (maxR - minR)
    return { x: (i + rand() * 0.8) * (1600 / count), y: 150 - r * (0.35 + rand() * 0.35), r }
  })
}

function CloudBank({ seed, count, minR, maxR, layer }: { seed: number; count: number; minR: number; maxR: number; layer: string }) {
  const puffs = useMemo(() => bankPuffs(seed, count, minR, maxR), [seed, count, minR, maxR])
  const id = `bank-${layer}`
  const shapes = (dx: number) => (
    <g transform={`translate(${dx} 0)`}>
      {puffs.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={p.r} />
      ))}
      <rect x={-40} y={140} width={1680} height={60} />
    </g>
  )
  return (
    <div className={`sky-bank sky-bank--${layer}`}>
      <svg viewBox="0 0 3200 200" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="200">
            <stop offset="0" stopColor="var(--bank-top)" />
            <stop offset="0.55" stopColor="var(--bank-mid)" />
            <stop offset="1" stopColor="var(--bank-low)" />
          </linearGradient>
        </defs>
        <g fill={`url(#${id})`}>
          {shapes(0)}
          {shapes(1600)}
        </g>
      </svg>
      <div className="sky-bank-base" />
    </div>
  )
}

function Stars() {
  const stars = useMemo(() => {
    const rand = seeded(7)
    return Array.from({ length: 34 }, () => ({
      x: rand() * 100,
      y: Math.pow(rand(), 1.6) * 34,
      r: 0.6 + rand() * 1.3,
      o: 0.35 + rand() * 0.55,
      t: rand() < 0.3 ? 3 + rand() * 5 : 0,
      d: rand() * 6,
    }))
  }, [])
  return (
    <div className="sky-stars" aria-hidden="true">
      {stars.map((s, i) => (
        <span
          key={i}
          className={s.t ? 'sky-star sky-star--twinkle' : 'sky-star'}
          style={{
            left: `${s.x}%`,
            top: `${s.y}%`,
            width: s.r * 2,
            height: s.r * 2,
            opacity: s.o,
            animationDuration: s.t ? `${s.t}s` : undefined,
            animationDelay: s.t ? `${-s.d}s` : undefined,
          }}
        />
      ))}
    </div>
  )
}

function Snow() {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = canvas.current
    if (!el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const ctx = el.getContext('2d')
    if (!ctx) return
    const rand = seeded(42)
    let w = 0
    let h = 0
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = el.clientWidth
      h = el.clientHeight
      el.width = w * dpr
      el.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const flakes = Array.from({ length: 46 }, () => ({
      x: rand(),
      y: rand(),
      r: 0.8 + rand() * 1.7,
      v: 9 + rand() * 16,
      sway: rand() * Math.PI * 2,
      a: 0.45 + rand() * 0.45,
    }))
    let last = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now
      ctx.clearRect(0, 0, w, h)
      for (const f of flakes) {
        f.y += (f.v * dt) / h
        f.sway += dt * 0.6
        if (f.y > 1.02) {
          f.y = -0.02
          f.x = rand()
        }
        const x = f.x * w + Math.sin(f.sway) * 14
        ctx.globalAlpha = f.a
        ctx.fillStyle = '#FFFBF4'
        ctx.beginPath()
        ctx.arc(x, f.y * h, f.r, 0, Math.PI * 2)
        ctx.fill()
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    window.addEventListener('resize', resize)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return <canvas ref={canvas} className="sky-snow" aria-hidden="true" />
}

export function Sky() {
  return (
    <div className="sky" aria-hidden="true">
      <Stars />
      <div className="sky-sun" />
      <CloudBank layer="far" seed={3} count={22} minR={36} maxR={78} />
      <CloudBank layer="mid" seed={11} count={16} minR={44} maxR={96} />
      <CloudBank layer="near" seed={19} count={12} minR={56} maxR={110} />
      <Snow />
    </div>
  )
}
