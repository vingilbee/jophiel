// Her surroundings and her air, against a real vanilla server (flat world): a hill with a cave
// mouth, water, a player to face, a fish suffocating on land next to her. Part of `bun run itest`.
import assert from 'node:assert/strict'
import { after, before, describe, test } from 'node:test'

import { attachSkills } from '../../src/pack.js'
import { readState } from '../../src/state.js'
import { describeState } from '../../src/describe.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const FRIEND = 'Friend'

describe('surroundings and air on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let friend
  let runner
  let origin

  const mc = (cmd) => server.command(cmd)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  before(async () => {
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    friend = await joinBot(server.port, FRIEND)
    ;({ runner } = attachSkills(bot, { memoryPath: () => null }))
    await until(() => bot.entity.onGround && friend.entity.onGround, 10000)
    origin = bot.entity.position.floored()
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('gamerule doDaylightCycle false')
    await mc(`fill ${at(-24, 0, -32)} ${at(24, 10, 24)} minecraft:air`)
    // A stone hill from 6 blocks north, with a 2-high tunnel into it that drops 2 further in.
    await mc(`fill ${at(-6, 0, -30)} ${at(6, 8, -6)} minecraft:stone`)
    await mc(`fill ${at(0, 0, -20)} ${at(0, 1, -6)} minecraft:air`)
    await mc(`fill ${at(0, -2, -20)} ${at(0, -1, -14)} minecraft:air`)
    // Water to the west, from 4 blocks out.
    await mc(`fill ${at(-20, -1, -2)} ${at(-4, -1, 2)} minecraft:water`)
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(0, 0, -10)}`)
    await mc(`tp ${FRIEND} ${at(3, 0, 3)} facing ${at(13, 0, 3)}`)
    await sleep(2000)
  })

  after(async () => {
    runner?.detach()
    friend?.quit()
    bot?.quit()
    await server?.stop()
  })

  // Compass, not sides: her gaze wanders while idle (src/motion), so what's "ahead" moves with it.
  test('she sees open sky, the cave mouth to the north, and water to the west', async () => {
    const s = await until(() => readState(bot).surroundings, 5000)
    assert.equal(s.overhead.kind, 'sky')
    const [mouth] = s.openings
    assert.ok(mouth, JSON.stringify(s))
    assert.equal(mouth.compass, 'north')
    assert.deepEqual(mouth.position, { x: origin.x, y: origin.y, z: origin.z - 6 })
    assert.equal(mouth.down, 2)
    assert.equal(s.ways.find((w) => w.compass === 'west').end.kind, 'water')
    const north = s.ways.find((w) => w.compass === 'north')
    assert.ok(north.walked >= 15, `walked ${north.walked} into the tunnel`)
    // The user turns east (a teleport doesn't move the head others see; turning does).
    await friend.look(-Math.PI / 2, 0, true)
    await until(() => readState(bot).players[0]?.facing?.compass === 'east', 5000)
    const text = describeState(readState(bot))
    assert.match(text, /Openings:\n {2}[a-z-]+ \(north\) 6 m/)
    assert.match(text, /Friend \([^)]*\): standing still; facing east \(your [a-z-]+\)/)
  })

  test('inside the tunnel she is underground', async () => {
    await mc(`tp ${NAME} ${at(0, 0, -9)} facing ${at(0, 0, -20)}`)
    await sleep(1000)
    const s = readState(bot).surroundings
    assert.equal(s.overhead.kind, 'underground')
    assert.deepEqual(s.openings, [])
    assert.equal(s.ways.find((w) => w.compass === 'west').walked, 0)
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(0, 0, -10)}`)
    await sleep(500)
  })

  test("a fish suffocating next to her is not her air (seen live: 'out of air' on dry land)", async () => {
    await mc(`summon minecraft:cod ${at(1, 0, 1)}`)
    const cod = await until(() => Object.values(bot.entities).find((e) => e.name === 'cod'), 5000)
    // The cod's air goes down out of water; mineflayer 4.39 alone would report it as hers.
    await until(() => typeof cod.metadata?.[1] === 'number' && cod.metadata[1] < 200, 10000)
    assert.ok(!(bot.oxygenLevel < 20), `her air read ${bot.oxygenLevel} on dry land`)
    await mc('kill @e[type=minecraft:cod]')
  })

  test('her own air still counts down under water', async () => {
    await mc(`fill ${at(9, 0, 9)} ${at(13, 4, 13)} minecraft:glass`)
    await mc(`fill ${at(10, 0, 10)} ${at(12, 4, 12)} minecraft:water`)
    await mc(`tp ${NAME} ${at(11, 0, 11)}`)
    await until(() => bot.oxygenLevel < 18, 10000)
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
  })
})
