// Her body on a real vanilla server: the exact spot, next to a player, where a player looks,
// putting armor on, and never drowning. `bun run itest`; SHOW_SERVER=1 for the server's log.
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { after, before, describe, test } from 'node:test'

import { attachSkills } from '../../src/pack.js'
import { headInWater } from '../../src/skills/water.js'
import { wearing } from '../../src/skills/armor.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const FRIEND = 'Vincent'
const MEMORY = 'var/test-body.json'

describe('her body on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let friend
  let runner
  let origin

  const mc = (cmd, expect, ms) => server.command(cmd, expect, ms)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  async function run (skill, params = {}, ms = 60000) {
    const record = runner.start(skill, params)
    const finished = await runner.wait(record.id, ms)
    assert.notEqual(finished.status, 'running', `${skill} still running after ${ms} ms`)
    return finished
  }

  async function reset () {
    runner.stop()
    await until(() => !runner.current, 25000) // a reflex run ends by itself
    await mc('time set noon')
    await mc(`effect clear ${NAME}`)
    await mc(`effect give ${NAME} minecraft:instant_health 1 10`)
    await mc(`clear ${NAME}`)
    await mc('kill @e[type=!player]')
    await mc(`fill ${at(-16, 0, -16)} ${at(16, 14, 16)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
    await mc(`tp ${FRIEND} ${at(-3, 0, 0)}`)
    await until(() => bot.entity.position.distanceTo(origin) < 1)
    await until(() => (bot.oxygenLevel ?? 20) >= 20 && !runner.current, 15000) // breath back from the last test
    await sleep(500)
  }

  before(async () => {
    rmSync(MEMORY, { force: true })
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    friend = await joinBot(server.port, FRIEND)
    ;({ runner } = attachSkills(bot, { memoryPath: () => MEMORY, maxSeconds: 120 }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
  })

  after(async () => {
    runner?.detach()
    bot?.quit()
    friend?.quit()
    await server?.stop()
  })

  test('go_to range 0 stands on that exact block', async () => {
    await reset()
    const r = await run('go_to', { target: at(7, 0, 4), range: 0 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.deepEqual(bot.entity.position.floored(), origin.offset(7, 0, 4))
  })

  test('"next to me": go_to player range 1 ends right beside them', async () => {
    await reset()
    await mc(`tp ${FRIEND} ${at(10, 0, 6)}`)
    await sleep(500)
    const r = await run('go_to', { target: `player:${FRIEND}`, range: 1 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    const d = bot.entity.position.distanceTo(friend.entity.position)
    assert.ok(d <= 2, `${d.toFixed(1)} m from them`)
  })

  test('"go there": the floor block a player looks at', async () => {
    await reset()
    const spot = origin.offset(3, -1, 4) // the ground (flat world): she should end up standing on it
    await friend.lookAt(spot.offset(0.5, 1, 0.5), true)
    await sleep(600) // their head turn reaches her
    const r = await run('go_to', { target: `looking:${FRIEND}`, range: 0 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    // Head angles reach her rounded to ~1.4 degrees, so a glancing look can land a block off.
    const off = bot.entity.position.floored().minus(spot.offset(0, 1, 0))
    assert.ok(Math.abs(off.x) <= 1 && off.y === 0 && Math.abs(off.z) <= 1, `off by ${off}`)
  })

  test('"go there": a block that isn\'t a floor (a crafting table) means next to it', async () => {
    await reset()
    const table = origin.offset(5, 0, -4)
    await mc(`setblock ${at(5, 0, -4)} minecraft:crafting_table`)
    await friend.lookAt(table.offset(0.5, 0.5, 0.5), true)
    await sleep(600)
    const r = await run('go_to', { target: `looking:${FRIEND}`, range: 0 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.on, 'crafting_table') // above it there's room: standing on top is fine too
  })

  test('place: a full block goes into water next to the bank (a bridge), a torch doesn\'t', async () => {
    await reset()
    await mc(`fill ${at(2, -3, -1)} ${at(4, -1, 1)} minecraft:water`) // a pool in the ground ahead
    await mc(`give ${NAME} minecraft:dirt 2`)
    await mc(`give ${NAME} minecraft:torch 1`)
    await until(() => bot.inventory.items().some((i) => i.name === 'dirt'))
    let r = await run('place', { block: 'torch', where: at(2, -1, 0) })
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /taken \(water\)/)
    r = await run('place', { block: 'dirt', where: at(2, -1, 0) }) // resting against the bank at x+1
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(bot.blockAt(origin.offset(2, -1, 0)).name, 'dirt')
    await mc(`fill ${at(2, -3, -1)} ${at(4, -2, 1)} minecraft:dirt`) // the ground back as it was
    await mc(`fill ${at(2, -1, -1)} ${at(4, -1, 1)} minecraft:grass_block`)
  })

  test('wear: her best armor goes on, a worse piece stays in her bag', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:iron_chestplate`)
    await mc(`give ${NAME} minecraft:leather_chestplate`)
    await mc(`give ${NAME} minecraft:diamond_helmet`)
    await mc(`give ${NAME} minecraft:golden_boots`)
    await until(() => bot.inventory.items().length >= 4)
    const r = await run('wear')
    assert.equal(r.status, 'done', JSON.stringify(r))
    await sleep(300)
    assert.deepEqual(wearing(bot), { head: 'diamond_helmet', torso: 'iron_chestplate', feet: 'golden_boots' })
    assert.ok(bot.inventory.items().some((i) => i.name === 'leather_chestplate'))
  })

  test('hold and put_in_hotbar: the server sees what she holds, and the slots she arranged', async () => {
    await reset()
    await mc(`item replace entity ${NAME} hotbar.0 with minecraft:stone_pickaxe`)
    await mc(`item replace entity ${NAME} inventory.5 with minecraft:wooden_sword`)
    await mc(`item replace entity ${NAME} inventory.6 with minecraft:shield`)
    await until(() => bot.inventory.items().length >= 3)
    let r = await run('hold', { item: 'sword' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(bot.heldItem?.name, 'wooden_sword')
    // What the other player sees in her hand is what the server has.
    await until(() => friend.players[NAME]?.entity?.heldItem?.name === 'wooden_sword', 5000)
    r = await run('hold', { item: 'shield', hand: 'off' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    await until(() => bot.inventory.slots[45]?.name === 'shield', 5000)
    r = await run('put_in_hotbar', { item: 'wooden_sword', slot: 9 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    await until(() => bot.inventory.slots[44]?.name === 'wooden_sword', 5000)
    r = await run('hold', { slot: 1 })
    assert.equal(r.status, 'done', JSON.stringify(r))
    await until(() => bot.heldItem?.name === 'stone_pickaxe', 5000)
  })

  test('fight: a sheep she is told to, one, with her best weapon', async () => {
    await reset()
    await mc(`item replace entity ${NAME} inventory.3 with minecraft:stone_sword`)
    await until(() => bot.inventory.items().length >= 1)
    await mc(`summon minecraft:sheep ${at(4, 0, 0)} {NoAI:1b}`)
    await mc(`summon minecraft:sheep ${at(-4, 0, 3)} {NoAI:1b}`)
    await until(() => Object.values(bot.entities).filter((e) => e.name === 'sheep').length === 2, 5000)
    const r = await run('fight', { target: 'sheep' }, 60000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.killed, 1)
    assert.equal(bot.heldItem?.name, 'stone_sword')
    await sleep(1500)
    assert.equal(Object.values(bot.entities).filter((e) => e.name === 'sheep').length, 1) // the other one lives
  })

  test('breathing: dropped deep in water with nothing to do, she swims up before she drowns', async () => {
    await reset()
    // A glass tank, 12 deep, full of water; her air drains while she sits at the bottom.
    await mc(`fill ${at(8, 0, 8)} ${at(12, 13, 12)} minecraft:glass hollow`)
    await mc(`fill ${at(9, 0, 9)} ${at(11, 12, 11)} minecraft:water`)
    await mc(`fill ${at(9, 13, 9)} ${at(11, 13, 11)} minecraft:air`) // open at the top
    await mc(`tp ${NAME} ${at(10, 0, 10)}`)
    await until(() => headInWater(bot), 5000)
    const health = bot.health
    await until(() => runner.current?.skill === 'surface', 20000) // her air ran low
    assert.equal(runner.current.source, 'reflex')
    await until(() => !headInWater(bot), 15000)
    await until(() => !runner.current, 10000)
    assert.equal(runner.history().at(-1).status, 'done', JSON.stringify(runner.history().at(-1)))
    assert.ok(bot.health >= health, `drowned a little: ${health} -> ${bot.health}`)
  })

  test('breathing: under a ceiling, she swims to the air pocket at the side', async () => {
    await reset()
    // A shallow tank with a glass lid, except one corner open to the air.
    await mc(`fill ${at(-12, 0, 6)} ${at(-6, 4, 12)} minecraft:glass hollow`)
    await mc(`fill ${at(-11, 0, 7)} ${at(-7, 3, 11)} minecraft:water`)
    await mc(`setblock ${at(-7, 4, 11)} minecraft:air`)
    await mc(`tp ${NAME} ${at(-10, 0, 8)}`)
    await until(() => headInWater(bot), 5000)
    const health = bot.health
    await until(() => runner.current?.skill === 'surface', 20000)
    await until(() => !runner.current, 25000)
    const last = runner.history().at(-1)
    assert.equal(last.status, 'done', JSON.stringify(last))
    assert.ok(!headInWater(bot) || bot.oxygenLevel >= 18, `air ${bot.oxygenLevel}`)
    assert.ok(bot.health >= health, `drowned a little: ${health} -> ${bot.health}`)
  })

  // Walking along the bottom fails today (the pathfinder plans no routes through water); what
  // matters here is that she never drowns trying.
  test('breathing: a walk along the bottom of deep water never drowns her', async () => {
    await reset()
    await mc(`fill ${at(-14, -1, -14)} ${at(-2, 12, -2)} minecraft:glass hollow`)
    await mc(`fill ${at(-13, 0, -13)} ${at(-3, 11, -3)} minecraft:water`)
    await mc(`fill ${at(-13, 12, -13)} ${at(-3, 12, -3)} minecraft:air`)
    await mc(`tp ${NAME} ${at(-12, 0, -12)}`)
    await until(() => headInWater(bot), 5000)
    await sleep(300)
    // Along the bottom to the far corner: slow enough under water that her air runs low on the way.
    const record = runner.start('go_to', { target: at(-4, 0, -4), range: 1, time_limit: 90 })
    const end = await runner.wait(record.id, 95000)
    console.log('walk under water:', end.status, JSON.stringify(end.data.interruptions ?? []))
    assert.notEqual(end.status, 'running')
    assert.ok(bot.health >= 19, `drowned: health ${bot.health}`) // whatever the walk did, she kept breathing
  })
})
