// Settings for the Minecraft pack, read once from the environment (.env via --env-file-if-exists).
import { createHash } from 'node:crypto'
import { lookup } from 'node:dns/promises'
import { createRequire } from 'node:module'
import net from 'node:net'

const require = createRequire(import.meta.url)
const minecraftData = require('minecraft-data')
const { latestSupportedVersion } = require('mineflayer/lib/version')

// The newest release her player can speak: Mineflayer's latest supported version, moved up to
// the last patch release with the same protocol (26.1 -> 26.1.2). Updating Mineflayer moves it.
export function newestSupportedVersion () {
  const protocol = minecraftData(latestSupportedVersion).version.version
  const sameProtocol = minecraftData.versions.pc
    .filter((v) => v.version === protocol && v.releaseType === 'release')
    .map((v) => v.minecraftVersion)
  return sameProtocol.sort((a, b) => compareVersions(b, a))[0] ?? latestSupportedVersion
}

// -1, 0, 1 by release number ("1.21.4" < "26.1" < "26.1.2").
export function compareVersions (a, b) {
  const pa = a.split('.').map(Number)
  const pb = b.split('.').map(Number)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const diff = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (diff) return Math.sign(diff)
  }
  return 0
}

export const DEFAULT_VERSION = newestSupportedVersion()

function int (value, fallback) {
  const parsed = Number.parseInt(value ?? '', 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function loadConfig (env = process.env) {
  const auth = env.MC_AUTH ?? 'offline'
  if (auth !== 'offline' && auth !== 'microsoft') {
    throw new Error(`MC_AUTH must be "offline" or "microsoft", got "${auth}"`)
  }
  return {
    host: env.MC_HOST ?? '127.0.0.1',
    port: int(env.MC_PORT, 25565),
    version: env.MC_VERSION || DEFAULT_VERSION,
    username: env.MC_USERNAME ?? 'Columbina',
    auth,
    authCacheDir: env.MC_AUTH_CACHE_DIR ?? 'var/auth',
    apiHost: '127.0.0.1', // never configurable: the API is for this Mac only
    apiPort: int(env.PACK_API_PORT, 8790),
    apiToken: env.PACK_API_TOKEN ?? '',
    viewerPort: int(env.PACK_VIEWER_PORT, 8791),
    viewerFirstPerson: (env.PACK_VIEWER_FIRST_PERSON ?? 'false') === 'true',
    viewDistance: int(env.PACK_VIEWER_VIEW_DISTANCE, 6),
    nearbyRadius: int(env.PACK_NEARBY_RADIUS, 32),
    // Her trajectory's buffer (src/trajectory.js): chunks kept for the backend to pull, oldest dropped first.
    trajectoryBufferMb: int(env.PACK_TRAJECTORY_BUFFER_MB, 64)
  }
}

// Shared safety rule: LAN or single-player only. She never joins a public server.
export function isPrivateAddress (address) {
  if (net.isIPv4(address)) {
    const [a, b] = address.split('.').map(Number)
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 169 && b === 254)
  }
  if (net.isIPv6(address)) {
    const lower = address.toLowerCase()
    if (lower.startsWith('::ffff:')) return isPrivateAddress(lower.slice(7))
    return lower === '::1' || lower.startsWith('fc') || lower.startsWith('fd') || lower.startsWith('fe80')
  }
  return false
}

export async function assertPrivateHost (host) {
  const { address } = await lookup(host)
  if (!isPrivateAddress(address)) {
    throw new Error(`refusing to join ${host} (${address}): only LAN or local worlds are allowed`)
  }
  return address
}

// The UUID an offline-mode server gives a name: v3 UUID of "OfflinePlayer:<name>".
export function offlineUuid (name) {
  const hash = createHash('md5').update(`OfflinePlayer:${name}`).digest()
  hash[6] = (hash[6] & 0x0f) | 0x30
  hash[8] = (hash[8] & 0x3f) | 0x80
  const hex = hash.toString('hex')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
