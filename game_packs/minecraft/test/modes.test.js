// Background modes: which mode fires when, and the runner putting a skill on hold for one and
// picking it back up. A fake bot; no server.
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { done, timeLimitParam } from '../src/skills/contract.js'
import { mine } from '../src/skills/mine.js'
import { ModeMemory, ModeSettings, describeAction, gatherIds, pickMode, worthSaying } from '../src/modes/modes.js'
import { SkillRunner } from '../src/skills/runner.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')
const registry = require('minecraft-data')('26.1')

function fakeBot ({ mobs = [], friendAt = null, blocks = [] } = {}) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.health = 20
  bot.food = 20
  bot.entity = { position: new Vec3(0, 64, 0) }
  bot.entities = {}
  mobs.forEach((m, i) => { bot.entities[i + 1] = { id: i + 1, type: 'hostile', isValid: true, ...m } })
  bot.players = friendAt ? { tearDrop12: { entity: { username: 'tearDrop12', type: 'player', position: friendAt } } } : {}
  bot.findBlocks = ({ matching, maxDistance }) => blocks
    .filter((b) => matching.includes(registry.blocksByName[b.name].id) && b.position.distanceTo(bot.entity.position) <= maxDistance)
    .map((b) => b.position)
  bot.blockAt = (p) => {
    const b = blocks.find((x) => x.position.equals(p))
    return b ? { name: b.name, type: registry.blocksByName[b.name].id, harvestTools: b.harvestTools, digTime: () => 1000 } : null
  }
  bot.inventory = { items: () => [] }
  bot.clearControlStates = () => {}
  return bot
}

const following = { skill: 'follow_player', params: { player: 'tearDrop12', seconds: 600 } }

test('settings: only known modes, booleans, real block names', () => {
  const settings = new ModeSettings()
  const bot = { registry }
  assert.equal(settings.describe(), null)
  assert.deepEqual(settings.set({ guard: true, gather: ['iron_ore', 'coal_ore', 'iron_ore'] }, bot),
    { guard: true, gather: ['iron_ore', 'coal_ore'], pickup: false })
  assert.match(settings.describe(), /guard on .*; gathering iron_ore, coal_ore on the way/)
  assert.throws(() => settings.set({ dance: true }, bot), /no mode called dance/)
  assert.throws(() => settings.set({ guard: 'yes' }, bot), /true or false/)
  assert.throws(() => settings.set({ gather: ['iron'] }, bot), /no block is called iron/)
  settings.set({ pickup: true }, bot) // the rest stays
  assert.deepEqual(settings.toJSON(), { guard: true, gather: ['iron_ore', 'coal_ore'], pickup: true })
})

test('iron_ore also means the deepslate kind', () => {
  const ids = gatherIds({ registry }, ['iron_ore'])
  assert.deepEqual(ids.map((id) => registry.blocks[id].name).sort(), ['deepslate_iron_ore', 'iron_ore'])
})

test('guard: a hostile near her or near the player she follows, never one far off', () => {
  const settings = new ModeSettings()
  settings.guard = true
  const near = fakeBot({ mobs: [{ name: 'zombie', position: new Vec3(5, 64, 0) }] })
  const pick = pickMode(near, settings, new ModeMemory(), following)
  assert.equal(pick.mode, 'guard')
  assert.deepEqual(pick.params, { target: 'zombie', radius: 12, time_limit: 30 })

  // 10 m from her but next to tearDrop12: still hers to deal with.
  const byFriend = fakeBot({ mobs: [{ name: 'spider', position: new Vec3(10, 64, 0) }], friendAt: new Vec3(11, 64, 0) })
  assert.match(pickMode(byFriend, settings, new ModeMemory(), following).why, /close to tearDrop12/)

  const far = fakeBot({ mobs: [{ name: 'zombie', position: new Vec3(20, 64, 0) }], friendAt: new Vec3(19, 64, 0) })
  assert.equal(pickMode(far, settings, new ModeMemory(), following), null)
  // Crafting isn't put on hold, and nothing fires with guard off.
  assert.equal(pickMode(near, settings, new ModeMemory(), { skill: 'craft', params: {} }), null)
  assert.equal(pickMode(near, new ModeSettings(), new ModeMemory(), following), null)
})

test('gather: listed blocks close by, only while the player is close, never without a tool', () => {
  const settings = new ModeSettings()
  settings.gather = ['iron_ore']
  const pickaxe = { [registry.itemsByName.stone_pickaxe.id]: true }
  const blocks = [
    { name: 'iron_ore', position: new Vec3(3, 64, 0) },
    { name: 'deepslate_iron_ore', position: new Vec3(3, 63, 0) },
    { name: 'coal_ore', position: new Vec3(2, 64, 0) }
  ]
  const bot = fakeBot({ blocks, friendAt: new Vec3(8, 64, 0) })
  const pick = pickMode(bot, settings, new ModeMemory(), following)
  assert.equal(pick.mode, 'gather')
  assert.equal(pick.params.block, 'iron_ore')

  const behind = fakeBot({ blocks, friendAt: new Vec3(30, 64, 0) })
  assert.equal(pickMode(behind, settings, new ModeMemory(), following), null) // they'd get away

  const noTool = fakeBot({ blocks: [{ name: 'iron_ore', position: new Vec3(3, 64, 0), harvestTools: pickaxe }] })
  const memory = new ModeMemory()
  assert.equal(pickMode(noTool, settings, memory, following), null)
  assert.ok(memory.skipping('block:(3, 64, 0)'))
})

