// What a view (src/view.js) shows, grouped: openings into covered space (a cave mouth, a tunnel, a
// doorway), passages where the space she is in goes on, possible structures, water, lava, and where
// the view runs into terrain that isn't loaded. Counted by code from the rays; each group keeps its
// evidence (how many rays, how far in they saw, what they hit) so a wrong label never hides what was
// measured. Names say what is likely ("a cave or tunnel mouth?"), never more than the rays show: a
// recess, a gap between surfaces and a deep cave can look alike from one place, and what is past a
// bend is not in view until she looks from somewhere else.
import { CLS, END, UP } from './view.js'
import { bearingWord, relativeBearing } from './state.js'

const MIN_INTO = 2.5 // blocks a ray must see into covered space to count (less: an overhang's lip)
const PASSAGE_FAR = 14 // inside covered space: rays this far show where it goes on
const WATER_MIN_RAYS = 10
const STRUCTURE_MIN_RAYS = 4

// Materials of things made, not grown: evidence of a structure (never proof: a dungeon is mossy
// cobblestone, a village path is dirt_path, a ruin is half of each).
const BUILT_SUFFIX = /(_planks|_stairs|_slab|_fence|_fence_gate|_door|_trapdoor|_wool|_carpet|_concrete|_bed|_wall|_bricks|glazed_terracotta|_pane|_stained_glass|_banner|_lantern|_sign|_button|_pressure_plate)$/
const BUILT_PREFIX = /^(polished_|chiseled_|cut_|stripped_|smooth_(stone|sandstone|red_sandstone|quartz)|waxed_)/
const BUILT = new Set(['cobblestone', 'mossy_cobblestone', 'bricks', 'bookshelf', 'crafting_table', 'furnace',
  'blast_furnace', 'smoker', 'chest', 'trapped_chest', 'barrel', 'hay_block', 'glass', 'dirt_path', 'bell', 'ladder',
  'lantern', 'iron_bars', 'scaffolding', 'composter', 'lectern', 'cartography_table', 'fletching_table',
  'smithing_table', 'loom', 'stonecutter', 'grindstone', 'anvil', 'cauldron', 'brewing_stand', 'enchanting_table',
  'spawner', 'quartz_block', 'purpur_block', 'end_stone_bricks', 'terracotta', 'white_concrete', 'iron_block',
  'gold_block', 'emerald_block', 'diamond_block', 'mud_bricks', 'packed_mud', 'tinted_glass', 'sea_lantern'])
export function built (name) {
  if (!name) return false
  if (name === 'smooth_basalt') return false // geodes
  return BUILT.has(name) || BUILT_SUFFIX.test(name) || BUILT_PREFIX.test(name)
}

const round = Math.round
const words = (name) => String(name).replace(/_/g, ' ')

// Union-find over ray indices.
function groups (items, linked) {
  const parent = new Map(items.map((k) => [k, k]))
  const find = (k) => {
    while (parent.get(k) !== k) {
      parent.set(k, parent.get(parent.get(k)))
      k = parent.get(k)
    }
    return k
  }
  linked((a, b) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent.set(ra, rb)
  })
  const out = new Map()
  for (const k of items) {
    const r = find(k)
    if (!out.has(r)) out.set(r, [])
    out.get(r).push(k)
  }
  return [...out.values()]
}

// Rays linked when their cells (from cellOf) are within `reach` blocks sideways and `reachY` up/down.
function spatialGroups (rays, cellOf, reach = 2, reachY = 3) {
  const buckets = new Map()
  const key = (x, y, z) => `${x},${y},${z}`
  for (const k of rays) {
    const [x, y, z] = cellOf(k)
    const id = key(x, y, z)
    if (!buckets.has(id)) buckets.set(id, [])
    buckets.get(id).push(k)
  }
  return groups(rays, (union) => {
    for (const [id, members] of buckets) {
      for (let m = 1; m < members.length; m++) union(members[0], members[m])
      const [x, y, z] = id.split(',').map(Number)
      for (let dx = -reach; dx <= reach; dx++) {
        for (let dy = -reachY; dy <= reachY; dy++) {
          for (let dz = -reach; dz <= reach; dz++) {
            const other = buckets.get(key(x + dx, y + dy, z + dz))
            if (other) union(members[0], other[0])
          }
        }
      }
    }
  })
}

// Grid rays linked when next to each other in the image (4-neighbours).
function imageGroups (obs, rays) {
  const { cols } = obs.camera
  const set = new Set(rays)
  return groups(rays, (union) => {
    for (const k of rays) {
      if (k % cols !== cols - 1 && set.has(k + 1)) union(k, k + 1)
      if (set.has(k + cols)) union(k, k + cols)
    }
  })
}

