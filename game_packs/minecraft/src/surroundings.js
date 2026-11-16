// The shape of the place she's in: what a player takes in at a glance and the block lists never
// said. Seen live: the user said "this direction is the cave" four times and she kept walking out
// of it; "go forward a few blocks" and "not that way, the other way" had no meaning for her,
// because all she knew of the world was the nearest log, ore, water... Pure over a block lookup
// (`at(x, y, z)` -> { name, boundingBox } or null when not loaded), so tests build worlds by hand.
//
// Three things, all counted by code:
// - overhead: open sky, under trees, under a roof, or underground (how far up the rock starts);
// - ways: for each of the 8 compass directions, how far she can walk along the ground (stepping
//   up one block or down at most three, like walking does) and what stops her there: a wall, a
//   drop, water, lava; plus where the walk ends, so go_to can take her there;
// - openings: from open sky, walkable ground that goes in under a rock roof (a cave mouth or a
//   tunnel), seen along 16 directions;
// - space: the open space she is in, filled through air from where she stands (never through rock,
//   so nothing she couldn't see): how big it is, the floor in it, and where it goes on past what was
//   counted (passages). Measurements only: whether that's a cave, a tunnel or a hole she dug is hers
//   to judge. Added 2026-11-04: underground nothing said which way a passage led, and "dig down
//   until you're in a cave" had nothing to be checked against.
// The walk reads the blocks along the ground in front of her, which she'd mostly see; it stops at
// the first wall, so it doesn't look through hills.
import { createRequire } from 'node:module'

import { bearingWord, relativeBearing } from './state.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

export const WAY_MAX = 24 // blocks walked per direction
const MAX_STEP_DOWN = 3 // a drop she walks down without thinking (the pathfinder's default fall)
const DROP_SCAN = 24
const ROOF_SCAN = 8 // a roof over a walked cell: solid within this many blocks above her head
const OVERHEAD_SCAN = 48
const WALL_SCAN = 8
const OPENING_MIN_RUN = 3 // roofed cells in a row before ground counts as going in (not an overhang)

const COMPASS = ['north', 'north-west', 'west', 'south-west', 'south', 'south-east', 'east', 'north-east']

const LIQUIDS = new Set(['water', 'lava', 'bubble_column'])
const liquid = (b) => Boolean(b && (LIQUIDS.has(b.name) || b.getProperties?.().waterlogged === true))
const solid = (b) => b?.boundingBox === 'block' && !liquid(b)
const open = (b) => Boolean(b) && b.boundingBox === 'empty' && !liquid(b)
// Trees overhead aren't a roof: under a canopy is still outdoors.
const tree = (b) => /(_leaves|_log|_wood|_stem|_hyphae)$/.test(b?.name ?? '') || b?.name === 'mangrove_roots'
const liquidName = (b) => (b.name === 'lava' ? 'lava' : 'water')

function roofOver (at, x, headY, z, scan = ROOF_SCAN) {
  for (let dy = 1; dy <= scan; dy++) {
    const b = at(x, headY + dy, z)
    if (!b) return null
    if (solid(b) && !tree(b)) return { name: b.name, up: dy }
  }
  return null
}

// What's over her head.
export function overhead (at, feet) {
  const x = Math.floor(feet.x)
  const z = Math.floor(feet.z)
  const head = Math.floor(feet.y) + 1
  let first = null
  let rock = 0
  let leaves = false
  for (let dy = 1; dy <= OVERHEAD_SCAN; dy++) {
    const b = at(x, head + dy, z)
    if (!b) break // above the loaded world: nothing more to count
    if (tree(b)) {
      leaves = true
      continue
    }
    if (!solid(b)) continue
    first ??= { name: b.name, up: dy }
    rock++
  }
  if (!first) return { kind: leaves ? 'trees' : 'sky' }
  // Three or more solid blocks above: earth, not a house roof.
  return { kind: rock >= 3 ? 'underground' : 'roof', block: first.name, up: first.up, solid_above: rock }
}

