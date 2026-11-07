// What she sees from her eyes: rays cast over the loaded world through a camera, one per cell of a
// perspective grid (docs/MINECRAFT_RAY_BASED_PERCEPTION_DESIGN.md). The other senses read the world
// along fixed lines (src/surroundings.js: 8 compass walks out to 24 m; terrain.js: column heights out
// to 64 m) and found cave mouths only from under open sky. A player sees forward, far, from wherever
// they stand: a mouth across a ravine, a wall of planks behind trees. This is that view, counted by
// code. It never decides what anything is: src/scene.js groups the rays into what they show.
//
// Pure over a world lookup, so tests build worlds by hand:
//   world.cls(x, y, z)   -> one of CLS, or UNLOADED (not loaded: unknown, never empty)
//   world.name(x, y, z)  -> the block's name (asked only for what a ray hits)
//   world.light(x, y, z) -> { sky, block } or null when the client has no light data
//   world.minY, world.maxY (build limits; maxY exclusive)
//
// Geometry. Mineflayer's yaw: 0 faces north (-z), growing counter-clockwise seen from above; pitch is
// positive looking up. The grid is a perspective projection: column i, row j (row 0 at the top) looks
// along forward + u·right + v·up with u, v spread evenly over the tangent of the half field of view,
// so equal cells are equal steps on the image plane. The vertical field of view follows from the
// horizontal one and the grid's aspect ratio. Distance is along the ray, from the eye, in blocks.
//
// What a ray can end on (END):
//   SURFACE: the first surface in sight. Solid blocks, partial ones (slabs, stairs, fences), logs and
//            leaves stop sight. Air and things without a collision box (grass, flowers, torches) don't.
//            Glass and iron bars are seen through (the ray notes it). Water seen from above is a
//            surface (what's under it, within BENEATH_SCAN, is kept as `beneath`); from under water
//            she sees through water up to WATER_SIGHT, then nothing more (murk). Lava stops sight.
//   UNKNOWN:  it reached terrain that isn't loaded, or below the world: not known to be empty.
//   SKY:      it rose past the top of the world: nothing can be in the way, so that is sky.
//   CLEAR:    nothing within the range. Not "sky": only that nothing was hit this far.
//   MURK:     under water, as far as she can see through it.
//   SKIPPED:  the work budget ran out before this ray was cast (the view says how many).
// Light: the light where the ray last passed through open air is kept. A surface in darkness (no sky
// light, no block light) more than DARK_SEEN blocks away keeps its shape but not its identity: what a
// player sees of the back of an unlit cave is black. (The older sight check, src/sight.js, ignores
// darkness; this view doesn't.) With no light data from the server, every surface keeps its identity.
//
// Cover: for each surface hit, whether the air in front of it has a roof (solid, not leaves or logs,
// within ROOF_SCAN blocks above). A ray that passed through open air and then into covered air saw
// into a covered space (a cave mouth, a tunnel, a doorway) from outside it: where it went under the
// cover is kept (`entry`). The cover is judged along the ray, wherever she stands: under trees, under
// an overhang, across water. Nothing about her overhead decides whether it is looked for.

export const CLS = { OPEN: 0, SOLID: 1, TREE: 2, GLASS: 3, WATER: 4, LAVA: 5 }
export const UNLOADED = -1
export const END = { CLEAR: 0, SURFACE: 1, UNKNOWN: 2, SKY: 3, MURK: 4, SKIPPED: 5 }
export const END_NAMES = ['clear', 'surface', 'unknown', 'sky', 'murk', 'skipped']
// Face normals, by index: the side of the block the ray came in through.
export const NORMALS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
export const NO_NORMAL = 255
export const UP = 2 // NORMALS[UP]: a top face, ground she could stand on

export const DEFAULTS = Object.freeze({
  fovH: 100, // degrees, horizontal
  cols: 64,
  rows: 36,
  range: 128, // blocks along the ray
  maxSteps: 400000, // block lookups for the whole view (cover checks included): perception never stalls her
  roofScan: 16,
  waterSight: 24,
  beneathScan: 12,
  darkSeen: 16
})

