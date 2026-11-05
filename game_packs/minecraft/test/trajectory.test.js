import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { gunzipSync } from 'node:zlib'

import { Trajectory, expandChunk, frameDelta, applyDelta } from '../src/trajectory.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

function fakeBot () {
  const bot = new EventEmitter()
  bot._client = new EventEmitter()
  bot.username = 'Jophiel'
  bot.version = '26.1.2'
  bot.entity = { id: 1, position: new Vec3(0, 64, 0), velocity: new Vec3(0, 0, 0), yaw: 0, pitch: 0, onGround: true }
  bot.entities = { 1: bot.entity }
  bot.inventory = { slots: new Array(46).fill(null) }
  bot.controlState = { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false }
  bot.health = 20
  bot.food = 20
  bot.foodSaturation = 5
  bot.oxygenLevel = 20
  bot.quickBarSlot = 0
  bot.game = { dimension: 'overworld', gameMode: 'survival' }
  bot.time = { timeOfDay: 1000, day: 1 }
  return bot
}

function clock () {
  let t = 1_000_000
  return { now: () => t, advance: (ms) => { t += ms } }
}

function decode (chunk) {
  return JSON.parse(gunzipSync(Buffer.from(chunk.data, 'base64')).toString('utf8'))
}

test('every tick is a frame: unchanged ticks kept, each state rebuilt from the chunk alone', async () => {
  const c = clock()
  const traj = new Trajectory({ now: c.now, timers: false, chunkTicks: 4 })
  const bot = fakeBot()
  traj.attach(bot)
  const truth = []
  for (let i = 0; i < 10; i++) {
    c.advance(50)
    if (i >= 2 && i < 5) bot.entity.position = new Vec3(i, 64, 0) // walks for 3 ticks, then stands
    if (i === 6) bot.controlState.jump = true
    if (i === 7) bot.inventory.slots[36] = { name: 'oak_log', count: 3 }
    truth.push({ x: bot.entity.position.x, jump: bot.controlState.jump, log: bot.inventory.slots[36]?.count ?? 0 })
    bot.emit('physicsTick')
  }
  traj.end('test over')
  await traj.settle()
  const out = traj.since(null)
  assert.deepEqual(out.chunks.map((ch) => [ch.seq, ch.first_tick, ch.ticks]), [[0, 0, 4], [1, 4, 4], [2, 8, 2]])
  // Any one chunk is readable without the others (its checkpoint is the state before its first frame).
  const states = []
  for (const ch of out.chunks) states.push(...expandChunk(decode(ch)))
  assert.equal(states.length, 10)
  states.forEach((s, i) => {
    assert.equal(s.tick, i)
    assert.equal(s.state.p[0], truth[i].x, `tick ${i} position`)
    assert.equal(Boolean(s.state.c & 16), truth[i].jump, `tick ${i} jump`)
    assert.equal(s.state.inv[36]?.[1] ?? 0, truth[i].log, `tick ${i} inventory`)
    assert.equal(s.at_ms, 1_000_000 + 50 * (i + 1))
  })
  const second = decode(out.chunks[1])
  assert.equal(second.frames[1].length, 1) // tick 5: standing still, nothing changed, still a frame
  assert.deepEqual(second.frames[2][1], { c: 16 }) // tick 6: only the jump
  assert.equal(decode(out.chunks.at(-1)).final, true)
  assert.equal(out.streams[0].end_reason, 'test over')
})

test('unknown values stay null, never zero', () => {
  const traj = new Trajectory({ timers: false })
  const bot = fakeBot()
  bot.health = undefined
  bot.food = undefined
  bot.entity = { id: 1, position: { x: NaN } }
  traj.attach(bot)
  bot.emit('physicsTick')
  const frame = traj.prev
  assert.equal(frame.h, null)
  assert.equal(frame.f, null)
  assert.equal(frame.p, null)
  assert.equal(frame.g, null)
})

