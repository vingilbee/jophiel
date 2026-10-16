import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeSurroundings, overhead, surroundings, walk } from '../src/surroundings.js'

// A world by hand: stone up to y=63 (she stands at y=64), air above, plus edits.
function world (edits = {}) {
  const key = (x, y, z) => `${x},${y},${z}`
  const set = new Map()
  const api = {
    put (name, x0, y0, z0, x1 = x0, y1 = y0, z1 = z0) {
      for (let x = Math.min(x0, x1); x <= Math.max(x0, x1); x++) {
        for (let y = Math.min(y0, y1); y <= Math.max(y0, y1); y++) {
          for (let z = Math.min(z0, z1); z <= Math.max(z0, z1); z++) set.set(key(x, y, z), name)
        }
      }
      return api
    },
    at (x, y, z) {
      if (y < -10 || y > 200) return null
      const name = set.get(key(x, y, z)) ?? (y <= 63 ? 'stone' : 'air')
      const box = name === 'air' || name === 'water' || name === 'lava' || name === 'short_grass' ? 'empty' : 'block'
      return { name, boundingBox: box }
    }
  }
  return api
}
const FEET = { x: 0.5, y: 64, z: 0.5 }
const NORTH = { x: 0, z: -1 }

test('flat open ground: walk the whole way everywhere, open sky, no openings', () => {
  const w = world()
  const s = surroundings(w.at, FEET, 0)
  assert.deepEqual(s.overhead, { kind: 'sky' })
  assert.equal(s.ways.length, 8)
  for (const way of s.ways) {
    assert.equal(way.end.kind, 'open')
    assert.ok(way.walked >= 23, `${way.compass} ${way.walked}`)
    assert.equal(way.rise, 0)
  }
  assert.deepEqual(s.openings, [])
  const ahead = s.ways.find((x) => x.side === 'ahead')
  assert.equal(ahead.compass, 'north')
  assert.deepEqual(ahead.to, { x: 0, y: 64, z: -24 })
})

test('a wall, a step up, a drop and water', () => {
  const w = world()
    .put('cobblestone', -1, 64, -5, 1, 67, -5) // a wall 4 high, 5 blocks north
    .put('dirt', 3, 64, -1, 30, 64, 1) // a step up east, onto higher ground
    .put('air', -1, 56, 4, 1, 63, 30) // a pit south: 8 deep
    .put('water', -30, 63, -1, -4, 63, 1) // water west from 4 blocks out
  const north = walk(w.at, FEET, NORTH.x, NORTH.z)
  assert.equal(north.walked, 4)
  assert.deepEqual(north.end, { kind: 'wall', block: 'cobblestone', height: 4, taller: false })
  const east = walk(w.at, FEET, 1, 0)
  assert.equal(east.end.kind, 'open')
  assert.equal(east.rise, 1)
  const south = walk(w.at, FEET, 0, 1)
  assert.equal(south.walked, 3)
  assert.deepEqual(south.end, { kind: 'drop', depth: 8, deeper: false })
  const west = walk(w.at, FEET, -1, 0)
  assert.equal(west.walked, 3)
  assert.deepEqual(west.end, { kind: 'water' })
})

test('small drops are walked down, like walking does', () => {
  const w = world().put('air', -1, 62, -10, 1, 63, -30) // ground 2 lower from 10 blocks north
  const north = walk(w.at, FEET, 0, -1)
  assert.equal(north.end.kind, 'open')
  assert.equal(north.rise, -2)
})

// A stone hill from 6 blocks north, 8 high, with a tunnel into it at ground level going down.
function hillWithCave () {
  return world()
    .put('stone', -6, 64, -30, 6, 71, -6)
    .put('air', 0, 64, -20, 0, 65, -6) // the tunnel, 2 high
    .put('air', 0, 62, -20, 0, 63, -14) // dropping 2 further in
}

test('a cave mouth in a hillside is an opening, and the way ahead walks into it', () => {
  const s = surroundings(hillWithCave().at, FEET, 0)
  const ahead = s.ways.find((x) => x.side === 'ahead')
  assert.ok(ahead.walked >= 15, `walked ${ahead.walked}`)
  assert.equal(s.openings.length, 1)
  const [o] = s.openings
  assert.deepEqual(o.position, { x: 0, y: 64, z: -6 })
  assert.equal(o.side, 'ahead')
  assert.equal(o.compass, 'north')
  assert.equal(o.roof, 'stone')
  assert.equal(o.down, 2)
  // The ways beside it hit the hillside.
  assert.equal(s.ways.find((x) => x.side === 'ahead-left').end.kind, 'wall')
  const text = describeSurroundings(s).join('\n')
  assert.match(text, /Around you: outdoors, open sky above\./)
  assert.match(text, /ahead \(north\) 6 m at \(0 64 -6\): the ground goes in under stone .*sloping down 2 m \(a cave or tunnel mouth\)/)
})

test('facing matters: the same cave is behind her when she faces south', () => {
  const s = surroundings(hillWithCave().at, FEET, Math.PI)
  assert.equal(s.openings[0].side, 'behind')
  assert.equal(s.ways.find((x) => x.side === 'ahead').compass, 'south')
})

test('an overhang (roof for a block or two) is not a cave', () => {
  const w = world().put('stone', -2, 67, -6, 2, 67, -5) // a ledge over the path, open ground after it
  const s = surroundings(w.at, FEET, 0)
  assert.deepEqual(s.openings, [])
})

test('inside the tunnel: underground, and the ways are the passages', () => {
  const w = hillWithCave()
  const inside = { x: 0.5, y: 64, z: -8.5 }
  const s = surroundings(w.at, inside, 0)
  assert.equal(s.overhead.kind, 'underground')
  assert.equal(s.overhead.up, 1) // a 2-high tunnel: the roof is just over her head
  assert.deepEqual(s.openings, []) // underground, everything is "under a roof"
  assert.equal(s.ways.find((x) => x.side === 'left').walked, 0)
  assert.ok(s.ways.find((x) => x.side === 'ahead').walked >= 5)
  assert.match(describeSurroundings(s).join('\n'), /left \(west\): blocked right there by a stone wall/)
})

test('leaves and a trunk overhead are outdoors, a thin roof is a roof', () => {
  const trees = world().put('oak_leaves', -2, 68, -2, 2, 70, 2).put('oak_log', 0, 66, 0)
  assert.equal(overhead(trees.at, { x: 0.5, y: 64, z: 0.5 }).kind, 'trees')
  const hut = world().put('oak_planks', -3, 68, -3, 3, 68, 3)
  assert.deepEqual(overhead(hut.at, FEET), { kind: 'roof', block: 'oak_planks', up: 3, solid_above: 1 })
})
