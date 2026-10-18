// The lie of the land, beyond the next few steps (src/surroundings.js stops at 24 blocks along 8
// lines). Seen live: asked to climb a mountain, she climbed ten blocks, then circled the slope and
// went back down into a cave, because nothing told her where the high ground was; underground she
// couldn't find the way out, because real passages bend.
//
// Two things, counted by code over a block lookup (`kind(x, y, z)` -> 'solid' | 'tree' | 'water' |
// 'lava' | 'open', or null when not loaded), so tests build worlds by hand:
// - land: the ground height every GRID blocks out to RADIUS: where the highest ground is, which way
//   the land rises or falls, where the water is, and how steep the straight way to the top is;
// - way out (underground): a search through open space (air and water, any direction, ledges and
//   shafts too) for the nearest spot with sky above, then the route judged: walkable, or the
//   biggest climb on it. Plus the direct option: how far the surface is straight up.
// Trees don't count as ground: a canopy isn't a hill.
import { createRequire } from 'node:module'

import { bearingWord, relativeBearing } from './state.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

export const RADIUS = 64
export const GRID = 4
const SCAN_UP = 112 // how far above her feet a column's ground is looked for
const SCAN_DOWN = 64
const RISE = 6 // a sector this much higher (on average) than her rises; this much lower falls
const WAY_OUT_CELLS = 12000 // open cells searched for the way out, at most
const WAY_OUT_REACH = 48 // blocks from her, any direction
const CLIMB_COST = 6 // going up from where she can't stand (a ledge, a shaft): walkable routes first
const CLOSE = 3 // "right next to her": every column this close (the grid skips them), for a wall or pillar she's at

const COMPASS = ['north', 'north-west', 'west', 'south-west', 'south', 'south-east', 'east', 'north-east']
const compassOf = (dx, dz) => COMPASS[(Math.round(Math.atan2(-dx, -dz) / (Math.PI / 4)) % 8 + 8) % 8]
const sideOf = (yaw, dx, dz) => bearingWord(relativeBearing(yaw, dx, dz))
const ground = (k) => k === 'solid' || k === 'water' || k === 'lava'
const passable = (k) => k === 'open' || k === 'water'

// The ground of one column: the first solid, water or lava block going down from `top`.
// { y, kind, capped } (capped: solid already at the top of the scan: higher than we look),
// or null (not loaded, or nothing down to `bottom`).
export function columnGround (kind, x, z, top, bottom) {
  for (let y = top; y >= bottom; y--) {
    const k = kind(x, y, z)
    if (k === null) return null
    if (ground(k)) return { y, kind: k, capped: y === top }
  }
  return null
}

// Column grounds, remembered: one scan per column until a block in it changes (forget).
export class GroundCache {
  constructor (kind) {
    this.kind = kind
    this.columns = new Map()
  }

  get (x, z, top, bottom) {
    const key = `${x},${z}`
    const hit = this.columns.get(key)
    if (hit !== undefined && hit.top >= top && hit.bottom <= bottom) return hit.ground
    const g = columnGround(this.kind, x, z, top, bottom)
    if (g !== null) this.columns.set(key, { top, bottom, ground: g })
    return g
  }

  forget (x, z) {
    this.columns.delete(`${x},${z}`)
  }
}

// ---- land ----

