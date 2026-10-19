// Watching other players, and seeing see-through blocks (beds). A fake bot; no server.
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { describePlayer } from '../src/describe.js'
import { seenBlocks } from '../src/sight.js'
import { attachWatching, watchedPlayers } from '../src/watching.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')
const registry = require('minecraft-data')('26.1')
const POSE = registry.entitiesByName.player.metadataKeys.indexOf('pose')

function block (name, position) {
  const data = registry.blocksByName[name]
  return { name, type: data.id, position, boundingBox: data.boundingBox, transparent: data.transparent }
}

function fakeBot ({ blocks = [] } = {}) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.username = 'Columbina'
  bot.entity = { position: new Vec3(0, 64, 0), yaw: 0, pitch: 0, eyeHeight: 1.62 }
  const friend = {
    type: 'player', username: 'tearDrop12', name: 'player', position: new Vec3(3, 64, 0),
    yaw: Math.PI / 2, pitch: 0, eyeHeight: 1.62, equipment: [], metadata: {}
  }
  bot.players = { Columbina: { username: 'Columbina', entity: bot.entity }, tearDrop12: { username: 'tearDrop12', entity: friend } }
  bot.entities = { 1: friend }
  bot.blockAt = (p) => blocks.find((b) => b.position.equals(p.floored())) ?? null
  bot.findBlocks = ({ matching }) => blocks.filter((b) => [].concat(matching).includes(b.type)).map((b) => b.position)
  // Everything not listed is air: walk the ray in small steps to the first listed block.
  bot.world = {
    raycast (from, dir, range, match) {
      for (let t = 0; t <= range; t += 0.05) {
        const b = bot.blockAt(from.plus(dir.scaled(t)))
        if (b && (!match || match(b))) return b
      }
      return null
    }
  }
  return { bot, friend }
}

function watch (bot) {
  let clock = 1000000
  const notices = []
  const watching = attachWatching(bot, { report: (kind, urgency, text, data) => notices.push({ kind, urgency, text, data }), now: () => clock })
  return { watching, notices, tick: (ms) => { clock += ms } }
}

test('a bed she can see is seen, though beds are see-through', () => {
  const bed = block('white_bed', new Vec3(2, 64, 0))
  assert.equal(bed.transparent, true) // why it used to be invisible
  const { bot } = fakeBot({ blocks: [bed] })
  assert.deepEqual(seenBlocks(bot, { matching: bed.type, maxDistance: 16 }).map(String), [String(bed.position)])
})

test('a block they swing at and break is theirs; a notable one is news', () => {
  const { bot, friend } = fakeBot()
  const { notices } = watch(bot)
  bot.emit('entitySwingArm', friend)
  bot.emit('blockUpdate', block('oak_log', new Vec3(4, 64, 0)), block('air', new Vec3(4, 64, 0)))
  bot.emit('entitySwingArm', friend)
  bot.emit('blockUpdate', block('oak_log', new Vec3(4, 65, 0)), block('air', new Vec3(4, 65, 0)))
  bot.emit('entitySwingArm', friend)
  bot.emit('blockUpdate', block('air', new Vec3(5, 64, 1)), block('crafting_table', new Vec3(5, 64, 1)))
  const [p] = watchedPlayers(bot)
  assert.deepEqual(p.recent.map((r) => r.text), ['broke oak log (x2)', 'placed crafting table'])
  assert.equal(notices.length, 1)
  assert.match(notices[0].text, /^tearDrop12 placed a crafting table, 5 m/)
})

test('a block changing with nobody swinging, or closer to her, is not theirs', () => {
  const { bot, friend } = fakeBot()
  const { tick } = watch(bot)
  bot.emit('blockUpdate', block('stone', new Vec3(4, 64, 0)), block('air', new Vec3(4, 64, 0))) // no swing
  bot.emit('entitySwingArm', friend)
  bot.emit('blockUpdate', block('air', new Vec3(-1, 64, 0)), block('white_bed', new Vec3(-1, 64, 0))) // her own
  tick(5000)
  bot.emit('blockUpdate', block('stone', new Vec3(4, 64, 1)), block('air', new Vec3(4, 64, 1))) // swing too old
  assert.deepEqual(watchedPlayers(bot)[0].recent, [])
})

