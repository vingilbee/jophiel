// Chests and furnaces: store, take, smelt. Container moves go one stack at a time and are
// confirmed before the next, like crafting: back-to-back window clicks leave Mineflayer's
// inventory out of step with the server's on 26.x.
import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import { blockIds, count, goals, requireItem, walkTo } from './common.js'

const CHESTS = ['chest', 'trapped_chest', 'barrel']
const FURNACES = ['furnace', 'blast_furnace', 'smoker']
const PACE_MS = 250

// What "store all" keeps on her: tools, weapons, armour, food, torches.
const KEEP = /(_pickaxe|_axe|_shovel|_hoe|_sword|^shield|^bow|^crossbow|_helmet|_chestplate|_leggings|_boots|^torch)$/

// Furnace fuel she might carry, and how many items one of it smelts.
const FUELS = [
  ['coal_block', 80], ['coal', 8], ['charcoal', 8], ['blaze_rod', 12],
  ['oak_log', 1.5], ['birch_log', 1.5], ['spruce_log', 1.5], ['jungle_log', 1.5], ['acacia_log', 1.5],
  ['dark_oak_log', 1.5], ['mangrove_log', 1.5], ['cherry_log', 1.5],
  ['oak_planks', 1.5], ['birch_planks', 1.5], ['spruce_planks', 1.5], ['jungle_planks', 1.5],
  ['acacia_planks', 1.5], ['dark_oak_planks', 1.5], ['mangrove_planks', 1.5], ['cherry_planks', 1.5],
  ['stick', 0.5]
]

async function goToContainer (ctx, kinds, label) {
  const { bot } = ctx
  const ids = kinds.flatMap((k) => blockIds(bot, k))
  const position = bot.findBlock({ matching: ids, maxDistance: 16 })?.position
  if (!position) throw new SkillEnd('failed', `no ${label} within 16 blocks`)
  if (!await walkTo(ctx, new goals.GoalLookAtBlock(position, bot.world, { reach: 4 }), { timeoutMs: 20000 })) {
    throw new SkillEnd('failed', `can't reach the ${label}`)
  }
  return bot.blockAt(position)
}

async function settle (ctx, check, ms = 1500) {
  const until = Date.now() + ms
  while (!check() && Date.now() < until) await ctx.sleep(50)
  return check()
}

// While a container is open, bot.inventory doesn't update until it closes; the window's own
// copy of her inventory (window.items()) does.
const held = (window, name) => window.items().filter((i) => i.name === name).reduce((n, i) => n + i.count, 0)

const inventoryCounts = (bot) => {
  const counts = new Map()
  for (const i of bot.inventory.items()) counts.set(i.name, (counts.get(i.name) ?? 0) + i.count)
  return counts
}

export const store = {
  name: 'store',
  description: 'Put items into the nearest chest or barrel: one kind, or "all" (keeps tools, weapons, armour, food, torches).',
  doneWhen: 'the items are in the chest',
  stopsWhen: 'no chest within 16 blocks, she has none of it, the chest is full',
  params: {
    item: { type: 'string', default: 'all', description: 'item name, or "all"' },
    count: { type: 'integer', min: 1, max: 2304, description: 'optional: how many (default: all she has)' },
    time_limit: timeLimitParam(60, 120)
  },
  validate (bot, params) {
    if (params.item !== 'all') requireItem(bot, params.item)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const foods = bot.registry.foodsByName
    const kinds = params.item === 'all'
      ? [...inventoryCounts(bot).keys()].filter((n) => !KEEP.test(n) && !foods[n])
      : [params.item]
    if (!kinds.some((k) => count(bot, [k]) > 0)) return failed(params.item === 'all' ? 'nothing to store' : `has no ${params.item}`)
    const block = await goToContainer(ctx, CHESTS, 'chest')
    const chest = await ctx.race(bot.openContainer(block))
    const stored = {}
    try {
      for (const name of kinds) {
        let left = params.item === 'all' ? held(chest, name) : Math.min(params.count ?? Infinity, held(chest, name))
        const type = bot.registry.itemsByName[name].id
        while (left > 0) {
          ctx.check()
          const stack = Math.min(left, bot.registry.itemsByName[name].stackSize)
          const before = held(chest, name)
          try {
            await ctx.race(chest.deposit(type, null, stack))
          } catch (err) {
            if (err instanceof SkillEnd) throw err
            return failed(/full|space/i.test(err.message) ? 'the chest is full' : `couldn't store ${name}: ${err.message}`, { stored })
          }
          await settle(ctx, () => held(chest, name) <= before - stack)
          const moved = before - held(chest, name)
          if (moved <= 0) return failed('the game didn\'t accept it', { stored })
          stored[name] = (stored[name] ?? 0) + moved
          left -= moved
          ctx.progress({ stored })
          await ctx.sleep(PACE_MS)
        }
      }
    } finally {
      chest.close()
    }
    return done({ stored })
  }
}

