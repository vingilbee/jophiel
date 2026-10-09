// The noticing layer against a real vanilla server (flat world). Part of `bun run itest`.
import assert from 'node:assert/strict'
import { after, before, describe, test } from 'node:test'

import { startApi } from '../../src/api.js'
import { attachSkills } from '../../src/pack.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'

describe('noticing on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let noticing
  let origin

  const mc = (cmd) => server.command(cmd)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`
  const latest = () => noticing.seq
  const newSince = (seq, kind) => noticing.since(seq).filter((n) => n.kind === kind)
  const waitFor = (seq, kind, ms = 8000) => until(() => newSince(seq, kind)[0], ms, 200)

  async function reset () {
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('weather clear')
    await mc(`effect give ${NAME} minecraft:instant_health 1 10`)
    await mc('kill @e[type=!player]')
    await mc(`fill ${at(-30, 0, -30)} ${at(30, 6, 30)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 0, 0)} facing ${at(0, 0, -10)}`) // facing north
    await sleep(800)
  }

  before(async () => {
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    ;({ noticing } = attachSkills(bot, { memoryPath: () => null }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
  })

  after(async () => {
    noticing?.detach()
    bot?.quit()
    await server?.stop()
  })

  test('a village seen from 40 blocks away is noticed once, with where it is', async () => {
    await reset()
    const seq = latest()
    await mc(`summon minecraft:villager ${at(40, 0, 0)} {NoAI:1b}`)
    const notice = await waitFor(seq, 'village')
    // Directions: compass from her position; the side depends on where she happens to be looking.
    assert.match(notice.text, /^a village \(saw a villager\), \d+ m east \((ahead|behind|left|right)/)
    assert.ok(Math.abs(notice.data.distance - 40) <= 2)
    await mc(`summon minecraft:villager ${at(44, 0, 6)} {NoAI:1b}`)
    await sleep(4000)
    assert.equal(newSince(seq, 'village').length, 1, 'the same village was noticed twice')
  })

  test('a hostile close by is noticed as urgent, once, with its direction', async () => {
    await reset()
    await mc('difficulty easy')
    const seq = latest()
    await mc(`summon minecraft:husk ${at(0, 0, 5)} {NoAI:1b}`)
    const notice = await waitFor(seq, 'hostile', 5000)
    assert.equal(notice.urgency, 'urgent')
    assert.equal(notice.data.direction, 'south')
    assert.match(notice.text, /^husk 5 m south \(/)
    await sleep(2500)
    assert.equal(newSince(seq, 'hostile').length, 1, 'the same husk was announced again')
  })

  test('ore in the open is noticed; ore hidden in stone is not', async () => {
    await reset()
    const seq = latest()
    await mc(`fill ${at(-7, 0, -1)} ${at(-5, 2, 1)} minecraft:stone`)
    await mc(`setblock ${at(-6, 1, 0)} minecraft:diamond_ore`) // inside the stone
    await sleep(4000)
    assert.equal(newSince(seq, 'ore').length, 0, 'saw a diamond through stone')
    await mc(`setblock ${at(6, 0, 0)} minecraft:diamond_ore`)
    const notice = await waitFor(seq, 'ore')
    assert.equal(notice.data.name, 'diamond_ore')
    assert.equal(notice.data.x, origin.x + 6)
  })

  test('nightfall, night and morning', async () => {
    await reset()
    const seq = latest()
    await mc('time set 12100')
    await waitFor(seq, 'night_coming', 4000)
    await mc('time set 14000')
    await waitFor(seq, 'night', 4000)
    await mc('time set 1000')
    await waitFor(seq, 'morning', 4000)
  })

  test('players joining and leaving', async () => {
    await reset()
    const seq = latest()
    const friend = await joinBot(server.port, 'Friend')
    const joined = await waitFor(seq, 'player_joined', 5000)
    assert.equal(joined.data.player, 'Friend')
    friend.quit()
    await waitFor(seq, 'player_left', 5000)
  })

  test('taking damage', async () => {
    await reset()
    const seq = latest()
    await mc(`damage ${NAME} 6`)
    const notice = await waitFor(seq, 'hurt', 4000)
    assert.ok(notice.data.health <= 14)
  })

  test('GET /notices waits for something new', async () => {
    await reset()
    const port = 18798
    const http = await startApi({
      host: '127.0.0.1',
      port,
      token: 't',
      pack: { status: () => ({ spawned: true }), state: () => ({}), runner: () => null, manifest: () => [], noticing: () => noticing }
    })
    try {
      const since = latest()
      const pending = fetch(`http://127.0.0.1:${port}/notices?since=${since}&wait=10`, { headers: { authorization: 'Bearer t' } })
        .then((r) => r.json())
      await sleep(500)
      await mc('weather rain')
      const body = await pending
      assert.ok(body.notices.some((n) => n.kind === 'weather'), JSON.stringify(body))
      assert.ok(body.next > since)
    } finally {
      http.close()
    }
  })
})
