// A small private Minecraft server on the Mac for Columbina and the user (stage G phase 0).
//
//   node scripts/server.js setup --bind <mac-lan-ip> --player <your-name> --bot <her-account-name>
//                                [--online] [--world <folder>] [--op] [--accept-eula]
//   node scripts/server.js start
//
// The version follows her player: MC_VERSION, or by default the newest release Mineflayer
// supports. `start` upgrades the server jar when that moves (the world upgrades with it on first
// load) and refuses to go back, since an older server can't open a newer world.
//
// Why a server on the Mac rather than "Open to LAN" in the VM: hosting the world inside the VM
// costs frames the game needs, and the VM sits behind VMware NAT, so the Mac can't reach a world
// opened inside it, while the VM can reach the Mac.
//
// --online keeps Minecraft's account check on: every player, her included (MC_AUTH=microsoft),
// must sign in with a real account, so nobody can join under someone else's name. Without it,
// accounts are off and a whitelist by name is the only gate. Either way, bind it to the home
// LAN, never a public address.
import { createHash } from 'node:crypto'
import { execFileSync, spawn, spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

import { DEFAULT_VERSION, compareVersions, isPrivateAddress, offlineUuid } from '../src/config.js'

const DIR = 'server'
const LEVEL = 'columbina'
// In a server folder: server.jar, and installed.json = { version, java } (what the jar is and needs).
const MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'

async function getJson (url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url}: ${res.status}`)
  return res.json()
}

async function downloadJar (version, dir) {
  const manifest = await getJson(MANIFEST)
  const entry = manifest.versions.find((v) => v.id === version)
  if (!entry) throw new Error(`unknown Minecraft version ${version}`)
  const { downloads, javaVersion } = await getJson(entry.url)
  const res = await fetch(downloads.server.url)
  const jar = Buffer.from(await res.arrayBuffer())
  const sha1 = createHash('sha1').update(jar).digest('hex')
  if (sha1 !== downloads.server.sha1) throw new Error('server jar checksum mismatch')
  writeFileSync(join(dir, 'server.jar'), jar)
  writeFileSync(join(dir, 'installed.json'), JSON.stringify({ version, java: javaVersion.majorVersion }, null, 2) + '\n')
}

function installed (dir) {
  const record = join(dir, 'installed.json')
  const jar = join(dir, 'server.jar')
  if (existsSync(record)) return JSON.parse(readFileSync(record, 'utf8'))
  if (!existsSync(jar)) return null
  // A jar from before installed.json existed: it carries its own version.json.
  const { id, java_version: java } = JSON.parse(execFileSync('unzip', ['-p', jar, 'version.json'], { encoding: 'utf8' }))
  return { version: id, java }
}

// Make server.jar the wanted version: download if missing, upgrade if older, never downgrade.
export async function ensureJar (wanted, dir = DIR) {
  mkdirSync(dir, { recursive: true })
  const have = installed(dir)
  if (have?.version === wanted) return have
  if (have && compareVersions(wanted, have.version) < 0) {
    throw new Error(`the server is on ${have.version} and you asked for ${wanted}: an older server can't open a world ` +
      `saved by a newer one. Set MC_VERSION=${have.version}, or move ${join(dir, LEVEL)} away to start a fresh world.`)
  }
  console.log(have ? `upgrading the server ${have.version} -> ${wanted}...` : `downloading the ${wanted} server from Mojang...`)
  await downloadJar(wanted, dir)
  return installed(dir)
}

