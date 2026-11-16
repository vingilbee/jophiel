// Her view and grounded movement on a real server (flat world): a raised stone platform she stands on,
// a ravine 9 deep across it, and beyond it a cliff with a cave mouth. She sees the mouth across the
// ravine; go_to to it finds no route, goes to the rim and says why; coordinates inside rock are refused
// without walking; a bridge makes the same go_to arrive. Part of `bun run itest`.
import assert from 'node:assert/strict'
import { after, before, describe, test } from 'node:test'

import { attachSkills } from '../../src/pack.js'
import { readState } from '../../src/state.js'
import { describeState } from '../../src/describe.js'
import { eulaAccepted, joinBot, sleep, startServer, until } from './harness.js'

const NAME = 'Columbina'

describe('her view and grounded go_to on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
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

  // Look north at the cliff (her idle gaze wanders, so the test aims her view itself).
  function lookNorth () {
    const p = bot.entity.position
    return bot.perception.look({ eye: { x: p.x, y: p.y + 1.62, z: p.z }, yaw: 0, pitch: -5 * Math.PI / 180 })
  }

  before(async () => {
    server = await startServer()
    bot = await joinBot(server.port, NAME)
    ;({ runner } = attachSkills(bot, { memoryPath: () => null, maxSeconds: 300 }))
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
    await mc('difficulty peaceful')
    await mc('time set noon')
    await mc('gamerule doDaylightCycle false')
    await mc(`fill ${at(-24, 0, -64)} ${at(24, 30, 12)} minecraft:air`)
    // The platform: stone 10 high (her feet at +10), its edges a 10 m drop to the flat ground.
    await mc(`fill ${at(-24, 0, -60)} ${at(24, 9, -26)} minecraft:stone`)
    await mc(`fill ${at(-24, 0, -25)} ${at(24, 9, 10)} minecraft:stone`)
    // The ravine across it: 7 wide, 9 deep.
    await mc(`fill ${at(-24, 1, -22)} ${at(24, 9, -16)} minecraft:air`)
    // The cliff, its face at -40, with a mouth 5 wide and 4 high going 16 in.
    await mc(`fill ${at(-24, 10, -60)} ${at(24, 25, -40)} minecraft:stone`)
    await mc(`fill ${at(-2, 10, -55)} ${at(2, 13, -40)} minecraft:air`)
    await mc(`tp ${NAME} ${at(0, 10, 0)} facing ${at(0, 10, -10)}`)
    await sleep(2500)
    await until(() => bot.entity.onGround && bot.entity.position.y > origin.y + 9, 10000)
  })

  after(async () => {
    runner?.detach()
    bot?.perception?.stop()
    bot?.quit()
    await server?.stop()
  })

  test('she sees the mouth across the ravine: where, how big, and the drop between', async () => {
    const t0 = Date.now()
    const now = lookNorth()
    console.log(`view: ${now.obs.ms} ms (${Date.now() - t0} ms with scene and landmarks), ${now.obs.rays} rays, ${now.obs.steps} lookups`)
    const [mouth] = now.scene.openings
    assert.ok(mouth, JSON.stringify(now.scene))
    assert.equal(mouth.z, origin.z - 40)
    assert.equal(mouth.y, origin.y + 10)
    assert.ok(mouth.width >= 4 && mouth.width <= 6, JSON.stringify(mouth))
    assert.ok(mouth.between.drop >= 8, JSON.stringify(mouth.between))
    assert.ok(now.obs.ms < 150, `a view took ${now.obs.ms} ms`)
    const state = readState(bot)
    const lm = [...state.view.in_view, ...state.view.remembered].find((l) => l.kind === 'opening')
    assert.ok(lm, JSON.stringify(state.view))
    assert.equal(lm.id, 'L1')
    const text = describeState(state)
    assert.match(text, /L1 opening \(cave or tunnel mouth\?\)/)
    console.log(text.split('\n').filter((l) => /In view|L1|Remembered|route/.test(l)).join('\n'))
  })

  test('go_to the mouth with no way across: no route, to the rim, says what is in the way', async () => {
    const t0 = Date.now()
    const r = await run('go_to', { target: 'L1' })
    const s = (Date.now() - t0) / 1000
    console.log(`go_to L1 (no bridge): ${r.status} in ${s.toFixed(1)} s: ${r.reason}`)
    assert.equal(r.status, 'failed')
    assert.match(r.reason, /no route to L1/)
    assert.match(r.reason, /drop/)
    assert.equal(r.data.nav.problem, 'no_route')
    assert.ok(s < 30, `took ${s} s`)
    // She went to the rim of the ravine: the closest she can get.
    const z = bot.entity.position.z - origin.z
    assert.ok(z <= -12 && z >= -16, `at z ${z}`)
    const lm = bot.perception.landmarks().get('L1')
    assert.equal(lm.approaches.at(-1).outcome, 'went to the closest reachable spot')
    assert.equal(lm.route.status, 'no_route')
  })

  test('coordinates inside rock are refused without walking; a guessed height is put on the ground', async () => {
    const before = bot.entity.position.clone()
    const r = await run('go_to', { target: `${origin.x + 10} ${origin.y + 16} ${origin.z - 50}`, range: 0 })
    assert.equal(r.status, 'failed')
    assert.equal(r.data.nav.problem, 'invalid_spot')
    assert.match(r.reason, /inside stone/)
    assert.ok(bot.entity.position.distanceTo(before) < 1)
    const up = await run('go_to', { target: `${origin.x + 3} ${origin.y + 18} ${origin.z - 8}`, range: 0 })
    assert.equal(up.status, 'done', JSON.stringify(up))
    assert.deepEqual(up.data.adjusted.to, { x: origin.x + 3, y: origin.y + 10, z: origin.z - 8 })
    assert.equal(Math.floor(bot.entity.position.y), origin.y + 10)
  })

  test('with a bridge across, the same go_to reaches the mouth; look_around there sees inside', async () => {
    await mc(`fill ${at(-1, 9, -23)} ${at(1, 9, -15)} minecraft:oak_planks`)
    await sleep(1500)
    const r = await run('go_to', { target: 'L1' })
    assert.equal(r.status, 'done', JSON.stringify(r))
    assert.equal(r.data.landmark, 'L1')
    const p = bot.entity.position.floored()
    assert.ok(Math.abs(p.z - (origin.z - 40)) <= 4 && Math.abs(p.x - origin.x) <= 3, `at ${p}`)
    const look = await run('look_around', {})
    assert.equal(look.status, 'done', JSON.stringify(look))
    assert.equal(look.data.views, 4)
    console.log('look_around:', JSON.stringify(look.data))
  })
})