export const take = {
  name: 'take',
  description: 'Take items out of the nearest chest or barrel.',
  doneWhen: 'she has taken count (or all there was)',
  stopsWhen: 'no chest within 16 blocks, the chest has none of it, her inventory is full',
  params: {
    item: { type: 'string', required: true, description: 'item name' },
    count: { type: 'integer', min: 1, max: 2304, default: 64, description: 'how many' },
    time_limit: timeLimitParam(60, 120)
  },
  validate (bot, params) {
    requireItem(bot, params.item)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const block = await goToContainer(ctx, CHESTS, 'chest')
    const chest = await ctx.race(bot.openContainer(block))
    const type = bot.registry.itemsByName[params.item].id
    let taken = 0
    try {
      const inChest = () => chest.containerItems().filter((i) => i.type === type).reduce((n, i) => n + i.count, 0)
      if (!inChest()) return failed(`the chest has no ${params.item}`)
      while (taken < params.count && inChest() > 0) {
        ctx.check()
        const stack = Math.min(params.count - taken, inChest(), bot.registry.items[type].stackSize)
        const before = held(chest, params.item)
        try {
          await ctx.race(chest.withdraw(type, null, stack))
        } catch (err) {
          if (err instanceof SkillEnd) throw err
          return failed(/full|space/i.test(err.message) ? 'inventory full' : `couldn't take ${params.item}: ${err.message}`, { taken })
        }
        await settle(ctx, () => held(chest, params.item) >= before + stack)
        const moved = held(chest, params.item) - before
        if (moved <= 0) return failed('the game didn\'t accept it', { taken })
        taken += moved
        ctx.progress({ taken })
        await ctx.sleep(PACE_MS)
      }
    } finally {
      chest.close()
    }
    return done({ taken, ...(taken < params.count ? { wanted: params.count } : {}) })
  }
}

export const smelt = {
  name: 'smelt',
  description: 'Smelt or cook items in the nearest furnace (raw iron -> iron ingot, raw beef -> steak...), adding fuel she carries.',
  doneWhen: 'she has taken out count smelted items',
  stopsWhen: 'no furnace within 16 blocks, she has none of it, no fuel, it can\'t be smelted',
  params: {
    item: { type: 'string', required: true, description: 'what to smelt, e.g. raw_iron' },
    count: { type: 'integer', min: 1, max: 64, default: 1, description: 'how many' },
    time_limit: timeLimitParam(120, 700)
  },
  validate (bot, params) {
    requireItem(bot, params.item)
    if (FUELS.some(([f]) => f === params.item)) throw new ParamError(`${params.item} is fuel; smelt something else`)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const have = count(bot, [params.item])
    if (!have) return failed(`has no ${params.item}`)
    const n = Math.min(have, params.count)
    const fuel = FUELS.find(([name]) => count(bot, [name]) > 0)
    const block = await goToContainer(ctx, FURNACES, 'furnace')
    const furnace = await ctx.race(bot.openFurnace(block))
    try {
      // Anything already finished in it is hers to take first.
      if (furnace.outputItem()) {
        await ctx.race(furnace.takeOutput())
        await ctx.sleep(PACE_MS)
      }
      const fuelInside = furnace.fuelItem()?.count ?? 0
      if (!fuel && !fuelInside && !furnace.fuel) return failed('no fuel (coal, charcoal, logs, planks...)')
      const type = bot.registry.itemsByName[params.item].id
      const before = held(furnace, params.item)
      await ctx.race(furnace.putInput(type, null, n))
      await settle(ctx, () => held(furnace, params.item) <= before - n)
      await ctx.sleep(PACE_MS)
      if (fuel) {
        const [name, per] = fuel
        const needed = Math.max(0, Math.ceil(n / per) - fuelInside)
        const put = Math.min(needed, held(furnace, name))
        if (put > 0) {
          await ctx.race(furnace.putFuel(bot.registry.itemsByName[name].id, null, put))
          await ctx.sleep(PACE_MS)
        }
      }

      let smelted = 0
      let output = null
      let lastChange = Date.now()
      while (smelted < n) {
        await ctx.sleep(1000)
        const out = furnace.outputItem()
        if (out?.count) {
          output = out.name
          const got = out.count
          await ctx.race(furnace.takeOutput())
          await ctx.sleep(PACE_MS)
          smelted += got
          lastChange = Date.now()
          ctx.progress({ smelted, output })
        } else if (furnace.progress > 0) {
          lastChange = Date.now()
        } else if (Date.now() - lastChange > 15000) {
          // Nothing is cooking: not smeltable, or out of fuel. Take the input back.
          if (furnace.inputItem()) await ctx.race(furnace.takeInput()).catch(() => {})
          return smelted
            ? failed('ran out of fuel', { smelted, output })
            : failed(`${params.item} can't be smelted (or no fuel burns)`)
        }
      }
      return done({ smelted, output })
    } finally {
      furnace.close()
    }
  }
}
