// eat, sleep, craft: short skills that either work now or say why not.
import { done, failed, timeLimitParam } from './contract.js'
import { bestFood, blockIds, count, eatBest, goals, isNight, requireItem, walkTo } from './common.js'

export const eat = {
  name: 'eat',
  description: 'Eat the best food she has, or a named food.',
  doneWhen: 'she has eaten',
  stopsWhen: 'not hungry, nothing to eat',
  params: {
    food: { type: 'string', description: 'optional item name, e.g. bread' },
    time_limit: timeLimitParam(10, 30)
  },
  validate (bot, params) {
    if (params.food) requireItem(bot, params.food)
  },
  async run (ctx, params) {
    const { bot } = ctx
    if (bot.food >= 20) return failed('not hungry')
    if (!bestFood(bot, params.food)) return failed(params.food ? `has no ${params.food}` : 'nothing to eat')
    const before = bot.food
    const ate = await ctx.race(eatBest(bot, params.food))
    return done({ ate, food: bot.food, gained: bot.food - before })
  }
}

export const sleep = {
  name: 'sleep',
  description: 'Sleep in the nearest bed until morning (also sets where she respawns).',
  doneWhen: 'she wakes up in the morning',
  stopsWhen: 'no bed nearby, not night, monsters nearby, bed taken',
  params: {
    time_limit: timeLimitParam(480, 600)
  },
  async run (ctx) {
    const { bot } = ctx
    if (!isNight(bot) && !bot.thunderState) return failed('it isn\'t night')
    const beds = bot.registry.blocksArray.filter((b) => b.name.endsWith('_bed')).map((b) => b.id)
    const position = bot.findBlock({ matching: beds, maxDistance: 32 })?.position
    if (!position) return failed('no bed within 32 blocks')
    if (!await walkTo(ctx, new goals.GoalLookAtBlock(position, bot.world, { reach: 3 }))) return failed('can\'t reach the bed')
    try {
      await ctx.race(bot.sleep(bot.blockAt(position)))
    } catch (err) {
      if (err?.status) throw err
      return failed(err.message) // e.g. "You may not rest now, there are monsters nearby"
    }
    // Night only skips once every player sleeps; otherwise she sleeps until it's over.
    await ctx.race(new Promise((resolve) => bot.once('wake', resolve)))
    await ctx.sleep(500) // the new time arrives just after the wake
    if (isNight(bot)) return failed('woke up before morning')
    return done({ day: bot.time.day })
  }
}

export const craft = {
  name: 'craft',
  description: 'Craft an item from what she carries, using a crafting table nearby if the recipe needs one.',
  doneWhen: 'she has crafted count',
  stopsWhen: 'no recipe, missing ingredients (says which), needs a crafting table and none is placed nearby',
  params: {
    item: { type: 'string', required: true, description: 'item name, e.g. oak_planks, stick, wooden_pickaxe' },
    count: { type: 'integer', min: 1, max: 64, default: 1, description: 'how many to end up with' },
    time_limit: timeLimitParam(30, 120)
  },
  validate (bot, params) {
    requireItem(bot, params.item)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const item = bot.registry.itemsByName[params.item]
    const before = count(bot, [params.item])
    const tablePos = bot.findBlock({ matching: blockIds(bot, 'crafting_table'), maxDistance: 32 })?.position
    const table = tablePos ? bot.blockAt(tablePos) : null

    let recipe = bot.recipesFor(item.id, null, 1, null)[0]
    let useTable = null
    if (!recipe && table) {
      recipe = bot.recipesFor(item.id, null, 1, table)[0]
      useTable = recipe ? table : null
    }
    if (!recipe) {
      const any = bot.recipesAll(item.id, null, table ?? true)
      if (!any.length) return failed(`no recipe for ${params.item}`)
      // A bed also has a no-table recipe (dye a bed), so "every recipe needs a table" misses it:
      // she had the wool and planks, was told "missing ingredients", and crafted planks four times.
      if (!table && (any.every((r) => r.requiresTable) || any.some((r) => r.requiresTable && affordable(bot, r)))) {
        return failed(tableMissing(bot))
      }
      // Of the recipe variants (oak, birch, cherry planks...), describe the one closest to what she has.
      const have = new Set(bot.inventory.items().map((i) => i.type))
      const closest = [...any].sort((a, b) => overlap(b, have) - overlap(a, have))[0]
      return failed('missing ingredients', { needs: ingredients(bot, closest) })
    }

    if (useTable && !await walkTo(ctx, new goals.GoalLookAtBlock(tablePos, bot.world, { reach: 4 }))) {
      return failed('can\'t reach the crafting table')
    }
    // One craft at a time, each confirmed: several crafts in one go (or back to back) leave
    // Mineflayer's inventory out of step with the server's on 26.x.
    let crafted = 0
    while (crafted < params.count) {
      ctx.check()
      const next = bot.recipesFor(item.id, null, 1, useTable)[0]
      if (!next) return failed('ran out of ingredients', { crafted, needs: ingredients(bot, recipe) })
      const target = crafted + next.result.count
      await ctx.race(bot.craft(next, 1, useTable ?? undefined))
      const until = Date.now() + 1500
      while (count(bot, [params.item]) - before < target && Date.now() < until) await ctx.sleep(50)
      const now = count(bot, [params.item]) - before
      if (now <= crafted) return failed('the game didn\'t accept the craft', { crafted })
      crafted = now
      ctx.progress({ crafted })
      await ctx.sleep(250)
    }
    return done({ crafted })
  }
}

function tableMissing (bot) {
  return count(bot, ['crafting_table']) > 0
    ? 'needs a crafting table nearby: place the one she carries first (place block=crafting_table)'
    : 'needs a crafting table nearby'
}

// Whether she carries everything a recipe consumes.
function affordable (bot, recipe) {
  return (recipe.delta ?? []).every(({ id, count: n }) => n >= 0 || count(bot, [bot.registry.items[id]?.name]) >= -n)
}

// How many of a recipe's ingredient kinds she carries.
function overlap (recipe, have) {
  return (recipe.delta ?? []).filter(({ id, count: n }) => n < 0 && have.has(id)).length
}

// What a recipe consumes, as { name: count }.
function ingredients (bot, recipe) {
  const out = {}
  for (const { id, count: n } of recipe.delta ?? []) {
    if (n < 0) out[bot.registry.items[id]?.name ?? id] = -n
  }
  return out
}