test('hits, kills and getting hurt', () => {
  const { bot, friend } = fakeBot()
  const { notices, tick } = watch(bot)
  const zombie = { type: 'hostile', name: 'zombie', position: new Vec3(5, 64, 0) }
  bot.emit('entityHurt', zombie, friend)
  bot.emit('entityHurt', zombie, friend)
  bot.emit('entityDead', zombie)
  bot.emit('entityHurt', friend, zombie)
  tick(1000)
  bot.emit('entityHurt', friend, zombie) // within 15 s: counted, not news again
  assert.deepEqual(watchedPlayers(bot)[0].recent.map((r) => r.text),
    ['hit zombie (x2)', 'killed zombie', 'got hit by zombie (x2)'])
  assert.deepEqual(notices.map((n) => [n.kind, n.urgency, n.text]), [
    ['player_activity', 'info', 'tearDrop12 killed a zombie'],
    ['player_hurt', 'heads_up', 'tearDrop12 got hit by a zombie']
  ])
})

test('an item leaving their hands towards her is news', () => {
  const { bot } = fakeBot()
  const { watching, notices, tick } = watch(bot)
  const pickaxe = { id: 7, position: new Vec3(3, 65.5, 0), getDroppedItem: () => ({ name: 'stone_pickaxe', count: 1 }) }
  const given = { id: 8, position: new Vec3(3, 65, 0), getDroppedItem: () => ({ name: 'bread', count: 1 }) }
  const dirt = { id: 9, position: new Vec3(8, 64, 0), getDroppedItem: () => ({ name: 'dirt', count: 1 }) }
  Object.assign(bot.entities, { 7: pickaxe, 9: dirt }) // the /give one is already gone
  for (const item of [pickaxe, pickaxe, pickaxe, given, dirt]) bot.emit('itemDrop', item) // reported on each update
  tick(600)
  watching.check()
  assert.deepEqual(watchedPlayers(bot)[0].recent.map((r) => r.text), ['threw stone pickaxe x1'])
  assert.deepEqual(notices.map((n) => n.text), ['tearDrop12 threw stone pickaxe x1 your way'])
})

test('moving, getting into bed, holding, wearing and looking at', () => {
  const log = block('oak_log', new Vec3(1, 65, 0))
  const { bot, friend } = fakeBot({ blocks: [log] })
  const { watching, notices, tick } = watch(bot)
  friend.equipment[0] = { name: 'stone_pickaxe' }
  friend.equipment[4] = { name: 'iron_chestplate' }
  watching.check()
  tick(1000)
  friend.position = new Vec3(9, 64, 0)
  watching.check()
  let [p] = watchedPlayers(bot)
  assert.equal(p.moving, 'running east, away from you')
  assert.equal(p.holding, 'stone_pickaxe')
  assert.deepEqual(p.armor, ['iron_chestplate'])

  friend.position = new Vec3(3.5, 64, 0.5) // mid-block, looking west (yaw pi/2) at the log 2 m away
  friend.metadata[POSE] = 2
  tick(1000)
  watching.check()
  ;[p] = watchedPlayers(bot)
  assert.equal(p.pose, 'sleeping')
  assert.deepEqual(p.looking_at, { name: 'oak_log', distance: 2 })
  assert.deepEqual(notices.map((n) => n.text), ['tearDrop12 got into bed'])
  assert.equal(describePlayer(p),
    'tearDrop12: in bed; facing west (your left); holding stone pickaxe; wearing iron chestplate; looking at oak log 2 m away. ' +
    'Last minute: got into bed 0 s ago.')
})