const WATERS = new Set(['water', 'bubble_column', 'kelp', 'kelp_plant', 'seagrass', 'tall_seagrass'])
const SEE_THROUGH = /(^|_)glass(_pane)?$|^glass_pane$|^iron_bars$|^barrier$/
const TREES = /(_leaves|_log|_wood|_stem|_hyphae)$/

// A block as one of CLS: { name, boundingBox, waterlogged? }.
export function classify (block) {
  if (!block) return UNLOADED
  const name = block.name
  if (name === 'lava') return CLS.LAVA
  if (WATERS.has(name)) return CLS.WATER
  if (block.boundingBox !== 'block') return block.waterlogged ? CLS.WATER : CLS.OPEN
  if (name === 'tinted_glass') return CLS.SOLID
  if (SEE_THROUGH.test(name)) return CLS.GLASS
  if ((TREES.test(name) && !name.startsWith('stripped_')) || name === 'mangrove_roots') return CLS.TREE
  return CLS.SOLID
}

const DEG = Math.PI / 180

// The camera's unit vectors for a yaw and pitch (mineflayer's).
export function cameraBasis (yaw, pitch) {
  const cp = Math.cos(pitch)
  const forward = [-Math.sin(yaw) * cp, Math.sin(pitch), -Math.cos(yaw) * cp]
  const right = [Math.cos(yaw), 0, -Math.sin(yaw)]
  // up = right × forward
  const up = [
    right[1] * forward[2] - right[2] * forward[1],
    right[2] * forward[0] - right[0] * forward[2],
    right[0] * forward[1] - right[1] * forward[0]
  ]
  return { forward, right, up }
}

export function verticalFov (fovH, cols, rows) {
  return 2 * Math.atan(Math.tan((fovH * DEG) / 2) * rows / cols) / DEG
}

// The direction of image point (i, j) (fractional cells allowed: 0.5 is the middle of the first).
export function rayDirection (basis, tanH, tanV, cols, rows, i, j) {
  const u = (i / cols * 2 - 1) * tanH
  const v = (1 - j / rows * 2) * tanV
  const d = [0, 1, 2].map((a) => basis.forward[a] + u * basis.right[a] + v * basis.up[a])
  const n = Math.hypot(d[0], d[1], d[2])
  return [d[0] / n, d[1] / n, d[2] / n]
}

// Where a world point falls in the image: { i, j, depth } (cells, fractional), or null behind her.
export function project (camera, point) {
  const basis = cameraBasis(camera.yaw, camera.pitch)
  const d = [point.x - camera.eye.x, point.y - camera.eye.y, point.z - camera.eye.z]
  const z = d[0] * basis.forward[0] + d[1] * basis.forward[1] + d[2] * basis.forward[2]
  if (z <= 0.01) return null
  const x = d[0] * basis.right[0] + d[1] * basis.right[1] + d[2] * basis.right[2]
  const y = d[0] * basis.up[0] + d[1] * basis.up[1] + d[2] * basis.up[2]
  const tanH = Math.tan((camera.fov_h * DEG) / 2)
  const tanV = Math.tan((camera.fov_v * DEG) / 2)
  const i = ((x / z) / tanH + 1) / 2 * camera.cols
  const j = (1 - (y / z) / tanV) / 2 * camera.rows
  return { i, j, depth: Math.hypot(d[0], d[1], d[2]) }
}

