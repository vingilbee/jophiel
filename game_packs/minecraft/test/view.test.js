import assert from 'node:assert/strict'
import { test } from 'node:test'

import { CLS, END, UNLOADED, cameraBasis, classify, observe, project, verticalFov } from '../src/view.js'
import { readScene, groundBetween } from '../src/scene.js'
import { Landmarks } from '../src/landmarks.js'
import { approachSpots, nearestStandable, spotProblem } from '../src/navigation.js'

// A world by hand: stone up to y=63 (she stands at y=64), air above, edits on top; `unloaded(x, y, z)`
// marks terrain the client doesn't have.
const EMPTY = new Set(['air', 'cave_air', 'short_grass', 'torch', 'water', 'lava'])
function world ({ unloaded = () => false, light = null } = {}) {
  const edits = new Map()
  const key = (x, y, z) => `${x},${y},${z}`
  const nameAt = (x, y, z) => edits.get(key(x, y, z)) ?? (y <= 63 ? 'stone' : 'air')
  const api = {
    minY: -64,
    maxY: 320,
    put (name, x0, y0, z0, x1 = x0, y1 = y0, z1 = z0) {
      for (let x = Math.min(x0, x1); x <= Math.max(x0, x1); x++) {
        for (let y = Math.min(y0, y1); y <= Math.max(y0, y1); y++) {
          for (let z = Math.min(z0, z1); z <= Math.max(z0, z1); z++) edits.set(key(x, y, z), name)
        }
      }
      return api
    },
    cls (x, y, z) {
      if (unloaded(x, y, z)) return UNLOADED
      const name = nameAt(x, y, z)
      return classify({ name, boundingBox: EMPTY.has(name) ? 'empty' : 'block' })
    },
    name: (x, y, z) => (unloaded(x, y, z) ? null : nameAt(x, y, z)),
    ...(light ? { light } : {})
  }
  return api
}

const FEET = { x: 0.5, y: 64, z: 0.5 }
const EYE = { x: 0.5, y: 65.62, z: 0.5 }
const NORTH = 0
const LEVEL = -5 * Math.PI / 180

// A cliff 40 blocks north with a cave mouth in it: 5 wide, 4 high, a tunnel 15 deep, its floor going
// down one block in three from 4 blocks in.
function cliffWithCave (w = world()) {
  w.put('stone', -25, 64, -60, 25, 85, -40)
  w.put('air', -2, 64, -55, 2, 67, -40)
  for (let z = -44; z >= -55; z--) w.put('air', -2, 64 - Math.floor((-44 - z) / 3) - 1, z, 2, 67, z)
  return w
}

test('classes: solid, partial, trees, glass, water, plants', () => {
  assert.equal(classify({ name: 'stone', boundingBox: 'block' }), CLS.SOLID)
  assert.equal(classify({ name: 'oak_stairs', boundingBox: 'block' }), CLS.SOLID)
  assert.equal(classify({ name: 'oak_leaves', boundingBox: 'block' }), CLS.TREE)
  assert.equal(classify({ name: 'stripped_oak_log', boundingBox: 'block' }), CLS.SOLID)
  assert.equal(classify({ name: 'glass_pane', boundingBox: 'block' }), CLS.GLASS)
  assert.equal(classify({ name: 'white_stained_glass', boundingBox: 'block' }), CLS.GLASS)
  assert.equal(classify({ name: 'tinted_glass', boundingBox: 'block' }), CLS.SOLID)
  assert.equal(classify({ name: 'water', boundingBox: 'empty' }), CLS.WATER)
  assert.equal(classify({ name: 'seagrass', boundingBox: 'empty' }), CLS.WATER)
  assert.equal(classify({ name: 'short_grass', boundingBox: 'empty' }), CLS.OPEN)
  assert.equal(classify(null), UNLOADED)
})

test('camera geometry: forward is north at yaw 0, the vertical field follows the aspect, projection round-trips', () => {
  const b = cameraBasis(0, 0)
  assert.deepEqual(b.forward.map((v) => Math.round(v * 1000) / 1000), [-0, 0, -1])
  assert.deepEqual(b.right.map((v) => Math.round(v)), [1, 0, -0])
  assert.ok(Math.abs(verticalFov(100, 64, 36) - 67.7) < 0.2)
  const obs = observe(world(), EYE, NORTH, LEVEL, { cols: 16, rows: 9, refineRays: 0 })
  const p = project(obs.camera, { x: EYE.x, y: EYE.y, z: EYE.z - 20 })
  assert.ok(Math.abs(p.i - 8) < 0.01, JSON.stringify(p))
  assert.equal(project(obs.camera, { x: EYE.x, y: EYE.y, z: EYE.z + 20 }), null)
})

