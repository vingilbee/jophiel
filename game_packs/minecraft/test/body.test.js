// Her body: putting armor on, never drowning (the breathing reflex), and the go_to spots
// "next to me" and "where I'm looking". A fake bot; no server (the real-server checks are in
// test/integration/body.itest.js).
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { done, timeLimitParam } from '../src/skills/contract.js'
import { armorSlot, wear, wearing } from '../src/skills/armor.js'
import { needsAir } from '../src/skills/water.js'
import { parseTarget } from '../src/skills/movement.js'
import { SkillRunner } from '../src/skills/runner.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')
const registry = require('minecraft-data')('26.1')

function item (name) {
  return { name, type: registry.itemsByName[name].id, count: 1 }
}

// Armor in `worn` (slot -> name) is on her; `carried` is in her inventory. Equipping moves it.
function armoredBot ({ worn = {}, carried = [] } = {}) {
  const slots = []
  const index = { head: 5, torso: 6, legs: 7, feet: 8 }
  for (const [slot, name] of Object.entries(worn)) slots[index[slot]] = item(name)
  let items = carried.map(item)
  return {
    registry,
    inventory: { slots, items: () => items },
    async equip (it, dest) {
      const old = slots[index[dest]]
      slots[index[dest]] = it
      items = items.filter((i) => i !== it).concat(old ? [old] : [])
    }
  }
}

function ctxFor (bot) {
  return { bot, race: (p) => p, check () {}, sleep: async () => {} }
}

test('armor goes where it is worn', () => {
  assert.equal(armorSlot('iron_helmet'), 'head')
  assert.equal(armorSlot('turtle_helmet'), 'head')
  assert.equal(armorSlot('diamond_chestplate'), 'torso')
  assert.equal(armorSlot('elytra'), 'torso')
  assert.equal(armorSlot('leather_leggings'), 'legs')
  assert.equal(armorSlot('golden_boots'), 'feet')
  assert.equal(armorSlot('iron_sword'), null)
})

test('wear: the best piece for each slot, never a worse one', async () => {
  const bot = armoredBot({ worn: { torso: 'iron_chestplate' }, carried: ['leather_chestplate', 'diamond_helmet', 'golden_helmet', 'chainmail_boots'] })
  const end = await wear.run(ctxFor(bot), {})
  assert.equal(end.status, 'done')
  assert.deepEqual(end.data.put_on, ['diamond_helmet', 'chainmail_boots'])
  assert.deepEqual(wearing(bot), { head: 'diamond_helmet', torso: 'iron_chestplate', feet: 'chainmail_boots' })
  assert.equal((await wear.run(ctxFor(bot), {})).reason, 'already wearing the best armor she has')
  assert.equal((await wear.run(ctxFor(armoredBot()), {})).reason, 'has no armor')
})

test('wear: a named piece goes on even when it is worse', async () => {
  const bot = armoredBot({ worn: { torso: 'iron_chestplate' }, carried: ['leather_chestplate'] })
  const end = await wear.run(ctxFor(bot), { item: 'leather_chestplate' })
  assert.deepEqual(end.data.wearing, { torso: 'leather_chestplate' })
  assert.throws(() => wear.validate(bot, { item: 'iron_sword' }), /isn't armor/)
  assert.equal((await wear.run(ctxFor(bot), { item: 'leather_chestplate' })).reason, 'already wearing leather_chestplate')
})

// Her head is in water when `underwater` says so.
function swimmer ({ air = 20, underwater = true } = {}) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.health = 20
  bot.food = 20
  bot.oxygenLevel = air
  bot.entity = { position: new Vec3(0, 50, 0), eyeHeight: 1.62 }
  bot.state = { underwater }
  bot.blockAt = () => ({ name: bot.state.underwater ? 'water' : 'air', boundingBox: 'empty', getProperties: () => ({}) })
  bot.entities = {}
  bot.players = {}
  bot.inventory = { items: () => [] }
  bot.clearControlStates = () => {}
  return bot
}

test('needs air: head under water and the air bar low (readings over 20 are noise)', () => {
  assert.equal(needsAir(swimmer({ air: 9 })), true)
  assert.equal(needsAir(swimmer({ air: 15 })), false)
  assert.equal(needsAir(swimmer({ air: 9, underwater: false })), false)
  assert.equal(needsAir(swimmer({ air: 398 })), false)
})

// A follow that runs a while, a craft that runs a while, and a surface that brings her air back.
function bodySkills (log) {
  const follow = {
    name: 'follow_player',
    params: { player: { type: 'string', required: true }, time_limit: timeLimitParam(5, 10) },
    async run (ctx) {
      log.push('follow')
      const until = Date.now() + 600
      while (Date.now() < until) await ctx.sleep(20)
      return done()
    }
  }
  const craft = {
    name: 'craft',
    params: { time_limit: timeLimitParam(5, 10) },
    async run (ctx) {
      log.push('craft')
      while (true) await ctx.sleep(20)
    }
  }
  const surface = {
    name: 'surface',
    params: { time_limit: timeLimitParam(20, 60) },
    async run (ctx) {
      log.push('surface')
      await ctx.sleep(100)
      ctx.bot.state.underwater = false
      ctx.bot.oxygenLevel = 20
      return done({ air: 20 })
    }
  }
  return [follow, craft, surface]
}

function finished (runner, id) {
  return new Promise((resolve) => runner.on('finish', (r) => r.id === id && resolve(r)))
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('breathing: out of air mid-follow, she swims up, then carries on following', async () => {
  const log = []
  const bot = swimmer()
  const runner = new SkillRunner(bot, bodySkills(log))
  const record = runner.start('follow_player', { player: 'tearDrop12' })
  const end = finished(runner, record.id)
  setTimeout(() => { bot.oxygenLevel = 8 }, 100)
  const result = await end
  runner.detach()
  assert.equal(result.status, 'done')
  assert.deepEqual(log, ['follow', 'surface', 'follow'])
  assert.equal(result.data.interruptions[0].text, 'breathe: swam up for air (air 20/20)')
})

test('breathing: a skill that can\'t wait ends, and with nothing running she swims up by herself', async () => {
  const log = []
  const bot = swimmer()
  const runner = new SkillRunner(bot, bodySkills(log))
  const record = runner.start('craft')
  const end = finished(runner, record.id)
  bot.oxygenLevel = 8
  const result = await end
  assert.equal(result.status, 'hurt')
  assert.match(result.reason, /running out of air/)
  await wait(400)
  const reflex = runner.history().find((r) => r.skill === 'surface')
  runner.detach()
  assert.equal(reflex.source, 'reflex')
  assert.equal(reflex.status, 'done')
  assert.deepEqual(log, ['craft', 'surface'])
})

test('breathing: a stop request doesn\'t stop her swimming up', async () => {
  const log = []
  const bot = swimmer({ air: 8 })
  const runner = new SkillRunner(bot, bodySkills(log))
  await wait(300)
  assert.equal(runner.current?.skill, 'surface')
  runner.stop('paused')
  assert.equal(runner.current?.status, 'running')
  await wait(200)
  runner.detach()
  assert.equal(runner.history().at(-1).status, 'done')
})

test('go_to: the spot a player is looking at', () => {
  assert.deepEqual(parseTarget('looking:tearDrop12'), { kind: 'looking', name: 'tearDrop12' })
})
