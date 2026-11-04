// Field geometry, drawn once from a seed so it keeps its shape between visits: the curved fibres between nodes.

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
