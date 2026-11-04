// Node geometry, drawn once from a seed so every node keeps its own shape between visits: an electric neuron's
// filaments, and the curved fibres between nodes.

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

/** How far from the centre anything reaches within ``spread`` radians of ``angle`` (at least ``min``). */
export function reachToward(points: Pt[], angle: number, spread: number, min: number): number {
  let reach = min
  for (const [x, y] of points) {
    const a = Math.atan2(y, x)
    if (Math.abs(Math.atan2(Math.sin(a - angle), Math.cos(a - angle))) <= spread) reach = Math.max(reach, Math.hypot(x, y))
  }
  return reach
}

/** A fibre from one node to another: a cubic curve leaving each node at its edge. */
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
  return { d, start: s, end: e }
}

/** An electric neuron's filaments: thin, slightly jagged strands leaving the core, some forking, kept clear of
 * the directions its fibres leave in. ``paths`` are open polylines to stroke; ``tips`` say how far they reach. */
export function filaments(r: number, seed: number, avoid: number[] = []): { paths: string[]; tips: Pt[] } {
  const rand = rng(seed ^ 0x5bd1e995)
  const paths: string[] = []
  const tips: Pt[] = []
  const count = 6 + Math.floor(rand() * 3)
  const strand = (x0: number, y0: number, angle: number, length: number, steps: number) => {
    const pts: Pt[] = [[x0, y0]]
    let a = angle
    for (let s = 1; s <= steps; s++) {
      a += (rand() - 0.5) * 0.5 // the jag of a discharge
      const d = (length / steps) * s
      pts.push([x0 + Math.cos(a) * d, y0 + Math.sin(a) * d])
    }
    tips.push(...pts)
    paths.push(`M${pts.map((p) => `${f(p[0])},${f(p[1])}`).join('L')}`)
    return pts
  }
  for (let i = 0; i < count; i++) {
    let angle = (i / count) * Math.PI * 2 + rand() * 0.5
    if (avoid.some((a) => Math.abs(Math.atan2(Math.sin(angle - a), Math.cos(angle - a))) < 0.3)) angle += 0.5
    const start = r * 0.85
    const pts = strand(Math.cos(angle) * start, Math.sin(angle) * start, angle, r * (0.55 + rand() * 0.75), 6)
    if (rand() < 0.55) {
      const at = pts[2 + Math.floor(rand() * 3)]
      strand(at[0], at[1], angle + (rand() < 0.5 ? -1 : 1) * (0.5 + rand() * 0.5), r * (0.2 + rand() * 0.3), 3)
    }
  }
  return { paths, tips }
}
