// The state reader: what she knows about the game, as plain JSON. Read-only; nothing here acts.
//
// Mineflayer's yaw is in radians, 0 = facing north (-z), growing counter-clockwise seen from
// above (pi/2 = west). Its pitch is positive looking up. We report bearings relative to where
// she faces, positive to her right, and pitch positive looking down to match the desktop
// driver's convention.

import { createRequire } from 'node:module'

import { seenBlock } from './sight.js'
import { outOfSight } from './sightings.js'
import { watchedPlayers } from './watching.js'
import { wearing } from './skills/armor.js'
import { hotbar, offHand, selectedSlot } from './skills/inventory.js'
import { readSurroundings } from './surroundings.js'
import { readTerrain } from './terrain.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const DEG = 180 / Math.PI

export function normalizeDegrees (deg) {
  const wrapped = ((deg + 180) % 360 + 360) % 360 - 180
  return wrapped === -180 ? 180 : wrapped
}

// Bearing of (dx, dz) relative to a facing yaw: 0 ahead, +90 right, -90 left, 180 behind.
export function relativeBearing (yaw, dx, dz) {
  const targetYaw = Math.atan2(-dx, -dz)
  return normalizeDegrees(-(targetYaw - yaw) * DEG)
}

const SIDES = [
  [22.5, 'ahead'], [67.5, 'ahead-right'], [112.5, 'right'], [157.5, 'behind-right'],
  [180, 'behind']
]

export function bearingWord (bearing) {
  const magnitude = Math.abs(bearing)
  const [, word] = SIDES.find(([limit]) => magnitude <= limit)
  return bearing < 0 ? word.replace('right', 'left') : word
}

const COMPASS = ['north', 'north-west', 'west', 'south-west', 'south', 'south-east', 'east', 'north-east']

export function compass (yaw) {
  const turns = (((yaw * DEG) % 360) + 360) % 360
  return COMPASS[Math.round(turns / 45) % 8]
}

// Minecraft day: 0 = sunrise-ish morning, 12000 sunset starts, hostile mobs from ~13000.
export function dayPhase (timeOfDay) {
  if (timeOfDay < 12000) return 'day'
  if (timeOfDay < 13000) return 'sunset'
  if (timeOfDay < 23000) return 'night'
  return 'sunrise'
}

const round1 = (n) => Math.round(n * 10) / 10

export function entityLabel (entity) {
  if (entity.type === 'player') return entity.username ?? 'player'
  if (entity.name === 'item' && typeof entity.getDroppedItem === 'function') {
    const item = entity.getDroppedItem()
    if (item) return `${item.name} (dropped)`
  }
  return entity.name ?? entity.displayName ?? entity.type ?? 'unknown'
}

export function nearbyEntities (bot, radius, limit = 20) {
  const self = bot.entity
  const out = []
  for (const entity of Object.values(bot.entities)) {
    if (entity === self || !entity.position) continue
    const dx = entity.position.x - self.position.x
    const dy = entity.position.y - self.position.y
    const dz = entity.position.z - self.position.z
    const distance = Math.hypot(dx, dy, dz)
    if (distance > radius) continue
    const bearing = relativeBearing(self.yaw, dx, dz)
    out.push({
      id: entity.id, // what her code passes to act.attack (stage H)
      label: entityLabel(entity),
      kind: entity.type ?? 'other',
      distance: round1(distance),
      bearing: Math.round(bearing),
      side: bearingWord(bearing),
      dy: round1(dy),
      // Where it is, for code of hers that needs coordinates (stage H1).
      position: { x: round1(entity.position.x), y: round1(entity.position.y), z: round1(entity.position.z) }
    })
  }
  out.sort((a, b) => a.distance - b.distance)
  return out.slice(0, limit)
}

// Kinds of block worth knowing about before picking a skill: nearest of each, within 24 blocks.
const NOTABLE = [
  ['log', (n) => n.endsWith('_log')],
  ['ore', (n) => n.endsWith('_ore')],
  ['water', (n) => n === 'water'],
  ['lava', (n) => n === 'lava'],
  ['crafting_table', (n) => n === 'crafting_table'],
  ['furnace', (n) => n === 'furnace'],
  ['bed', (n) => n.endsWith('_bed')],
  ['chest', (n) => n === 'chest']
]

export function notableBlocks (bot, radius = 24) {
  const self = bot.entity
  const out = []
  for (const [kind, test] of NOTABLE) {
    const ids = bot.registry.blocksArray.filter((b) => test(b.name)).map((b) => b.id)
    const position = seenBlock(bot, { matching: ids, maxDistance: radius })?.position
    if (!position) continue
    const block = bot.blockAt(position)
    const dx = position.x + 0.5 - self.position.x
    const dz = position.z + 0.5 - self.position.z
    const bearing = relativeBearing(self.yaw, dx, dz)
    out.push({
      kind,
      name: block?.name ?? kind,
      distance: round1(Math.hypot(dx, position.y - self.position.y, dz)),
      side: bearingWord(bearing),
      dy: round1(position.y - self.position.y)
    })
  }
  return out.sort((a, b) => a.distance - b.distance)
}

