// Her hands and her hotbar: what she holds, and what goes in which of the 9 hotbar slots.
// Before this she couldn't choose: mine, eat, place and fight switch to what they need by
// themselves, but asked to "use your wooden sword" on a sheep (2026-10-17) she said she would,
// nothing could, and her learned move swung with whatever was in her hand.
//
// Slots are numbered 1-9 as the player sees them (window slots 36-44); the off-hand is slot 45.
import { done, failed, timeLimitParam } from './contract.js'

const HOTBAR_START = 36
const OFF_HAND = 45
// Best first, for a word like "sword" or "pickaxe".
const MATERIALS = ['netherite', 'diamond', 'iron', 'copper', 'stone', 'golden', 'wooden']

const rank = (name) => {
  const i = MATERIALS.findIndex((m) => name.startsWith(`${m}_`))
  return i === -1 ? MATERIALS.length : i
}

// The 9 hotbar slots as the player sees them: [{ slot: 1, name, count } | { slot: 2, name: null }, ...].
export function hotbar (bot) {
  return Array.from({ length: 9 }, (_, i) => {
    const item = bot.inventory.slots[HOTBAR_START + i]
    return item ? { slot: i + 1, name: item.name, count: item.count } : { slot: i + 1, name: null, count: 0 }
  })
}

export function selectedSlot (bot) {
  return (bot.quickBarSlot ?? 0) + 1
}

export function offHand (bot) {
  const item = bot.inventory.slots[OFF_HAND]
  return item ? { name: item.name, count: item.count } : null
}

// What she carries that `word` means: the exact item, else the best of a kind ("sword" ->
// her best sword, "pickaxe", "axe", "planks", "log"). null when she has none.
export function carried (bot, word) {
  const items = bot.inventory.items()
  const name = String(word).trim().toLowerCase().replace(/\s+/g, '_')
  const exact = items.find((i) => i.name === name)
  if (exact) return exact
  const kind = items.filter((i) => i.name.endsWith(`_${name}`) || i.name.endsWith(`_${name.replace(/s$/, '')}`))
  return kind.sort((a, b) => rank(a.name) - rank(b.name))[0] ?? null
}

function hotbarIndexOf (bot, item) {
  for (let i = 0; i < 9; i++) {
    if (bot.inventory.slots[HOTBAR_START + i] === item) return i
  }
  return -1
}

export const hold = {
  name: 'hold',
  description: 'Hold an item in her hand (or off-hand): by name, by kind ("sword" = her best sword), or the item in a hotbar slot 1-9. ' +
    'Use it when the user asks her to use or switch to something. It stays in her hand until a skill needs another (mine, eat, place and fight switch by themselves).',
  doneWhen: 'it is in her hand',
  stopsWhen: 'she has no such item, or that slot is empty',
  params: {
    item: { type: 'string', description: 'an item id or kind, e.g. wooden_sword, sword, torch; "nothing" for an empty hand' },
    slot: { type: 'integer', min: 1, max: 9, description: 'a hotbar slot instead of an item: hold what is in it' },
    hand: { type: 'enum', values: ['main', 'off'], default: 'main', description: 'main hand (default) or off-hand (shield, torch, totem)' },
    time_limit: timeLimitParam(5, 15)
  },
  async run (ctx, params) {
    const { bot } = ctx
    if (params.slot && !params.item) {
      if (params.hand === 'off') return failed('a hotbar slot is for her main hand; name the item for the off-hand')
      const at = bot.inventory.slots[HOTBAR_START + params.slot - 1]
      bot.setQuickBarSlot(params.slot - 1)
      return done({ slot: params.slot, holding: at?.name ?? null }, at ? null : 'that slot is empty: her hand is empty')
    }
    if (!params.item) return failed('say which item (or a hotbar slot)')
    if (params.item === 'nothing' || params.item === 'empty') {
      const empty = hotbar(bot).find((s) => !s.name)
      if (params.hand === 'off') await ctx.race(bot.unequip('off-hand'))
      else if (empty) bot.setQuickBarSlot(empty.slot - 1)
      else await ctx.race(bot.unequip('hand'))
      return done({ holding: null, slot: selectedSlot(bot) })
    }
    const item = carried(bot, params.item)
    if (!item) return failed(`has no ${params.item}`, { inventory: bot.inventory.items().map((i) => i.name) })
    if (params.hand === 'off') {
      await ctx.race(bot.equip(item, 'off-hand'))
      return done({ off_hand: item.name })
    }
    const at = hotbarIndexOf(bot, item)
    if (at >= 0) bot.setQuickBarSlot(at) // already on the hotbar: just select it
    else await ctx.race(bot.equip(item, 'hand')) // moves it into the selected slot
    return done({ holding: bot.heldItem?.name ?? item.name, slot: selectedSlot(bot) })
  }
}

export const putInHotbar = {
  name: 'put_in_hotbar',
  description: 'Arrange her hotbar: put an item into hotbar slot 1-9 (what was there swaps to where the item was). ' +
    'For when the user wants things in particular slots ("sword in slot 1").',
  doneWhen: 'the item is in that slot',
  stopsWhen: 'she has no such item',
  params: {
    item: { type: 'string', required: true, description: 'an item id or kind, e.g. stone_pickaxe, sword' },
    slot: { type: 'integer', required: true, min: 1, max: 9, description: 'the hotbar slot, 1-9' },
    time_limit: timeLimitParam(10, 30)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const item = carried(bot, params.item)
    if (!item) return failed(`has no ${params.item}`, { inventory: bot.inventory.items().map((i) => i.name) })
    const dest = HOTBAR_START + params.slot - 1
    if (bot.inventory.slots[dest] === item) return done({ hotbar: hotbar(bot) }, `${item.name} is already in slot ${params.slot}`)
    await ctx.race(bot.moveSlotItem(item.slot, dest))
    const now = bot.inventory.slots[dest]
    if (now?.name !== item.name) return failed(`${item.name} didn't move (the game refused)`, { hotbar: hotbar(bot) })
    return done({ hotbar: hotbar(bot) })
  }
}
