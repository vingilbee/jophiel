// The sealed box her own code runs in (src/sandbox.js): it can use skills, state and log, and
// nothing else; endless loops, time limits, stop and getting hurt end the whole program; the
// pack's own thread never waits on it. A fake bot and fake skills; no server.
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'

import { SkillEnd, done, timeLimitParam } from '../src/skills/contract.js'
import { BusyError, SkillRunner } from '../src/skills/runner.js'
import { runProgram } from '../src/sandbox.js'

function fakeBot () {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.oxygenLevel = 20
  bot.entity = { position: { offset: () => ({}) }, eyeHeight: 1.62 }
  bot.blockAt = () => ({ name: 'air', boundingBox: 'empty', getProperties: () => ({}) })
  bot.entities = {}
  bot.players = {}
  bot.inventory = { items: () => [] }
  bot.clearControlStates = () => {}
  return bot
}

// collect: counts logs; slow: runs until stopped; ouch: ends hurt.
function fakeSkills () {
  return [
    {
      name: 'collect',
      params: { count: { type: 'integer', min: 1, max: 64, default: 1 }, time_limit: timeLimitParam(5, 10) },
      async run (ctx, params) {
        await ctx.sleep(10)
        return done({ got: params.count })
      }
    },
    {
      name: 'slow',
      params: { time_limit: timeLimitParam(5, 10) },
      async run (ctx) {
        while (true) await ctx.sleep(20)
      }
    },
    {
      name: 'ouch',
      params: { time_limit: timeLimitParam(5, 10) },
      async run () {
        throw new SkillEnd('hurt', 'a zombie')
      }
    }
  ]
}

function setup () {
  const runner = new SkillRunner(fakeBot(), fakeSkills())
  return { runner, state: () => ({ inventory: { oak_log: 3 }, health: 20 }) }
}

test('her program runs skills in order, reads state, logs, and returns a value', async () => {
  const { runner, state } = setup()
  const result = await runProgram(runner, `
    const logs = state().inventory.oak_log
    log('have', logs)
    const a = await skills.collect({ count: 2 })
    const b = await skills.collect({ count: 5 })
    return { logs, got: a.data.got + b.data.got, status: b.status }
  `, { state })
  runner.detach()
  assert.equal(result.status, 'done', result.reason)
  assert.deepEqual(result.value, { logs: 3, got: 7, status: 'done' })
  assert.deepEqual(result.calls.map((c) => [c.skill, c.status]), [['collect', 'done'], ['collect', 'done']])
  assert.deepEqual(result.log, ['have 3'])
  assert.equal(runner.history().every((r) => r.source === 'program'), true)
})

test('the box has no require, process, files, network or timers', async () => {
  const { runner, state } = setup()
  const seen = await runProgram(runner, `
    return [typeof require, typeof process, typeof fetch, typeof setTimeout, typeof globalThis.Buffer, typeof import.meta]
  `, { state }).catch((err) => ({ status: 'compile', reason: err.message }))
  // import.meta isn't even valid outside a module: that's a syntax error, also fine.
  assert.notEqual(seen.status, 'done')
  const types = await runProgram(runner, 'return [typeof require, typeof process, typeof fetch, typeof setTimeout, typeof Buffer]', { state })
  assert.deepEqual(types.value, ['undefined', 'undefined', 'undefined', 'undefined', 'undefined'])
  const tried = await runProgram(runner, `\nconst fs = require('fs')\nreturn fs.readFileSync('/etc/passwd', 'utf8')`, { state })
  runner.detach()
  assert.equal(tried.status, 'error')
  assert.match(tried.reason, /require is not defined \(line 2\)/)
})

test('bad params come back to her code as "invalid", and her program goes on', async () => {
  const { runner, state } = setup()
  const result = await runProgram(runner, `
    const bad = await skills.collect({ count: 999 })
    const missing = await skills.fly({})
    const good = await skills.collect({ count: 1 })
    return [bad.status, missing.status, good.status, bad.reason]
  `, { state })
  runner.detach()
  assert.deepEqual(result.value.slice(0, 3), ['invalid', 'invalid', 'done'])
  assert.match(result.value[3], /at most 64/)
})

test('an endless loop is killed, and the pack keeps running meanwhile', async () => {
  const { runner, state } = setup()
  let ticks = 0
  const ticker = setInterval(() => ticks++, 20)
  const sync = await runProgram(runner, 'while (true) {}', { state, cpuMs: 300 })
  const afterSkill = await runProgram(runner, 'await skills.collect({}); while (true) {}', { state, cpuMs: 300 })
  clearInterval(ticker)
  runner.detach()
  assert.notEqual(sync.status, 'done')
  assert.equal(afterSkill.status, 'failed')
  assert.match(afterSkill.reason, /ran too long/)
  assert.ok(ticks > 10, `the pack's own timers ticked ${ticks} times`)
})

test('her code can\'t eat all the memory', async () => {
  const { runner, state } = setup()
  const result = await runProgram(runner, 'const a = []; while (true) a.push(new Array(1e5).fill(1))', { state, cpuMs: 10000 })
  runner.detach()
  assert.notEqual(result.status, 'done')
})

test('stop ends the program and the skill it is running', async () => {
  const { runner, state } = setup()
  const pending = runProgram(runner, 'await skills.slow({}); await skills.collect({}); return "went on"', { state })
  await new Promise((resolve) => setTimeout(resolve, 100))
  assert.equal(runner.current?.skill, 'slow')
  runner.stop('paused')
  const result = await pending
  runner.detach()
  assert.equal(result.status, 'stopped')
  assert.equal(result.reason, 'paused')
  assert.deepEqual(result.calls.map((c) => c.skill), ['slow'])
  assert.equal(runner.current, null)
})

test('a skill ending hurt ends the whole program; so does the time limit', async () => {
  const { runner, state } = setup()
  const hurt = await runProgram(runner, 'await skills.ouch({}); await skills.collect({}); return 1', { state })
  assert.equal(hurt.status, 'hurt')
  assert.deepEqual(hurt.calls.map((c) => c.skill), ['ouch'])
  const slow = await runProgram(runner, 'await skills.slow({ time_limit: 10 })', { state, seconds: 0.3 })
  runner.detach()
  assert.equal(slow.status, 'timeout')
})

test('while her program runs, nobody else starts a skill, and only one program at a time', async () => {
  const { runner, state } = setup()
  const pending = runProgram(runner, 'await skills.slow({})', { state })
  await new Promise((resolve) => setTimeout(resolve, 100))
  assert.throws(() => runner.start('collect', {}, { source: 'driver' }), BusyError)
  await assert.rejects(runProgram(runner, 'return 1', { state }), BusyError)
  runner.stop()
  await pending
  assert.equal((await runProgram(runner, 'return 2', { state })).value, 2) // free again
  runner.detach()
})
