import assert from 'node:assert/strict'
import { test } from 'node:test'

import { GroundCache, describeTerrain, land, terrain, wayOut } from '../src/terrain.js'

// A world by hand: a ground height per column (solid at and below it), plus edits by block.
function world (height = () => 63) {
  const edits = new Map()
  const k = (x, y, z) => `${x},${y},${z}`
  const api = {
    put (kind, x0, y0, z0, x1 = x0, y1 = y0, z1 = z0) {
      for (let x = Math.min(x0, x1); x <= Math.max(x0, x1); x++) {
        for (let y = Math.min(y0, y1); y <= Math.max(y0, y1); y++) {
          for (let z = Math.min(z0, z1); z <= Math.max(z0, z1); z++) edits.set(k(x, y, z), kind)
        }
      }
      return api
    },
    kind (x, y, z) {
      if (y < -64 || y > 320) return null
      return edits.get(k(x, y, z)) ?? (y <= height(x, z) ? 'solid' : 'open')
    }
  }
  api.cache = () => new GroundCache(api.kind)
  return api
}
const FEET = { x: 0.5, y: 64, z: 0.5 }

test('flat land: nothing rises, no peak', () => {
  const w = world()
  const l = land(w.cache(), FEET, 0)
  assert.equal(l.peak, null)
  assert.ok(l.sectors.every((s) => s.mean === 0 && s.water === 0))
  assert.match(describeTerrain({ land: l }).join('\n'), /Land out to 64 m: level all around\.\nHighest ground \(peak\): you are on it/)
})

// A hill east of her: a pyramid 30 high around x=40, one block per block (walkable).
const hill = (x, z) => 63 + Math.max(0, 30 - Math.abs(x - 40) - Math.abs(z))

test('a hill to the east: the peak, which way the land rises, and a walkable way up', () => {
  const w = world(hill)
  const t = terrain(w.kind, w.cache(), FEET, 0)
  const p = t.land.peak
  assert.deepEqual([p.x, p.y, p.z, p.rel], [40, 94, 0, 30])
  assert.equal(p.compass, 'east')
  assert.equal(p.side, 'right') // she faces north
  assert.deepEqual(p.way_up, { biggest_step: 1, water_blocks: 0 })
  assert.ok(t.land.sectors.find((s) => s.compass === 'east').mean >= 6)
  const text = describeTerrain(t).join('\n')
  assert.match(text, /east: rises/)
  assert.match(text, /Highest ground \(peak\): right \(east\) 40 m at \(40 94 0\), 30 m above you: a mountain top\. The straight way there is walkable\./)
})

test('a cliff: the way up has big steps, and trees are not ground', () => {
  // Terraces 3 high every 4 blocks east of x=10, up to +12.
  const w = world((x) => 63 + (x >= 10 ? Math.min(12, 3 * (1 + Math.floor((x - 10) / 4))) : 0))
    .put('tree', -20, 64, -20, -16, 90, -16) // a tall tree north-west: not a peak
  const l = land(w.cache(), FEET, 0)
  assert.equal(l.peak.compass, 'east')
  assert.equal(l.peak.rel, 12)
  assert.equal(l.peak.way_up.biggest_step, 3)
  assert.match(describeTerrain({ land: l }).join('\n'), /steps up to 3 blocks high \(dig=yes digs steps\)/)
})

test('water: a lake to the west', () => {
  const w = world().put('water', -64, 63, -64, -20, 63, 64)
  const west = land(w.cache(), FEET, 0).sectors.find((s) => s.compass === 'west')
  assert.ok(west.water >= 0.5)
  assert.match(describeTerrain({ land: land(w.cache(), FEET, 0) }).join('\n'), /west: mostly water/)
})

// Underground: rock up to y=80; she's in a 2-high tunnel at y=64 running east.
function cave () {
  return world(() => 80).put('open', 0, 64, 0, 10, 65, 0)
}

test('underground: a tunnel that rises in steps to the sky is a walkable way out', () => {
  const w = cave()
  for (let i = 0; i < 16; i++) w.put('open', 11 + i, 65 + i, 0, 11 + i, 66 + i + 1, 0) // a staircase to y=81
  const t = terrain(w.kind, w.cache(), FEET, 0, { underground: true })
  assert.ok(t.way_out, 'found a way out')
  assert.equal(t.way_out.climb, null)
  assert.equal(t.way_out.compass, 'east')
  assert.ok(t.way_out.rise >= 14, JSON.stringify(t.way_out)) // where the staircase breaks through the rock
  assert.deepEqual(t.surface, { y: 81, up: 17, capped: false })
  const text = describeTerrain(t).join('\n')
  assert.match(text, /Way out: \d+ m through open space, starting right \(east\), to the sky at .* walkable\. go_to target "surface"/)
  assert.match(text, /Surface: about 17 m straight above you/)
})

test('a 2-block ledge on the way is found and named, not hidden (all paths 2 higher)', () => {
  const w = cave()
    .put('open', 11, 64, 0, 11, 67, 0) // the passage goes up 2 at x=11...
    .put('open', 12, 66, 0, 12, 67, 0)
  for (let i = 0; i < 14; i++) w.put('open', 13 + i, 66 + i, 0, 13 + i, 68 + i + 1, 0) // ...then steps to the sky
  const way = wayOut(w.kind, w.cache(), FEET)
  assert.ok(way, 'a way out even though every path has a 2-block ledge')
  assert.equal(way.climb.height, 2)
  assert.equal(way.climb.at, 12) // after walking the tunnel
  assert.match(describeTerrain({ land: null, way_out: way, ...terrain(w.kind, w.cache(), FEET, 0, { underground: true }) }).join('\n'),
    /a 2 m climb \(a ledge or shaft\) after \d+ m: walking can't go up it, digging steps can/)
})

test('a walkable way wins over a shorter shaft', () => {
  const w = cave().put('open', 3, 66, 0, 3, 80, 0) // a shaft to the sky 3 blocks along: shorter, but a climb
  for (let i = 0; i < 16; i++) w.put('open', 11 + i, 65 + i, 0, 11 + i, 66 + i + 1, 0)
  const way = wayOut(w.kind, w.cache(), FEET)
  assert.equal(way.climb, null, JSON.stringify(way))
  assert.equal(way.compass, undefined) // compass/side are added by terrain()
  assert.equal(way.first.dx, 1)
})

test('a sealed room: no way out, and digging up is still named', () => {
  const w = cave()
  const t = terrain(w.kind, w.cache(), FEET, 0, { underground: true })
  assert.equal(t.way_out, null)
  const text = describeTerrain(t).join('\n')
  assert.match(text, /Way out: no opening to the sky found through open space within 48 m\./)
  assert.match(text, /Surface: about 17 m straight above you \(digging up is always a way\)/)
})
