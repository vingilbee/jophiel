import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'

import { attachKills, HIT_WINDOW_MS } from '../src/kills.js'

function fakeBot () {
  const bot = new EventEmitter()
  bot.entity = { id: 1 }
  bot.swings = []
  bot.attack = (entity) => bot.swings.push(entity.id)
  return bot
}

test('a mob she hit that dies is her kill; one she never hit is not', () => {
  let clock = 1000
  const bot = fakeBot()
  attachKills(bot, { now: () => clock })
  const sheep = { id: 7, name: 'sheep' }
  bot.attack(sheep)
  assert.deepEqual(bot.swings, [7]) // the real swing still happens
  clock += 1200
  bot.emit('entityDead', sheep)
  bot.emit('entityDead', { id: 8, name: 'sheep' }) // the user's kill
  assert.deepEqual(bot.killCounts, { sheep: 1 })
})

test('a death long after her last hit is not hers, and each death counts once', () => {
  let clock = 0
  const bot = fakeBot()
  const kills = []
  bot.on('kill', (label) => kills.push(label))
  attachKills(bot, { now: () => clock })
  const zombie = { id: 9, name: 'zombie' }
  bot.attack(zombie)
  clock += HIT_WINDOW_MS + 1
  bot.emit('entityDead', zombie)
  assert.deepEqual(bot.killCounts, {})
  bot.attack(zombie)
  bot.emit('entityDead', zombie)
  bot.emit('entityDead', zombie)
  assert.deepEqual(bot.killCounts, { zombie: 1 })
  assert.deepEqual(kills, ['zombie'])
})
