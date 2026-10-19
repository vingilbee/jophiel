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
    assert.deepEqual(result.body, { in_water_seconds: 0, lowest_health: 20 })
  })

  test('her code can look at the world: blockAt and findBlocks', async () => {
    await mc(`setblock ${origin.x + 3} ${origin.y} ${origin.z} minecraft:crafting_table`)
    await sleep(300)
    const result = await runProgram(runner, `
      const p = state().position
      const ground = blockAt(p.x, p.y - 1, p.z)
      const tables = findBlocks('crafting_table', { radius: 8 })
      return { ground: ground.name, solid: ground.solid, table: tables[0] }
    `)
    assert.equal(result.status, 'done', JSON.stringify(result))
    assert.equal(result.value.solid, true)
    assert.deepEqual([result.value.table.x, result.value.table.y, result.value.table.z], [origin.x + 3, origin.y, origin.z])
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

  test('a move no skill has, out of single actions: she hits a rabbit until it is gone', async () => {
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
    await mc(`kill @e[type=rabbit]`)
    await mc(`summon minecraft:rabbit ${origin.x + 3} ${origin.y} ${origin.z} {NoAI:1b}`)
    await until(() => Object.values(bot.entities).some((e) => e.name === 'rabbit'), 5000)
    const result = await runProgram(runner, `
      const rabbit = state().nearby.find((e) => e.label === 'rabbit')
      if (!rabbit) return { done: false, reason: 'no rabbit' }
      const near = await skills.go_to({ target: 'mob:rabbit', range: 1 })
      if (near.status !== 'done') return { done: false, reason: near.reason }
      let swings = 0
      while (swings < 30 && state().nearby.some((e) => e.id === rabbit.id)) {
        const hit = await act.attack(rabbit.id)
        swings++
        if (hit.status !== 'done') log(hit.reason)
        await wait(hit.data.cooldown_ms ?? 300)
      }
      return { done: !state().nearby.some((e) => e.id === rabbit.id), swings }
    `)
    assert.equal(result.status, 'done', JSON.stringify(result))
    assert.equal(result.value.done, true, JSON.stringify(result))
    assert.ok(result.actions.by.attack >= 1)
  })

  test('climbing out by single actions: jump, put dirt under her feet, three times', async () => {
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
    await mc(`clear ${NAME}`)
    await mc(`give ${NAME} minecraft:dirt 8`)
    await until(() => bot.inventory.items().some((i) => i.name === 'dirt'))
    await until(() => bot.entity.onGround, 5000)
    const startY = bot.entity.position.y
    const result = await runProgram(runner, `
      for (let i = 0; i < 3; i++) {
        const p = state().position
        const x = Math.floor(p.x), y = Math.floor(p.y), z = Math.floor(p.z)
        await act.look(x + 0.5, y - 1, z + 0.5)
        await act.jump() // resolves once she's a block up
        const placed = await act.place(x, y, z, 'dirt')
        log(placed.status, placed.reason)
        await wait(500)
      }
      return state().position.y
    `)
    assert.equal(result.status, 'done', JSON.stringify(result))
    assert.ok(bot.entity.position.y >= startY + 2, JSON.stringify(result))
    // and back down: dig the block she stands on
    const down = await runProgram(runner, `
      const p = state().position
      return await act.dig(Math.floor(p.x), Math.floor(p.y) - 1, Math.floor(p.z))
    `)
    assert.equal(down.value.status, 'done', JSON.stringify(down))
    await mc(`fill ${origin.x} ${origin.y} ${origin.z} ${origin.x} ${origin.y + 3} ${origin.z} minecraft:air`) // no pillar to land in
  })

  // Live (2026-10-16): her code looked at the corner of the block under her, (x, y - 1, z), so place
  // turned her head first and she had landed back in the spot: "the game didn't accept it", 4 tries.
  test('a block under her own feet mid-jump goes in wherever she looks', async () => {
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
    await mc(`clear ${NAME}`)
    await mc(`give ${NAME} minecraft:dirt 8`)
    await until(() => bot.inventory.items().some((i) => i.name === 'dirt'))
    await until(() => bot.entity.onGround, 5000)
    const startY = bot.entity.position.y
    const result = await runProgram(runner, `
      const looks = [[0, -1, 0], [0, 1.6, 6], [0, -1, 0], [6, 1.6, 0]] // the corner below, ahead, the corner, aside
      const results = []
      for (const [dx, dy, dz] of looks) {
        const p = state().position
        const x = Math.floor(p.x), y = Math.floor(p.y), z = Math.floor(p.z)
        await act.look(x + dx, y + dy, z + dz)
        await act.jump()
        const placed = await act.place(x, y, z, 'dirt')
        results.push(placed.status + (placed.reason ? ': ' + placed.reason : ''))
        await wait(400)
      }
      return results
    `)
    assert.equal(result.status, 'done', JSON.stringify(result))
    assert.deepEqual(result.value, ['done', 'done', 'done', 'done'], JSON.stringify(result.value))
    assert.ok(bot.entity.position.y >= startY + 4, `rose only ${bot.entity.position.y - startY}`)
    await mc(`fill ${origin.x} ${origin.y} ${origin.z} ${origin.x} ${origin.y + 5} ${origin.z} minecraft:air`)
    await mc(`tp ${NAME} ${origin.x} ${origin.y} ${origin.z}`)
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
