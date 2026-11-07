// Columbina's Minecraft pack: join the world as her own player, read state, let the user watch,
// and run one skill at a time when asked through the API (her hands call it in phase 3).
//   node --env-file-if-exists=.env src/index.js
import { randomBytes } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import mineflayer from 'mineflayer'

import { startApi } from './api.js'
import { needsForBot } from './needs.js'
import { recipeFor } from './recipes.js'
import { assertPrivateHost, loadConfig } from './config.js'
import { describeState } from './describe.js'
import { attachSkills } from './pack.js'
import { ModeSettings } from './modes/modes.js'
import { readState } from './state.js'
import { startViewer, viewerSupports } from './viewer.js'
import { Trajectory } from './trajectory.js'

const TOKEN_FILE = 'var/api_token'
const config = loadConfig()

const log = (...parts) => console.log(new Date().toISOString(), ...parts)

if (!config.apiToken) {
  // Zero config: a fresh token per run, readable only by this user.
  config.apiToken = randomBytes(24).toString('base64url')
  mkdirSync('var', { recursive: true })
  writeFileSync(TOKEN_FILE, config.apiToken + '\n', { mode: 0o600 })
  log(`api token written to ${TOKEN_FILE}`)
}

let bot = null
let runner = null
let motion = null
let noticing = null
let sightings = null
let watching = null
let manifest = []
let spawned = false
let viewer = null
let retryMs = 2000
let quitting = false
const modes = new ModeSettings() // background modes, kept across reconnects
// Her trajectory (src/trajectory.js): one run per process, one stream per connection, kept for the backend.
const trajectory = new Trajectory({ bufferBytes: config.trajectoryBufferMb * 1024 * 1024 })

// Kick reasons arrive as chat components, sometimes wrapped in NBT.
function reasonText (reason) {
  if (typeof reason === 'string') return reason
  return reason?.value?.text?.value ?? reason?.text ?? JSON.stringify(reason)
}

function getState () {
  const state = readState(bot, { radius: config.nearbyRadius })
  trajectory.perceived(state) // what she perceived of the place, now (it is cached between reads)
  const recent = Date.now() - 120000
  state.noticed = (noticing?.since(0) ?? []).filter((n) => Date.parse(n.at) >= recent).slice(-5)
  const skill = runner?.current
  const on = modes.describe()
  return {
    ...state,
    skill: skill ? { id: skill.id, name: skill.skill, params: skill.params } : null,
    modes: modes.toJSON(),
    motion: motion ? { style: motion.style, max_turn_per_tick_deg: Math.round(motion.stats.maxStepDeg * 10) / 10, snaps: motion.stats.snaps, missed_swings: motion.stats.misses } : null,
    text: describeState(state) + `\nBackground modes: ${on ?? 'none on'}.`
  }
}

// Explored chunks and noted places, kept per world.
const memoryPath = () => `var/explore/${config.host}_${config.port}.json`

async function join () {
  await assertPrivateHost(config.host)
  const who = config.auth === 'microsoft' ? 'her Microsoft account' : config.username
  log(`joining ${config.host}:${config.port} as ${who} (${config.version})`)
  bot = mineflayer.createBot({
    host: config.host,
    port: config.port,
    username: config.username,
    auth: config.auth,
    version: config.version === 'auto' ? false : config.version, // false: ask the server
    profilesFolder: config.authCacheDir,
    // Microsoft sign-in prints a device code once; the token is cached in authCacheDir.
    onMsaCode: (data) => log(`sign in for her account: open ${data.verification_uri} and enter ${data.user_code}`)
  })
  ;({ runner, motion, noticing, sightings, watching, manifest } = attachSkills(bot, { memoryPath, modes, trajectory }))
  runner.on('start', (r) => log(`skill ${r.skill} ${JSON.stringify(r.params)}`))
  noticing.onNotice?.((n) => log(`noticed [${n.urgency}] ${n.kind}: ${n.text}`))
  runner.on('finish', (r) => log(`skill ${r.skill} -> ${r.status}${r.reason ? ` (${r.reason})` : ''} ${JSON.stringify(r.data)}`))

  bot.once('spawn', () => {
    spawned = true
    retryMs = 2000
    log(`in the world as ${bot.username}`)
    if (!viewerSupports(bot.version)) {
      log(`no browser viewer: it has no graphics for ${bot.version}; watch from the game or /spectate ${bot.username}`)
      return
    }
    viewer = startViewer(bot, {
      host: '127.0.0.1',
      port: config.viewerPort,
      firstPerson: config.viewerFirstPerson,
      viewDistance: config.viewDistance
    })
    log(`viewer on http://127.0.0.1:${config.viewerPort}`)
  })
  // Health and food arrive just after spawn.
  bot.once('health', () => log('\n' + describeState(readState(bot, { radius: config.nearbyRadius }))))
  bot.on('death', () => log('she died; respawning'))
  bot.on('kicked', (reason) => log('kicked:', reasonText(reason)))
  bot.on('error', (err) => log('error:', err.message))
  bot.once('end', (reason) => {
    spawned = false
    runner?.detach()
    noticing?.detach()
    sightings?.detach()
    watching?.detach()
    viewer?.close()
    viewer = null
    if (quitting) return
    log(`disconnected (${reason}); retrying in ${retryMs / 1000} s`)
    setTimeout(() => join().catch(fail), retryMs)
    retryMs = Math.min(retryMs * 2, 30000)
  })
}

function fail (err) {
  log('fatal:', err.message)
  process.exit(1)
}

await startApi({
  host: config.apiHost,
  port: config.apiPort,
  token: config.apiToken,
  pack: {
    status: () => ({ connected: Boolean(bot?.player), spawned }),
    state: getState,
    runner: () => (spawned ? runner : null),
    manifest: () => manifest,
    noticing: () => (spawned ? noticing : null),
    modes: () => modes,
    recipe: (item) => recipeFor(bot, item),
    needs: (name, count) => needsForBot(bot, name, count),
    trajectory: () => trajectory,
    perception: () => (spawned ? bot?.perception ?? null : null)
  }
})
log(`api on http://${config.apiHost}:${config.apiPort}`)
join().catch(fail)

for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(signal, () => {
    log('leaving the world')
    quitting = true
    trajectory.end('shutting down') // its last chunk says so (still in memory: a pack that exits loses its buffer)
    runner?.detach()
    bot?.quit()
    setTimeout(() => process.exit(0), 300)
  })
}