export function land (cache, feet, yaw, { radius = RADIUS, grid = GRID } = {}) {
  const fx = Math.floor(feet.x)
  const fy = Math.floor(feet.y)
  const fz = Math.floor(feet.z)
  const top = fy + SCAN_UP
  const bottom = fy - SCAN_DOWN
  const sectors = COMPASS.map((name) => ({ compass: name, rel: [], water: 0, n: 0 }))
  let peak = null
  let seen = 0
  for (let dx = -radius; dx <= radius; dx += grid) {
    for (let dz = -radius; dz <= radius; dz += grid) {
      const d = Math.hypot(dx, dz)
      if (d > radius || d < grid) continue
      const g = cache.get(fx + dx, fz + dz, top, bottom)
      if (!g) continue
      seen++
      const rel = g.y + 1 - fy // where she'd stand there, against where she stands
      if (d >= 16) {
        const s = sectors[COMPASS.indexOf(compassOf(dx, dz))]
        s.n++
        if (g.kind === 'water') s.water++
        else s.rel.push(rel)
      }
      if (g.kind !== 'solid') continue
      if (!peak || rel > peak.rel || (rel === peak.rel && d < peak.distance)) {
        peak = { x: fx + dx, y: g.y + 1, z: fz + dz, rel, distance: Math.round(d), capped: g.capped }
      }
    }
  }
  if (!seen) return null
  // The highest ground right next to her, every column: the land grid starts 4 blocks out, so a
  // pillar she's climbing beside never showed. Seen live: she built 11 blocks past an ice pillar's top.
  let close = null
  for (let dx = -CLOSE; dx <= CLOSE; dx++) {
    for (let dz = -CLOSE; dz <= CLOSE; dz++) {
      if (!dx && !dz) continue
      const g = cache.get(fx + dx, fz + dz, top, bottom)
      if (!g || g.kind !== 'solid') continue
      const rel = g.y + 1 - fy
      if (!close || rel > close.rel) close = { rel, distance: Math.round(Math.hypot(dx, dz)), capped: g.capped }
    }
  }
  const out = { sectors: [], peak: null, close }
  for (const s of sectors) {
    if (!s.n) continue
    const mean = s.rel.length ? s.rel.reduce((a, b) => a + b, 0) / s.rel.length : 0
    const max = s.rel.length ? Math.max(...s.rel) : 0
    const min = s.rel.length ? Math.min(...s.rel) : 0
    out.sectors.push({
      compass: s.compass,
      mean: Math.round(mean),
      max,
      min,
      water: Math.round((s.water / s.n) * 100) / 100
    })
  }
  if (peak && peak.rel >= RISE) {
    const dx = peak.x + 0.5 - feet.x
    const dz = peak.z + 0.5 - feet.z
    out.peak = {
      ...peak,
      side: sideOf(yaw, dx, dz),
      compass: compassOf(dx, dz),
      at_edge: peak.distance >= radius - grid * 1.5, // the land may keep rising past what we looked at
      way_up: wayUp(cache, feet, peak, top, bottom)
    }
  }
  return out
}

// The straight way to the top, block by block: how steep it gets (the biggest step up from one
// block to the next) and how much of it is water.
function wayUp (cache, feet, peak, top, bottom) {
  const fx = Math.floor(feet.x)
  const fz = Math.floor(feet.z)
  const steps = Math.max(Math.abs(peak.x - fx), Math.abs(peak.z - fz))
  let last = Math.floor(feet.y) - 1
  let biggest = 0
  let water = 0
  for (let i = 1; i <= steps; i++) {
    const x = Math.round(fx + (peak.x - fx) * i / steps)
    const z = Math.round(fz + (peak.z - fz) * i / steps)
    const g = cache.get(x, z, top, bottom)
    if (!g) return null
    if (g.kind === 'water') water++
    biggest = Math.max(biggest, g.y - last)
    last = g.y
  }
  return { biggest_step: biggest, water_blocks: water }
}

// ---- the way out from underground ----

class Heap {
  constructor () { this.items = [] }
  get size () { return this.items.length }
  push (item) {
    const a = this.items
    a.push(item)
    let i = a.length - 1
    while (i > 0) {
      const p = (i - 1) >> 1
      if (a[p][0] <= a[i][0]) break
      ;[a[p], a[i]] = [a[i], a[p]]
      i = p
    }
  }

  pop () {
    const a = this.items
    const top = a[0]
    const last = a.pop()
    if (a.length) {
      a[0] = last
      let i = 0
      for (;;) {
        const l = 2 * i + 1
        const r = l + 1
        let m = i
        if (l < a.length && a[l][0] < a[m][0]) m = l
        if (r < a.length && a[r][0] < a[m][0]) m = r
        if (m === i) break
        ;[a[m], a[i]] = [a[i], a[m]]
        i = m
      }
    }
    return top
  }
}

const MOVES = [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0], [0, -1, 0]]

// Somewhere she can be without falling: ground under her, or in water (she swims).
const standing = (kind, x, y, z) => kind(x, y, z) === 'water' || kind(x, y - 1, z) === 'solid'