test('flat ground: rays below the horizon hit the ground, above it reach the range with no claim of sky', () => {
  const obs = observe(world(), EYE, NORTH, 0, { refineRays: 0 })
  assert.equal(obs.rays, 64 * 36)
  assert.ok(obs.ends.surface > 0 && obs.ends.clear > 0, JSON.stringify(obs.ends))
  assert.equal(obs.ends.sky, 0) // the top of the world is 250 blocks up: past the range
  assert.equal(obs.ends.unknown, 0)
  const scene = readScene(obs, world(), FEET)
  assert.deepEqual(scene.openings, [])
  assert.deepEqual(scene.structures, [])
  console.log(`flat view: ${obs.ms} ms, ${obs.steps} lookups`)
})

test('a cave mouth in a cliff, seen from open ground: where, how big, the floor going down inside', () => {
  const w = cliffWithCave()
  // From 40 m away, low down: where it is and how big; the floor sloping away inside isn't in sight
  // (the back wall is).
  const obs = observe(w, EYE, NORTH, LEVEL)
  const far = readScene(obs, w, FEET).openings
  assert.equal(far.length, 1, JSON.stringify(far))
  const o = far[0]
  assert.equal(o.distance, 40)
  assert.equal(o.z, -40)
  assert.ok(o.x >= -2 && o.x <= 2)
  assert.equal(o.y, 64)
  assert.equal(o.side, 'ahead')
  assert.ok(o.width >= 4 && o.width <= 6, `width ${o.width}`)
  assert.ok(o.height >= 3, `height ${o.height}`)
  assert.equal(o.roof, 'stone')
  assert.equal(o.natural, true)
  assert.ok(o.seen_in >= 6, `seen in ${o.seen_in}`)
  assert.equal(o.floor_seen, false)
  assert.deepEqual(o.stand, { x: o.x, y: 64, z: -40 })
  // From the mouth, looking in and down, the floor and its slope are.
  const eye = { x: 0.5, y: 65.62, z: -37.5 }
  const near = readScene(observe(w, eye, NORTH, -20 * Math.PI / 180), w, { x: 0.5, y: 64, z: -37.5 }).openings
  assert.equal(near.length, 1, JSON.stringify(near))
  assert.equal(near[0].floor_seen, true)
  assert.ok(near[0].slope <= -2, `slope ${near[0].slope}`)
  console.log(`cave view: ${obs.ms} ms, ${obs.rays} rays`)
})

test('the mouth is seen whatever is over her head: under trees, under an overhang', () => {
  const trees = cliffWithCave().put('oak_leaves', -3, 68, -3, 3, 70, 3)
  const t = readScene(observe(trees, EYE, NORTH, LEVEL), trees, FEET)
  assert.equal(t.openings.length, 1)
  // An overhang right over her (rock 2 blocks above her head, open in front).
  const roofed = cliffWithCave().put('stone', -4, 68, -2, 4, 70, 4)
  const r = readScene(observe(roofed, EYE, NORTH, LEVEL), roofed, FEET)
  assert.equal(r.openings.length, 1, JSON.stringify(r.openings))
  assert.equal(r.openings[0].z, -40)
})

test('across a ravine: the mouth is seen and the drop between is said, not a way across', () => {
  const w = cliffWithCave().put('air', -25, 40, -25, 25, 63, -18)
  const scene = readScene(observe(w, EYE, NORTH, LEVEL), w, FEET)
  assert.equal(scene.openings.length, 1)
  const b = scene.openings[0].between
  assert.ok(b.drop >= 20, JSON.stringify(b))
  assert.ok(b.drop_at >= 17 && b.drop_at <= 26, JSON.stringify(b))
})