// Ray results, one slot per ray, in typed arrays (grid rays first, row by row, then refined ones).
function rayStore (n) {
  return {
    n: 0,
    end: new Uint8Array(n),
    dist: new Float32Array(n),
    hit: new Int32Array(n * 3), // the block hit
    open: new Int32Array(n * 3), // the last open cell the ray passed through before it
    normal: new Uint8Array(n).fill(NO_NORMAL),
    cls: new Int8Array(n).fill(UNLOADED),
    name: new Int16Array(n).fill(-1), // index into names; -1 none, -2 too dark to tell
    under: new Uint8Array(n), // 1: the air in front of the surface is covered (a roof above it)
    entry: new Float32Array(n).fill(NaN), // where it went under cover, along the ray
    entryCell: new Int32Array(n * 3),
    light: new Int8Array(n * 2).fill(-1), // sky, block at the open cell (-1: unknown)
    glass: new Int32Array(n * 3), // first see-through block passed (when glassAt)
    glassAt: new Float32Array(n).fill(NaN),
    beneathDepth: new Float32Array(n).fill(NaN), // water: how deep before what's under it
    beneathName: new Int16Array(n).fill(-1),
    dir: new Float32Array(n * 3),
    image: new Float32Array(n * 2) // its image point (i, j)
  }
}

export class Budget {
  constructor (max) {
    this.max = max
    this.used = 0
  }

  take (n = 1) {
    this.used += n
    return this.used <= this.max
  }

  get left () { return this.max - this.used }
}

// Cover: is there a roof (solid or glass, not trees) within roofScan above this open cell?
// true / false, or null when the column above isn't loaded far enough to tell. Cached per cell.
function makeCover (world, eye, roofScan, budget) {
  const cache = new Map()
  const ex = Math.floor(eye.x)
  const ez = Math.floor(eye.z)
  const key = (x, y, z) => ((x - ex + 1024) * 2048 + (z - ez + 1024)) * 1024 + (y - world.minY)
  return (x, y, z) => {
    const k = key(x, y, z)
    const known = cache.get(k)
    if (known !== undefined) return known
    let out = false
    for (let d = 1; d <= roofScan; d++) {
      if (y + d >= world.maxY) break
      budget.take()
      const c = world.cls(x, y + d, z)
      if (c === UNLOADED) { out = null; break }
      if (c === CLS.SOLID || c === CLS.GLASS) { out = true; break }
    }
    cache.set(k, out)
    return out
  }
}

