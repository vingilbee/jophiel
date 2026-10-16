// The lie of the land on a real server (flat world): a steep terraced hill to climb, a cave whose
// only way out has a 2-block ledge, a player in spectator mode. Part of `bun run itest`.
import assert from 'node:assert/strict'
import { after, before, describe, test } from 'node:test'

import { attachSkills } from '../../src/pack.js'
import { readState } from '../../src/state.js'
import { describeState } from '../../src/describe.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'
const FRIEND = 'Friend'

describe('terrain on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let friend
  let runner
  let origin

  const mc = (cmd) => server.command(cmd)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  async function run (skill, params = {}, ms = 120000) {
    const record = runner.start(skill, params)
    const finished = await runner.wait(record.id, ms)
    assert.notEqual(finished.status, 'running', `${skill} still running after ${ms} ms`)
    return finished
  }

  before(async () => {
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    friend = await joinBot(server.port, FRIEND)
    ;({ runner } = attachSkills(bot, { memoryPath: () => null, maxSeconds: 300 }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('gamerule doDaylightCycle false')
    await mc(`gamemode creative ${FRIEND}`) // out of the way, and not a target
    // A steep hill east: dirt terraces 3 high, 2 in, around x+36. Top at +20 (standing +21).
    for (let k = 0; k <= 6; k++) {
      await mc(`fill ${at(21 + 2 * k, 0, -15 + 2 * k)} ${at(51 - 2 * k, 3 * k + 2, 15 - 2 * k)} minecraft:dirt`)
    }
    // A cave west: stone up to +14, a tunnel east from x-45, a 2-block ledge at x-35, then steps
    // up to the sky.
    await mc(`fill ${at(-60, 0, -10)} ${at(-15, 14, 10)} minecraft:stone`)
    await mc(`fill ${at(-45, 0, 0)} ${at(-36, 1, 0)} minecraft:air`)
    await mc(`fill ${at(-35, 0, 0)} ${at(-35, 3, 0)} minecraft:air`)
    await mc(`fill ${at(-34, 2, 0)} ${at(-34, 3, 0)} minecraft:air`)
    for (let i = 0; i <= 12; i++) await mc(`fill ${at(-33 + i, 2 + i, 0)} ${at(-33 + i, 4 + i, 0)} minecraft:air`)
    await mc(`tp ${FRIEND} ${at(0, 0, 30)}`)
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(0, 0, -10)}`)
    await sleep(2500)
  })

  after(async () => {
    runner?.detach()
    friend?.quit()
    bot?.quit()
    await server?.stop()
  })

  test('outdoors she knows where the high ground is, and how steep the way up is', async () => {
    const cold = Date.now()
    const t = readState(bot).terrain
    const coldMs = Date.now() - cold
    const warm = Date.now()
    readState(bot)
    const warmMs = Date.now() - warm
    console.log(`terrain: first read ${coldMs} ms, next ${warmMs} ms`)
    assert.ok(warmMs < 100, `a state read took ${warmMs} ms`)
    const p = t.land.peak
    assert.ok(p, JSON.stringify(t.land))
    assert.equal(p.compass, 'east')
    assert.equal(p.rel, 21)
    assert.equal(p.way_up.biggest_step, 3)
    assert.ok(t.land.sectors.find((s) => s.compass === 'east').max >= 15)
    assert.match(describeState(readState(bot)), /Highest ground \(peak\): [a-z-]+ \(east\) \d+ m .* 21 m above you: a hilltop\. The straight way there has steps up to 3 blocks high/)
  })

  test('go_to peak: walking alone can\'t climb 3-block steps, digging steps can', async () => {
    const walk = await run('go_to', { target: 'peak', time_limit: 40 })
    assert.notEqual(walk.status, 'done', JSON.stringify(walk))
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
    await sleep(1000)
    const r = await run('go_to', { target: 'peak', dig: 'yes', time_limit: 240 }, 250000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.ok(bot.entity.position.y >= origin.y + 18, `only got to ${bot.entity.position.y}`)
  })

  test('underground: the way out has a 2-block ledge, and go_to surface digs her out', async () => {
    await mc(`tp ${NAME} ${at(-45, 0, 0)} facing ${at(-40, 0, 0)}`)
    await sleep(1500)
    const s = readState(bot)
    assert.equal(s.surroundings.overhead.kind, 'underground')
    const w = s.terrain.way_out
    assert.ok(w, JSON.stringify(s.terrain))
    assert.equal(w.compass, 'east')
    assert.equal(w.climb?.height, 2, JSON.stringify(w))
    assert.equal(s.terrain.surface.up, 15)
    const text = describeState(s)
    assert.match(text, /Way out: \d+ m through open space, starting [a-z-]+ \(east\).*a 2 m climb/)
    assert.match(text, /Surface: about 15 m straight above you/)
    const r = await run('go_to', { target: 'surface', dig: 'yes', time_limit: 180 }, 200000)
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.ok(readState(bot).surroundings.overhead.kind !== 'underground', 'still underground')
  })

  test('a player in spectator mode is not looked for', async () => {
    await mc(`tp ${NAME} ${at(0, 0, 0)}`)
    await mc(`gamemode spectator ${FRIEND}`)
    await until(() => readState(bot).players_spectating.includes(FRIEND), 5000)
    assert.match(describeState(readState(bot)), /Friend is in spectator mode: invisible to you .* Don't go looking/)
    const r = await run('go_to', { target: `player:${FRIEND}`, time_limit: 10 })
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /spectator mode/)
    await mc(`gamemode creative ${FRIEND}`)
  })
})
