// Before she joins: can the Mac reach the world, and which version does it run?
//   node --env-file-if-exists=.env src/check.js
import { createRequire } from 'node:module'
import { assertPrivateHost, loadConfig } from './config.js'

const require = createRequire(import.meta.url)
const { ping } = require('minecraft-protocol')
const { testedVersions } = require('mineflayer/lib/version')
const minecraftData = require('minecraft-data')
const { viewerSupports } = await import('./viewer.js')

const config = loadConfig()
await assertPrivateHost(config.host)

let status
try {
  status = await ping({ host: config.host, port: config.port, closeTimeout: 5000 })
} catch (err) {
  console.error(`can't reach ${config.host}:${config.port} (${err.code ?? err.message}).`)
  console.error('Is the world open to LAN with that port, the VM on Bridged networking, and Java allowed through the VM firewall?')
  process.exit(1)
}

const version = status.version?.name ?? 'unknown'
const players = status.players?.sample?.map((p) => p.name).join(', ') || 'none listed'
console.log(`reached ${config.host}:${config.port}: Minecraft ${version}, ${status.players?.online ?? '?'} online (${players})`)

const pinned = config.version === 'auto' ? version : config.version
if (pinned !== version) {
  console.log(`MC_VERSION is ${config.version} but the world runs ${version}: set MC_VERSION=${version} (or auto).`)
}
// A patch release that shares a tested version's protocol (26.1.2 vs 26.1) is the same to her.
const protocol = status.version?.protocol
const tested = testedVersions.find((v) => minecraftData(v)?.version.version === protocol)
console.log(tested
  ? `Mineflayer supports ${version}${tested === version ? '' : ` (same protocol as ${tested})`}.`
  : `Mineflayer doesn't support ${version} yet (newest: ${testedVersions.at(-1)}); she can't join this world.`)
if (!viewerSupports(tested ?? version)) {
  console.log(`No browser viewer for ${version}; watch from the game or with /spectate.`)
}