// Walk from her feet along (dx, dz) (a unit vector), one block at a time.
export function walk (at, feet, dx, dz, max = WAY_MAX) {
  let foot = Math.floor(feet.y)
  let px = Math.floor(feet.x)
  let pz = Math.floor(feet.z)
  const startFoot = foot
  const startRoofed = Boolean(roofOver(at, px, foot + 1, pz))
  let walked = 0
  let end = null
  let roofRun = 0 // roofed cells in a row
  let mouth = null // first roofed cell of the current run
  let opening = null // a run that held for OPENING_MIN_RUN cells
  let out = false // walked out from under it again (an arch, a short tunnel): its size is final
  let insideFoot = foot
  for (let i = 1; i <= max; i++) {
    const cx = Math.floor(feet.x + dx * i)
    const cz = Math.floor(feet.z + dz * i)
    if (cx === px && cz === pz) continue
    const b0 = at(cx, foot, cz)
    const b1 = at(cx, foot + 1, cz)
    if (!b0 || !b1) {
      end = { kind: 'unloaded' }
      break
    }
    if (liquid(b0) || liquid(b1)) {
      end = { kind: liquidName(liquid(b0) ? b0 : b1) }
      break
    }
    if (open(b0) && open(b1)) {
      const below = at(cx, foot - 1, cz)
      if (!below) {
        end = { kind: 'unloaded' }
        break
      }
      if (liquid(below)) {
        end = { kind: liquidName(below) }
        break
      }
      if (!solid(below)) {
        let fall = null
        let into = null
        for (let d = 1; d <= DROP_SCAN; d++) {
          const g = at(cx, foot - 1 - d, cz)
          if (!g) break
          if (liquid(g)) {
            fall = d
            into = liquidName(g)
            break
          }
          if (solid(g)) {
            fall = d
            break
          }
        }
        if (fall === null || into || fall > MAX_STEP_DOWN) {
          end = { kind: 'drop', depth: fall ?? DROP_SCAN, deeper: fall === null, ...(into ? { into } : {}) }
          break
        }
        foot -= fall
      }
    } else if (solid(b0) && open(b1) && open(at(cx, foot + 2, cz)) && open(at(px, foot + 2, pz))) {
      foot += 1 // one block up: a step she walks (jumps) up
    } else {
      let height = 0
      while (height < WALL_SCAN && solid(at(cx, foot + height, cz))) height++
      // A wall that starts at head height (a low ceiling) still stops her.
      const face = solid(b0) ? b0 : b1
      end = { kind: 'wall', block: face.name, height: Math.max(height, 1), taller: height >= WALL_SCAN }
      break
    }
    px = cx
    pz = cz
    walked = i
    if (!startRoofed && !out) {
      const roof = roofOver(at, cx, foot + 1, cz)
      if (roof) {
        roofRun++
        insideFoot = foot
        if (roofRun === 1) mouth = { at: i, x: cx, y: foot, z: cz, roof: roof.name }
        if (roofRun >= OPENING_MIN_RUN) opening = mouth
      } else if (opening) {
        out = true
      } else {
        roofRun = 0
        mouth = null
      }
    }
  }
  const last = { x: px, y: foot, z: pz }
  const result = { walked, to: last, rise: foot - startFoot, end: end ?? { kind: 'open' } }
  // Ground that goes in under rock: the roof held for a few blocks (or to where the walk ended).
  if (opening) {
    result.opening = {
      at: opening.at,
      position: { x: opening.x, y: opening.y, z: opening.z },
      roof: opening.roof,
      goes_in: roofRun, // blocks walked under the roof (the walk may have stopped before its end)
      down: Math.max(0, opening.y - insideFoot) // how far the ground drops from the mouth inwards
    }
  }
  return result
}

const vector = (yaw) => ({ x: -Math.sin(yaw), z: -Math.cos(yaw) })

