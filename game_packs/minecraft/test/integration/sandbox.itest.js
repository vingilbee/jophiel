// Her own code in the sealed box, on a real vanilla server: a program made of skills crafts what
// it set out to, reads what she has, and stop() ends it mid-walk. `bun run itest`.
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { after, before, describe, test } from 'node:test'

import { attachSkills } from '../../src/pack.js'
import { runProgram } from '../../src/sandbox.js'
import { startApi } from '../../src/api.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const MEMORY = 'var/test-sandbox.json'

describe('her code on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let runner
  let origin

  const mc = (cmd, expect, ms) => server.command(cmd, expect, ms)

  before(async () => {
    rmSync(MEMORY, { force: true })
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    ;({ runner } = attachSkills(bot, { memoryPath: () => MEMORY, maxSeconds: 120 }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
  })

  after(async () => {
    runner?.detach()
    bot?.quit()
    await server?.stop()
  })

  test('a program of skills: logs to planks to sticks, checked against what she carries', async () => {
    await mc(`clear ${NAME}`)
    await mc(`give ${NAME} minecraft:oak_log 2`)
    await until(() => bot.inventory.items().some((i) => i.name === 'oak_log'))
    const result = await runProgram(runner, `
      const planks = await skills.craft({ item: 'oak_planks', count: 8 })
      if (planks.status !== 'done') return { stuck: planks.reason }
      const sticks = await skills.craft({ item: 'stick', count: 4 })
      const have = state().inventory
      log('have', have)
      return { planks: planks.status, sticks: sticks.status, inventory: have }
    `)
    assert.equal(result.status, 'done', JSON.stringify(result))
    assert.equal(result.value.sticks, 'done', JSON.stringify(result))
    assert.ok(bot.inventory.items().some((i) => i.name === 'stick'))
    assert.deepEqual(result.calls.map((c) => c.skill), ['craft', 'craft'])
  })

  test('stop ends her program mid-walk, and she stands still', async () => {
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
    await sleep(500)
    const pending = runProgram(runner, `
      await skills.go_to({ target: '${origin.x + 40} ${origin.y} ${origin.z}' })
      await skills.go_to({ target: '${origin.x} ${origin.y} ${origin.z + 40}' })
      return 'walked both'
    `)
    await until(() => runner.current?.skill === 'go_to', 5000)
    await sleep(1500)
    runner.stop('paused')
    const result = await pending
    assert.equal(result.status, 'stopped', JSON.stringify(result))
    assert.equal(result.calls.length, 1)
    const at = bot.entity.position.clone()
    await sleep(1000)
    assert.ok(bot.entity.position.distanceTo(at) < 0.5, 'still moving after stop')
  })

  test('through the API: start a program, busy while it runs, stop it, read its result', async () => {
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
    await sleep(500)
    const port = 18798
    const http = await startApi({
      host: '127.0.0.1',
      port,
      token: 'test-token',
      pack: { status: () => ({ connected: true, spawned: true }), state: () => ({}), runner: () => runner, manifest: () => [] }
    })
    try {
      const call = (method, path, body) => fetch(`http://127.0.0.1:${port}${path}`, {
        method,
        headers: { authorization: 'Bearer test-token', 'content-type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
      }).then(async (res) => ({ status: res.status, body: await res.json() }))

      assert.equal((await call('POST', '/programs', {})).status, 400)
      const quick = await call('POST', '/programs', { code: 'log("hi"); return state().health' })
      assert.equal(quick.status, 202)
      const quickDone = await call('GET', `/programs/${quick.body.id}?wait=10`)
      assert.equal(quickDone.body.status, 'done', JSON.stringify(quickDone.body))
      assert.equal(quickDone.body.value, 20)
      assert.deepEqual(quickDone.body.log, ['hi'])

      const walk = await call('POST', '/programs', { code: `await skills.go_to({ target: '${origin.x + 40} ${origin.y} ${origin.z}' })` })
      assert.equal(walk.status, 202)
      await until(() => runner.current?.skill === 'go_to', 5000)
      assert.equal((await call('POST', '/skills/run', { skill: 'eat' })).status, 409, 'her program holds the body')
      assert.equal((await call('POST', '/programs', { code: 'return 1' })).status, 409, 'one program at a time')
      await call('POST', '/skills/stop')
      const stopped = await call('GET', `/programs/${walk.body.id}?wait=10`)
      assert.equal(stopped.body.status, 'stopped', JSON.stringify(stopped.body))
      assert.equal((await call('GET', '/programs/00000000-0000-0000-0000-000000000000')).status, 404)
    } finally {
      http.close()
    }
  })
})
