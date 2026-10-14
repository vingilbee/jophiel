import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { ParamError, SkillEnd, manifestEntry, validateParams } from '../src/skills/contract.js'
import { nextFrontier, parseGoal } from '../src/skills/explore.js'
import { pickTarget } from '../src/skills/fight.js'
import { isHostile } from '../src/skills/common.js'
import { dropNames } from '../src/skills/mine.js'
import { parseTarget } from '../src/skills/movement.js'
import { createSkills } from '../src/skills/index.js'
import { WorldMemory } from '../src/world_memory.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')
const registry = require('minecraft-data')('26.1')

test('params: defaults, ranges, unknown keys, types', () => {
  const spec = {
    block: { type: 'string', required: true },
    count: { type: 'integer', min: 1, max: 64, default: 1 },
    mode: { type: 'enum', values: ['a', 'b'] }
  }
  assert.deepEqual(validateParams(spec, { block: ' oak_log ' }), { block: 'oak_log', count: 1 })
  assert.throws(() => validateParams(spec, {}), ParamError)
  assert.throws(() => validateParams(spec, { block: 'x', count: 0 }), /at least 1/)
  assert.throws(() => validateParams(spec, { block: 'x', count: 1.5 }), /whole number/)
  assert.throws(() => validateParams(spec, { block: 'x', colour: 'red' }), /unknown parameter colour/)
  assert.throws(() => validateParams(spec, { block: 'x', mode: 'c' }), /one of a, b/)
  assert.throws(() => validateParams(spec, []), /must be an object/)
})

test('outcomes are a closed list', () => {
  assert.throws(() => new SkillEnd('succeeded'), /unknown outcome/)
  assert.equal(new SkillEnd('lost', 'gone').status, 'lost')
})

test('every skill states its parameters, done and stop conditions, and a capped time limit', () => {
  for (const skill of createSkills({ memory: new WorldMemory(null) })) {
    const entry = manifestEntry(skill)
    assert.ok(entry.description && entry.done_when && entry.stops_when, skill.name)
    assert.ok(entry.params.time_limit?.max, `${skill.name} has no time limit`)
    for (const [key, p] of Object.entries(entry.params)) assert.ok(p.description, `${skill.name}.${key}`)
  }
})

test('go_to targets', () => {
  assert.deepEqual(parseTarget('10 64 -3'), { kind: 'place', x: 10, y: 64, z: -3 })
  assert.deepEqual(parseTarget('10, -3'), { kind: 'place', x: 10, z: -3 })
  assert.deepEqual(parseTarget('player:tearDrop12'), { kind: 'player', name: 'tearDrop12' })
  assert.deepEqual(parseTarget('block:oak_log'), { kind: 'block', name: 'oak_log' })
  assert.throws(() => parseTarget('1 2 3 4'), ParamError)
  assert.throws(() => parseTarget('the big tree'), ParamError)
})

test('explore goals', () => {
  assert.equal(parseGoal(undefined), null)
  assert.deepEqual(parseGoal('mob:villager'), { kind: 'mob', name: 'villager' })
  assert.throws(() => parseGoal('village'), ParamError)
})

test('fight order: creeper, then skeletons, then zombies, nearest first within a rank', () => {
  const bot = { entity: { position: new Vec3(0, 0, 0) } }
  const mob = (name, x) => ({ name, position: new Vec3(x, 0, 0) })
  const zombie = mob('zombie', 2)
  const skeleton = mob('skeleton', 9)
  const creeper = mob('creeper', 12)
  const spider = mob('spider', 1)
  assert.equal(pickTarget(bot, [zombie, skeleton, creeper, spider]), creeper)
  assert.equal(pickTarget(bot, [zombie, skeleton, spider]), skeleton)
  assert.equal(pickTarget(bot, [mob('zombie', 5), zombie, spider]), zombie)
  assert.equal(pickTarget(bot, []), null)
})

test('explore frontier: nearest unexplored chunk within the radius', () => {
  const start = { x: 8, z: 8 }
  const visited = new Set(['0,0'])
  const next = nextFrontier(visited, start, { x: 8, z: 8 }, 32)
  assert.ok(next && next.key !== '0,0')
  assert.equal(Math.hypot(next.x - 8, next.z - 8), 16) // a neighbouring chunk
  const all = new Set()
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) all.add(`${x},${z}`)
  assert.equal(nextFrontier(all, start, start, 32), null) // covered
})

test('mining counts what a block drops, not the block', () => {
  assert.deepEqual(dropNames(registry, [registry.blocksByName.stone.id]), ['cobblestone'])
  assert.deepEqual(dropNames(registry, [registry.blocksByName.oak_log.id]), ['oak_log'])
})

test('neutral mobs are not hostile until provoked', () => {
  assert.ok(isHostile({ type: 'hostile', name: 'zombie' }))
  assert.ok(isHostile({ type: 'mob', name: 'slime' }))
  assert.ok(!isHostile({ type: 'hostile', name: 'enderman' }))
  assert.ok(!isHostile({ type: 'hostile', name: 'zombified_piglin' }))
  assert.ok(!isHostile({ type: 'animal', name: 'cow' }))
})