test('a target that didn\'t work out is left alone, and each mode cools down', () => {
  let now = 1000
  const memory = new ModeMemory(() => now)
  const settings = new ModeSettings()
  settings.guard = true
  const bot = fakeBot({ mobs: [{ name: 'zombie', position: new Vec3(4, 64, 0) }] })
  const pick = pickMode(bot, settings, memory, following)
  memory.acted(pick, false)
  now += 2500
  assert.equal(pickMode(bot, settings, memory, following), null) // that zombie: skipped
  now += 60000
  assert.equal(pickMode(bot, settings, memory, following).mode, 'guard')
})

test('mine picks up where it left off', () => {
  const params = mine.resume({ block: 'coal_ore', count: 5, radius: 16 }, { mined: 2, collected: 2 })
  assert.deepEqual(params._before, { mined: 2, collected: 2 })
  assert.equal(params.count, 5)
})

test('what a mode did, in a line', () => {
  assert.equal(describeAction({ mode: 'guard', params: { target: 'zombie' }, status: 'done', data: { killed: 1 } }),
    'guard: killed 1 zombie')
  assert.equal(describeAction({ mode: 'gather', params: { block: 'iron_ore' }, status: 'done', data: { collected: 3 } }),
    'gather: mined 3 iron_ore on the way')
  assert.equal(describeAction({ mode: 'pickup', params: {}, status: 'done', data: { collected: { coal: 1 } } }),
    'pickup: picked up coal x1')
  assert.equal(describeAction({ mode: 'guard', near: 'Friend', params: { target: 'zombie' }, status: 'done', data: { killed: 1 } }),
    'guard: killed 1 zombie near Friend')
})

test('only some of what modes do is worth a line to the user', () => {
  assert.equal(worthSaying({ mode: 'gather', status: 'done' }), true)
  assert.equal(worthSaying({ mode: 'guard', status: 'done', near: 'Friend' }), true)
  assert.equal(worthSaying({ mode: 'guard', status: 'done', near: null }), false) // only she was in it
  assert.equal(worthSaying({ mode: 'guard', status: 'timeout' }), true) // didn't work
  assert.equal(worthSaying({ mode: 'pickup', status: 'done' }), false)
  assert.equal(worthSaying({ mode: 'pickup', status: 'failed' }), false)
})

// A follow that counts its segments, and a fight that kills whatever it's sent after.
function fakeSkills (log) {
  const follow = {
    name: 'follow_player',
    params: { player: { type: 'string', required: true }, seconds: { type: 'number', default: 1 }, time_limit: timeLimitParam(5, 10) },
    async run (ctx, params) {
      log.push('follow')
      const until = Date.now() + 400
      while (Date.now() < until) await ctx.sleep(20)
      return done({ followed: true })
    }
  }
  const fight = {
    name: 'fight',
    params: { target: { type: 'string' }, radius: { type: 'integer' }, time_limit: timeLimitParam(30, 60) },
    async run (ctx, params) {
      log.push(`fight ${params.target}`)
      await ctx.sleep(30)
      for (const [id, e] of Object.entries(ctx.bot.entities)) if (e.name === params.target) delete ctx.bot.entities[id]
      return done({ killed: 1 })
    }
  }
  return [follow, fight]
}

function finished (runner, id) {
  return new Promise((resolve) => runner.on('finish', (r) => r.id === id && resolve(r)))
}

test('runner: a zombie shows up mid-follow, she fights it, then carries on following', async () => {
  const log = []
  const bot = fakeBot()
  const modes = new ModeSettings()
  modes.guard = true
  const runner = new SkillRunner(bot, fakeSkills(log), { modes })
  const seen = []
  runner.on('mode', (m) => seen.push(m.text))
  const record = runner.start('follow_player', { player: 'tearDrop12' })
  const end = finished(runner, record.id)
  setTimeout(() => { bot.entities[9] = { id: 9, name: 'zombie', type: 'hostile', isValid: true, position: new Vec3(4, 64, 0) } }, 100)
  const result = await end
  assert.equal(result.status, 'done')
  assert.deepEqual(log, ['follow', 'fight zombie', 'follow'])
  assert.equal(result.data.interruptions.length, 1)
  assert.equal(result.data.interruptions[0].text, 'guard: killed 1 zombie')
  assert.deepEqual(seen, ['guard: killed 1 zombie'])
})

test('runner: stopping her during a mode ends the whole run', async () => {
  const log = []
  const bot = fakeBot({ mobs: [{ name: 'zombie', position: new Vec3(4, 64, 0) }] })
  const modes = new ModeSettings()
  modes.guard = true
  const skills = fakeSkills(log)
  skills[1].run = async (ctx) => {
    log.push('fight')
    while (true) await ctx.sleep(20)
  }
  const runner = new SkillRunner(bot, skills, { modes })
  const record = runner.start('follow_player', { player: 'tearDrop12' })
  const end = finished(runner, record.id)
  await new Promise((resolve) => setTimeout(resolve, 350))
  assert.deepEqual(log, ['follow', 'fight'])
  runner.stop()
  const result = await end
  assert.equal(result.status, 'stopped')
  assert.deepEqual(log, ['follow', 'fight']) // no follow afterwards
  assert.equal(runner.current, null)
})
