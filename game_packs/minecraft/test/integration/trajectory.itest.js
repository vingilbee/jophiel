// Her trajectory against a real vanilla server (flat world): every tick she was in the world, checked against
// an independent listener; the damage causes the server reports (fall, lava, a mob, a death message); skill
// runs, commands and low-level actions in order; a reconnect as a new stream. Also measures what it costs:
// tick-handler time, encode time, bytes per minute, written to var/trajectory-measure.json.
// Part of `bun run itest` (needs the EULA accepted, see README).
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { after, before, describe, test } from 'node:test'
import { gunzipSync } from 'node:zlib'

import { DEFAULT_VERSION } from '../../src/config.js'
import { startApi } from '../../src/api.js'
import { attachSkills } from '../../src/pack.js'
import { CHUNK_TICKS, Trajectory, expandChunk } from '../../src/trajectory.js'
import { eulaAccepted, sleep, startServer, until } from './harness.js'

const require = createRequire(import.meta.url)
const mineflayer = require('mineflayer')
const NAME = 'Jophiel'

const decode = (chunk) => JSON.parse(gunzipSync(Buffer.from(chunk.data, 'base64')).toString('utf8'))

describe('her trajectory on a real server', { skip: !eulaAccepted() && 'accept the Minecraft EULA first (see README)' }, () => {
  let server
  let bot
  let runner
  let api
  let origin
  const token = 'trajectory-test-token'
  const port = 18000 + Math.floor(Math.random() * 1000)
  const trajectory = new Trajectory()
  const truth = [] // [tick index counted here, x, y, z]: an independent listener, not the encoder
  const mc = (cmd) => server.command(cmd)
  const at = (dx, dy, dz) => `${origin.x + dx} ${origin.y + dy} ${origin.z + dz}`

  function join () {
    const b = mineflayer.createBot({ host: '127.0.0.1', port: server.port, username: NAME, auth: 'offline', version: DEFAULT_VERSION })
    // Right after createBot, as the service does: the damage types arrive before she spawns.
    ;({ runner } = attachSkills(b, { memoryPath: () => null, trajectory }))
    let n = 0
    b.on('physicsTick', () => {
      const p = b.entity.position
      truth.push([n++, p.x, p.y, p.z])
    })
    return new Promise((resolve, reject) => {
      b.once('spawn', () => resolve(b))
      b.once('kicked', (reason) => reject(new Error(`kicked: ${JSON.stringify(reason)}`)))
      b.once('error', reject)
    })
  }

  async function call (method, path, body) {
    const response = await fetch(`http://127.0.0.1:${port}${path}`, {
      method,
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    })
    return { status: response.status, body: await response.json() }
  }

  async function everything () {
    await trajectory.settle()
    const out = trajectory.since(null, { maxChunks: 100000, maxBytes: Infinity })
    return { out, chunks: out.chunks.map(decode) }
  }

  before(async () => {
    server = await startServer()
    bot = await join()
    api = await startApi({
      host: '127.0.0.1', port, token,
      pack: {
        status: () => ({ connected: true, spawned: true }), state: () => ({}), runner: () => runner,
        manifest: () => [], noticing: () => null, modes: () => null, recipe: () => null, needs: () => null,
        trajectory: () => trajectory
      }
    })
    await until(() => bot.entity.onGround, 10000)
    origin = bot.entity.position.floored()
    await mc('gamerule immediate_respawn true')
    await mc(`gamemode survival ${NAME}`)
  })

  after(async () => {
    api?.close()
    bot?.quit()
    await server?.stop()
  })

  test('activity, damage from the server\'s own causes, and a death, all in one ordered record', async () => {
    const started = Date.now()
    await mc('difficulty peaceful')
    for (let i = 0; i < 6; i++) await mc(`summon minecraft:cow ${at(4 + i, 0, 6)}`) // entities around her
    await sleep(3000) // standing: unchanged ticks
    const go = await call('POST', '/skills/run', { skill: 'go_to', params: { target: `${origin.x + 8} ${origin.y} ${origin.z}` }, source: 'driver' })
    assert.equal(go.status, 202)
    await until(() => runner.get(go.body.id)?.status !== 'running', 30000)
    await mc(`setblock ${at(9, 0, 0)} minecraft:dirt`)
    await until(() => bot.blockAt(bot.entity.position.floored().offset(1, 0, 0))?.name === 'dirt' ||
      bot.findBlock({ matching: (b) => b.name === 'dirt', maxDistance: 4 }), 5000)
    const mine = await call('POST', '/skills/run', { skill: 'mine', params: { block: 'dirt', count: 1 } })
    await until(() => runner.get(mine.body.id)?.status !== 'running', 30000)
    const busy = await call('POST', '/skills/run', { skill: 'nothing_like_this' })
    assert.equal(busy.status, 400)

    await mc('difficulty normal')
    const here = bot.entity.position.floored()
    await mc(`tp ${NAME} ${here.x} ${here.y + 12} ${here.z}`) // a fall
    await sleep(4000)
    await mc(`effect give ${NAME} minecraft:instant_health 1 5`)
    await sleep(1000)
    await mc(`setblock ${here.x} ${here.y} ${here.z} minecraft:lava`) // lava at her feet
    await sleep(1500)
    await mc(`setblock ${here.x} ${here.y} ${here.z} minecraft:air`)
    await mc(`effect give ${NAME} minecraft:fire_resistance 30 1`)
    await mc(`effect give ${NAME} minecraft:instant_health 1 5`)
    await sleep(1500)
    await mc(`summon minecraft:zombie ${here.x + 1} ${here.y} ${here.z}`)
    await until(() => bot.health < 20, 15000).catch(() => {})
    await sleep(3000)
    await mc('kill @e[type=zombie]')
    await mc(`kill ${NAME}`)
    await sleep(3000)
    await mc('difficulty peaceful')

    const { out, chunks } = await everything()
    const events = chunks.flatMap((c) => c.events.map((e) => ({ tick: e[0], ms: c.started_ms + e[1], kind: e[2], data: e[3] })))
    const damage = events.filter((e) => e.kind === 'damage').map((e) => e.data)
    const types = new Set(damage.map((d) => d.type))
    assert.ok(types.has('minecraft:fall'), [...types].join())
    assert.ok(types.has('minecraft:lava'), [...types].join())
    const mob = damage.find((d) => d.type === 'minecraft:mob_attack')
    assert.ok(mob, [...types].join())
    assert.equal(mob.cause.name, 'zombie')
    assert.ok(damage.every((d) => d.type_source === 'server damage_type registry'))
    const death = events.find((e) => e.kind === 'death_message')
    assert.ok(death && /^death\./.test(death.data.translate), JSON.stringify(death))
    assert.ok(events.some((e) => e.kind === 'respawn'))
    // Every damage packet is there, however close together (her notices keep one per 15 s).
    const healthDrops = events.filter((e) => e.kind === 'health')
    assert.ok(healthDrops.length >= damage.length)

    // Commands, skill runs and actions, in order, linked by the run id.
    const kinds = events.map((e) => e.kind)
    const command = events.find((e) => e.kind === 'command' && e.data.answer?.id === go.body.id)
    const start = events.find((e) => e.kind === 'skill_start' && e.data.run_id === go.body.id)
    const finish = events.find((e) => e.kind === 'skill_finish' && e.data.run_id === go.body.id)
    assert.ok(command && start && finish)
    assert.ok(kinds.indexOf('skill_start') <= kinds.lastIndexOf('skill_finish'))
    assert.ok(start.ms <= finish.ms && finish.data.status)
    assert.equal(events.find((e) => e.kind === 'command' && e.data.status === 400)?.data.answer.error !== undefined, true)
    assert.ok(events.some((e) => e.kind === 'act' && e.data.fn === 'pathfinder.setGoal'))
    const mined = runner.get(mine.body.id)
    assert.equal(mined.status, 'done', JSON.stringify(mined))
    assert.ok(events.some((e) => e.kind === 'act' && e.data.fn === 'dig'),
      [...new Set(events.filter((e) => e.kind === 'act').map((e) => e.data.fn))].join())

    // Every tick, rebuilt from the chunks, matches what an independent listener saw at that tick.
    const stream = out.streams[0].id
    const states = chunks.filter((c) => c.stream === stream).flatMap(expandChunk)
    // One frame per tick, none skipped, up to the chunk still open (not sealed yet, so not served).
    states.forEach((s, i) => assert.equal(s.tick, i, 'one frame per tick'))
    assert.ok(truth.length >= states.length && truth.length - states.length <= CHUNK_TICKS,
      `${states.length} frames for ${truth.length} ticks`)
    let checked = 0
    for (const s of states) {
      const t = truth[s.tick]
      assert.equal(t[0], s.tick)
      assert.ok(Math.abs(s.state.p[0] - t[1]) < 0.002 && Math.abs(s.state.p[1] - t[2]) < 0.002 && Math.abs(s.state.p[2] - t[3]) < 0.002,
        `tick ${s.tick}: ${s.state.p} vs ${t.slice(1)}`)
      checked++
    }
    assert.ok(checked > 200)
    // No ticks while she was dead: a stretch of time without frames, never invented frames.
    const gaps = []
    for (let i = 1; i < states.length; i++) if (states[i].at_ms - states[i - 1].at_ms > 500) gaps.push(states[i].at_ms - states[i - 1].at_ms)
    assert.ok(gaps.length >= 1, 'the death shows as time without ticks')
    // Seq without holes, and each chunk starts where the last one ended.
    const own = chunks.filter((c) => c.stream === stream)
    own.forEach((c, i) => {
      assert.equal(c.seq, i)
      if (i) assert.equal(c.first_tick, own[i - 1].first_tick + own[i - 1].ticks)
    })

    const measured = measure(out.chunks, Date.now() - started)
    console.log('trajectory measurement (activity):', JSON.stringify(measured))
    mkdirSync('var', { recursive: true })
    writeFileSync('var/trajectory-measure.json', JSON.stringify({ when: new Date().toISOString(), activity: measured }, null, 2))
  })

  test('the API serves the chunks after a cursor, and a reconnect is a new stream', async () => {
    const first = await call('GET', '/trajectory')
    assert.equal(first.status, 200)
    const last = first.body.chunks.at(-1)
    const after = await call('GET', `/trajectory?stream=${last.stream}&seq=${last.seq}`)
    assert.ok(after.body.chunks.every((c) => c.stream !== last.stream || c.seq > last.seq))
    const old = trajectory.stream.id
    bot.quit()
    await until(() => trajectory.stream === null, 5000)
    truth.length = 0
    bot = await join()
    await sleep(2000)
    const { out } = await everything()
    const [a, b] = [out.streams.find((s) => s.id === old), out.streams.at(-1)]
    assert.ok(a.ended_at && a.end_reason)
    assert.notEqual(b.id, a.id)
    assert.equal(out.chunks.filter((c) => c.stream === b.id)[0]?.first_tick ?? 0, 0)
  })

  test('standing still for a minute: what an idle minute costs', async () => {
    const before = (await everything()).out.chunks.length
    const t0 = Date.now()
    await sleep(60000)
    const { out } = await everything()
    const idle = measure(out.chunks.slice(before), Date.now() - t0)
    console.log('trajectory measurement (idle):', JSON.stringify(idle))
    const file = 'var/trajectory-measure.json'
    let saved = {}
    try { saved = JSON.parse((await import('node:fs')).readFileSync(file, 'utf8')) } catch {}
    writeFileSync(file, JSON.stringify({ ...saved, idle }, null, 2))
    assert.ok(idle.ticks > 1000)
  })
})

