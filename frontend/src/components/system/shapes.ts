// Cell geometry, drawn once from a seed so every cell keeps its own shape between visits: an irregular soma,
// tapered dendrites, and curved axons with varicosities.

export type Pt = [number, number]

/** Small deterministic PRNG (mulberry32). */
export function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function seedOf(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619)
  return h >>> 0
}

const f = (n: number) => n.toFixed(1)

/** A closed smooth path through points (Catmull-Rom as cubic Béziers). */
function closedCurve(pts: Pt[]): string {
  const n = pts.length
  let d = `M${f(pts[0][0])},${f(pts[0][1])}`
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n]
    const p1 = pts[i]
    const p2 = pts[(i + 1) % n]
    const p3 = pts[(i + 2) % n]
    const c1: Pt = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]
    const c2: Pt = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
    d += `C${f(c1[0])},${f(c1[1])} ${f(c2[0])},${f(c2[1])} ${f(p2[0])},${f(p2[1])}`
  }
  return d + 'Z'
}

/** An irregular soma outline around (0, 0). */
export function somaPath(r: number, seed: number): string {
  const rand = rng(seed)
  const waves = [2, 3, 5].map((k) => ({ k, a: 0.04 + rand() * 0.07, p: rand() * Math.PI * 2 }))
  const pts: Pt[] = []
  const N = 18
  for (let i = 0; i < N; i++) {
    const t = (i / N) * Math.PI * 2
    const m = 1 + waves.reduce((s, w) => s + w.a * Math.sin(w.k * t + w.p), 0)
    pts.push([Math.cos(t) * r * m, Math.sin(t) * r * m])
  }
  return closedCurve(pts)
}

/** A tapered filled stroke along a centreline, from width w0 down to w1. */
function taper(line: Pt[], w0: number, w1: number): string {
  const left: Pt[] = []
  const right: Pt[] = []
  for (let i = 0; i < line.length; i++) {
    const a = line[Math.max(0, i - 1)]
    const b = line[Math.min(line.length - 1, i + 1)]
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const len = Math.hypot(dx, dy) || 1
    const w = (w0 + (w1 - w0) * (i / (line.length - 1))) / 2
    left.push([line[i][0] - (dy / len) * w, line[i][1] + (dx / len) * w])
    right.push([line[i][0] + (dy / len) * w, line[i][1] - (dx / len) * w])
  }
  const pts = [...left, ...right.reverse()]
  return `M${pts.map((p) => `${f(p[0])},${f(p[1])}`).join('L')}Z`
}

/** Dendrites growing out of a soma of radius r: a few tapered, gently bending branches, some forked.
 * ``tips`` says how far they reach in each direction, so labels can sit clear of them. */
export function dendrites(r: number, seed: number, avoid: number[] = []): { paths: string[]; tips: Pt[] } {
  const rand = rng(seed ^ 0x9e3779b9)
  const out: string[] = []
  const tips: Pt[] = []
  const count = 5 + Math.floor(rand() * 3)
  for (let i = 0; i < count; i++) {
    let angle = (i / count) * Math.PI * 2 + rand() * 0.6
    // Keep clear of the directions axons leave in, so a wire never reads as a dendrite.
    if (avoid.some((a) => Math.abs(Math.atan2(Math.sin(angle - a), Math.cos(angle - a))) < 0.35)) angle += 0.55
    const length = r * (0.7 + rand() * 0.9)
    const bend = (rand() - 0.5) * 0.9
    const line: Pt[] = []
    for (let s = 0; s <= 8; s++) {
      const t = s / 8
      const a = angle + bend * t * t
      const d = r * 0.82 + length * t
      line.push([Math.cos(a) * d, Math.sin(a) * d])
    }
    out.push(taper(line, r * 0.2, 0.6))
    tips.push(...line)
    if (rand() < 0.6) {
      const fork = line[4 + Math.floor(rand() * 2)]
      const fa = angle + bend * 0.3 + (rand() < 0.5 ? -1 : 1) * (0.5 + rand() * 0.4)
      const fl = length * (0.35 + rand() * 0.3)
      const branch: Pt[] = []
      for (let s = 0; s <= 5; s++) {
        const t = s / 5
        branch.push([fork[0] + Math.cos(fa) * fl * t, fork[1] + Math.sin(fa) * fl * t])
      }
      out.push(taper(branch, r * 0.08, 0.4))
      tips.push(...branch)
    }
  }
  return { paths: out, tips }
}

/** How far from the centre anything reaches within ``spread`` radians of ``angle`` (at least ``min``). */
export function reachToward(points: Pt[], angle: number, spread: number, min: number): number {
  let reach = min
  for (const [x, y] of points) {
    const a = Math.atan2(y, x)
    if (Math.abs(Math.atan2(Math.sin(a - angle), Math.cos(a - angle))) <= spread) reach = Math.max(reach, Math.hypot(x, y))
  }
  return reach
}

/** An axon from one cell to another: a cubic curve leaving each soma at its edge. */
export function axon(a: { x: number; y: number; r: number }, b: { x: number; y: number; r: number }, seed: number) {
  const rand = rng(seed)
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy)
  const ux = dx / len
  const uy = dy / len
  // Start and end tucked a little inside each soma, so the slight drift never shows a gap.
  const s: Pt = [a.x + ux * a.r * 0.75, a.y + uy * a.r * 0.75]
  const e: Pt = [b.x - ux * b.r * 0.75, b.y - uy * b.r * 0.75]
  const bow = (rand() - 0.5) * 0.5 * len
  const c1: Pt = [s[0] + dx * 0.3 - uy * bow, s[1] + dy * 0.3 + ux * bow]
  const c2: Pt = [e[0] - dx * 0.3 - uy * bow * 0.6, e[1] - dy * 0.3 + ux * bow * 0.6]
  const d = `M${f(s[0])},${f(s[1])}C${f(c1[0])},${f(c1[1])} ${f(c2[0])},${f(c2[1])} ${f(e[0])},${f(e[1])}`
  const at = (t: number): Pt => {
    const m = 1 - t
    return [
      m * m * m * s[0] + 3 * m * m * t * c1[0] + 3 * m * t * t * c2[0] + t * t * t * e[0],
      m * m * m * s[1] + 3 * m * m * t * c1[1] + 3 * m * t * t * c2[1] + t * t * t * e[1],
    ]
  }
  // Varicosities: small swellings along the axon, unevenly spaced.
  const beads: Pt[] = []
  let t = 0.12 + rand() * 0.06
  while (t < 0.9) {
    beads.push(at(t))
    t += 0.07 + rand() * 0.09
  }
  return { d, start: s, end: e, beads, angleFrom: Math.atan2(dy, dx), angleTo: Math.atan2(-dy, -dx) }
}
