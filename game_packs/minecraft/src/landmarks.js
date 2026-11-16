// Places she has seen, kept under stable ids (L1, L2, ...) across views: what a view showed (src/scene.js)
// matched to what she saw before, so "L3" in one decision is the same opening in the next, never a
// different one that happens to be listed third. An id is never reused, in this world, ever: a landmark
// that's gone stays gone (a reference to it fails, saying so), it doesn't quietly point at something new.
//
// Each landmark keeps its kind, dimension, where it is (an anchor, a standing spot when one was found,
// an extent for structures), the latest facts seen, how sure (clear / possible), when first and last
// seen, whether it's in view now or remembered, and what happened when she tried to get there
// (approaches, route checks: a failed approach is one way that didn't work, not proof none does).
// Pure: the world lookup comes in (src/view.js), the clock too.
import { CLS } from './view.js'
import { built } from './scene.js'

const MATCH = { opening: 6, passage: 8, structure: 12, water: 24, lava: 8 }
const KEEP = 120 // landmarks per world, the longest unseen dropped first
const APPROACHES_KEPT = 6

const dist3 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)

function boxesTouch (a, b, pad = 4) {
  return a && b && a.x0 - pad <= b.x1 && b.x0 - pad <= a.x1 && a.z0 - pad <= b.z1 && b.z0 - pad <= a.z1 &&
    a.y0 - pad <= b.y1 && b.y0 - pad <= a.y1
}

const FACT_KEYS = {
  opening: ['width', 'height', 'roof', 'natural', 'seen_in', 'floor_seen', 'slope', 'rise', 'dark', 'between'],
  passage: ['far', 'near', 'beyond', 'floor_seen'],
  structure: ['size', 'materials', 'door', 'windows', 'behind_trees'],
  water: ['across', 'deepest_seen', 'bottom_seen'],
  lava: []
}

function factsOf (f) {
  return Object.fromEntries((FACT_KEYS[f.kind] ?? []).filter((k) => f[k] !== undefined).map((k) => [k, f[k]]))
}

export class Landmarks {
  // saved: { seq, items } from toJSON(); changed(): called after anything changes (to save soon).
  constructor (saved = null, changed = () => {}) {
    this.seq = saved?.seq ?? 0
    this.items = (saved?.items ?? []).map((l) => ({ ...l, status: l.status === 'gone' ? 'gone' : 'remembered' }))
    this.changed = changed
  }

  get (id) {
    const key = String(id).toUpperCase()
    return this.items.find((l) => l.id === key) ?? null
  }

  // A view's features into landmarks. Returns { visible: [ids], added: [ids] }.
  update (scene, { dimension, now = Date.now(), observation = null, world = null } = {}) {
    const iso = new Date(now).toISOString()
    const features = [...scene.openings, ...scene.passages, ...scene.structures, ...scene.water, ...scene.lava]
    const visible = new Set()
    const added = []
    for (const f of features) {
      const lm = this._match(f, dimension, visible)
      if (lm) {
        // A closer look is the better one: its position and facts replace the older ones.
        if (f.distance <= (lm.best_distance ?? Infinity) + 4) {
          Object.assign(lm, { x: f.x, y: f.y, z: f.z, stand: f.stand ?? lm.stand ?? null, facts: factsOf(f) })
          if (f.facing) lm.facing = f.facing
          if (f.box) lm.box = f.box
          if (f.evidence) lm.evidence = f.evidence
          lm.best_distance = Math.min(lm.best_distance ?? Infinity, f.distance)
        } else if (f.kind === 'opening') {
          lm.facts.seen_in = Math.max(lm.facts.seen_in ?? 0, f.seen_in)
          lm.facts.floor_seen = lm.facts.floor_seen || f.floor_seen
        }
        if (f.kind === 'structure' && f.box && lm.box) {
          lm.box = { x0: Math.min(lm.box.x0, f.box.x0), x1: Math.max(lm.box.x1, f.box.x1), y0: Math.min(lm.box.y0, f.box.y0),
            y1: Math.max(lm.box.y1, f.box.y1), z0: Math.min(lm.box.z0, f.box.z0), z1: Math.max(lm.box.z1, f.box.z1) }
          lm.facts.size = { w: lm.box.x1 - lm.box.x0 + 1, h: lm.box.y1 - lm.box.y0 + 1, d: lm.box.z1 - lm.box.z0 + 1 }
        }
        if (f.confidence === 'clear') lm.confidence = 'clear'
        lm.last_seen = iso
        lm.seen_count = (lm.seen_count ?? 1) + 1
        lm.status = 'visible'
        lm.last_view = observation
        visible.add(lm.id)
        continue
      }
      const fresh = {
        id: `L${++this.seq}`,
        kind: f.kind,
        dimension,
        x: f.x, y: f.y, z: f.z,
        stand: f.stand ?? null,
        ...(f.facing ? { facing: f.facing } : {}),
        ...(f.box ? { box: f.box } : {}),
        ...(f.evidence ? { evidence: f.evidence } : {}),
        facts: factsOf(f),
        confidence: f.confidence,
        first_seen: iso,
        last_seen: iso,
        best_distance: f.distance,
        seen_count: 1,
        status: 'visible',
        last_view: observation,
        approaches: [],
        route: null
      }
      this.items.push(fresh)
      visible.add(fresh.id)
      added.push(fresh.id)
    }
    for (const lm of this.items) {
      if (lm.status === 'visible' && !visible.has(lm.id)) lm.status = 'remembered'
    }
    if (world) this.verify(world, dimension, iso)
    this._prune()
    if (features.length || added.length) this.changed()
    return { visible: [...visible], added }
  }