// Through open space from her feet to the nearest spot with sky above (above its column's ground).
// Going up from somewhere she can't stand (a ledge, a shaft, open air) costs more, so a walkable
// way wins when there is one.
// { exit, path_length, rise, climb: { height, at } | null, swim, first: {dx, dz} } or null.
export function wayOut (kind, cache, feet, { cells = WAY_OUT_CELLS, reach = WAY_OUT_REACH, waypoints = false } = {}) {
  const sx = Math.floor(feet.x)
  const sy = Math.floor(feet.y)
  const sz = Math.floor(feet.z)
  const top = sy + SCAN_UP
  const bottom = sy - SCAN_DOWN
  const key = (x, y, z) => `${x},${y},${z}`
  const skyAt = (x, y, z) => {
    const g = cache.get(x, z, top, bottom)
    return g !== null && !g.capped && y > g.y
  }
  if (!passable(kind(sx, sy, sz))) return null
  const start = [sx, sy, sz]
  const parent = new Map([[key(...start), null]])
  const cost = new Map([[key(...start), 0]])
  const heap = new Heap()
  heap.push([0, start])
  let popped = 0
  while (heap.size && popped < cells) {
    const [c, cell] = heap.pop()
    const [x, y, z] = cell
    if (c > cost.get(key(x, y, z))) continue
    popped++
    if ((x !== sx || y !== sy || z !== sz) && skyAt(x, y, z)) return judge(kind, parent, cell, key, feet, waypoints)
    const onGround = standing(kind, x, y, z)
    for (const [dx, dy, dz] of MOVES) {
      const nx = x + dx
      const ny = y + dy
      const nz = z + dz
      if (Math.abs(nx - sx) > reach || Math.abs(ny - sy) > reach || Math.abs(nz - sz) > reach) continue
      if (!passable(kind(nx, ny, nz))) continue
      const nk = key(nx, ny, nz)
      const nc = c + (dy === 1 && !onGround ? CLIMB_COST : 1)
      if (cost.has(nk) && cost.get(nk) <= nc) continue
      cost.set(nk, nc)
      parent.set(nk, cell)
      heap.push([nc, [nx, ny, nz]])
    }
  }
  return null
}

// The route found, walked back: how long, which way it starts, and the biggest climb on it, the
// height gained between two places she can stand (1 is a step she walks up; more is a ledge or a
// shaft walking can't do).
const WAYPOINT_EVERY = 6 // path cells between the stops go_to surface walks to, one short search each

function judge (kind, parent, end, key, feet, withWaypoints = false) {
  const cells = []
  for (let c = end; c; c = parent.get(key(...c))) cells.push(c)
  cells.reverse()
  let horizontal = 0
  let lastStand = cells[0][1]
  let climb = null
  let swim = 0
  let first = null
  for (let i = 1; i < cells.length; i++) {
    const [x0, y0, z0] = cells[i - 1]
    const [x, y, z] = cells[i]
    if (y === y0) {
      horizontal++
      if (!first) first = { dx: x - x0, dz: z - z0 }
    }
    if (kind(x, y, z) === 'water') swim++
    if (!standing(kind, x, y, z)) continue
    const gained = y - lastStand
    if (gained >= 2 && (!climb || gained > climb.height)) climb = { height: gained, at: horizontal }
    lastStand = y
  }
  const [ex, ey, ez] = end
  // Spots she can stand on along the way, every few cells: walking (and digging) to each in turn
  // is a short search, where one search for the whole way through rock gave up (seen in tests).
  const stops = []
  if (withWaypoints) {
    let since = 0
    for (const [x, y, z] of cells.slice(1, -1)) {
      if (++since >= WAYPOINT_EVERY && standing(kind, x, y, z)) {
        stops.push({ x, y, z })
        since = 0
      }
    }
  }
  return {
    ...(withWaypoints ? { waypoints: stops } : {}),
    exit: { x: ex, y: ey, z: ez },
    path_length: cells.length - 1,
    rise: ey - Math.floor(feet.y),
    climb,
    swim,
    first: first ?? { dx: ex - Math.floor(feet.x), dz: ez - Math.floor(feet.z) }
  }
}

// How far the ground's surface is straight above her (null outdoors or unknown).
export function surfaceAbove (cache, feet) {
  const fx = Math.floor(feet.x)
  const fy = Math.floor(feet.y)
  const fz = Math.floor(feet.z)
  const g = cache.get(fx, fz, fy + SCAN_UP, fy - SCAN_DOWN)
  if (!g || g.y < fy) return null
  return { y: g.y + 1, up: g.y + 1 - fy, capped: g.capped }
}

