// Single actions for her code (src/actions.js) and how the sandbox lets her use them: one swing,
// one block broken or placed, reach checked, nobody off limits; counted, one at a time, never
// over a running skill, cut short by stop. A fake bot; no server.
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { createRequire } from 'node:module'
import { test } from 'node:test'

import { done, timeLimitParam } from '../src/skills/contract.js'
import { SkillRunner } from '../src/skills/runner.js'
import { createActions } from '../src/actions.js'
import { runProgram } from '../src/sandbox.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const AIR = { name: 'air', boundingBox: 'empty', diggable: false }

function fakeBot () {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.oxygenLevel = 20
  bot.entity = { position: new Vec3(0.5, 64, 0.5), eyeHeight: 1.62, yaw: 0, pitch: 0, onGround: true }
  const world = new Map() // "x,y,z" -> block
  bot.world = world
  bot.blockAt = (p) => world.get(`${p.x},${p.y},${p.z}`) ?? AIR
  bot.entities = {}
  bot.players = {}
  bot.heldItem = null
  bot.inventory = { items: () => bot.items ?? [] }
  bot.registry = { blocksByName: { dirt: { name: 'dirt', boundingBox: 'block' } } }
  bot.clearControlStates = () => {}
  bot.controls = {}
  bot.setControlState = (name, on) => { bot.controls[name] = on }
  bot.lookAt = async () => {}
  bot.equip = async (item) => { bot.heldItem = item }
  bot.hits = []
  bot.attack = (entity) => bot.hits.push(entity.id)
  bot.dig = async (block) => { world.delete(`${block.position.x},${block.position.y},${block.position.z}`) }
  bot.placeBlock = async (reference, face) => {
    const p = reference.position.plus(face)
    world.set(`${p.x},${p.y},${p.z}`, solid('dirt', p))
  }
  return bot
}

function solid (name, position) {
  return { name, position, boundingBox: 'block', diggable: true, harvestTools: undefined, digTime: () => 750 }
}

function mob (id, name, x, y, z, type = 'animal') {
  return { id, name, type, position: new Vec3(x, y, z), width: 0.4, height: 0.5, isValid: true }
}

test('attack: one swing at anything in reach, players included; too far is said with the distance', async () => {
  const bot = fakeBot()
  bot.entities = { 7: mob(7, 'rabbit', 2, 64, 0.5), 8: mob(8, 'rabbit', 9, 64, 0.5),
    9: { ...mob(9, null, 1.5, 64, 0.5, 'player'), username: 'tearDrop12', height: 1.8, width: 0.6 } }
  const act = createActions(bot)
  const hit = await act.attack(7)
  assert.equal(hit.status, 'done', hit.reason)
  assert.equal(hit.data.target, 'rabbit')
  assert.equal(hit.data.cooldown_ms, 250) // bare hand
  const far = await act.attack(8)
  assert.equal(far.status, 'failed')
  assert.match(far.reason, /out of reach: rabbit is \d+\.\d m away, needs 3 or less/)
  assert.equal((await act.attack(9)).status, 'done') // nobody is off limits
  assert.equal((await act.attack(42)).status, 'failed')
  assert.deepEqual(bot.hits, [7, 9])
})

test('dig breaks one block in reach; air and far blocks are refused', async () => {
  const bot = fakeBot()
  const above = new Vec3(0, 66, 0)
  bot.world.set('0,66,0', solid('stone', above))
  bot.world.set('0,80,0', solid('stone', new Vec3(0, 80, 0)))
  const act = createActions(bot)
  const dug = await act.dig(0, 66, 0)
  assert.equal(dug.status, 'done', dug.reason)
  assert.equal(dug.data.block, 'stone')
  assert.equal(bot.blockAt(above).name, 'air')
  assert.match((await act.dig(0, 66, 0)).reason, /nothing to dig there/)
  assert.match((await act.dig(0, 80, 0)).reason, /out of reach/)
})

