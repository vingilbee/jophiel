// Seeing a place and having a way there are two different pieces of evidence. This is the second:
// whether a spot is somewhere she can stand, and whether the pathfinder finds a route to it under the
// same rules go_to walks by (no block placing, drops of at most 4, digging only with dig=yes).
// Seen live: go_to was given coordinates straight toward her goal, across ravines and into rock, and
// four identical tries ended "no path" with nothing said about why or what to try instead.
//
// A route check is a bounded search, run in slices so her body keeps moving meanwhile. Its result is
// one of:
//   success:  a route was found (it can still change as she walks: terrain loads, things move);
//   no_route: the search ran out of places to try: no route under these rules within loaded terrain;
//   incomplete: its time budget ran out first: not proof there's none.
// Either failure keeps the closest spot the search reached, as an intermediate place to go.
import { createRequire } from 'node:module'

import { CLS, UNLOADED } from './view.js'
import { standable } from './scene.js'

const require = createRequire(import.meta.url)
const { goals } = require('mineflayer-pathfinder')

export { standable }

// What's at a spot, for a person: "inside stone", "in the air (nothing to stand on)", "in water".
export function spotProblem (world, x, y, z) {
  const feet = world.cls(x, y, z)
  const head = world.cls(x, y + 1, z)
  const below = world.cls(x, y - 1, z)
  if (feet === UNLOADED || head === UNLOADED || below === UNLOADED) return 'not loaded (unknown terrain)'
  if (feet === CLS.WATER || head === CLS.WATER) return 'in water'
  if (feet === CLS.LAVA || head === CLS.LAVA || below === CLS.LAVA) return 'in lava'
  if (feet !== CLS.OPEN) return `inside ${(world.name(x, y, z) ?? 'a block').replace(/_/g, ' ')}`
  if (head !== CLS.OPEN) return `no room to stand (${(world.name(x, y + 1, z) ?? 'a block').replace(/_/g, ' ')} at head height)`
  if (below === CLS.OPEN) return 'in the air (nothing to stand on)'
  if (below === CLS.WATER) return 'on water'
  return null
}

// The nearest spot she can stand on to (x, y, z): the same column first (up or down, a guessed height
// is the usual mistake), then rings around it. null if none within `radius` sideways / `column` up or down.
export function nearestStandable (world, x, y, z, { radius = 3, column = 8, avoid = null } = {}) {
  const ok = (px, py, pz) => standable(world, px, py, pz) && !(avoid && avoid(px, py, pz))
  if (ok(x, y, z)) return { x, y, z }
  for (let d = 1; d <= column; d++) {
    if (ok(x, y + d, z)) return { x, y: y + d, z }
    if (ok(x, y - d, z)) return { x, y: y - d, z }
  }
  for (let r = 1; r <= radius; r++) {
    let best = null
    let bestD = Infinity
    for (let dx = -r; dx <= r; dx++) {
      for (let dz = -r; dz <= r; dz++) {
        if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue
        for (let dy = -4; dy <= 4; dy++) {
          if (!ok(x + dx, y + dy, z + dz)) continue
          const d = Math.hypot(dx, dy * 1.5, dz)
          if (d < bestD) { best = { x: x + dx, y: y + dy, z: z + dz }; bestD = d }
        }
      }
    }
    if (best) return best
  }
  return null
}

// The top standing spot of a column near a height (for "x z": the ground there). null if not loaded.
export function groundSpot (world, x, z, nearY, { up = 48, down = 48 } = {}) {
  let loaded = false
  for (let y = Math.min(world.maxY - 2, nearY + up); y >= Math.max(world.minY + 1, nearY - down); y--) {
    const c = world.cls(x, y, z)
    if (c === UNLOADED) continue
    loaded = true
    if (standable(world, x, y, z)) return { x, y, z }
  }
  return loaded ? false : null
}

// Is a spot loaded at all?
export function loaded (world, x, y, z) {
  return world.cls(x, y, z) !== UNLOADED
}

const p3 = (p) => ({ x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) })