const cell = (arr, k) => [arr[k * 3], arr[k * 3 + 1], arr[k * 3 + 2]]

// Where something is from her: distance, side (relative to where she faces) and height difference.
export function placeFrom (camera, feetY, p) {
  const dx = p.x + 0.5 - camera.eye.x
  const dz = p.z + 0.5 - camera.eye.z
  return {
    distance: round(Math.hypot(dx, p.y + 0.5 - camera.eye.y, dz)),
    side: bearingWord(relativeBearing(camera.yaw, dx, dz)),
    dy: round(p.y - feetY)
  }
}

export function standable (world, x, y, z) {
  const below = world.cls(x, y - 1, z)
  return (below === CLS.SOLID || below === CLS.TREE || below === CLS.GLASS) &&
    world.cls(x, y, z) === CLS.OPEN && world.cls(x, y + 1, z) === CLS.OPEN
}

// The standing spot under an open cell: down through open air (at most `down`) to ground.
function groundUnder (world, x, y, z, down = 4) {
  for (let d = 0; d <= down; d++) {
    if (standable(world, x, y - d, z)) return { x, y: y - d, z }
    if (world.cls(x, y - d, z) !== CLS.OPEN) return null
  }
  return null
}

function roofOver (world, x, y, z, scan = 16) {
  for (let d = 1; d <= scan; d++) {
    const c = world.cls(x, y + d, z)
    if (c === CLS.SOLID || c === CLS.GLASS) return { name: world.name(x, y + d, z), up: d }
    if (c < 0) return null
  }
  return null
}

// The ground along the straight line between two spots, as far as it's loaded: the lowest ground
// under the line (a drop: a ravine, a pit), water or lava on the way. Only the straight line: a way
// round may exist.
export function groundBetween (world, from, to) {
  const len = Math.hypot(to.x - from.x, to.z - from.z)
  const top = Math.max(from.y, to.y) + 4
  const low = Math.min(from.y, to.y)
  const out = { drop: 0, drop_at: null, deeper: false, water: 0, lava: 0, unknown: 0, samples: 0 }
  for (let s = 1; s < len; s++) {
    const x = Math.floor(from.x + (to.x - from.x) * s / len)
    const z = Math.floor(from.z + (to.z - from.z) * s / len)
    out.samples++
    let found = null
    for (let y = top; y >= low - 32; y--) {
      const c = world.cls(x, y, z)
      if (c < 0) { found = 'unknown'; break }
      if (c === CLS.OPEN || c === CLS.GLASS) continue
      found = c === CLS.WATER ? 'water' : c === CLS.LAVA ? 'lava' : 'ground'
      const depth = low - (y + 1)
      if (found === 'ground' && depth > out.drop) { out.drop = depth; out.drop_at = round(s) }
      break
    }
    if (found === null) { out.deeper = true; out.drop = Math.max(out.drop, 32); out.drop_at ??= round(s) }
    else if (found === 'water') out.water++
    else if (found === 'lava') out.lava++
    else if (found === 'unknown') out.unknown++
  }
  return out
}