// How many of each block there are within `radius` of `center` (a cube), air left out: counted
// before and after a program of hers, it shows what she built or broke (stage H1).
const AIRS = new Set(['air', 'cave_air', 'void_air'])
export const CENSUS_MAX_RADIUS = 16
export function blockCensus (bot, center, radius = 10) {
  const r = Math.max(1, Math.min(CENSUS_MAX_RADIUS, Math.floor(radius)))
  const c = { x: Math.floor(center.x), y: Math.floor(center.y), z: Math.floor(center.z) }
  const counts = {}
  let unloaded = 0
  for (let dx = -r; dx <= r; dx++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dz = -r; dz <= r; dz++) {
        const block = bot.blockAt(new Vec3(c.x + dx, c.y + dy, c.z + dz))
        if (!block) {
          unloaded++
          continue
        }
        if (!AIRS.has(block.name)) counts[block.name] = (counts[block.name] ?? 0) + 1
      }
    }
  }
  return { center: c, radius: r, counts, unloaded }
}

function mergedInventory (bot) {
  const counts = new Map()
  for (const item of bot.inventory.items()) {
    counts.set(item.name, (counts.get(item.name) ?? 0) + item.count)
  }
  return [...counts].map(([name, count]) => ({ name, count }))
}

export function readState (bot, { radius = 32, now = new Date() } = {}) {
  const self = bot.entity
  const below = bot.blockAt(self.position.offset(0, -0.5, 0))
  const timeOfDay = bot.time.timeOfDay
  const held = bot.heldItem
  const surroundings = safeSurroundings(bot)
  return {
    // Her height now and a while ago, counted from her own position over time.
    heights: heightTrail(bot, now.getTime(), self.position.y),
    at: now.toISOString(),
    username: bot.username,
    // Everyone else in the world right now (the user is one of them).
    players_online: Object.keys(bot.players).filter((name) => name !== bot.username),
    // Online but beyond entity range, with where she last saw them (null: not since she joined).
    players_out_of_sight: outOfSight(bot, now.getTime()),
    // In spectator mode: invisible to her and passing through walls, so not to be looked for.
    players_spectating: Object.values(bot.players)
      .filter((p) => p.username !== bot.username && p.gamemode === 3).map((p) => p.username),
    // The players she can see, and what they're doing (src/watching.js).
    players: watchedPlayers(bot),
    version: bot.version,
    dimension: bot.game.dimension,
    game_mode: bot.game.gameMode,
    position: { x: round1(self.position.x), y: round1(self.position.y), z: round1(self.position.z) },
    facing: {
      compass: compass(self.yaw),
      pitch_down_deg: Math.round(-self.pitch * DEG)
    },
    standing_on: below?.name ?? null,
    on_ground: self.onGround,
    in_water: Boolean(self.isInWater),
    health: round1(bot.health),
    food: bot.food,
    saturation: round1(bot.foodSaturation ?? 0),
    oxygen: bot.oxygenLevel ?? null,
    xp_level: bot.experience?.level ?? 0,
    held: held ? { name: held.name, count: held.count } : null,
    // Her 9 hotbar slots (1-9, as the player sees them), which one is in her hand, and her off-hand.
    hotbar: hotbar(bot),
    hotbar_selected: selectedSlot(bot),
    off_hand: offHand(bot),
    armor: wearing(bot),
    inventory: mergedInventory(bot),
    // Mobs she killed since she joined, counted (src/kills.js): {label: count}.
    kills: { ...(bot.killCounts ?? {}) },
    time: { time_of_day: timeOfDay, day: bot.time.day, phase: dayPhase(timeOfDay) },
    weather: bot.thunderState > 0 ? 'thunder' : bot.isRaining ? 'rain' : 'clear',
    nearby: nearbyEntities(bot, radius),
    blocks: notableBlocks(bot),
    // The shape of the place: sky or rock overhead, how far each way she can walk, cave mouths.
    surroundings,
    // The lie of the land: the high ground, which way it rises or falls, and underground the way out.
    terrain: safeTerrain(bot, surroundings)
  }
}

// Her height over the last few minutes, sampled as her state is read: what a player feels going down a
// shaft or up a hill. Kept on the bot (one per player), at most one sample per HEIGHT_EVERY_MS.
const HEIGHT_EVERY_MS = 5000
const HEIGHT_KEPT_MS = 6 * 60 * 1000
export function heightTrail (bot, nowMs, y) {
  const trail = (bot.heightSamples ??= [])
  if (!trail.length || nowMs - trail[trail.length - 1].t >= HEIGHT_EVERY_MS) trail.push({ t: nowMs, y })
  while (trail.length && nowMs - trail[0].t > HEIGHT_KEPT_MS) trail.shift()
  const at = (ago) => {
    const old = trail.find((s) => nowMs - s.t <= ago + HEIGHT_EVERY_MS)
    return old && nowMs - old.t >= ago - HEIGHT_EVERY_MS ? Math.floor(old.y) : null
  }
  const ys = trail.map((s) => Math.floor(s.y))
  return {
    now: Math.floor(y),
    one_min_ago: at(60 * 1000),
    five_min_ago: at(5 * 60 * 1000),
    lowest: ys.length ? Math.min(...ys, Math.floor(y)) : Math.floor(y),
    highest: ys.length ? Math.max(...ys, Math.floor(y)) : Math.floor(y),
    over_s: trail.length ? Math.round((nowMs - trail[0].t) / 1000) : 0
  }
}

function safeSurroundings (bot) {
  try {
    return readSurroundings(bot)
  } catch {
    return null // a half-loaded world
  }
}

function safeTerrain (bot, surroundings) {
  const over = surroundings?.overhead?.kind
  try {
    return readTerrain(bot, { underground: over === 'underground' || over === 'roof' })
  } catch {
    return null
  }
}