export const SPACE_REACH = 12 // blocks out from her, sideways, the open space is counted
export const SPACE_ABOVE = 4 // and above her head (more would count the whole sky over a shaft she dug)
export const SPACE_BELOW = 8 // and below her feet
const SPACE_MAX_CELLS = 6000

const compassOf = (dx, dz) => COMPASS[Math.round(((Math.atan2(-dx, -dz) / (Math.PI / 4)) % 8 + 8)) % 8]

// The open space around her: air connected to where she stands (feet or head), side by side through
// the six faces of each block, within SPACE_REACH sideways, SPACE_ABOVE above her head and SPACE_BELOW below
// her feet.
export function space (at, feet, yaw, { reach = SPACE_REACH, above = SPACE_ABOVE, below = SPACE_BELOW,
  maxCells = SPACE_MAX_CELLS } = {}) {
  const fx = Math.floor(feet.x)
  const fy = Math.floor(feet.y)
  const fz = Math.floor(feet.z)
  const key = (x, y, z) => `${x},${y},${z}`
  const seen = new Set()
  const queue = []
  for (const y of [fy, fy + 1]) {
    if (open(at(fx, y, fz))) {
      seen.add(key(fx, y, fz))
      queue.push([fx, y, fz])
    }
  }
  if (!queue.length) return null
  const bounds = { x0: fx, x1: fx, y0: fy, y1: fy, z0: fz, z1: fz }
  const floor = new Set()
  const touches = new Set()
  const leaks = new Map() // compass or up/down -> { cells, low, high, x, y, z (the nearest cell) }
  let full = false
  const leak = (dir, x, y, z) => {
    const l = leaks.get(dir) ?? { cells: 0, low: Infinity, high: -Infinity, near: Infinity }
    l.cells++
    l.low = Math.min(l.low, y - fy)
    l.high = Math.max(l.high, y - fy)
    const d = Math.hypot(x - fx, z - fz)
    if (d < l.near) Object.assign(l, { near: d, at: { x, y, z } })
    leaks.set(dir, l)
  }
  for (let i = 0; i < queue.length; i++) {
    const [x, y, z] = queue[i]
    bounds.x0 = Math.min(bounds.x0, x); bounds.x1 = Math.max(bounds.x1, x)
    bounds.y0 = Math.min(bounds.y0, y); bounds.y1 = Math.max(bounds.y1, y)
    bounds.z0 = Math.min(bounds.z0, z); bounds.z1 = Math.max(bounds.z1, z)
    const under = at(x, y - 1, z)
    if (solid(under) && open(at(x, y + 1, z))) floor.add(`${x},${z}`)
    for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0], [0, -1, 0]]) {
      const nx = x + dx
      const ny = y + dy
      const nz = z + dz
      const k = key(nx, ny, nz)
      if (seen.has(k)) continue
      const b = at(nx, ny, nz)
      if (!b) continue
      if (liquid(b)) {
        touches.add(liquidName(b))
        continue
      }
      if (!open(b)) continue
      if (Math.abs(nx - fx) > reach || Math.abs(nz - fz) > reach) {
        seen.add(k) // counted once as a way on, never as part of the space
        leak(compassOf(nx - fx, nz - fz), x, y, z)
        continue
      }
      if (ny - (fy + 1) > above) { seen.add(k); leak('up', x, y, z); continue }
      if (fy - ny > below) { seen.add(k); leak('down', x, y, z); continue }
      if (queue.length >= maxCells) { full = true; continue }
      seen.add(k)
      queue.push([nx, ny, nz])
    }
  }
  const passages = [...leaks].map(([dir, l]) => {
    const dx = l.at.x + 0.5 - feet.x
    const dz = l.at.z + 0.5 - feet.z
    return {
      dir, cells: l.cells, low: l.low, high: l.high, at: l.at,
      side: dir === 'up' || dir === 'down' ? dir : bearingWord(relativeBearing(yaw, dx, dz))
    }
  })
  // The widest first: a big opening onward before a crack.
  passages.sort((a, b) => b.cells - a.cells)
  let headroom = 0
  while (headroom < above && open(at(fx, fy + 2 + headroom, fz))) headroom++
  return {
    cells: queue.length,
    full,
    across: { x: bounds.x1 - bounds.x0 + 1, z: bounds.z1 - bounds.z0 + 1 },
    below: fy - bounds.y0, // how far the space reaches below her feet
    above: bounds.y1 - (fy + 1), // and above her head
    headroom, // open blocks straight above her head
    floor: floor.size, // blocks of floor in it she could stand on
    touches: [...touches].sort(),
    passages: passages.slice(0, 5),
    reach,
    reach_above: above,
    reach_below: below
  }
}