function openings (obs, world, feetY) {
  const { store, camera } = obs
  const rays = []
  for (let k = 0; k < store.n; k++) {
    if (store.end[k] !== END.SURFACE || Number.isNaN(store.entry[k])) continue
    if (store.dist[k] - store.entry[k] >= MIN_INTO) rays.push(k)
  }
  const out = []
  for (const g of spatialGroups(rays, (k) => cell(store.entryCell, k))) {
    const seenIn = Math.max(...g.map((k) => store.dist[k] - store.entry[k]))
    if (g.length < 2 && seenIn < 4) continue
    const cells = g.map((k) => cell(store.entryCell, k))
    const minY = Math.min(...cells.map((c) => c[1]))
    const lowCells = cells.filter((c) => c[1] <= minY + 1)
    const cx = lowCells.reduce((s, c) => s + c[0], 0) / lowCells.length
    const cz = lowCells.reduce((s, c) => s + c[2], 0) / lowCells.length
    const [ax, ay, az] = lowCells.sort((a, b) => Math.hypot(a[0] - cx, a[2] - cz) - Math.hypot(b[0] - cx, b[2] - cz))[0]
    // Width across her line of sight, height up the mouth.
    const hx = ax + 0.5 - camera.eye.x
    const hz = az + 0.5 - camera.eye.z
    const hn = Math.hypot(hx, hz) || 1
    const across = cells.map((c) => (c[0] - ax) * (-hz / hn) + (c[2] - az) * (hx / hn))
    const width = round(Math.max(...across) - Math.min(...across)) + 1
    const height = Math.max(...cells.map((c) => c[1])) - minY + 1
    const roof = roofOver(world, ax, ay, az)
    const floorRays = g.filter((k) => store.normal[k] === UP)
    const stand = groundUnder(world, ax, ay, az)
    // How the floor inside goes: the open cells rays ended in that have ground under them (a floor hit,
    // or the foot of a step's riser).
    const floorYs = g.map((k) => cell(store.open, k))
      .filter(([x, y, z]) => { const c = world.cls(x, y - 1, z); return c === CLS.SOLID || c === CLS.TREE })
      .map(([, y]) => y)
    const base = stand?.y ?? ay
    const withLight = g.filter((k) => store.light[k * 2] >= 0)
    const dark = withLight.filter((k) => store.light[k * 2] === 0 && store.light[k * 2 + 1] === 0).length
    out.push({
      kind: 'opening',
      x: ax, y: base, z: az,
      stand,
      facing: { x: round(hx / hn * 100) / 100, z: round(hz / hn * 100) / 100 },
      width,
      height,
      roof: roof?.name ?? null,
      natural: !built(roof?.name),
      seen_in: round(seenIn),
      floor_seen: floorRays.length > 0 || floorYs.length > 0,
      slope: floorYs.length ? Math.min(...floorYs) - base : 0,
      rise: floorYs.length ? Math.max(...floorYs) - base : 0,
      dark: withLight.length ? round(dark / withLight.length * 100) / 100 : null,
      rays: g.length,
      confidence: g.length >= 6 && seenIn >= 5 ? 'clear' : 'possible',
      ...placeFrom(camera, feetY, { x: ax, y: base, z: az })
    })
  }
  return out
}

// Inside covered space: the parts of the view that go on far (where the space leads).
function passages (obs, world, feetY) {
  const { store, camera } = obs
  if (!obs.eye_covered) return []
  const rays = []
  for (let k = 0; k < obs.grid; k++) {
    const far = store.end[k] === END.SURFACE ? store.under[k] && Number.isNaN(store.entry[k]) && store.dist[k] >= PASSAGE_FAR
      : store.end[k] === END.CLEAR || store.end[k] === END.UNKNOWN ? store.dist[k] >= PASSAGE_FAR : false
    if (far) rays.push(k)
  }
  const out = []
  for (const g of imageGroups(obs, rays)) {
    if (g.length < 4) continue
    const floors = g.filter((k) => store.normal[k] === UP && store.end[k] === END.SURFACE)
    const pick = (floors.length ? floors : g).reduce((a, b) => (store.dist[b] > store.dist[a] ? b : a))
    let spot = null
    if (store.normal[pick] === UP) {
      const [x, y, z] = cell(store.hit, pick)
      if (standable(world, x, y + 1, z)) spot = { x, y: y + 1, z }
    }
    const [ox, oy, oz] = cell(store.open, pick)
    spot ??= groundUnder(world, ox, oy, oz, 8)
    const anchor = spot ?? { x: ox, y: oy, z: oz }
    const beyond = g.some((k) => store.end[k] !== END.SURFACE)
    const dists = g.map((k) => store.dist[k])
    out.push({
      kind: 'passage',
      x: anchor.x, y: anchor.y, z: anchor.z,
      stand: spot,
      far: round(Math.max(...dists)),
      near: round(Math.min(...dists)),
      beyond, // some rays ran out of range or into unloaded terrain: it goes on further than seen
      floor_seen: floors.length > 0,
      rays: g.length,
      confidence: g.length >= 8 ? 'clear' : 'possible',
      ...placeFrom(camera, feetY, anchor)
    })
  }
  return out.sort((a, b) => b.rays - a.rays).slice(0, 4)
}

