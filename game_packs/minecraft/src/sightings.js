// Where she last saw each other player. Mineflayer only knows a player's position while they're
// within entity range (roughly 100 blocks); past that bot.players[name].entity is null although
// they're still online. Without this she can't tell "out of sight" from "gone", and after a death
// (she respawns far away) she couldn't find the user again.
const TICKS_PER_CHECK = 20 // 1 s

const SIGHTINGS = new WeakMap()

export function attachSightings (bot) {
  const seen = new Map() // username -> { x, y, z, at }
  SIGHTINGS.set(bot, seen)
  let ticks = 0
  const onTick = () => {
    if (++ticks % TICKS_PER_CHECK) return
    record(bot, seen, Date.now())
  }
  bot.on('physicsTick', onTick)
  return {
    detach () { bot.off('physicsTick', onTick) }
  }
}

export function record (bot, seen, now) {
  for (const [name, player] of Object.entries(bot.players)) {
    if (name === bot.username || !player.entity?.position) continue
    const p = player.entity.position
    seen.set(name, { x: Math.round(p.x), y: Math.round(p.y), z: Math.round(p.z), at: now })
  }
}

// { x, y, z, at } or null if she hasn't seen them since the pack started.
export function lastSeen (bot, username) {
  return SIGHTINGS.get(bot)?.get(username) ?? null
}

// Online players she can't see right now, with where she last saw them.
export function outOfSight (bot, now = Date.now()) {
  const self = bot.entity?.position
  return Object.entries(bot.players)
    .filter(([name, player]) => name !== bot.username && !player.entity)
    .map(([name]) => {
      const seen = lastSeen(bot, name)
      if (!seen || !self) return { name, last_seen: null }
      return {
        name,
        last_seen: { x: seen.x, y: seen.y, z: seen.z },
        seconds_ago: Math.round((now - seen.at) / 1000),
        distance: Math.round(Math.hypot(seen.x - self.x, seen.z - self.z))
      }
    })
}