// One ray, by voxel stepping (each block it passes, in order, with the face it came in through).
function castRay (world, eye, dir, opts, ctx, store, k) {
  const { range, waterSight, beneathScan } = opts
  const { budget, names } = ctx
  const nameIndex = (name) => {
    let idx = ctx.nameIds.get(name)
    if (idx === undefined) {
      idx = names.length
      names.push(name)
      ctx.nameIds.set(name, idx)
    }
    return idx
  }
  const [dx, dy, dz] = dir
  let x = Math.floor(eye.x)
  let y = Math.floor(eye.y)
  let z = Math.floor(eye.z)
  const stepX = dx > 0 ? 1 : dx < 0 ? -1 : 0
  const stepY = dy > 0 ? 1 : dy < 0 ? -1 : 0
  const stepZ = dz > 0 ? 1 : dz < 0 ? -1 : 0
  const tDeltaX = stepX ? 1 / Math.abs(dx) : Infinity
  const tDeltaY = stepY ? 1 / Math.abs(dy) : Infinity
  const tDeltaZ = stepZ ? 1 / Math.abs(dz) : Infinity
  let tMaxX = stepX > 0 ? (x + 1 - eye.x) * tDeltaX : stepX < 0 ? (eye.x - x) * tDeltaX : Infinity
  let tMaxY = stepY > 0 ? (y + 1 - eye.y) * tDeltaY : stepY < 0 ? (eye.y - y) * tDeltaY : Infinity
  let tMaxZ = stepZ > 0 ? (z + 1 - eye.z) * tDeltaZ : stepZ < 0 ? (eye.z - z) * tDeltaZ : Infinity
  let inWater = ctx.eyeInWater
  let waterFrom = 0
  let ox = x
  let oy = y
  let oz = z
  let t = 0
  let normal = NO_NORMAL
  const finish = (end, dist) => {
    store.end[k] = end
    store.dist[k] = Math.min(dist, range)
    store.open[k * 3] = ox; store.open[k * 3 + 1] = oy; store.open[k * 3 + 2] = oz
  }
  while (true) {
    if (tMaxX < tMaxY && tMaxX < tMaxZ) {
      x += stepX; t = tMaxX; tMaxX += tDeltaX; normal = stepX > 0 ? 1 : 0
    } else if (tMaxY < tMaxZ) {
      y += stepY; t = tMaxY; tMaxY += tDeltaY; normal = stepY > 0 ? 3 : 2
    } else {
      z += stepZ; t = tMaxZ; tMaxZ += tDeltaZ; normal = stepZ > 0 ? 5 : 4
    }
    if (t > range) return finish(END.CLEAR, range)
    if (y >= world.maxY) return finish(END.SKY, t)
    if (y < world.minY) return finish(END.UNKNOWN, t)
    if (!budget.take()) return finish(END.SKIPPED, t)
    const c = world.cls(x, y, z)
    if (c === UNLOADED) return finish(END.UNKNOWN, t)
    if (c === CLS.OPEN) {
      inWater = false
      ox = x; oy = y; oz = z
      continue
    }
    if (c === CLS.GLASS) {
      if (Number.isNaN(store.glassAt[k])) {
        store.glassAt[k] = t
        store.glass[k * 3] = x; store.glass[k * 3 + 1] = y; store.glass[k * 3 + 2] = z
      }
      continue
    }
    if (c === CLS.WATER && inWater) {
      // Under water: she sees through it, not far.
      ox = x; oy = y; oz = z
      if (t - waterFrom > waterSight) return finish(END.MURK, t)
      continue
    }
    // A surface.
    finish(END.SURFACE, t)
    store.hit[k * 3] = x; store.hit[k * 3 + 1] = y; store.hit[k * 3 + 2] = z
    store.normal[k] = normal
    store.cls[k] = c
    store.name[k] = nameIndex(world.name(x, y, z))
    if (c === CLS.WATER) {
      // What's under the water's surface (a shallow ford or a deep lake), along the same ray.
      for (let s = 0; s < beneathScan * 2; s++) {
        if (tMaxX < tMaxY && tMaxX < tMaxZ) {
          x += stepX; tMaxX += tDeltaX
        } else if (tMaxY < tMaxZ) {
          y += stepY; tMaxY += tDeltaY
        } else {
          z += stepZ; tMaxZ += tDeltaZ
        }
        if (!budget.take()) break
        const b = world.cls(x, y, z)
        if (b === UNLOADED) break
        if (b !== CLS.WATER) {
          if (b !== CLS.OPEN) {
            store.beneathDepth[k] = Math.max(0, store.hit[k * 3 + 1] - y)
            store.beneathName[k] = nameIndex(world.name(x, y, z))
          }
          break
        }
      }
    }
    return
  }
}

// Cover along a surface ray: where it went under a roof, walking back from the hit to the eye.
function coverEntry (eye, store, k, covered) {
  if (store.end[k] !== END.SURFACE) return
  const ox = store.open[k * 3]
  const oy = store.open[k * 3 + 1]
  const oz = store.open[k * 3 + 2]
  if (covered(ox, oy, oz) !== true) return
  store.under[k] = 1
  const dx = store.dir[k * 3]
  const dy = store.dir[k * 3 + 1]
  const dz = store.dir[k * 3 + 2]
  let last = [ox, oy, oz]
  for (let t = store.dist[k] - 0.25; t > 0; t -= 0.5) {
    const x = Math.floor(eye.x + dx * t)
    const y = Math.floor(eye.y + dy * t)
    const z = Math.floor(eye.z + dz * t)
    const c = covered(x, y, z)
    if (c === null) return // can't tell
    if (c === false) {
      // Out from under it: the ray came in from open air here.
      store.entry[k] = t + 0.5
      store.entryCell[k * 3] = last[0]; store.entryCell[k * 3 + 1] = last[1]; store.entryCell[k * 3 + 2] = last[2]
      return
    }
    last = [x, y, z]
  }
  // Covered all the way back to her eyes: she is inside that covered space.
}

function lightAt (world, store, k) {
  const l = world.light?.(store.open[k * 3], store.open[k * 3 + 1], store.open[k * 3 + 2])
  if (!l) return
  store.light[k * 2] = l.sky
  store.light[k * 2 + 1] = l.block
}