  _match (f, dimension, taken) {
    let best = null
    let bestD = Infinity
    for (const lm of this.items) {
      if (lm.kind !== f.kind || lm.dimension !== dimension || lm.status === 'gone') continue
      const d = dist3(lm, f)
      const near = f.kind === 'structure' ? (d <= MATCH.structure || boxesTouch(lm.box, f.box)) : d <= MATCH[f.kind]
      if (!near) continue
      // Two things in one view are two landmarks unless they're really the same place.
      if (taken.has(lm.id) && d > MATCH[f.kind] / 2) continue
      if (d < bestD) {
        best = lm
        bestD = d
      }
    }
    return best
  }

  // Remembered landmarks whose place is loaded and checkably changed: gone (filled in, broken down).
  verify (world, dimension, iso) {
    for (const lm of this.items) {
      if (lm.dimension !== dimension || lm.status !== 'remembered') continue
      const c = world.cls(lm.x, lm.y, lm.z)
      if (c < 0) continue
      let gone = false
      if (lm.kind === 'opening' || lm.kind === 'passage') gone = c !== CLS.OPEN && c !== CLS.WATER
      else if (lm.kind === 'water') gone = c !== CLS.WATER && world.cls(lm.x, lm.y - 1, lm.z) !== CLS.WATER
      else if (lm.kind === 'lava') gone = c !== CLS.LAVA
      else if (lm.kind === 'structure' && lm.evidence?.length) {
        const checks = lm.evidence.map((p) => (world.cls(p.x, p.y, p.z) < 0 ? null : built(world.name(p.x, p.y, p.z))))
        gone = checks.every((v) => v === false)
      }
      if (gone) {
        lm.status = 'gone'
        lm.gone_at = iso
        this.changed()
      }
    }
  }

  _prune () {
    if (this.items.length <= KEEP) return
    const order = [...this.items].sort((a, b) => (a.status === 'gone') - (b.status === 'gone') || b.last_seen.localeCompare(a.last_seen))
    const keep = new Set(order.slice(0, KEEP).map((l) => l.id))
    this.items = this.items.filter((l) => keep.has(l.id))
  }

  // Known landmarks in this dimension, nearest first. near: {x, y, z}.
  list ({ dimension, near, within = 256, max = 10, includeGone = false } = {}) {
    return this.items
      .filter((l) => l.dimension === dimension && (includeGone || l.status !== 'gone'))
      .map((l) => ({ l, d: near ? dist3(l, near) : 0 }))
      .filter(({ d }) => d <= within)
      .sort((a, b) => a.d - b.d)
      .slice(0, max)
      .map(({ l }) => l)
  }

  // What happened when she tried to get there.
  noteApproach (id, record) {
    const lm = this.get(id)
    if (!lm) return
    lm.approaches = [...(lm.approaches ?? []), record].slice(-APPROACHES_KEPT)
    this.changed()
  }

  noteRoute (id, route) {
    const lm = this.get(id)
    if (!lm) return
    lm.route = route
    this.changed()
  }

  toJSON () {
    return { seq: this.seq, items: this.items }
  }
}
