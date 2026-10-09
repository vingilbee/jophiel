// The state reader: what she knows about the game, as plain JSON. Read-only; nothing here acts.
//
// Mineflayer's yaw is in radians, 0 = facing north (-z), growing counter-clockwise seen from
// above (pi/2 = west). Its pitch is positive looking up. We report bearings relative to where
// she faces, positive to her right, and pitch positive looking down to match the desktop
// driver's convention.

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

function entityLabel (entity) {
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
      label: entityLabel(entity),
      kind: entity.type ?? 'other',
      distance: round1(distance),
      bearing: Math.round(bearing),
      side: bearingWord(bearing),
      dy: round1(dy)
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
    const position = bot.findBlock({ matching: ids, maxDistance: radius })?.position
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
  return {
    at: now.toISOString(),
    username: bot.username,
    // Everyone else in the world right now (the user is one of them).
    players_online: Object.keys(bot.players).filter((name) => name !== bot.username),
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
    inventory: mergedInventory(bot),
    time: { time_of_day: timeOfDay, day: bot.time.day, phase: dayPhase(timeOfDay) },
    weather: bot.thunderState > 0 ? 'thunder' : bot.isRaining ? 'rain' : 'clear',
    nearby: nearbyEntities(bot, radius),
    blocks: notableBlocks(bot)
  }
}