test('damage keeps the server\'s cause; an id the registry never named stays unknown; none is lost', async () => {
  const c = clock()
  const traj = new Trajectory({ now: c.now, timers: false })
  const bot = fakeBot()
  traj.attach(bot)
  bot._client.emit('registry_data', { id: 'minecraft:damage_type', entries: [{ key: 'minecraft:fall' }, { key: 'minecraft:lava' }] })
  const zombie = { id: 9, name: 'zombie', type: 'hostile', position: new Vec3(1, 64, 0) }
  bot.entities[9] = zombie
  for (let i = 0; i < 6; i++) { // six hits within a third of a second: notices would show one of them
    c.advance(50)
    bot._client.emit('damage_event', { entityId: 1, sourceTypeId: 1, sourceCauseId: 0, sourceDirectId: 0 })
    bot.health -= 3
    bot.emit('health')
  }
  bot._client.emit('damage_event', { entityId: 1, sourceTypeId: 7, sourceCauseId: 10, sourceDirectId: 10 })
  bot._client.emit('damage_event', { entityId: 2, sourceTypeId: 0, sourceCauseId: 0, sourceDirectId: 0 }) // not her
  bot._client.emit('death_combat_event', { playerId: 1, message: { translate: 'death.attack.lava', with: ['Jophiel'] } })
  traj.end('over')
  await traj.settle()
  const events = traj.since(null).chunks.flatMap((ch) => decode(ch).events)
  const damage = events.filter((e) => e[2] === 'damage').map((e) => e[3])
  assert.equal(damage.length, 7)
  assert.deepEqual(damage.slice(0, 6).map((d) => d.type), Array(6).fill('minecraft:lava'))
  assert.equal(damage[6].type, null)
  assert.match(damage[6].type_source, /unknown/)
  assert.deepEqual(damage[6].cause, { id: 9, name: 'zombie', type: 'hostile', at: [1, 64, 0], distance: 1 })
  assert.equal(events.filter((e) => e[2] === 'health').length, 6)
  const death = events.find((e) => e[2] === 'death_message')[3]
  assert.equal(death.translate, 'death.attack.lava')
  assert.equal(death.source, 'server death_combat_event')
})

test('a damage type is unknown until the registry arrives', async () => {
  const traj = new Trajectory({ timers: false })
  const bot = fakeBot()
  traj.attach(bot)
  bot._client.emit('damage_event', { entityId: 1, sourceTypeId: 3, sourceCauseId: 0, sourceDirectId: 0 })
  traj.end('over')
  await traj.settle()
  const damage = traj.since(null).chunks.flatMap((ch) => decode(ch).events).find((e) => e[2] === 'damage')[3]
  assert.equal(damage.type, null)
  assert.equal(damage.type_id, 3)
})

test('events keep their order and the tick they came after', async () => {
  const c = clock()
  const traj = new Trajectory({ now: c.now, timers: false })
  const bot = fakeBot()
  traj.attach(bot)
  traj.event('skill_start', { run_id: 'r1' })
  c.advance(50)
  bot.emit('physicsTick')
  traj.event('act', { fn: 'dig' })
  traj.event('skill_finish', { run_id: 'r1', status: 'done' })
  c.advance(50)
  bot.emit('physicsTick')
  traj.event('block', { at: [0, 63, 0], from: 'stone', to: 'air' }) // after the skill said done: still kept
  traj.end('over')
  await traj.settle()
  const events = traj.since(null).chunks.flatMap((ch) => decode(ch).events)
  assert.deepEqual(events.map((e) => [e[0], e[2]]),
    [[-1, 'skill_start'], [0, 'act'], [0, 'skill_finish'], [1, 'block'], [1, 'stream_end']])
})

test('a full buffer drops the oldest unread chunks and says so once; a cursor resumes after what was read', async () => {
  const c = clock()
  const traj = new Trajectory({ now: c.now, timers: false, chunkTicks: 1, bufferChunks: 3 })
  const bot = fakeBot()
  traj.attach(bot)
  const tick = async (n) => {
    for (let i = 0; i < n; i++) {
      c.advance(50)
      bot.emit('physicsTick')
    }
    await traj.settle()
  }
  await tick(2)
  const first = traj.since(null)
  assert.deepEqual(first.chunks.map((ch) => ch.seq), [0, 1])
  await tick(5) // seq 2..6; the ring keeps 3: seq 2 and 3 leave unread
  const second = traj.since({ stream: first.chunks.at(-1).stream, seq: 1 })
  assert.deepEqual(second.chunks.map((ch) => ch.seq), [4, 5, 6])
  assert.deepEqual(second.dropped.map((d) => [d.from_seq, d.to_seq, d.reason]), [[2, 3, 'buffer_full']])
  const again = traj.since({ stream: first.chunks.at(-1).stream, seq: 6 })
  assert.deepEqual(again.chunks, [])
  assert.deepEqual(again.dropped, []) // reported once
  // A reader that restarted and asks from what it saved last gets what is still held after it.
  const resumed = traj.since({ stream: first.chunks.at(-1).stream, seq: 4 })
  assert.deepEqual(resumed.chunks.map((ch) => ch.seq), [5, 6])
})