// Everything above, for her at `feet` facing `yaw` (mineflayer: 0 north, +pi/2 west).
export function surroundings (at, feet, yaw) {
  const over = overhead(at, feet)
  const ways = []
  for (let k = 0; k < 8; k++) {
    const { x, z } = vector(k * Math.PI / 4)
    // Diagonals: one block east and one north per step, not 0.7 of each.
    const scale = k % 2 ? Math.SQRT2 : 1
    const w = walk(at, feet, x * scale, z * scale, k % 2 ? Math.ceil(WAY_MAX / Math.SQRT2) : WAY_MAX)
    const side = bearingWord(relativeBearing(yaw, x, z))
    if (w.opening) w.opening.goes_in = Math.round(w.opening.goes_in * scale)
    ways.push({ compass: COMPASS[k], side, ...w, walked: Math.round(w.walked * scale) })
  }
  const openings = []
  if (over.kind === 'sky' || over.kind === 'trees') {
    for (let k = 0; k < 16; k++) {
      const yawK = k * Math.PI / 8
      const { x, z } = vector(yawK)
      const o = k % 2 === 0 ? ways[k / 2].opening : walk(at, feet, x, z).opening
      if (!o) continue
      const dx = o.position.x + 0.5 - feet.x
      const dz = o.position.z + 0.5 - feet.z
      // The same mouth seen along two neighbouring rays: keep the nearer.
      if (openings.some((p) => Math.hypot(p.position.x - o.position.x, p.position.z - o.position.z) < 4)) continue
      openings.push({
        ...o,
        distance: Math.round(Math.hypot(dx, dz)),
        side: bearingWord(relativeBearing(yaw, dx, dz)),
        compass: COMPASS[Math.round(((Math.atan2(-dx, -dz) / (Math.PI / 4)) % 8 + 8)) % 8]
      })
    }
    openings.sort((a, b) => a.distance - b.distance)
  }
  for (const w of ways) delete w.opening
  return { overhead: over, ways, openings: openings.slice(0, 4), space: space(at, feet, yaw) }
}

export function readSurroundings (bot) {
  const self = bot.entity
  const at = (x, y, z) => bot.blockAt(new Vec3(x, y, z), false) ?? null
  return surroundings(at, self.position, self.yaw)
}

// ---- words ----

const ORDER = ['ahead', 'ahead-right', 'right', 'behind-right', 'behind', 'behind-left', 'left', 'ahead-left']
const words = (name) => name.replace(/_/g, ' ')

export function describeOverhead (o) {
  if (!o) return null
  if (o.kind === 'sky') return 'outdoors, open sky above'
  if (o.kind === 'trees') return 'outdoors, under trees'
  if (o.kind === 'roof') return `under a roof (${words(o.block)} ${o.up} m above your head)`
  return `underground: ${words(o.block)} ${o.up} m above your head, rock all the way up`
}

export function describeEnd (e) {
  if (e.kind === 'open') return null
  if (e.kind === 'unloaded') return 'the world not loaded beyond'
  if (e.kind === 'wall') return `a ${words(e.block)} wall ${e.height}${e.taller ? '+' : ''} m high`
  if (e.kind === 'drop') return `a drop of ${e.depth}${e.deeper ? '+' : ''} m${e.into ? ` into ${e.into}` : ''}`
  return e.kind // water, lava
}