// Everything for the state. `underground`: from surroundings' overhead (roof or rock above).
export function terrain (kind, cache, feet, yaw, { underground = false } = {}) {
  const out = { land: land(cache, feet, yaw) }
  if (underground) {
    const way = wayOut(kind, cache, feet)
    if (way) {
      const dx = way.exit.x + 0.5 - feet.x
      const dz = way.exit.z + 0.5 - feet.z
      way.distance = Math.round(Math.hypot(dx, dz))
      way.compass = compassOf(way.first.dx, way.first.dz)
      way.side = sideOf(yaw, way.first.dx, way.first.dz)
    }
    out.way_out = way
    out.surface = surfaceAbove(cache, feet)
  }
  return out
}

// ---- reading the real world ----

const TREE = /(_leaves|_log|_wood|_stem|_hyphae)$/

function kindOfBlock (b) {
  if (!b) return 'open'
  if (b.name === 'water' || b.name === 'bubble_column') return 'water'
  if (b.name === 'lava') return 'lava'
  if (TREE.test(b.name) || b.name === 'mangrove_roots') return 'tree'
  return b.boundingBox === 'block' ? 'solid' : 'open'
}

// kind(x, y, z) for a bot: by block state id, each id classified once.
export function kindReader (bot) {
  const byState = new Map()
  const v = new Vec3(0, 0, 0)
  return (x, y, z) => {
    v.x = x
    v.y = y
    v.z = z
    let id
    try {
      id = bot.world.getBlockStateId(v)
    } catch {
      return null
    }
    if (id === undefined || id === null) return null
    let k = byState.get(id)
    if (k === undefined) {
      k = kindOfBlock(bot.registry.blocksByStateId[id])
      byState.set(id, k)
    }
    return k
  }
}

const caches = new WeakMap()

// One kind reader and ground cache per bot, forgetting a column when a block in it changes.
export function groundFor (bot) {
  let entry = caches.get(bot)
  if (!entry) {
    const kind = kindReader(bot)
    const cache = new GroundCache(kind)
    const onUpdate = (old, block) => {
      const p = (block ?? old)?.position
      if (p) cache.forget(p.x, p.z)
    }
    bot.on('blockUpdate', onUpdate)
    bot.once('end', () => bot.off('blockUpdate', onUpdate))
    entry = { kind, cache }
    caches.set(bot, entry)
  }
  return entry
}

const REFRESH_MS = 5000
const REFRESH_MOVED = 2

// For the state, read about once a second: recomputed after she moves a couple of blocks, every
// few seconds, or when she goes underground or comes out; otherwise the last one, with sides
// turned to where she faces now.
export function readTerrain (bot, { underground = false, now = Date.now() } = {}) {
  const entry = groundFor(bot)
  const p = bot.entity.position
  const last = entry.last
  if (last && last.underground === underground && now - last.at < REFRESH_MS &&
      p.distanceTo(last.position) < REFRESH_MOVED) {
    return turned(last.terrain, p, bot.entity.yaw)
  }
  const t = terrain(entry.kind, entry.cache, p, bot.entity.yaw, { underground })
  entry.last = { at: now, position: p.clone(), underground, terrain: t }
  return t
}

function turned (t, feet, yaw) {
  const out = { ...t }
  if (t.land?.peak) {
    const pk = t.land.peak
    out.land = { ...t.land, peak: { ...pk, side: sideOf(yaw, pk.x + 0.5 - feet.x, pk.z + 0.5 - feet.z) } }
  }
  if (t.way_out) out.way_out = { ...t.way_out, side: sideOf(yaw, t.way_out.first.dx, t.way_out.first.dz) }
  return out
}

// Targets for go_to, fresh: the peak's standing spot, or the way out's exit.
export function peakSpot (bot) {
  const { cache } = groundFor(bot)
  const feet = bot.entity.position
  const peak = land(cache, feet, bot.entity.yaw)?.peak ?? null
  if (peak) peak.waypoints = lineStops(cache, feet, peak)
  return peak
}