// A Java new enough for this server: MC_JAVA, Homebrew's openjdk@<n>, java_home, then PATH.
export function findJava (major) {
  const candidates = [
    process.env.MC_JAVA,
    `/opt/homebrew/opt/openjdk@${major}/bin/java`,
    `/usr/local/opt/openjdk@${major}/bin/java`,
    (() => { try { return execFileSync('/usr/libexec/java_home', ['-v', String(major)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim() + '/bin/java' } catch { return null } })(),
    'java'
  ].filter(Boolean)
  for (const java of candidates) {
    const result = spawnVersion(java)
    if (result !== null && result >= major) return java
  }
  throw new Error(`this server needs Java ${major}: brew install openjdk@${major} (or set MC_JAVA to its java)`)
}

function spawnVersion (java) {
  // `java -version` prints to stderr, e.g. 'openjdk version "25.0.4" ...' or '"1.8.0_..."'.
  const { stderr, error } = spawnSync(java, ['-version'], { encoding: 'utf8' })
  return error ? null : parseJavaMajor(stderr)
}

function parseJavaMajor (text) {
  const match = /version "(\d+)(?:\.(\d+))?/.exec(text ?? '')
  if (!match) return null
  const first = Number(match[1])
  return first === 1 ? Number(match[2]) : first
}

// An account's real UUID, from Mojang (online-mode servers whitelist by this, not by name).
async function onlineUuid (name) {
  const res = await fetch(`https://api.mojang.com/users/profiles/minecraft/${encodeURIComponent(name)}`)
  if (res.status === 404 || res.status === 204) throw new Error(`no Minecraft account is named ${name}`)
  if (!res.ok) throw new Error(`Mojang lookup for ${name}: ${res.status}`)
  const { id, name: exact } = await res.json()
  return { uuid: `${id.slice(0, 8)}-${id.slice(8, 12)}-${id.slice(12, 16)}-${id.slice(16, 20)}-${id.slice(20)}`, name: exact }
}

function properties (bind, port, online) {
  return Object.entries({
    'server-ip': bind,
    'server-port': port,
    'online-mode': online,
    'enforce-secure-profile': false, // chat signing only; off so a bot's chat never gets her kicked
    'white-list': true,
    'enforce-whitelist': true,
    'max-players': 4,
    'level-name': LEVEL,
    motd: 'Columbina',
    gamemode: 'survival',
    difficulty: 'easy',
    'spawn-protection': 0,
    'view-distance': 8,
    'simulation-distance': 8,
    'enable-rcon': false,
    'enable-query': false
  }).map(([k, v]) => `${k}=${v}`).join('\n') + '\n'
}

async function setup (args) {
  const { values } = parseArgs({
    args,
    options: {
      bind: { type: 'string', default: '127.0.0.1' },
      port: { type: 'string', default: '25565' },
      player: { type: 'string', multiple: true, default: [] },
      bot: { type: 'string', default: process.env.MC_USERNAME ?? 'Columbina' },
      version: { type: 'string', default: process.env.MC_VERSION || DEFAULT_VERSION },
      online: { type: 'boolean', default: false },
      world: { type: 'string' },
      op: { type: 'boolean', default: false },
      'accept-eula': { type: 'boolean', default: false }
    }
  })
  if (!isPrivateAddress(values.bind)) throw new Error(`--bind must be a LAN or loopback address, got ${values.bind}`)
  mkdirSync(DIR, { recursive: true })

  await ensureJar(values.version)

  // --world: start from a copy of an existing save (its folder, e.g. from .minecraft/saves).
  // It must come from the same Minecraft version or older; a newer save won't load.
  if (values.world) {
    const target = join(DIR, LEVEL)
    if (existsSync(target)) throw new Error(`${target} already exists; move it away to start from another world`)
    if (!existsSync(join(values.world, 'level.dat'))) throw new Error(`${values.world} is not a world folder (no level.dat)`)
    cpSync(values.world, target, { recursive: true })
    console.log(`copied ${values.world} -> ${target}`)
  }

  writeFileSync(join(DIR, 'server.properties'), properties(values.bind, values.port, values.online))
  const resolve = values.online ? onlineUuid : async (name) => ({ uuid: offlineUuid(name), name })
  const bot = await resolve(values.bot)
  const players = await Promise.all(values.player.map(resolve))
  const names = [bot, ...players].map((p) => p.name)
  writeFileSync(join(DIR, 'whitelist.json'), JSON.stringify([bot, ...players], null, 2))
  // --op lets the user /spectate her first-person from their own client.
  const ops = values.op ? players.map((p) => ({ ...p, level: 4, bypassesPlayerLimit: false })) : []
  writeFileSync(join(DIR, 'ops.json'), JSON.stringify(ops, null, 2))
  if (values['accept-eula']) writeFileSync(join(DIR, 'eula.txt'), 'eula=true\n')

  console.log(`ready: ${values.bind}:${values.port}, accounts ${values.online ? 'checked' : 'off'}, ` +
    `whitelist ${names.join(', ')}${values.op ? ', players are ops' : ''}`)
  if (!values['accept-eula']) {
    console.log('Read https://aka.ms/MinecraftEULA, then run setup again with --accept-eula.')
  }
}

async function start () {
  if (!existsSync(join(DIR, 'server.properties'))) throw new Error('run setup first')
  const { version, java: major } = await ensureJar(process.env.MC_VERSION || DEFAULT_VERSION)
  const java = findJava(major)
  console.log(`starting Minecraft ${version} with ${java}`)
  const child = spawn(java, ['-Xms1G', '-Xmx2G', '-jar', 'server.jar', 'nogui'], { cwd: DIR, stdio: 'inherit' })
  // Every way of being stopped (Ctrl+C, mprocs, a closed terminal) becomes a clean server stop,
  // which saves the world. Without the SIGHUP handler a closed terminal killed this wrapper and
  // left Java running headless, holding the port.
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => child.kill('SIGTERM'))
  child.on('exit', (code) => process.exit(code ?? 0))
}

// CLI only when run directly; the test harness imports ensureJar/findJava.
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [command, ...rest] = process.argv.slice(2)
  if (command === 'setup') await setup(rest)
  else if (command === 'start') await start()
  else {
    console.error('usage: node scripts/server.js setup|start')
    process.exit(2)
  }
}
