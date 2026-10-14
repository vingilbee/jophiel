// Watching another player, and seeing a bed, against a real vanilla server (flat world). A second
// bot plays the user. Part of `bun run itest`.
import assert from 'node:assert/strict'
import { after, before, describe, test } from 'node:test'
import { createRequire } from 'node:module'

import { attachSkills } from '../../src/pack.js'
import { readState } from '../../src/state.js'
import { describeState } from '../../src/describe.js'
import { seenBlocks } from '../../src/sight.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const NAME = 'Columbina'
const FRIEND = 'Friend'

describe('watching a player on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let friend
  let noticing
  let origin

  const mc = (cmd) => server.command(cmd)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`
  const pos = (dx, dy, dz) => origin.offset(dx, dy, dz)
  const recent = () => readState(bot).players.find((p) => p.name === FRIEND)?.recent.map((r) => r.text) ?? []
  const newSince = (seq, kind) => noticing.since(seq).filter((n) => n.kind === kind)

  before(async () => {
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    friend = await joinBot(server.port, FRIEND)
    ;({ noticing } = attachSkills(bot, { memoryPath: () => null }))
    await until(() => bot.entity.onGround && friend.entity.onGround, 10000)
    origin = bot.entity.position.floored()
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('gamerule doDaylightCycle false')
    await mc(`fill ${at(-12, 0, -12)} ${at(12, 6, 12)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(4, 0, 0)}`)
    await mc(`tp ${FRIEND} ${at(4, 0, 0)} facing ${at(4, 0, -4)}`)
    await sleep(1500)
  })

  after(async () => {
    noticing?.detach()
    friend?.quit()
    bot?.quit()
    await server?.stop()
  })

  test('she sees what they hold and wear, and a crafting table they place', async () => {
    await mc(`item replace entity ${FRIEND} weapon.mainhand with minecraft:stone_pickaxe`)
    await mc(`item replace entity ${FRIEND} armor.chest with minecraft:iron_chestplate`)
    await mc(`give ${FRIEND} minecraft:crafting_table 1`)
    await until(() => readState(bot).players[0]?.armor.length, 5000)
    const seen = readState(bot).players.find((p) => p.name === FRIEND)
    assert.equal(seen.holding, 'stone_pickaxe')
    assert.deepEqual(seen.armor, ['iron_chestplate'])

    const seq = noticing.seq
    await until(() => friend.inventory.items().some((i) => i.name === 'crafting_table'), 5000)
    await friend.equip(friend.inventory.items().find((i) => i.name === 'crafting_table'), 'hand')
    await friend.placeBlock(friend.blockAt(pos(4, -1, -2)), new Vec3(0, 1, 0))
    await until(() => newSince(seq, 'player_activity')[0], 5000)
    assert.match(newSince(seq, 'player_activity')[0].text, /^Friend placed a crafting table, \d+ m/)
    assert.ok(recent().includes('placed crafting table'))
  })

  test('a block they break is theirs', async () => {
    await mc(`setblock ${at(4, 0, -3)} minecraft:dirt`)
    await sleep(500)
    await friend.dig(friend.blockAt(pos(4, 0, -3)))
    await until(() => recent().includes('broke dirt'), 5000)
  })

  test('an item they throw her way is news', async () => {
    const seq = noticing.seq
    await mc(`give ${FRIEND} minecraft:bread 2`)
    await until(() => friend.inventory.items().some((i) => i.name === 'bread'), 5000)
    await friend.lookAt(bot.entity.position.offset(0, 1.6, 0))
    await friend.toss(friend.registry.itemsByName.bread.id, null, 1)
    await until(() => newSince(seq, 'player_activity').find((n) => /threw bread x1 your way/.test(n.text)), 5000)
  })

  test('a bed is seen (beds are see-through), and their getting into it is news', async () => {
    await mc(`setblock ${at(4, 0, 2)} minecraft:white_bed[facing=south,part=foot]`)
    await mc(`setblock ${at(4, 0, 3)} minecraft:white_bed[facing=south,part=head]`)
    await sleep(800)
    const beds = bot.registry.blocksArray.filter((b) => b.name.endsWith('_bed')).map((b) => b.id)
    assert.ok(seenBlocks(bot, { matching: beds, maxDistance: 16 }).length >= 1, 'she cannot see the bed')
    assert.ok(readState(bot).blocks.some((b) => b.kind === 'bed'))

    const seq = noticing.seq
    await mc('time set midnight')
    await sleep(500)
    await friend.sleep(friend.blockAt(pos(4, 0, 3)))
    await until(() => newSince(seq, 'player_sleeping')[0], 6000)
    const text = describeState(readState(bot))
    assert.match(text, /Friend \([^)]*\): in bed;/)
    console.log(text) // for a look at the whole state as her hands and Columbina get it
    await friend.wake().catch(() => {})
  })
})