function measure (chunks, ms) {
  const ticks = chunks.reduce((n, c) => n + c.ticks, 0)
  const bytes = chunks.reduce((n, c) => n + c.bytes, 0)
  const raw = chunks.reduce((n, c) => n + c.raw_bytes, 0)
  const tickMs = chunks.reduce((n, c) => n + c.overhead.tick_ms_total, 0)
  const maxTick = Math.max(0, ...chunks.map((c) => c.overhead.tick_ms_max))
  const encode = chunks.reduce((n, c) => n + c.overhead.encode_ms, 0)
  const minutes = ms / 60000
  return {
    seconds: Math.round(ms / 1000), chunks: chunks.length, ticks, bytes, raw_bytes: raw,
    bytes_per_minute: Math.round(bytes / minutes), bytes_per_hour: Math.round(bytes / minutes * 60),
    projected_30_days_continuous_mb: Math.round(bytes / minutes * 60 * 24 * 30 / 1e6),
    tick_handler_ms_mean: ticks ? Math.round(tickMs / ticks * 1000) / 1000 : null,
    tick_handler_ms_max: Math.round(maxTick * 1000) / 1000,
    encode_ms_per_chunk: chunks.length ? Math.round(encode / chunks.length * 1000) / 1000 : null,
    compression: raw ? Math.round(raw / bytes * 10) / 10 : null
  }
}
