import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeState } from '../src/describe.js'
import { attachSightings, lastSeen, outOfSight, record } from '../src/sightings.js'

function fakeBot () {
  const handlers = {}
  return {
    username: 'Piqeeee',
    entity: { position: { x: 0, y: 64, z: 0 } },
    players: {
      Piqeeee: { entity: {} },
      tearDrop12: { entity: { position: { x: 30.4, y: 70, z: -40.6 } } }
    },
    on (event, fn) { handlers[event] = fn },
    off (event) { delete handlers[event] }
  }
}

test('a player in sight is not listed as out of sight', () => {
  const bot = fakeBot()
  attachSightings(bot)
  assert.deepEqual(outOfSight(bot), [])
})

test('last seen position survives the player leaving entity range', () => {
  const bot = fakeBot()
  const seen = new Map()
  attachSightings(bot)
  record(bot, seen, 1000)
  assert.deepEqual(seen.get('tearDrop12'), { x: 30, y: 70, z: -41, at: 1000 })
  assert.equal(seen.has('Piqeeee'), false)
})

test('out of sight lists online players without an entity, with last seen distance and age', () => {
  const bot = fakeBot()
  const handlers = {}
  bot.on = (event, fn) => { handlers[event] = fn }
  attachSightings(bot)
  const now = Date.now()
  for (let i = 0; i < 20; i++) handlers.physicsTick() // the 1 s check
  bot.players.tearDrop12 = { entity: null } // they walk out of entity range
  const [p] = outOfSight(bot, now + 25000)
  assert.equal(p.name, 'tearDrop12')
  assert.deepEqual(p.last_seen, { x: 30, y: 70, z: -41 })
  assert.equal(p.distance, 51)
  assert.ok(p.seconds_ago >= 24 && p.seconds_ago <= 26)
  assert.deepEqual(lastSeen(bot, 'tearDrop12'), { ...p.last_seen, at: lastSeen(bot, 'tearDrop12').at })
})

test('never seen: out of sight with no last position', () => {
  const bot = fakeBot()
  bot.players.tearDrop12 = { entity: null }
  attachSightings(bot)
  assert.deepEqual(outOfSight(bot), [{ name: 'tearDrop12', last_seen: null }])
})

test('the state text says out of sight, not offline', () => {
  const state = {
    username: 'Piqeeee', players_online: ['tearDrop12'],
    players_out_of_sight: [{ name: 'tearDrop12', last_seen: { x: 30, y: 70, z: -41 }, distance: 51, seconds_ago: 25 }],
    position: { x: 0, y: 64, z: 0 }, dimension: 'minecraft:overworld', facing: { compass: 'north', pitch_down_deg: 0 },
    standing_on: 'grass_block', health: 20, food: 20, held: null, time: { day: 1, phase: 'day', time_of_day: 1000 },
    weather: 'clear', inventory: [], nearby: [], blocks: []
  }
  const text = describeState(state)
  assert.match(text, /Health 10 of 10 hearts \(20\/20\)/)
  assert.match(text, /tearDrop12 is online but out of sight: last seen at \(30, 70, -41\), 51 m from you, 25 s ago\./)
})
