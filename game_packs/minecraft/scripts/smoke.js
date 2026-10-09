// End-to-end smoke test with no Minecraft install: start a throwaway flying-squid server on
// loopback, run the pack against it, and read her state through the API.
//   node scripts/smoke.js
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// flying-squid and the viewer stop at 1.21.4, so the smoke test stays there whatever she plays on.
const SMOKE_VERSION = '1.21.4'

import mineflayer from 'mineflayer'

const require = createRequire(import.meta.url)
const mcServer = require('flying-squid')

const MC_PORT = 25599
const API_PORT = 18790
const VIEWER_PORT = 18791
const TOKEN = 'smoke-token'
const worldDir = mkdtempSync(join(tmpdir(), 'columbina-smoke-'))

const server = mcServer.createMCServer({
  'motd': 'smoke',
  'port': MC_PORT,
  'host': '127.0.0.1',
  'max-players': 2,
  'online-mode': false,
  'logging': false,
  'gameMode': 0,
  'difficulty': 1,
  'worldFolder': join(worldDir, 'world'),
  'generation': { name: 'superflat', options: { worldHeight: 80 } },
  'kickTimeout': 10000,
  'plugins': {},
  'modpe': false,
  'view-distance': 4,
  'player-list-text': { header: '', footer: '' },
  'everybody-op': false,
  'max-entities': 50,
  'version': SMOKE_VERSION
})

const pack = spawn(process.execPath, ['src/index.js'], {
  env: {
    ...process.env,
    MC_HOST: '127.0.0.1',
    MC_PORT: String(MC_PORT),
    MC_AUTH: 'offline',
    MC_VERSION: SMOKE_VERSION,
    PACK_API_PORT: String(API_PORT),
    PACK_VIEWER_PORT: String(VIEWER_PORT),
    PACK_API_TOKEN: TOKEN
  },
  stdio: ['ignore', 'inherit', 'inherit']
})

let packExit = null
pack.on('exit', (code) => { packExit = code })

async function get (path, token = TOKEN) {
  const res = await fetch(`http://127.0.0.1:${API_PORT}${path}`, { headers: { authorization: `Bearer ${token}` } })
  return { status: res.status, body: await res.json() }
}

function finish (code, message) {
  console.log(message)
  pack.kill('SIGTERM')
  server.quit?.()
  rmSync(worldDir, { recursive: true, force: true })
  setTimeout(() => process.exit(code), 500)
}

async function check () {
  const deadline = Date.now() + 60000
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    if (packExit !== null) return `FAIL: the pack exited (${packExit})`
    const health = await get('/health').catch(() => null)
    if (!health?.body.spawned) continue

    const unauthorized = await get('/state', 'wrong')
    if (unauthorized.status !== 401) return `FAIL: wrong token gave ${unauthorized.status}`
    const { status, body } = await get('/state')
    if (status !== 200) return `FAIL: /state gave ${status}`
    for (const key of ['position', 'health', 'food', 'inventory', 'time', 'nearby']) {
      if (!(key in body)) return `FAIL: state has no ${key}`
    }
    // A second player joins: she should see them with a distance and a side.
    const friend = mineflayer.createBot({ host: '127.0.0.1', port: MC_PORT, username: 'Vincent', auth: 'offline', version: SMOKE_VERSION })
    await new Promise((resolve) => friend.once('spawn', resolve))
    await new Promise((resolve) => setTimeout(resolve, 1500))
    const after = await get('/state')
    const her = after.body.position
    const expected = friend.entity.position.distanceTo({ x: her.x, y: her.y, z: her.z })
    friend.quit()
    // Matched by distance, not label: flying-squid sends players as entity type 0, which
    // mineflayer decodes as a boat. A vanilla server labels them by username.
    const seen = after.body.nearby.find((e) => Math.abs(e.distance - expected) < 0.5)
    if (!seen) return `FAIL: she can't see the other player: ${JSON.stringify(after.body.nearby)}`
    if (!seen.side) return `FAIL: bad nearby entry ${JSON.stringify(seen)}`

    const viewer = await fetch(`http://127.0.0.1:${VIEWER_PORT}/`).then((r) => r.status).catch(() => 0)
    if (viewer !== 200) return `FAIL: viewer gave ${viewer}`
    console.log('\n' + after.body.text + '\n')
    return null
  }
  return 'FAIL: she never spawned'
}

const failure = await check()
finish(failure ? 1 : 0, failure ?? 'PASS: joined, state read, other player seen, viewer up')
