// Electricity around a node, drawn straight into an SVG group (no React re-render): living arcs that keep
// re-forming around the ring like a plasma ball, and bursts of bolts and flying sparks when the core discharges
// or a pulse arrives. Everything is in the node's own coordinates (its centre at 0, 0).
import type { PartState } from '../../types/system'
import { FAIL } from './nodeStates'

const NS = 'http://www.w3.org/2000/svg'

type Pt = [number, number]

/** A lightning path by midpoint displacement: a bolt from a to b that jags sideways at every scale. */
function bolt(a: Pt, b: Pt, jag: number, depth = 4): Pt[] {
  if (depth === 0) return [a, b]
  const mx = (a[0] + b[0]) / 2
  const my = (a[1] + b[1]) / 2
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len = Math.hypot(dx, dy) || 1
  const off = (Math.random() - 0.5) * jag * len
  const m: Pt = [mx - (dy / len) * off, my + (dx / len) * off]
  return [...bolt(a, m, jag, depth - 1).slice(0, -1), ...bolt(m, b, jag, depth - 1)]
}

const d = (pts: Pt[]) => `M${pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('L')}`

/** One arc leaving the ring at ``angle``: a bolt bending gently away, sometimes forking. */
function arcPaths(ringR: number, r: number, angle: number, reach: number): string[] {
  const start: Pt = [Math.cos(angle) * ringR, Math.sin(angle) * ringR]
  const bend = angle + (Math.random() - 0.5) * 0.9
  const len = r * reach
  const end: Pt = [start[0] + Math.cos(bend) * len, start[1] + Math.sin(bend) * len]
  const main = bolt(start, end, 0.42)
  const out = [d(main)]
  if (Math.random() < 0.45) {
    const at = main[Math.floor(main.length * (0.3 + Math.random() * 0.4))]
    const fa = bend + (Math.random() < 0.5 ? -1 : 1) * (0.5 + Math.random() * 0.6)
    const fl = len * (0.3 + Math.random() * 0.3)
    out.push(d(bolt(at, [at[0] + Math.cos(fa) * fl, at[1] + Math.sin(fa) * fl], 0.6, 3)))
  }
  return out
}

function strokePath(g: SVGGElement, path: string, color: string, width: number, opacity: number) {
  const el = document.createElementNS(NS, 'path')
  el.setAttribute('d', path)
  el.setAttribute('fill', 'none')
  el.setAttribute('stroke', color)
  el.setAttribute('stroke-width', String(width))
  el.setAttribute('stroke-opacity', String(opacity))
  el.setAttribute('stroke-linecap', 'round')
  el.setAttribute('stroke-linejoin', 'round')
  el.setAttribute('vector-effect', 'non-scaling-stroke')
  g.appendChild(el)
  return el
}

/** How busy the electricity is in each state: arcs at a time, how far they reach (× the node's radius). */
const ARCS: Partial<Record<PartState, { n: [number, number]; reach: [number, number] }>> = {
  alive: { n: [1, 3], reach: [0.35, 0.75] },
  busy: { n: [3, 5], reach: [0.45, 0.9] },
  partial: { n: [0, 2], reach: [0.35, 0.7] },
  failing: { n: [2, 4], reach: [0.35, 0.85] },
}

/** Re-form a node's living arcs (call every ~110 ms). ``line`` thickens strokes with the zoom. */
export function reformArcs(g: SVGGElement, state: PartState | undefined, color: string, r: number, ringR: number, line: number) {
  g.replaceChildren()
  const spec = state ? ARCS[state] : undefined
  if (!spec) return
  const n = spec.n[0] + Math.floor(Math.random() * (spec.n[1] - spec.n[0] + 1))
  const tint = state === 'failing' ? FAIL : color
  for (let i = 0; i < n; i++) {
    const reach = spec.reach[0] + Math.random() * (spec.reach[1] - spec.reach[0])
    for (const p of arcPaths(ringR, r, Math.random() * Math.PI * 2, reach)) {
      strokePath(g, p, tint, 3.2 * line, 0.28) // the glow around the discharge
      strokePath(g, p, '#F4FFFE', 0.9 * line, 0.9) // its white-hot middle
    }
  }
}

/** A burst: bolts crack out all round and vanish, and sparks fly off and fade. */
export function burst(g: SVGGElement, state: PartState | undefined, color: string, r: number, ringR: number, line: number) {
  const tint = state === 'failing' ? FAIL : color
  const bolts = 4 + Math.floor(Math.random() * 3)
  const made: SVGElement[] = []
  for (let i = 0; i < bolts; i++) {
    const a = (i / bolts) * Math.PI * 2 + Math.random() * 0.6
    for (const p of arcPaths(ringR, r, a, 0.9 + Math.random() * 0.6)) {
      made.push(strokePath(g, p, tint, 4 * line, 0.4), strokePath(g, p, '#FFFFFF', 1.2 * line, 1))
    }
  }
  for (const el of made) {
    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 170, easing: 'ease-in' }).onfinish = () => el.remove()
  }
  for (let i = 0; i < 9; i++) {
    const a = Math.random() * Math.PI * 2
    const from: Pt = [Math.cos(a) * ringR, Math.sin(a) * ringR]
    const dist = r * (0.6 + Math.random() * 1.1)
    const dot = document.createElementNS(NS, 'circle')
    dot.setAttribute('r', String(Math.max(0.6, r * 0.025)))
    dot.setAttribute('fill', Math.random() < 0.5 ? '#FFFFFF' : tint)
    dot.setAttribute('cx', String(from[0]))
    dot.setAttribute('cy', String(from[1]))
    g.appendChild(dot)
    dot.animate(
      [{ transform: 'translate(0,0)', opacity: 1 }, { transform: `translate(${Math.cos(a) * dist}px,${Math.sin(a) * dist}px)`, opacity: 0 }],
      { duration: 380 + Math.random() * 200, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    ).onfinish = () => dot.remove()
  }
}