// A bounded route search from where she stands to `goal` (a pathfinder goal). Yields to the event loop
// between slices of `sliceMs`, so physics and her senses keep running.
// check(): throws to abandon the search (the skill was stopped).
export async function checkRoute (bot, goal, movements, { budgetMs = 2500, sliceMs = 15, check = () => {} } = {}) {
  const started = Date.now()
  const gen = bot.pathfinder.getPathFromTo(movements, bot.entity.position, goal, { timeout: budgetMs, tickTimeout: sliceMs })
  let result = null
  while (true) {
    const step = gen.next()
    if (step.done) break
    result = step.value.result
    if (result.status !== 'partial') break
    await new Promise((resolve) => setImmediate(resolve))
    check()
  }
  const path = result?.path ?? []
  const last = path.length ? path[path.length - 1] : bot.entity.position
  const status = result?.status === 'success' ? 'success' : result?.status === 'noPath' ? 'no_route' : 'incomplete'
  return {
    status,
    ms: Date.now() - started,
    searched: result?.visitedNodes ?? 0,
    steps: path.length,
    // The furthest point of the best route the search found (for success: the goal).
    closest: p3(last),
    from: p3(bot.entity.position)
  }
}

// Spots to stand at to reach a landmark. Openings and passages: the mouth's floor first, then spots
// just outside and just inside it, alternating outward (the mouth itself is the place she was sent);
// structures: around it, nearest to her first; water: the shore; lava: never closer than 3 blocks.
export function approachSpots (world, lm, from, { max = 8 } = {}) {
  const spots = []
  const seen = new Set()
  const add = (p) => {
    if (!p) return
    const key = `${p.x},${p.y},${p.z}`
    if (seen.has(key)) return
    seen.add(key)
    spots.push(p)
  }
  const inWater = (x, y, z) => world.cls(x, y, z) === CLS.WATER
  if (lm.kind === 'opening' || lm.kind === 'passage') {
    if (lm.stand && standable(world, lm.stand.x, lm.stand.y, lm.stand.z)) add(lm.stand)
    const f = lm.facing ?? { x: 0, z: 0 }
    for (const k of [0, -1, 1, -2, 2, -3, 3, 4]) {
      const x = Math.floor(lm.x + 0.5 + f.x * k)
      const z = Math.floor(lm.z + 0.5 + f.z * k)
      add(nearestStandable(world, x, lm.y, z, { radius: 1, column: 4 }))
    }
  } else if (lm.kind === 'structure') {
    const b = lm.box ?? { x0: lm.x, x1: lm.x, y0: lm.y, y1: lm.y, z0: lm.z, z1: lm.z }
    const cx = (b.x0 + b.x1) / 2
    const cz = (b.z0 + b.z1) / 2
    const r = Math.max(b.x1 - b.x0, b.z1 - b.z0) / 2 + 2
    add(nearestStandable(world, lm.x, lm.y, lm.z, { radius: 2, column: 4 }))
    for (let a = 0; a < 8; a++) {
      const x = Math.floor(cx + Math.cos(a * Math.PI / 4) * r)
      const z = Math.floor(cz + Math.sin(a * Math.PI / 4) * r)
      const g = groundSpot(world, x, z, b.y0, { up: 6, down: 8 })
      if (g) add(g)
    }
  } else if (lm.kind === 'water') {
    add(nearestStandable(world, lm.x, lm.y + 1, lm.z, { radius: 4, column: 3, avoid: inWater }))
  } else if (lm.kind === 'lava') {
    const fx = from.x - lm.x
    const fz = from.z - lm.z
    const n = Math.hypot(fx, fz) || 1
    add(nearestStandable(world, Math.floor(lm.x + fx / n * 4), lm.y + 1, Math.floor(lm.z + fz / n * 4), { radius: 2, column: 4 }))
  }
  if (lm.kind === 'opening' || lm.kind === 'passage') return spots.slice(0, max)
  return spots
    .map((p) => ({ p, d: Math.hypot(p.x - from.x, p.y - from.y, p.z - from.z) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, max)
    .map(({ p }) => p)
}

export function spotGoal (spot, range = 0) {
  return range <= 0.5 ? new goals.GoalBlock(spot.x, spot.y, spot.z) : new goals.GoalNear(spot.x, spot.y, spot.z, range)
}
