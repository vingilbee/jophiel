// Her air, and only hers. Mineflayer 4.39 sets bot.oxygenLevel from the air_supply of ANY entity
// whose metadata arrives (entities.js never checks whose it is), so a fish or a player drowning
// nearby read as her air. Seen live: standing on stone, nowhere near water, she counted 20 -> -1
// and kept crying out for air; 89 of 111 low-air readings in one session were out of water.
// This makes bot.oxygenLevel read only her own player's air_supply; mineflayer's writes are ignored.

const AIR_SUPPLY_FALLBACK_INDEX = 1 // the metadata index air_supply has had in every version so far

export function airSupplyIndex (registry) {
  const keys = registry?.entitiesByName?.player?.metadataKeys
  const i = Array.isArray(keys) ? keys.indexOf('air_supply') : -1
  return i >= 0 ? i : AIR_SUPPLY_FALLBACK_INDEX
}

// Air from one entity_metadata packet, if it's about her and carries air_supply (0-300 ticks
// -> 0-20, the way mineflayer counts it). undefined: the packet says nothing about her air.
export function ownAirFromPacket (packet, selfId, index) {
  if (selfId === undefined || selfId === null || packet?.entityId !== selfId) return undefined
  for (const m of packet.metadata ?? []) {
    if (m.key === index && typeof m.value === 'number') return Math.round(m.value / 15)
  }
  return undefined
}

export function attachOwnAir (bot) {
  let air = bot.oxygenLevel
  Object.defineProperty(bot, 'oxygenLevel', {
    configurable: true,
    enumerable: true,
    get: () => air,
    set: () => {} // mineflayer's writes: possibly another entity's air
  })
  let index = null
  const onMetadata = (packet) => {
    index ??= airSupplyIndex(bot.registry)
    const value = ownAirFromPacket(packet, bot.entity?.id, index)
    if (value === undefined) return
    air = value
    bot.emit('breath')
  }
  bot._client.on('entity_metadata', onMetadata)
  return () => bot._client.removeListener('entity_metadata', onMetadata)
}
