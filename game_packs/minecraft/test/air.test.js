import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'

import { airSupplyIndex, attachOwnAir, ownAirFromPacket } from '../src/air.js'

const registry = { entitiesByName: { player: { metadataKeys: ['shared_flags', 'air_supply', 'custom_name'] } } }
const packet = (entityId, value) => ({ entityId, metadata: [{ key: 1, type: 'int', value }] })

test('air_supply index comes from the registry, 1 when unknown', () => {
  assert.equal(airSupplyIndex(registry), 1)
  assert.equal(airSupplyIndex({ entitiesByName: { player: { metadataKeys: ['a', 'b', 'air_supply'] } } }), 2)
  assert.equal(airSupplyIndex(undefined), 1)
})

test('only her own entity counts as her air', () => {
  assert.equal(ownAirFromPacket(packet(7, 150), 7, 1), 10)
  assert.equal(ownAirFromPacket(packet(8, 0), 7, 1), undefined) // a fish, a squid, the user
  assert.equal(ownAirFromPacket(packet(7, 150), undefined, 1), undefined) // not spawned yet
  assert.equal(ownAirFromPacket({ entityId: 7, metadata: [{ key: 0, value: 1 }] }, 7, 1), undefined)
})

test('another creature drowning nearby never moves her air (seen live)', () => {
  const bot = new EventEmitter()
  bot._client = new EventEmitter()
  bot.registry = registry
  bot.entity = { id: 7 }
  attachOwnAir(bot)
  let breaths = 0
  bot.on('breath', () => breaths++)

  bot._client.emit('entity_metadata', packet(7, 300))
  assert.equal(bot.oxygenLevel, 20)
  // What mineflayer 4.39 does for any entity: writes bot.oxygenLevel. Ignored now.
  bot._client.emit('entity_metadata', packet(42, -20))
  bot.oxygenLevel = -1
  assert.equal(bot.oxygenLevel, 20)
  bot._client.emit('entity_metadata', packet(7, 120))
  assert.equal(bot.oxygenLevel, 8)
  assert.equal(breaths, 2)
})
