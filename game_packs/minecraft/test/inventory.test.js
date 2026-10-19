// Her hands and her hotbar: hold an item (by name, kind or slot), arrange the 9 slots, and the
// hotbar in the state text. fight takes a named animal, never a player. A fake bot; no server
// (the real-server checks are in test/integration/body.itest.js).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { carried, hold, hotbar, putInHotbar, selectedSlot } from '../src/skills/inventory.js'
import { fight } from '../src/skills/fight.js'
import { describeHotbar } from '../src/describe.js'

const require = createRequire(import.meta.url)
const registry = require('minecraft-data')('26.1')

// `slots`: window slot -> item name (36-44 hotbar, 45 off-hand, 9-35 the rest of the inventory).
function inventoryBot (slots = {}, selected = 0) {
  const inv = []
  for (const [at, name] of Object.entries(slots)) inv[Number(at)] = { name, count: 1, slot: Number(at), type: registry.itemsByName[name].id }
  const bot = {
    registry,
    players: { tearDrop12: {} },
    quickBarSlot: selected,
    inventory: { slots: inv, items: () => inv.filter((i, at) => i && at >= 9 && at <= 44) },
    get heldItem () { return inv[36 + bot.quickBarSlot] ?? null },
    setQuickBarSlot (n) { bot.quickBarSlot = n },
    async moveSlotItem (from, to) {
      const a = inv[from]
      const b = inv[to]
      inv[to] = a
      inv[from] = b
      if (a) a.slot = to
      if (b) b.slot = from
    },
    async equip (item, dest) {
      if (dest === 'off-hand') return bot.moveSlotItem(item.slot, 45)
      return bot.moveSlotItem(item.slot, 36 + bot.quickBarSlot)
    },
    async unequip () {}
  }
  return bot
}

const ctxFor = (bot) => ({ bot, race: (p) => p, check () {}, sleep: async () => {} })

test('a kind means her best one: sword, pickaxe, planks', () => {
  const bot = inventoryBot({ 10: 'wooden_sword', 11: 'stone_sword', 36: 'iron_pickaxe', 12: 'birch_planks' })
  assert.equal(carried(bot, 'sword').name, 'stone_sword')
  assert.equal(carried(bot, 'wooden_sword').name, 'wooden_sword')
  assert.equal(carried(bot, 'pickaxe').name, 'iron_pickaxe')
  assert.equal(carried(bot, 'planks').name, 'birch_planks')
  assert.equal(carried(bot, 'axe'), null)
})

test('hold: an item already on the hotbar is selected, one elsewhere comes into her hand', async () => {
  const bot = inventoryBot({ 36: 'stone_pickaxe', 38: 'wooden_sword', 20: 'torch' })
  let end = await hold.run(ctxFor(bot), { item: 'sword', hand: 'main' })
  assert.equal(end.status, 'done')
  assert.deepEqual(end.data, { holding: 'wooden_sword', slot: 3 })
  assert.equal(selectedSlot(bot), 3)
  end = await hold.run(ctxFor(bot), { item: 'torch', hand: 'main' })
  assert.deepEqual(end.data, { holding: 'torch', slot: 3 }) // swapped into the selected slot
  assert.equal(bot.inventory.slots[20].name, 'wooden_sword')
  end = await hold.run(ctxFor(bot), { slot: 1, hand: 'main' })
  assert.deepEqual(end.data, { slot: 1, holding: 'stone_pickaxe' })
  end = await hold.run(ctxFor(bot), { item: 'diamond_sword', hand: 'main' })
  assert.equal(end.status, 'failed')
  assert.equal(end.reason, 'has no diamond_sword')
})

test('hold: the off-hand, and an empty hand', async () => {
  const bot = inventoryBot({ 36: 'stone_pickaxe', 15: 'shield' })
  const end = await hold.run(ctxFor(bot), { item: 'shield', hand: 'off' })
  assert.deepEqual(end.data, { off_hand: 'shield' })
  assert.equal(bot.inventory.slots[45].name, 'shield')
  const empty = await hold.run(ctxFor(bot), { item: 'nothing', hand: 'main' })
  assert.equal(empty.data.holding, null)
  assert.equal(bot.heldItem, null)
})

test('put_in_hotbar: the item goes to the slot and what was there swaps', async () => {
  const bot = inventoryBot({ 36: 'stone_pickaxe', 22: 'wooden_sword' })
  const end = await putInHotbar.run(ctxFor(bot), { item: 'sword', slot: 1 })
  assert.equal(end.status, 'done')
  assert.equal(hotbar(bot)[0].name, 'wooden_sword')
  assert.equal(bot.inventory.slots[22].name, 'stone_pickaxe')
  const again = await putInHotbar.run(ctxFor(bot), { item: 'wooden_sword', slot: 1 })
  assert.equal(again.reason, 'wooden_sword is already in slot 1')
})

test('her hotbar in the state text', () => {
  const state = {
    hotbar: [{ slot: 1, name: 'wooden_sword', count: 1 }, { slot: 2, name: 'torch', count: 12 },
      ...Array.from({ length: 7 }, (_, i) => ({ slot: i + 3, name: null, count: 0 }))],
    hotbar_selected: 2,
    off_hand: { name: 'shield', count: 1 }
  }
  assert.deepEqual(describeHotbar(state), ['Hotbar: 1 wooden_sword, 2 [in hand] torch x12, 3 empty, 4 empty, 5 empty, ' +
    '6 empty, 7 empty, 8 empty, 9 empty. Off-hand: shield.'])
  assert.deepEqual(describeHotbar({}), [])
})

test('fight takes a named animal, never a player', () => {
  const bot = { registry, players: { tearDrop12: {} } }
  assert.doesNotThrow(() => fight.validate(bot, { target: 'sheep' }))
  assert.doesNotThrow(() => fight.validate(bot, { target: 'zombie' }))
  assert.throws(() => fight.validate(bot, { target: 'player' }), /doesn't fight players/)
  assert.throws(() => fight.validate(bot, { target: 'tearDrop12' }), /doesn't fight players/)
  assert.throws(() => fight.validate(bot, { target: 'dragonfly' }), /no mob is called dragonfly/)
})