function structures (obs, world, feetY) {
  const { store, camera, names } = obs
  const rays = []
  const glassOf = new Map()
  for (let k = 0; k < store.n; k++) {
    if (store.end[k] === END.SURFACE && store.name[k] >= 0 && built(names[store.name[k]])) rays.push(k)
    else if (!Number.isNaN(store.glassAt[k])) {
      rays.push(k)
      glassOf.set(k, true)
    }
  }
  const at = (k) => (glassOf.has(k) ? cell(store.glass, k) : cell(store.hit, k))
  const out = []
  for (const g of spatialGroups(rays, at, 3, 3)) {
    const voxels = new Map()
    for (const k of g) {
      const c = at(k)
      voxels.set(c.join(','), c)
    }
    if (g.length < STRUCTURE_MIN_RAYS || voxels.size < 3) continue
    const materials = new Map()
    const count = (name) => materials.set(name, (materials.get(name) ?? 0) + 1)
    for (const k of g) {
      if (!glassOf.has(k)) count(names[store.name[k]])
      // Glass the ray passed through on the way (a window), whatever it hit behind.
      if (!Number.isNaN(store.glassAt[k])) count(world.name(store.glass[k * 3], store.glass[k * 3 + 1], store.glass[k * 3 + 2]) ?? 'glass')
    }
    const cs = [...voxels.values()]
    const box = {
      x0: Math.min(...cs.map((c) => c[0])), x1: Math.max(...cs.map((c) => c[0])),
      y0: Math.min(...cs.map((c) => c[1])), y1: Math.max(...cs.map((c) => c[1])),
      z0: Math.min(...cs.map((c) => c[2])), z1: Math.max(...cs.map((c) => c[2]))
    }
    const nearest = g.reduce((a, b) => (store.dist[b] < store.dist[a] ? b : a))
    const [nx, ny, nz] = at(nearest)
    // Partly behind trees: grid neighbours of its rays that hit leaves or logs nearer to her.
    const { cols } = camera
    let behind = 0
    for (const k of g) {
      if (k >= obs.grid) continue
      for (const n of [k - 1, k + 1, k - cols, k + cols]) {
        if (n < 0 || n >= obs.grid) continue
        if (store.cls[n] === CLS.TREE && store.end[n] === END.SURFACE && store.dist[n] < store.dist[k] - 1) behind++
      }
    }
    const names2 = [...materials].sort((a, b) => b[1] - a[1])
    const eye = camera.eye
    const inside = eye.x >= box.x0 - 1 && eye.x <= box.x1 + 2 && eye.z >= box.z0 - 1 && eye.z <= box.z1 + 2 &&
      eye.y >= box.y0 - 1 && eye.y <= box.y1 + 3
    out.push({
      kind: 'structure',
      x: nx, y: ny, z: nz,
      box,
      size: { w: box.x1 - box.x0 + 1, h: box.y1 - box.y0 + 1, d: box.z1 - box.z0 + 1 },
      materials: names2.slice(0, 4).map(([name]) => name),
      door: names2.some(([n]) => n.endsWith('_door')),
      windows: names2.some(([n]) => n.includes('glass')),
      behind_trees: behind >= 2,
      next_to_you: inside,
      evidence: cs.slice(0, 8).map(([x, y, z]) => ({ x, y, z })),
      rays: g.length,
      confidence: g.length >= 12 && voxels.size >= 8 ? 'clear' : 'possible',
      ...placeFrom(camera, feetY, { x: nx, y: ny, z: nz })
    })
  }
  return out.sort((a, b) => a.distance - b.distance).slice(0, 4)
}

function liquids (obs, feetY, which, minRays) {
  const { store, camera } = obs
  const rays = []
  for (let k = 0; k < obs.grid; k++) if (store.end[k] === END.SURFACE && store.cls[k] === which) rays.push(k)
  const out = []
  for (const g of imageGroups(obs, rays)) {
    if (g.length < minRays) continue
    const nearest = g.reduce((a, b) => (store.dist[b] < store.dist[a] ? b : a))
    const [x, y, z] = cell(store.hit, nearest)
    const xs = g.map((k) => store.hit[k * 3])
    const zs = g.map((k) => store.hit[k * 3 + 2])
    const across = round(Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs))) + 1
    const depths = g.map((k) => store.beneathDepth[k]).filter((d) => !Number.isNaN(d))
    out.push({
      kind: which === CLS.WATER ? 'water' : 'lava',
      x, y, z,
      across,
      deepest_seen: depths.length ? Math.max(...depths) : null,
      bottom_seen: depths.length > 0,
      rays: g.length,
      confidence: 'clear',
      ...placeFrom(camera, feetY, { x, y, z })
    })
  }
  return out.sort((a, b) => a.distance - b.distance).slice(0, 3)
}

// Where the view runs into terrain that isn't loaded, by third of the image.
function unknown (obs) {
  const { store, camera } = obs
  const thirds = [{ side: 'left', rays: 0, from: Infinity }, { side: 'ahead', rays: 0, from: Infinity }, { side: 'right', rays: 0, from: Infinity }]
  for (let k = 0; k < obs.grid; k++) {
    if (store.end[k] !== END.UNKNOWN) continue
    const t = thirds[Math.min(2, Math.floor((k % camera.cols) / camera.cols * 3))]
    t.rays++
    t.from = Math.min(t.from, store.dist[k])
  }
  return thirds.filter((t) => t.rays >= 3).map((t) => ({ ...t, from: round(t.from) }))
}

