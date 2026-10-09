// Skills against a real vanilla server (flat world). `bun run itest`; SHOW_SERVER=1 for its log.
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { after, before, describe, test } from 'node:test'

import { startApi } from '../../src/api.js'
import { attachSkills } from '../../src/pack.js'
import { notableBlocks } from '../../src/state.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const MEMORY = 'var/test-explore.json'

describe('skills on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let runner
  let motion
  let manifest
  let origin // where she stands at the start of each test

  const mc = (cmd, expect, ms) => server.command(cmd, expect, ms)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  async function run (skill, params = {}, ms = 60000) {
    const record = runner.start(skill, params)
    const finished = await runner.wait(record.id, ms)
    assert.notEqual(finished.status, 'running', `${skill} still running after ${ms} ms`)
    return finished
  }

  // Fresh, still, fed and healthy at the same spot before each scenario.
  async function reset ({ clearItems = true } = {}) {
    runner.stop()
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('weather clear')
    await mc(`effect clear ${NAME}`)
    await mc(`effect give ${NAME} minecraft:instant_health 1 10`)
    if (clearItems) await mc(`clear ${NAME}`)
    await mc(`kill @e[type=!player]`)
    await mc(`fill ${at(-20, 0, -20)} ${at(20, 6, 20)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
    await until(() => bot.entity.position.distanceTo(origin) < 1)
    await sleep(300)
  }

  before(async () => {
    rmSync(MEMORY, { force: true })
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    ;({ runner, motion, manifest } = attachSkills(bot, { memoryPath: () => MEMORY, maxSeconds: 120 }))
    await until(() => bot.entity.onGround, 10000)
    const p = bot.entity.position.floored()
    origin = p
  })

  after(async () => {
    runner?.detach()
    bot?.quit()
    await server?.stop()
  })

  test('go_to a place arrives within range', async () => {
    await reset()
    const r = await run('go_to', { target: at(12, 0, 8), range: 2 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.ok(bot.entity.position.distanceTo(origin.offset(12, 0, 8)) <= 3)
  })

  test('motion: turning round to walk away is smooth, never a snap', async () => {
    await reset()
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(0, 0, -10)}`) // face north, then walk south
    await sleep(800)
    const steps = []
    let last = bot.entity.yaw
    const onTick = () => {
      const d = Math.abs(Math.atan2(Math.sin(bot.entity.yaw - last), Math.cos(bot.entity.yaw - last))) * 180 / Math.PI
      steps.push(d)
      last = bot.entity.yaw
    }
    bot.on('physicsTick', onTick)
    const r = await run('go_to', { target: at(0, 0, 14), range: 1.5 })
    bot.off('physicsTick', onTick)
    assert.equal(r.status, 'done', JSON.stringify(r))
    const turning = steps.filter((d) => d > 0.5)
    assert.ok(turning.length >= 8, `turned in only ${turning.length} ticks: that's a snap`)
    assert.ok(Math.max(...steps) <= 420 * 0.05 + 1.5, `a ${Math.max(...steps).toFixed(1)}° jump in one tick`)
    assert.equal(motion.stats.snaps, 0)
    assert.equal(motion.style, 'relaxed')
  })

  // A stone hut east of her with a door in its west wall; inside is (8, 0, 0).
  async function buildHut (doorOpen) {
    await mc(`fill ${at(6, -1, -2)} ${at(10, 3, 2)} minecraft:stone hollow`)
    await mc(`setblock ${at(6, 0, 0)} minecraft:oak_door[facing=east,half=lower,open=${doorOpen}]`)
    await mc(`setblock ${at(6, 1, 0)} minecraft:oak_door[facing=east,half=upper,open=${doorOpen}]`)
    await sleep(300)
  }
  const doorOpen = () => bot.blockAt(origin.offset(6, 0, 0))?.getProperties().open

  test('go_to walks in through an open door', async () => {
    await reset()
    await buildHut(true)
    const r = await run('go_to', { target: at(8, 0, 0), range: 1 }, 30000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(bot.blockAt(origin.offset(6, 0, 0)).name, 'oak_door', 'she broke the door')
  })

  test('go_to opens a closed door, goes in, and closes it behind her', async () => {
    await reset()
    await buildHut(false)
    const r = await run('go_to', { target: at(8, 0, 0), range: 1 }, 30000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(bot.blockAt(origin.offset(6, 0, 0)).name, 'oak_door', 'she broke the door')
    await until(() => doorOpen() === false, 4000) // closed behind her
  })

  test('follow_player follows a player into a house through its door', async () => {
    await reset()
    await buildHut(false)
    const friend = await import('./harness.js').then((h) => h.joinBot(server.port, 'Friend'))
    try {
      await mc(`tp Friend ${at(3, 0, 0)}`)
      await until(() => bot.players.Friend?.entity, 5000)
      const record = runner.start('follow_player', { player: 'Friend', distance: 1.5, seconds: 12 })
      await sleep(1500)
      await mc(`tp Friend ${at(9, 0, 1)}`) // now inside the hut
      const inside = () => {
        const p = bot.entity.position
        return p.x > origin.x + 7 && p.x < origin.x + 10 && Math.abs(p.z - origin.z - 0.5) < 2
      }
      await until(inside, 10000)
      runner.stop()
      await runner.wait(record.id, 2000)
    } finally {
      friend.quit()
    }
  })

  test('go_to never breaks a wall to get in', async () => {
    await reset()
    await mc(`fill ${at(6, -1, -2)} ${at(10, 3, 2)} minecraft:stone hollow`) // no door at all
    const r = await run('go_to', { target: at(8, 0, 0), range: 1, time_limit: 15 }, 30000)
    assert.notEqual(r.status, 'done')
    const walls = bot.findBlocks({ matching: bot.registry.blocksByName.stone.id, maxDistance: 16, count: 200 })
    assert.equal(walls.length, 5 * 5 * 5 - 3 * 3 * 3, 'a wall block is missing')
  })

  test('she refuses to follow, go to, give to or look at herself', async () => {
    await reset()
    assert.throws(() => runner.start('follow_player', { player: NAME }), /is you \(your own player\)/)
    assert.throws(() => runner.start('go_to', { target: `player:${NAME}` }), /is you/)
    assert.throws(() => runner.start('look_at', { target: `player:${NAME}` }), /is you/)
    assert.throws(() => runner.start('give', { player: NAME, item: 'bread' }), /is you/)
  })

  test('go_to an unknown block is refused before starting', async () => {
    await reset()
    assert.throws(() => runner.start('go_to', { target: 'block:not_a_block' }), /no block is called/)
  })

  test('stop ends a skill at once', async () => {
    await reset()
    const record = runner.start('go_to', { target: `${origin.x + 200} ${origin.z}` })
    await sleep(800)
    const t = Date.now()
    runner.stop()
    const r = await runner.wait(record.id, 2000)
    assert.equal(r.status, 'stopped')
    assert.ok(Date.now() - t < 500, 'stop took too long')
    await sleep(300)
    const p = bot.entity.position.clone()
    await sleep(600)
    assert.ok(bot.entity.position.distanceTo(p) < 0.5, 'she kept walking after stop')
  })

  test('a skill times out with its progress', async () => {
    await reset()
    const r = await run('go_to', { target: `${origin.x + 300} ${origin.z}`, time_limit: 2 })
    assert.equal(r.status, 'timeout')
  })

  test('mine collects logs from a tree trunk', async () => {
    await reset()
    await mc(`fill ${at(5, 0, 3)} ${at(5, 2, 3)} minecraft:oak_log`)
    await until(() => notableBlocks(bot).some((b) => b.kind === 'log'), 3000) // the state reader sees the tree
    const r = await run('mine', { block: 'oak_log', count: 3 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.ok(r.data.collected >= 3)
    assert.ok(bot.inventory.items().some((i) => i.name === 'oak_log' && i.count >= 3))
  })

  test('mine without a pickaxe says which tool it needs', async () => {
    await reset()
    await mc(`setblock ${at(3, 0, 0)} minecraft:stone`)
    const r = await run('mine', { block: 'stone', count: 1 })
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /no tool that can harvest stone \(needs .*pickaxe/)
  })

  test('craft: logs -> planks -> table -> sticks -> pickaxe, then mine stone with it', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:oak_log 3`)
    await until(() => bot.inventory.items().some((i) => i.name === 'oak_log'))

    let r = await run('craft', { item: 'wooden_pickaxe' })
    assert.equal(r.status, 'failed', 'a pickaxe needs a table and sticks')

    r = await run('craft', { item: 'oak_planks', count: 12 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.crafted, 12)
    r = await run('craft', { item: 'stick', count: 4 })
    assert.equal(r.status, 'done', JSON.stringify(r))

    r = await run('craft', { item: 'wooden_pickaxe' })
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /crafting table/)

    await mc(`setblock ${at(4, 0, 4)} minecraft:crafting_table`)
    r = await run('craft', { item: 'wooden_pickaxe' })
    assert.equal(r.status, 'done', JSON.stringify(r))

    await mc(`fill ${at(-4, 0, 2)} ${at(-4, 1, 2)} minecraft:stone`)
    r = await run('mine', { block: 'stone', count: 2 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.ok(bot.inventory.items().some((i) => i.name === 'cobblestone' && i.count >= 2))
  })

  test('eat: refuses when full, eats when hungry', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:bread 3`)
    await until(() => bot.inventory.items().some((i) => i.name === 'bread'))
    let r = await run('eat')
    assert.equal(r.status, 'failed')
    assert.equal(r.reason, 'not hungry')
    await mc('difficulty easy') // hunger doesn't drain on peaceful

    await mc(`effect give ${NAME} minecraft:hunger 15 255`)
    await until(() => bot.food < 16, 20000)
    await mc(`effect clear ${NAME}`)
    r = await run('eat')
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.ate, 'bread')
    assert.ok(r.data.gained > 0)
  })

  test('fight kills a husk with a sword', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:iron_sword`)
    await until(() => bot.inventory.items().some((i) => i.name === 'iron_sword'))
    await mc('difficulty easy')
    await mc(`summon minecraft:husk ${at(6, 0, 0)}`)
    await until(() => Object.values(bot.entities).some((e) => e.name === 'husk'))
    const r = await run('fight', { target: 'nearest_hostile' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.killed, 1)
    assert.equal(bot.heldItem?.name, 'iron_sword')
  })

  test('fight refuses passive mobs', async () => {
    await reset()
    assert.throws(() => runner.start('fight', { target: 'cow' }), /isn't hostile/)
  })

  test('low health ends any skill as hurt, then she eats', async () => {
    await reset()
    await mc('difficulty easy') // hunger doesn't drain on peaceful
    await mc(`give ${NAME} minecraft:cooked_beef 2`)
    await until(() => bot.inventory.items().some((i) => i.name === 'cooked_beef'))
    await mc(`effect give ${NAME} minecraft:hunger 15 255`)
    await until(() => bot.food < 18, 20000)
    await mc(`effect clear ${NAME}`)
    const record = runner.start('go_to', { target: `${origin.x + 200} ${origin.z}` })
    await sleep(800)
    await mc(`damage ${NAME} 15`)
    const r = await runner.wait(record.id, 15000)
    assert.equal(r.status, 'hurt', JSON.stringify(r))
    assert.match(r.reason, /health low/)
    assert.equal(r.data.ate, 'cooked_beef')
  })

  test('explore finds a goal block and remembers where it has been', async () => {
    await reset()
    await mc(`forceload add ${origin.x - 64} ${origin.z - 64} ${origin.x + 64} ${origin.z + 64}`, /chunks?|Marked|already/i, 15000).catch(() => {})
    await mc(`setblock ${at(40, 0, 25)} minecraft:diamond_block`)
    const r = await run('explore', { goal: 'block:diamond_block', radius: 64 }, 120000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.found.name, 'diamond_block')
    assert.ok(r.data.explored_chunks >= 1)
    await mc(`forceload remove all`)
  })

  test('explore stops at night', async () => {
    await reset()
    await mc('time set midnight')
    await until(() => bot.time.timeOfDay > 17000)
    const r = await run('explore', { radius: 32 })
    assert.equal(r.status, 'failed')
    assert.equal(r.reason, 'night is coming')
  })

  test('sleep in a bed until morning', async () => {
    await reset()
    let r = await run('sleep')
    assert.equal(r.status, 'failed')
    assert.equal(r.reason, 'it isn\'t night')
    await mc(`setblock ${at(3, 0, 0)} minecraft:red_bed[facing=east,part=foot]`)
    await mc(`setblock ${at(4, 0, 0)} minecraft:red_bed[facing=east,part=head]`)
    await mc('time set midnight')
    await until(() => bot.time.timeOfDay > 17000)
    r = await run('sleep', {}, 30000)
    assert.equal(r.status, 'done', JSON.stringify(r)) // the only player asleep: the night skips
  })

  test('follow_player stays near a walking player', async () => {
    await reset()
    const friend = await import('./harness.js').then((h) => h.joinBot(server.port, 'Friend'))
    try {
      await mc(`tp Friend ${at(4, 0, 0)}`)
      await until(() => bot.players.Friend?.entity, 5000)
      const record = runner.start('follow_player', { player: 'Friend', distance: 3, seconds: 6 })
      await sleep(500)
      friend.setControlState('forward', true)
      await sleep(2500)
      friend.setControlState('forward', false)
      const r = await runner.wait(record.id, 10000)
      assert.equal(r.status, 'done', JSON.stringify(r))
      const gap = bot.entity.position.distanceTo(friend.entity.position)
      assert.ok(gap < 6, `${gap.toFixed(1)} blocks behind; friend at ${friend.entity.position}, she at ${bot.entity.position}`)
    } finally {
      friend.quit()
    }
  })

  test('the API runs a skill and reports its outcome', async () => {
    await reset()
    const port = 18797
    const http = await startApi({
      host: '127.0.0.1',
      port,
      token: 'test-token',
      pack: { status: () => ({ connected: true, spawned: true }), state: () => ({}), runner: () => runner, manifest: () => manifest }
    })
    try {
      const call = (method, path, body) => fetch(`http://127.0.0.1:${port}${path}`, {
        method,
        headers: { authorization: 'Bearer test-token', 'content-type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
      }).then(async (res) => ({ status: res.status, body: await res.json() }))

      const skills = await call('GET', '/skills')
      assert.deepEqual(skills.body.map((s) => s.name), [
        'go_to', 'mine', 'fight', 'explore', 'eat', 'sleep', 'craft', 'follow_player',
        'place', 'collect_items', 'give', 'look_at', 'store', 'take', 'smelt'
      ])
      assert.equal((await call('POST', '/skills/run', { skill: 'go_to', params: { target: 'block:nope' } })).status, 400)
      assert.equal((await call('POST', '/skills/run', { skill: 'dance' })).status, 400)

      const started = await call('POST', '/skills/run', { skill: 'go_to', params: { target: at(6, 0, 0) } })
      assert.equal(started.status, 202)
      assert.equal((await call('POST', '/skills/run', { skill: 'eat' })).status, 409, 'one skill at a time')
      const finished = await call('GET', `/skills/runs/${started.body.id}?wait=30`)
      assert.equal(finished.body.status, 'done', JSON.stringify(finished.body))
      const history = await call('GET', '/skills/history')
      assert.equal(history.body.at(-1).id, started.body.id)
    } finally {
      http.close()
    }
  })

  // What the server says she (or someone) carries: the client's view can lag or drift.
  async function serverCount (player, item) {
    const line = await server.command(`clear ${player} minecraft:${item} 0`, new RegExp(`(Found \\d+ matching|No items were found).*${player}`))
    return Number(/Found (\d+)/.exec(line)?.[1] ?? 0)
  }

  test('place: a crafting table next to her, then at a spot she stands on, refused on stone', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:crafting_table 2`)
    await until(() => bot.inventory.items().some((i) => i.name === 'crafting_table'))
    let r = await run('place', { block: 'crafting_table' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(bot.blockAt(origin.offset(r.data.x - origin.x, r.data.y - origin.y, r.data.z - origin.z)).name, 'crafting_table')
    const feet = bot.entity.position.floored()
    r = await run('place', { block: 'crafting_table', where: `${feet.x} ${feet.y} ${feet.z}` })
    assert.equal(r.status, 'done', JSON.stringify(r)) // stepped aside first
    await mc(`setblock ${at(5, 0, 5)} minecraft:stone`)
    await mc(`give ${NAME} minecraft:crafting_table 1`)
    await sleep(300)
    r = await run('place', { block: 'crafting_table', where: at(5, 0, 5) })
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /taken \(stone\)/)
  })

  test('collect_items picks up what lies around', async () => {
    await reset()
    await mc(`summon minecraft:item ${at(4, 0, 2)} {Item:{id:"minecraft:apple",count:3}}`)
    await mc(`summon minecraft:item ${at(-3, 0, 3)} {Item:{id:"minecraft:bread",count:2}}`)
    await sleep(500)
    const r = await run('collect_items', {})
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(await serverCount(NAME, 'apple'), 3)
    assert.equal(await serverCount(NAME, 'bread'), 2)
    assert.equal((await run('collect_items', {})).status, 'failed', 'nothing left to pick up')
  })

  test('give and look_at with another player', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:bread 3`)
    const friend = await import('./harness.js').then((h) => h.joinBot(server.port, 'Friend'))
    try {
      await mc(`tp Friend ${at(6, 0, 0)}`)
      await until(() => bot.players.Friend?.entity, 5000)
      let r = await run('look_at', { target: 'player:Friend', seconds: 1.5 })
      assert.equal(r.status, 'done', JSON.stringify(r))
      const d = friend.entity.position.minus(bot.entity.position)
      const yawToFriend = Math.atan2(-d.x, -d.z)
      const off = Math.abs(Math.atan2(Math.sin(bot.entity.yaw - yawToFriend), Math.cos(bot.entity.yaw - yawToFriend))) * 180 / Math.PI
      assert.ok(off < 15, `looking ${off.toFixed(0)}° away from Friend`)

      r = await run('give', { player: 'Friend', item: 'bread', count: 2 })
      assert.equal(r.status, 'done', JSON.stringify(r))
      // A tossed item can't be picked up for 2 s (Minecraft's pickup delay).
      let got = 0
      for (let i = 0; i < 12 && got < 2; i++) {
        await sleep(500)
        got = await serverCount('Friend', 'bread')
      }
      const lying = Object.values(bot.entities).filter((e) => e.name === 'item').map((e) => e.position.distanceTo(friend.entity.position).toFixed(1))
      assert.equal(got, 2, `items lying ${lying.join(', ')} blocks from Friend`)
      assert.equal(await serverCount(NAME, 'bread'), 1)
    } finally {
      friend.quit()
    }
  })

  test('store all (keeping tools and food), then take some back', async () => {
    await reset()
    await mc(`setblock ${at(3, 0, 0)} minecraft:chest`)
    await mc(`give ${NAME} minecraft:cobblestone 20`)
    await mc(`give ${NAME} minecraft:dirt 5`)
    await mc(`give ${NAME} minecraft:iron_pickaxe`)
    await mc(`give ${NAME} minecraft:bread 2`)
    await until(() => bot.inventory.items().length >= 4)
    let r = await run('store', { item: 'all' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.deepEqual(r.data.stored, { cobblestone: 20, dirt: 5 })
    assert.equal(await serverCount(NAME, 'cobblestone'), 0)
    assert.equal(await serverCount(NAME, 'iron_pickaxe'), 1)
    assert.equal(await serverCount(NAME, 'bread'), 2)
    const chest = await server.command(`data get block ${at(3, 0, 0)} Items`, /block data|has no|not a block entity/)
    assert.match(chest, /count: 20, Slot: \db, id: "minecraft:cobblestone"/)

    r = await run('take', { item: 'cobblestone', count: 7 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(await serverCount(NAME, 'cobblestone'), 7)
    r = await run('take', { item: 'diamond', count: 1 })
    assert.equal(r.status, 'failed')
  })

  test('smelt raw iron into ingots; dirt can\'t be smelted', async () => {
    await reset()
    await mc(`setblock ${at(-3, 0, 0)} minecraft:furnace`)
    await mc(`give ${NAME} minecraft:raw_iron 2`)
    await mc(`give ${NAME} minecraft:coal 2`)
    await until(() => bot.inventory.items().some((i) => i.name === 'raw_iron'))
    let r = await run('smelt', { item: 'raw_iron', count: 2 }, 90000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.output, 'iron_ingot')
    assert.equal(await serverCount(NAME, 'iron_ingot'), 2)

    await mc(`give ${NAME} minecraft:dirt 1`)
    await sleep(300)
    r = await run('smelt', { item: 'dirt' }, 60000)
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /can't be smelted/)
  })
})