let observationSeq = 0

// The view from `eye` ({x, y, z}: the eye itself, not the feet) looking along yaw/pitch.
// Returns the observation: camera, ray store, names, the work done. Refined rays (`refine`) are cast
// after the grid, finer where the grid shows a sudden jump in depth or rays that saw into cover: a
// narrow gap can fall between grid rays.
export function observe (world, eye, yaw, pitch, options = {}) {
  const opts = { ...DEFAULTS, ...options }
  const started = Date.now()
  const fovV = verticalFov(opts.fovH, opts.cols, opts.rows)
  const camera = {
    eye: { x: eye.x, y: eye.y, z: eye.z }, yaw, pitch,
    fov_h: opts.fovH, fov_v: Math.round(fovV * 10) / 10, cols: opts.cols, rows: opts.rows, range: opts.range
  }
  const basis = cameraBasis(yaw, pitch)
  const tanH = Math.tan((opts.fovH * DEG) / 2)
  const tanV = Math.tan((fovV * DEG) / 2)
  const gridN = opts.cols * opts.rows
  const refineMax = opts.refineRays ?? 1200
  const store = rayStore(gridN + refineMax)
  const budget = new Budget(opts.maxSteps)
  const eyeCell = world.cls(Math.floor(eye.x), Math.floor(eye.y), Math.floor(eye.z))
  const ctx = { budget, names: [], nameIds: new Map(), eyeInWater: eyeCell === CLS.WATER }
  const covered = makeCover(world, eye, opts.roofScan, budget)

  const cast = (i, j) => {
    const k = store.n++
    const d = rayDirection(basis, tanH, tanV, opts.cols, opts.rows, i, j)
    store.dir[k * 3] = d[0]; store.dir[k * 3 + 1] = d[1]; store.dir[k * 3 + 2] = d[2]
    store.image[k * 2] = i; store.image[k * 2 + 1] = j
    castRay(world, eye, d, opts, ctx, store, k)
    if (store.end[k] === END.SURFACE) {
      coverEntry(eye, store, k, covered)
      lightAt(world, store, k)
      const sky = store.light[k * 2]
      const blockLight = store.light[k * 2 + 1]
      if (sky === 0 && blockLight === 0 && store.dist[k] > opts.darkSeen) store.name[k] = -2
    }
    return k
  }

  for (let j = 0; j < opts.rows; j++) {
    for (let i = 0; i < opts.cols; i++) cast(i + 0.5, j + 0.5)
  }

  // Refinement: around grid cells next to a jump in depth (an edge: a gap may hide between rays), or
  // cells whose ray saw into cover (a mouth: its edges are worth knowing), 4 more rays per cell.
  if (refineMax > 0) {
    const want = []
    const at = (i, j) => j * opts.cols + i
    for (let j = 0; j < opts.rows; j++) {
      for (let i = 0; i < opts.cols; i++) {
        const k = at(i, j)
        if (store.end[k] === END.SKIPPED) continue
        let edge = !Number.isNaN(store.entry[k])
        if (!edge) {
          for (const [ni, nj] of [[i + 1, j], [i, j + 1]]) {
            if (ni >= opts.cols || nj >= opts.rows) continue
            const a = store.dist[k]
            const b = store.dist[at(ni, nj)]
            if (Math.abs(a - b) > Math.max(4, 0.5 * Math.min(a, b))) edge = true
          }
        }
        if (edge) want.push([i, j, Math.min(store.dist[k], 999)])
      }
    }
    // The nearest edges first: a near gap is one she could walk to.
    want.sort((a, b) => a[2] - b[2])
    for (const [i, j] of want) {
      if (store.n + 4 > gridN + refineMax || budget.left < 2000) break
      for (const [fi, fj] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]) cast(i + fi, j + fj)
    }
  }

  const ends = new Array(END_NAMES.length).fill(0)
  for (let k = 0; k < store.n; k++) ends[store.end[k]]++
  return {
    id: ++observationSeq,
    at: new Date(started).toISOString(),
    camera,
    grid: gridN,
    rays: store.n,
    store,
    names: ctx.names,
    eye_in_water: ctx.eyeInWater,
    eye_covered: covered(Math.floor(eye.x), Math.floor(eye.y), Math.floor(eye.z)) === true,
    light_known: Boolean(world.light) && store.light.some((v) => v >= 0),
    ends: Object.fromEntries(END_NAMES.map((n, i) => [n, ends[i]])),
    steps: budget.used,
    truncated: ends[END.SKIPPED] > 0,
    ms: Date.now() - started
  }
}