test('a shallow recess in the cliff is not an opening', () => {
  const w = world().put('stone', -25, 64, -60, 25, 85, -40).put('air', -2, 64, -41, 2, 67, -40)
  const scene = readScene(observe(w, EYE, NORTH, LEVEL), w, FEET)
  assert.deepEqual(scene.openings, [])
})

test('unloaded terrain ahead is unknown, not empty', () => {
  const w = world({ unloaded: (x, y, z) => z < -50 })
  const obs = observe(w, EYE, NORTH, 0, { refineRays: 0 })
  assert.ok(obs.ends.unknown > 100, JSON.stringify(obs.ends))
  const scene = readScene(obs, w, FEET)
  assert.ok(scene.unknown.some((u) => u.side === 'ahead' && u.from >= 50 && u.from <= 60), JSON.stringify(scene.unknown))
})

test('a structure partly behind trees: what it is made of, what was seen, no door', () => {
  const w = world()
  w.put('oak_planks', -4, 64, -30, 4, 68, -30) // its front wall
  w.put('oak_planks', -4, 64, -38, 4, 68, -30) // and the rest of it (a solid block of planks)
  w.put('glass_pane', 0, 66, -30, 1, 67, -30) // a 2 × 2 window (one pane at 30 m falls between rays)
  w.put('oak_leaves', -6, 64, -20, -1, 69, -19) // a tree in front of the left half
  w.put('oak_log', -3, 64, -21, -3, 67, -21)
  const scene = readScene(observe(w, EYE, NORTH, LEVEL), w, FEET)
  assert.equal(scene.structures.length, 1, JSON.stringify(scene.structures))
  const s = scene.structures[0]
  assert.equal(s.materials[0], 'oak_planks')
  assert.equal(s.behind_trees, true)
  assert.equal(s.door, false)
  assert.equal(s.windows, true)
  assert.deepEqual(scene.openings, [])
})

test('inside a tunnel: where the space goes on', () => {
  const w = world().put('stone', -20, 64, -80, 20, 90, 20).put('air', -1, 64, -70, 1, 66, 2)
  const eye = { x: 0.5, y: 65.62, z: 0.5 }
  const obs = observe(w, eye, NORTH, LEVEL)
  assert.equal(obs.eye_covered, true)
  const scene = readScene(obs, w, FEET)
  assert.equal(scene.openings.length, 0)
  assert.ok(scene.passages.length >= 1, JSON.stringify(scene.passages))
  const p = scene.passages[0]
  assert.equal(p.side, 'ahead')
  assert.ok(p.far >= 60, JSON.stringify(p))
  assert.ok(p.stand && p.stand.y === 64, JSON.stringify(p))
})

test('water seen from above: a surface, with how deep where the bottom shows', () => {
  const w = world().put('water', -10, 61, -30, 10, 63, -15)
  const scene = readScene(observe(w, EYE, NORTH, -12 * Math.PI / 180), w, FEET)
  assert.equal(scene.water.length, 1)
  assert.equal(scene.water[0].bottom_seen, true)
  assert.ok(scene.water[0].deepest_seen <= 3)
})

test('darkness: far unlit surfaces keep their shape but not their name', () => {
  const w = cliffWithCave(world({ light: (x, y, z) => (z <= -42 ? { sky: 0, block: 0 } : { sky: 15, block: 0 }) }))
  const obs = observe(w, EYE, NORTH, LEVEL)
  let dark = 0
  for (let k = 0; k < obs.rays; k++) if (obs.store.end[k] === END.SURFACE && obs.store.name[k] === -2) dark++
  assert.ok(dark > 0)
  const scene = readScene(obs, w, FEET)
  assert.equal(scene.openings.length, 1)
  assert.ok(scene.openings[0].dark >= 0.5)
})

test('a bounded view: the work budget cuts it short and says so', () => {
  const obs = observe(world(), EYE, NORTH, 0, { maxSteps: 5000, refineRays: 0 })
  assert.equal(obs.truncated, true)
  assert.ok(obs.ends.skipped > 0)
})

