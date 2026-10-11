// Background modes against a real vanilla server: following a player while guarding and gathering.
// `bun run itest`; SHOW_SERVER=1 for its log.
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { after, before, describe, test } from 'node:test'

import { ModeSettings } from '../../src/modes/modes.js'
import { attachSkills } from '../../src/pack.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const MEMORY = 'var/test-modes-explore.json'

describe('background modes on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let friend
  let runner
  let noticing
  let origin
  const modes = new ModeSettings()

  const mc = (cmd, expect, ms) => server.command(cmd, expect, ms)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  async function reset () {
    runner.stop()
    modes.set({ guard: false, gather: [], pickup: false }, bot)
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc(`effect give ${NAME} minecraft:instant_health 1 10`)
    await mc(`clear ${NAME}`)
    await mc('kill @e[type=!player]')
    await mc(`fill ${at(-20, 0, -20)} ${at(20, 6, 20)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
    await mc(`tp Friend ${at(8, 0, 0)}`)
    await until(() => bot.entity.position.distanceTo(origin) < 1)
    await until(() => bot.players.Friend?.entity, 5000)
    await sleep(300)
  }

  before(async () => {
    rmSync(MEMORY, { force: true })
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    friend = await joinBot(server.port, 'Friend')
    ;({ runner, noticing } = attachSkills(bot, { memoryPath: () => MEMORY, maxSeconds: 120, modes }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
  })

  after(async () => {
    runner?.detach()
    noticing?.detach()
    friend?.quit()
    bot?.quit()
    await server?.stop()
  })

  test('guard: a husk next to the player she follows, she fights it, then follows again', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:iron_sword`)
    await until(() => bot.inventory.items().some((i) => i.name === 'iron_sword'))
    modes.set({ guard: true }, bot)
    const record = runner.start('follow_player', { player: 'Friend', distance: 3, seconds: 20 })
    await sleep(1500)
    await mc('difficulty easy')
    await mc(`summon minecraft:husk ${at(11, 0, 2)}`)
    await until(() => record.data.interruptions?.some((i) => i.mode === 'guard'), 25000)
    assert.equal(record.status, 'running', 'still following afterwards')
    const [guarded] = record.data.interruptions
    assert.equal(guarded.status, 'done', JSON.stringify(guarded))
    assert.match(guarded.text, /killed 1 husk/)
    await until(() => bot.entity.position.distanceTo(friend.entity.position) < 5, 10000)
    assert.ok(noticing.since(0).some((n) => n.kind === 'mode' && /killed 1 husk/.test(n.text)))
    runner.stop()
  })

  test('without guard, the same husk ends the follow as hurt', async () => {
    await reset()
    const record = runner.start('follow_player', { player: 'Friend', distance: 3, seconds: 30 })
    await sleep(1000)
    await mc('difficulty easy')
    await mc(`summon minecraft:husk ${at(1, 0, 1)}`)
    const r = await runner.wait(record.id, 30000)
    assert.equal(r.status, 'hurt', JSON.stringify(r))
    assert.equal(r.data.interruptions, undefined)
  })

  test('gather: iron ore close by on the way, she mines it, then follows again', async () => {
    await reset()
    await mc(`give ${NAME} minecraft:stone_pickaxe`)
    await until(() => bot.inventory.items().some((i) => i.name === 'stone_pickaxe'))
    await mc(`setblock ${at(3, 0, 3)} minecraft:iron_ore`)
    modes.set({ gather: ['iron_ore'] }, bot)
    const record = runner.start('follow_player', { player: 'Friend', distance: 3, seconds: 20 })
    await until(() => record.data.interruptions?.some((i) => i.mode === 'gather'), 25000)
    const [gathered] = record.data.interruptions
    assert.equal(gathered.status, 'done', JSON.stringify(gathered))
    await until(() => bot.inventory.items().some((i) => i.name === 'raw_iron'), 5000)
    assert.equal(bot.blockAt(origin.offset(3, 0, 3)).name, 'air')
    assert.equal(record.status, 'running', 'still following afterwards')
    runner.stop()
  })

  test('gather leaves ore alone she has no pickaxe for', async () => {
    await reset()
    await mc(`setblock ${at(3, 0, 3)} minecraft:iron_ore`)
    modes.set({ gather: ['iron_ore'] }, bot)
    const record = runner.start('follow_player', { player: 'Friend', distance: 3, seconds: 4 })
    const r = await runner.wait(record.id, 10000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.interruptions, undefined)
    assert.equal(bot.blockAt(origin.offset(3, 0, 3)).name, 'iron_ore')
  })
})