// Standing spots every WAYPOINT_EVERY blocks on the straight way to a place (the ground there + 1).
function lineStops (cache, feet, to) {
  const fx = Math.floor(feet.x)
  const fy = Math.floor(feet.y)
  const fz = Math.floor(feet.z)
  const steps = Math.max(Math.abs(to.x - fx), Math.abs(to.z - fz))
  const stops = []
  for (let i = WAYPOINT_EVERY; i < steps - 2; i += WAYPOINT_EVERY) {
    const x = Math.round(fx + (to.x - fx) * i / steps)
    const z = Math.round(fz + (to.z - fz) * i / steps)
    const g = cache.get(x, z, fy + SCAN_UP, fy - SCAN_DOWN)
    if (g && g.kind === 'solid') stops.push({ x, y: g.y + 1, z })
  }
  return stops
}

export function surfaceSpot (bot) {
  const { kind, cache } = groundFor(bot)
  const feet = bot.entity.position
  return { way: wayOut(kind, cache, feet, { waypoints: true }), above: surfaceAbove(cache, feet) }
}

// ---- words ----

function sectorWords (s) {
  if (s.water >= 0.5) return `${s.compass}: mostly water`
  const parts = []
  if (s.mean >= RISE) parts.push(`rises (about +${s.mean} m, up to +${s.max})`)
  else if (s.mean <= -RISE) parts.push(`falls (about ${s.mean} m, down to ${s.min})`)
  else if (s.max >= RISE * 2) parts.push(`mostly level, high ground up to +${s.max}`)
  else return null
  if (s.water >= 0.2) parts.push('some water')
  return `${s.compass}: ${parts.join(', ')}`
}

export function describeTerrain (t) {
  if (!t?.land) return []
  const lines = []
  const sectors = t.land.sectors.map(sectorWords).filter(Boolean)
  lines.push(`Land out to ${RADIUS} m: ${sectors.length ? sectors.join('; ') : 'level all around'}.`)
  const p = t.land.peak
  if (p) {
    const height = p.rel >= 30 ? 'a mountain top' : p.rel >= 12 ? 'a hilltop' : 'a rise'
    let way = ''
    if (p.way_up) {
      way = p.way_up.biggest_step <= 1
        ? ' The straight way there is walkable'
        : ` The straight way there has steps up to ${p.way_up.biggest_step} blocks high (dig=yes digs steps)`
      if (p.way_up.water_blocks) way += `, ${p.way_up.water_blocks} m of it water`
      way += '.'
    }
    lines.push(`Highest ground (peak): ${p.side} (${p.compass}) ${p.distance} m at (${p.x} ${p.y} ${p.z}), ` +
      `${p.capped ? 'over ' : ''}${p.rel} m above you: ${height}` +
      `${p.at_edge ? ', and the land may keep rising beyond' : ''}.${way} go_to target "peak" goes there.`)
  } else {
    lines.push('Highest ground (peak): you are on it, nothing near is more than a few blocks higher.')
  }
  const c = t.land?.close
  if (c && c.rel >= 2) {
    lines.push(`Right next to you (${c.distance} m): ground ${c.capped ? 'more than ' : ''}${c.rel} m above you (a wall, a pillar).`)
  } else if (c) {
    lines.push('Right next to you: nothing higher than where you stand.')
  }
  if (t.surface) lines.push(`Surface: ${t.surface.capped ? 'more than ' : 'about '}${t.surface.up} m straight above you (digging up is always a way).`)
  if ('way_out' in t) {
    const w = t.way_out
    if (!w) {
      lines.push(`Way out: no opening to the sky found through open space within ${WAY_OUT_REACH} m.`)
    } else {
      const how = w.climb
        ? `on the way, a ${w.climb.height} m climb (a ledge or shaft) after ${w.climb.at} m: walking can't go up it, digging steps can (dig=yes)`
        : 'walkable'
      lines.push(`Way out: ${w.path_length} m through open space, starting ${w.side} (${w.compass}), to the sky at ` +
        `(${w.exit.x} ${w.exit.y} ${w.exit.z}), ${w.rise >= 0 ? `${w.rise} m up` : `${-w.rise} m down`}; ${how}` +
        `${w.swim ? `; ${w.swim} m swimming` : ''}. go_to target "surface" goes there.`)
    }
  }
  return lines
}