test('a reconnect is a new stream: ticks and seq start again, the old stream ended', async () => {
  const traj = new Trajectory({ timers: false })
  const one = fakeBot()
  traj.attach(one)
  one.emit('physicsTick')
  one.emit('end', 'socketClosed')
  const two = fakeBot()
  traj.attach(two)
  two.emit('physicsTick')
  one.emit('physicsTick') // the old bot's tick after it ended counts for nothing
  traj.end('over')
  await traj.settle()
  const out = traj.since(null)
  assert.equal(out.streams.length, 2)
  assert.equal(out.streams[0].end_reason, 'socketClosed')
  const [a, b] = out.streams.map((s) => s.id)
  const byStream = (id) => out.chunks.filter((ch) => ch.stream === id)
  assert.deepEqual(byStream(a).map((ch) => [ch.seq, ch.ticks]), [[0, 1]])
  assert.deepEqual(byStream(b).map((ch) => [ch.seq, ch.first_tick, ch.ticks]), [[0, 0, 1]])
})

test('her actions are recorded without changing what they do or return', async () => {
  const traj = new Trajectory({ timers: false })
  const bot = fakeBot()
  let dug = null
  bot.dig = async (block) => { dug = block.name; return 'dug' }
  bot.equip = async () => { throw new Error('no such item') }
  bot.setQuickBarSlot = (slot) => { bot.quickBarSlot = slot }
  traj.instrument(bot)
  traj.attach(bot)
  const block = { name: 'stone', type: 1, position: new Vec3(0, 63, 0), boundingBox: 'block' }
  assert.equal(await bot.dig(block), 'dug')
  assert.equal(dug, 'stone')
  await assert.rejects(bot.equip({ name: 'iron_sword', count: 1, type: 5 }, 'hand'), /no such item/)
  bot.setQuickBarSlot(3)
  assert.equal(bot.quickBarSlot, 3)
  await new Promise((resolve) => setImmediate(resolve))
  traj.end('over')
  await traj.settle()
  const acts = traj.since(null).chunks.flatMap((ch) => decode(ch).events).filter((e) => e[2].startsWith('act'))
  assert.deepEqual(acts.map((e) => [e[2], e[3].fn, e[3].ok ?? null]), [
    ['act', 'dig', null], ['act_end', 'dig', true], ['act', 'equip', null], ['act_end', 'equip', false],
    ['act', 'setQuickBarSlot', null], ['act_end', 'setQuickBarSlot', true]])
  assert.deepEqual(acts[0][3].args[0], { block: 'stone', at: [0, 63, 0] })
  assert.equal(acts[3][3].error, 'no such item')
})

test('what she perceived is kept once per chunk, then by digest', async () => {
  const traj = new Trajectory({ timers: false })
  const bot = fakeBot()
  traj.attach(bot)
  const state = { at: 'now', surroundings: { overhead: { kind: 'sky' } }, terrain: null, blocks: [], heights: { now: 64 } }
  traj.perceived(state)
  traj.perceived(state)
  traj.end('over')
  await traj.settle()
  const seen = traj.since(null).chunks.flatMap((ch) => decode(ch).events).filter((e) => e[2] === 'perceived').map((e) => e[3])
  assert.equal(seen.length, 2)
  assert.deepEqual(seen[0].content.surroundings, { overhead: { kind: 'sky' } })
  assert.equal(seen[1].same_as_earlier, true)
  assert.equal(seen[1].digest, seen[0].digest)
})

test('deltas round-trip, maps by key', () => {
  const a = { p: [0, 1, 2], inv: { 36: ['dirt', 1] }, ent: { 5: ['cow', 1, 2, 3] }, h: 20 }
  const b = { p: [0, 1, 2], inv: { 37: ['stone', 2] }, ent: { 5: ['cow', 1, 2, 4] }, h: 19 }
  const d = frameDelta(a, b)
  assert.deepEqual(d, { inv: { 37: ['stone', 2], 36: null }, ent: { 5: ['cow', 1, 2, 4] }, h: 19 })
  assert.deepEqual(applyDelta(a, d), b)
  assert.equal(frameDelta(b, b), null)
})