// ---- the bot's world -------------------------------------------------------------------

// A world lookup over mineflayer's loaded chunks, by block state id (no Block objects on the way: a
// view is hundreds of thousands of lookups). Columns are cached for one view.
export function botWorld (bot) {
  const registry = bot.registry
  const table = classTable(registry)
  const columns = new Map()
  const column = (x, z) => {
    const cx = x >> 4
    const cz = z >> 4
    const key = cx * 65536 + cz
    let col = columns.get(key)
    if (col === undefined) {
      col = bot.world.getColumn(cx, cz) ?? null
      columns.set(key, col)
    }
    return col
  }
  const pos = { x: 0, y: 0, z: 0 }
  const stateId = (x, y, z) => {
    const col = column(x, z)
    if (!col) return -1
    pos.x = x & 15; pos.y = y; pos.z = z & 15
    return col.getBlockStateId(pos)
  }
  return {
    minY: bot.game?.minY ?? 0,
    maxY: (bot.game?.minY ?? 0) + (bot.game?.height ?? 256),
    cls (x, y, z) {
      const id = stateId(x, y, z)
      if (id < 0) return UNLOADED
      return table.cls(id)
    },
    name (x, y, z) {
      const id = stateId(x, y, z)
      return id < 0 ? null : table.name(id)
    },
    light (x, y, z) {
      const col = column(x, z)
      if (!col?.getSkyLight) return null
      pos.x = x & 15; pos.y = y; pos.z = z & 15
      try {
        return { sky: col.getSkyLight(pos), block: col.getBlockLight(pos) }
      } catch {
        return null
      }
    }
  }
}

const tables = new WeakMap()
function classTable (registry) {
  if (tables.has(registry)) return tables.get(registry)
  const classes = new Map()
  const names = new Map()
  const blockFor = (id) => registry.blocksByStateId?.[id] ?? null
  const table = {
    cls (id) {
      let c = classes.get(id)
      if (c === undefined) {
        const b = blockFor(id)
        c = b ? classify({ name: b.name, boundingBox: b.boundingBox, waterlogged: waterlogged(b, id) }) : CLS.SOLID
        classes.set(id, c)
      }
      return c
    },
    name (id) {
      let n = names.get(id)
      if (n === undefined) {
        n = blockFor(id)?.name ?? 'unknown'
        names.set(id, n)
      }
      return n
    }
  }
  tables.set(registry, table)
  return table
}

// Whether this state of the block is waterlogged (seagrass-like plants in water are water to the eye).
function waterlogged (block, stateId) {
  const states = block.states
  if (!states?.length || !states.some((s) => s.name === 'waterlogged')) return false
  // States are laid out in order; each property's index is the state offset divided by the product
  // of the sizes of the properties after it.
  let offset = stateId - block.minStateId
  for (let i = states.length - 1; i >= 0; i--) {
    const s = states[i]
    const size = s.num_values ?? s.values?.length ?? 2
    const value = offset % size
    offset = Math.floor(offset / size)
    if (s.name === 'waterlogged') return s.type === 'bool' ? value === 0 : (s.values?.[value] === 'true')
  }
  return false
}

// The bot's camera: her eyes, where she faces.
export function botCamera (bot) {
  const p = bot.entity.position
  return { eye: { x: p.x, y: p.y + (bot.entity.eyeHeight ?? 1.62), z: p.z }, yaw: bot.entity.yaw, pitch: bot.entity.pitch }
}