export function describeWay (w) {
  const ground = w.rise >= 2 ? `, rising ${w.rise} m` : w.rise <= -2 ? `, going down ${-w.rise} m` : ''
  const end = describeEnd(w.end)
  const head = `${w.side} (${w.compass}): `
  if (w.walked === 0) return head + `blocked right there by ${end}`
  return head + `walk ${w.walked}${w.end.kind === 'open' ? '+' : ''} m to (${w.to.x} ${w.to.y} ${w.to.z})${ground}` +
    (end ? `, then ${end}` : '')
}

export function describeOpening (o) {
  return `${o.side} (${o.compass}) ${o.distance} m at (${o.position.x} ${o.position.y} ${o.position.z}): ` +
    `the ground goes in under ${words(o.roof)} for ${o.goes_in}+ m` +
    `${o.down >= 2 ? `, sloping down ${o.down} m` : ''} (a cave or tunnel mouth)`
}

function level (low, high) {
  if (high <= -2) return `from ${-high}${high === low ? '' : `-${-low}`} m below your feet`
  if (low >= 2) return `from ${low}${high === low ? '' : `-${high}`} m above your feet`
  if (low <= -2) return `level with you and down to ${-low} m below`
  if (high >= 2) return `level with you and up to ${high} m above`
  return 'level with you'
}

export function describePassage (p, reach) {
  if (p.dir === 'up') return `up: it goes on higher than ${reach.reach_above} m above your head (${p.cells} open block${p.cells === 1 ? '' : 's'} wide)`
  if (p.dir === 'down') return `down: it goes on deeper than ${reach.reach_below} m below your feet (${p.cells} open block${p.cells === 1 ? '' : 's'} wide)`
  return `${p.side} (${p.dir}): it goes on past ${reach.reach} m, ${level(p.low, p.high)} ` +
    `(${p.cells} open block${p.cells === 1 ? '' : 's'} wide, nearest at ${p.at.x} ${p.at.y} ${p.at.z})`
}

export function describeSpace (sp) {
  if (!sp) return []
  const size = sp.full ? `more than ${sp.cells} open blocks (counting stopped)` : `${sp.cells} open blocks`
  const lines = [
    `Open space you're in (air connected to where you stand, counted out to ${sp.reach} m sideways, ` +
      `${sp.reach_above} m above your head and ${sp.reach_below} m below your feet, never through rock): ${size}, ${sp.across.x} × ${sp.across.z} m across, from ` +
      `${sp.below} m below your feet to ${sp.above} m above your head; ${sp.headroom} m open straight above ` +
      `your head; floor you could stand on: ${sp.floor} block${sp.floor === 1 ? '' : 's'}` +
      (sp.touches.length ? `; it meets ${sp.touches.join(' and ')}` : '') + '.'
  ]
  if (sp.passages.length) {
    lines.push('Where it goes on (passages out of what was counted, widest first):')
    for (const p of sp.passages) lines.push(`  ${describePassage(p, sp)}`)
  } else {
    lines.push('Where it goes on: nowhere within what was counted: closed in all round.')
  }
  return lines
}

// The lines for her state text, ahead first, then clockwise.
export function describeSurroundings (s) {
  if (!s) return []
  const ways = [...s.ways].sort((a, b) => ORDER.indexOf(a.side) - ORDER.indexOf(b.side))
  const lines = [`Around you: ${describeOverhead(s.overhead)}.`, 'Ways (walking along the ground from where you stand):']
  for (const w of ways) lines.push(`  ${describeWay(w)}`)
  if (s.openings?.length) {
    lines.push('Openings:')
    for (const o of s.openings) lines.push(`  ${describeOpening(o)}`)
  }
  lines.push(...describeSpace(s.space))
  return lines
}