test('landmarks: the same mouth keeps its id from another spot; a filled-in mouth is gone; ids are never reused', () => {
  const w = cliffWithCave()
  const marks = new Landmarks()
  const a = marks.update(readScene(observe(w, EYE, NORTH, LEVEL), w, FEET), { dimension: 'overworld', world: w })
  assert.deepEqual(a.added, ['L1'])
  const eye2 = { x: 6.5, y: 65.62, z: -10.5 }
  const s2 = readScene(observe(w, eye2, 0.15, LEVEL), w, { x: 6.5, y: 64, z: -10.5 })
  const b = marks.update(s2, { dimension: 'overworld', world: w })
  assert.deepEqual(b.added, [])
  assert.deepEqual(b.visible, ['L1'])
  assert.equal(marks.get('l1').seen_count, 2)
  // Turned away: remembered. Filled in: gone.
  const away = marks.update(readScene(observe(w, EYE, Math.PI, LEVEL), w, FEET), { dimension: 'overworld', world: w })
  assert.deepEqual(away.visible, [])
  assert.equal(marks.get('L1').status, 'remembered')
  w.put('cobblestone', -2, 64, -55, 2, 67, -40)
  marks.update(readScene(observe(w, EYE, Math.PI, LEVEL), w, FEET), { dimension: 'overworld', world: w })
  assert.equal(marks.get('L1').status, 'gone')
  // A new mouth elsewhere gets a new id, even where the old one was.
  w.put('air', -2, 64, -55, 2, 67, -40)
  const c = marks.update(readScene(observe(w, EYE, NORTH, LEVEL), w, FEET), { dimension: 'overworld', world: w })
  assert.deepEqual(c.added, ['L2'])
  // Another dimension never matches.
  const d = marks.update(readScene(observe(w, EYE, NORTH, LEVEL), w, FEET), { dimension: 'the_nether', world: w })
  assert.deepEqual(d.added, ['L3'])
  // Saved and loaded: the counter goes on.
  const again = new Landmarks(JSON.parse(JSON.stringify(marks.toJSON())))
  assert.equal(again.get('L2').status, 'remembered')
  const e = again.update({ openings: [], passages: [], structures: [{ kind: 'structure', x: 99, y: 64, z: 99, distance: 5, confidence: 'possible', box: { x0: 99, x1: 101, y0: 64, y1: 66, z0: 99, z1: 101 } }], water: [], lava: [] }, { dimension: 'overworld' })
  assert.deepEqual(e.added, ['L4'])
})

test('spots: what is wrong with one, the nearest place to stand, and where to stand at a mouth', () => {
  const w = cliffWithCave()
  assert.equal(spotProblem(w, 0, 64, 0), null)
  assert.match(spotProblem(w, 0, 63, 0), /inside stone/)
  assert.match(spotProblem(w, 0, 70, 0), /in the air/)
  assert.deepEqual(nearestStandable(w, 0, 70, 0), { x: 0, y: 64, z: 0 }) // a guessed height: same column
  assert.deepEqual(nearestStandable(w, 10, 70, -45), null) // deep inside the cliff
  const marks = new Landmarks()
  marks.update(readScene(observe(w, EYE, NORTH, LEVEL), w, FEET), { dimension: 'overworld', world: w })
  const spots = approachSpots(w, marks.get('L1'), { x: 0, y: 64, z: 0 })
  assert.ok(spots.length >= 3)
  for (const s of spots) assert.equal(spotProblem(w, s.x, s.y, s.z), null)
  assert.deepEqual(spots[0], { x: marks.get('L1').x, y: 64, z: -40 }) // the mouth itself first
})

test('ground between two spots: water and a drop on the straight line', () => {
  const w = world().put('water', -1, 63, -8, 1, 63, -6).put('air', -1, 50, -15, 1, 63, -12)
  const g = groundBetween(w, { x: 0.5, y: 64, z: 0.5 }, { x: 0.5, y: 64, z: -20.5 })
  assert.ok(g.water >= 2)
  assert.equal(g.drop, 14)
})

test('images: material and depth from the same rays, the same size, labels drawn', async () => {
  const { renderView } = await import('../src/view_image.js')
  const w = cliffWithCave()
  const obs = observe(w, EYE, NORTH, LEVEL)
  const material = renderView(obs, { layer: 'material', labels: [{ id: 'L1', x: 0, y: 64, z: -40 }], scale: 4 })
  const depth = renderView(obs, { layer: 'depth', scale: 4 })
  for (const png of [material, depth]) {
    assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
    assert.equal(png.readUInt32BE(16), 64 * 4)
    assert.equal(png.readUInt32BE(20), 36 * 4)
  }
})