test('place puts the held block against a solid one; with nothing to rest on it says so', async () => {
  const bot = fakeBot()
  bot.world.set('2,63,0', solid('stone', new Vec3(2, 63, 0)))
  const act = createActions(bot)
  assert.match((await act.place(2, 64, 0)).reason, /hand is empty/)
  bot.items = [{ name: 'dirt', type: 1, count: 5 }]
  const placed = await act.place(2, 64, 0, 'dirt')
  assert.equal(placed.status, 'done', placed.reason)
  assert.equal(bot.blockAt(new Vec3(2, 64, 0)).name, 'dirt')
  assert.match((await act.place(-2, 70, 0)).reason, /out of reach|nothing solid/)
})

test('jump resolves at the top of the jump, only from the ground', async () => {
  const bot = fakeBot()
  bot.entity.velocity = { y: 0 }
  let ticks = 0
  // In the air from the first tick, rising for two, then falling.
  const sleep = async () => {
    ticks++
    bot.entity.onGround = false
    bot.entity.velocity.y = ticks <= 2 ? 0.3 : -0.1
    bot.entity.position = bot.entity.position.offset(0, ticks <= 2 ? 0.6 : 0, 0)
  }
  const act = createActions(bot, { sleep })
  const jumped = await act.jump()
  assert.equal(jumped.status, 'done', jumped.reason)
  assert.equal(ticks, 3)
  assert.equal(jumped.data.rose, 1.2)
  assert.equal(bot.controls.jump, false)
  assert.equal((await act.jump()).status, 'failed') // still in the air
})

function runnerWith (bot) {
  return new SkillRunner(bot, [{
    name: 'collect',
    params: { time_limit: timeLimitParam(5, 10) },
    async run (ctx) {
      await ctx.sleep(30)
      return done({})
    }
  }])
}

test('her program builds a move out of actions: a fight is many swings, and each is counted', async () => {
  const bot = fakeBot()
  bot.entities = { 7: mob(7, 'rabbit', 2, 64, 0.5) }
  const runner = runnerWith(bot)
  const result = await runProgram(runner, `
    let hits = 0
    for (let i = 0; i < 5; i++) {
      const r = await act.attack(7)
      if (r.status === 'done') hits++
      await wait(1)
    }
    return { hits }
  `, { state: () => ({}) })
  runner.detach()
  assert.equal(result.status, 'done', result.reason)
  assert.deepEqual(result.value, { hits: 5 })
  assert.equal(result.actions.total, 5)
  assert.deepEqual(result.actions.by, { attack: 5 })
  assert.equal(bot.hits.length, 5)
})

test('an action while a skill of hers still runs is refused as busy; await the skill first', async () => {
  const bot = fakeBot()
  bot.entities = { 7: mob(7, 'rabbit', 2, 64, 0.5) }
  const runner = runnerWith(bot)
  const result = await runProgram(runner, `
    const going = skills.collect({})
    const r = await act.attack(7)
    await going
    return r.status
  `, { state: () => ({}) })
  runner.detach()
  assert.equal(result.value, 'busy')
  assert.equal(bot.hits.length, 0)
})

test('stop cuts an action short (a turn that never finishes), and the program ends', async () => {
  const bot = fakeBot()
  bot.entities = { 7: mob(7, 'rabbit', 2, 64, 0.5) }
  bot.lookAt = () => new Promise(() => {})
  const runner = runnerWith(bot)
  const running = runProgram(runner, 'await act.attack(7); return "after"', { state: () => ({}) })
  await new Promise((resolve) => setTimeout(resolve, 50))
  runner.stop()
  const result = await running
  runner.detach()
  assert.equal(result.status, 'stopped')
  assert.equal(bot.hits.length, 0)
})

test('too many actions end the program; an unknown one is invalid', async () => {
  const bot = fakeBot()
  const runner = runnerWith(bot)
  const unknown = await runProgram(runner, 'return (await act.fly()).status', { state: () => ({}) })
    .catch((err) => ({ status: 'error', reason: err.message }))
  assert.equal(unknown.status, 'error') // act.fly isn't a function in the box
  const result = await runProgram(runner, 'for (let i = 0; i < 500; i++) await act.look(0, 64, 5); return "never"',
    { state: () => ({}), cpuMs: 5000 })
  runner.detach()
  assert.equal(result.status, 'failed')
  assert.match(result.reason, /more than 400 single actions/)
})