// Everything the view shows. `feet`: her feet ({x, y, z}).
export function readScene (obs, world, feet) {
  const feetY = Math.floor(feet.y)
  const scene = {
    openings: openings(obs, world, feetY).sort((a, b) => b.rays - a.rays).slice(0, 5),
    passages: passages(obs, world, feetY),
    structures: structures(obs, world, feetY),
    water: liquids(obs, feetY, CLS.WATER, WATER_MIN_RAYS),
    lava: liquids(obs, feetY, CLS.LAVA, 1),
    unknown: unknown(obs)
  }
  // The straight ground between her and each opening: a ravine in between is worth saying.
  const from = { x: feet.x, y: feetY, z: feet.z }
  for (const o of scene.openings) {
    if (o.distance < 4) continue
    const g = groundBetween(world, from, { x: o.x + 0.5, y: o.y, z: o.z + 0.5 })
    // A drop deeper than the 4 blocks walking goes down is worth saying.
    o.between = { drop: g.drop > 4 ? g.drop : 0, drop_at: g.drop > 4 ? g.drop_at : null, deeper: g.deeper,
      water: g.water >= 2, lava: g.lava > 0, unknown: g.unknown > 0 }
  }
  return scene
}

// ---- words ----

const deeper = (b) => (b.deeper ? '+' : '')

export function describeBetween (b) {
  if (!b) return null
  const parts = []
  if (b.drop) parts.push(`a drop of ${b.drop}${deeper(b)} m about ${b.drop_at} m out`)
  if (b.water) parts.push('water')
  if (b.lava) parts.push('lava')
  if (b.unknown) parts.push('unloaded ground')
  return parts.length ? `along the straight line to it: ${parts.join(', ')}` : null
}

export function describeFeature (f) {
  const where = `${f.distance} m ${f.side}${Math.abs(f.dy) >= 2 ? `, ${Math.abs(f.dy)} m ${f.dy > 0 ? 'above' : 'below'} you` : ''}`
  if (f.kind === 'opening') {
    const what = f.natural ? 'cave or tunnel mouth?' : 'way into a building? (doorway or window)'
    const size = `${f.width} wide, ${f.height}${f.height >= 2 ? '' : ' (low)'} high`
    const parts = [`opening (${what}): ${size}, under ${f.roof ? words(f.roof) : 'cover'}, ${where}, at (${f.x} ${f.y} ${f.z})`]
    if (f.floor_seen) {
      parts.push(f.slope <= -2 ? `floor seen inside, going down ${-f.slope} m` : f.rise >= 2 ? `floor seen inside, rising ${f.rise} m` : 'floor seen inside')
    } else {
      parts.push('no floor seen inside')
    }
    parts.push(`seen ${f.seen_in} m in, past that out of sight`)
    if (f.dark !== null && f.dark >= 0.5) parts.push('dark inside')
    const between = describeBetween(f.between)
    if (between) parts.push(between)
    return parts.join('; ')
  }
  if (f.kind === 'passage') {
    return `the space goes on ${f.side}: open for ${f.far}${f.beyond ? '+' : ''} m (from ${f.near} m), ` +
      `${f.floor_seen ? `floor seen out to (${f.x} ${f.y} ${f.z})` : `no floor seen (furthest open spot (${f.x} ${f.y} ${f.z}))`}` +
      `${Math.abs(f.dy) >= 2 ? `, ${Math.abs(f.dy)} m ${f.dy > 0 ? 'up' : 'down'}` : ''}`
  }
  if (f.kind === 'structure') {
    const parts = [`possible structure: ${f.materials.map(words).join(', ')}; ${f.size.w} × ${f.size.h} × ${f.size.d} m of it seen, ${where}`]
    if (f.next_to_you) parts.push('you are in it or right next to it')
    if (f.behind_trees) parts.push('partly behind trees')
    parts.push(f.door ? 'a door seen' : 'no door seen')
    if (f.windows) parts.push('glass seen')
    return parts.join('; ')
  }
  if (f.kind === 'water') {
    return `water: ${where}, about ${f.across} m across in view` +
      (f.bottom_seen ? `, bottom seen ${f.deepest_seen} m down at most` : ', no bottom seen within 12 m')
  }
  if (f.kind === 'lava') return `lava: ${where}`
  return `${f.kind}: ${where}`
}
