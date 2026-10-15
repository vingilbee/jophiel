// wear: put armor on. Before this she had no way to, and once said "I'll put the armor on" when
// no skill could. When to wear what is hers to decide (or the user's); this only does it.
import { ParamError, done, failed, timeLimitParam } from './contract.js'
import { requireItem } from './common.js'

// Inventory slots of what she's wearing, and the equip destination for each.
export const ARMOR_SLOTS = { head: 5, torso: 6, legs: 7, feet: 8 }
// Best first. The turtle shell is an iron-strength helmet that also lets her breathe longer.
const MATERIALS = ['netherite', 'diamond', 'iron', 'turtle', 'chainmail', 'golden', 'copper', 'leather']

// Where an item is worn, or null when it isn't armor. An elytra goes on the chest, but only
// when asked for by name: it's no armor.
export function armorSlot (name) {
  if (name.endsWith('_helmet')) return 'head'
  if (name.endsWith('_chestplate') || name === 'elytra') return 'torso'
  if (name.endsWith('_leggings')) return 'legs'
  if (name.endsWith('_boots')) return 'feet'
  return null
}

function rank (name) {
  const material = MATERIALS.findIndex((m) => name.startsWith(`${m}_`))
  return material === -1 ? MATERIALS.length : material
}

// What she has on: { head: 'iron_helmet', ... } for the slots that aren't empty.
export function wearing (bot) {
  const out = {}
  for (const [slot, index] of Object.entries(ARMOR_SLOTS)) {
    const item = bot.inventory.slots[index]
    if (item) out[slot] = item.name
  }
  return out
}

export const wear = {
  name: 'wear',
  description: 'Put armor on: a named piece, or the best she carries for every slot that has nothing better on already.',
  doneWhen: 'the armor is on',
  stopsWhen: 'no armor to put on, or nothing better than what she wears',
  params: {
    item: { type: 'string', description: 'optional armor item, e.g. iron_chestplate; leave out for her best armor' },
    time_limit: timeLimitParam(10, 30)
  },
  validate (bot, params) {
    if (!params.item) return
    requireItem(bot, params.item)
    if (!armorSlot(params.item)) throw new ParamError(`${params.item} isn't armor`)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const items = bot.inventory.items()
    const now = wearing(bot)
    let picks
    if (params.item) {
      const item = items.find((i) => i.name === params.item)
      if (!item) return failed(now[armorSlot(params.item)] === params.item ? `already wearing ${params.item}` : `has no ${params.item}`, { wearing: now })
      picks = [[armorSlot(params.item), item]]
    } else {
      picks = []
      for (const slot of Object.keys(ARMOR_SLOTS)) {
        const best = items.filter((i) => armorSlot(i.name) === slot && i.name !== 'elytra')
          .sort((a, b) => rank(a.name) - rank(b.name))[0]
        if (best && (!now[slot] || rank(best.name) < rank(now[slot]))) picks.push([slot, best])
      }
      if (!picks.length) {
        const any = items.some((i) => armorSlot(i.name) && i.name !== 'elytra')
        return failed(any ? 'already wearing the best armor she has' : 'has no armor', { wearing: now })
      }
    }
    const putOn = []
    for (const [slot, item] of picks) {
      await ctx.race(bot.equip(item, slot))
      putOn.push(item.name)
    }
    return done({ put_on: putOn, wearing: wearing(bot) })
  }
}
