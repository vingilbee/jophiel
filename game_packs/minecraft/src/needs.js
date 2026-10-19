// What it takes to get an item or mine a block, against what she has: the things to mine,
// smelt and craft first, in order, worked out from the game's own data (harvest tools, drops,
// recipes). Columbina's planner gets this before it plans (backend services/game_planning.py),
// so a plan for iron can't skip the stone pickaxe: live, a replan dropped it and sent her for
// iron with a wooden one.
//
// Counted, not guessed, but not the whole game: tools are never used up, a crafting table or
// furnace is placed once and stays near, and items that only come from mobs, trades or chests
// are named as such or reported as gaps.
import { createRequire } from 'node:module'

import { carried, suggestNames } from './recipes.js'

const require = createRequire(import.meta.url)

const MAX_DEPTH = 12
const MAX_VARIANTS = 6

// Smelting isn't in the recipe data: what goes into the furnace for each result.
export const SMELT = {
  iron_ingot: 'raw_iron',
  gold_ingot: 'raw_gold',
  copper_ingot: 'raw_copper',
  glass: 'sand',
  stone: 'cobblestone',
  smooth_stone: 'stone',
  charcoal: 'oak_log',
  cooked_beef: 'beef',
  cooked_porkchop: 'porkchop',
  cooked_chicken: 'chicken',
  cooked_mutton: 'mutton',
  cooked_cod: 'cod',
  cooked_salmon: 'salmon',
  baked_potato: 'potato'
}
const SMELTS_PER_LOG = 1.5

// The usual block for an item several blocks drop (stone, not a placed cobblestone block).
const SOURCE = {
  cobblestone: 'stone',
  cobbled_deepslate: 'deepslate',
  raw_iron: 'iron_ore',
  raw_gold: 'gold_ore',
  raw_copper: 'copper_ore',
  coal: 'coal_ore',
  diamond: 'diamond_ore',
  emerald: 'emerald_ore',
  redstone: 'redstone_ore',
  lapis_lazuli: 'lapis_ore',
  flint: 'gravel'
}

// Items that come from animals or monsters, not from blocks.
const MOBS = {
  beef: 'cow',
  leather: 'cow',
  porkchop: 'pig',
  chicken: 'chicken',
  feather: 'chicken',
  mutton: 'sheep',
  white_wool: 'sheep',
  string: 'spider',
  bone: 'skeleton',
  arrow: 'skeleton',
  gunpowder: 'creeper',
  rotten_flesh: 'zombie',
  ender_pearl: 'enderman',
  slime_ball: 'slime'
}

const FUEL = /^(coal|charcoal|.*_planks|.*_log)$/
// Between equally cheap recipes: the everyday materials (oak, cobblestone), not bamboo or blackstone.
const COMMON = /^(oak_|cobblestone$)/

function itemName (registry, drop) {
  const id = typeof drop === 'number' ? drop : drop?.drop?.id ?? drop?.drop ?? drop?.id
  return registry.items[id]?.name
}

function dropNames (registry, block) {
  return (block?.drops ?? []).map((drop) => itemName(registry, drop)).filter(Boolean)
}

// Blocks that drop each item, natural-looking ones only (not storage blocks or potted plants).
function droppedBy (registry) {
  if (registry.__droppedBy) return registry.__droppedBy
  const map = {}
  for (const block of Object.values(registry.blocksByName)) {
    if (/(_block$|^potted_|^infested_|_wall_|^waxed_)/.test(block.name)) continue
    for (const name of dropNames(registry, block)) (map[name] ??= []).push(block.name)
  }
  Object.defineProperty(registry, '__droppedBy', { value: map, enumerable: false })
  return map
}

function sourceBlock (ctx, name) {
  const candidates = droppedBy(ctx.registry)[name] ?? []
  if (SOURCE[name] && ctx.registry.blocksByName[SOURCE[name]]) return SOURCE[name]
  if (candidates.includes(name)) return name
  return [...candidates].sort((a, b) => a.length - b.length || a.localeCompare(b))[0] ?? null
}

// Recipe variants worth trying, deduplicated, the ones using what she has (or oak) first.
// Unpacking a storage block (iron_block -> iron_ingot) or nuggets is never how she gets something.
function variants (ctx, name) {
  ctx.variantCache ??= {}
  if (!ctx.variantCache[name]) {
    const item = ctx.registry.itemsByName[name]
    const seen = new Set()
    const found = []
    const all = item ? ctx.Recipe.find(item.id, null) : []
    if (all.length) (ctx.crafted ??= new Set()).add(name)
    for (const recipe of all) {
      const needs = {}
      for (const { id, count } of recipe.delta ?? []) {
        if (count < 0) {
          const ingredient = ctx.registry.items[id]?.name ?? String(id)
          needs[ingredient] = (needs[ingredient] ?? 0) - count
        }
      }
      const names = Object.keys(needs)
      if (!names.length) continue
      if (names.length === 1 && /(_block|_nugget)$/.test(names[0])) continue
      const key = JSON.stringify(needs)
      if (seen.has(key)) continue
      seen.add(key)
      found.push({ needs, makes: recipe.result?.count ?? 1, table: Boolean(recipe.requiresTable) })
    }
    ctx.variantCache[name] = found
  }
  const score = (v) => Object.keys(v.needs).filter((n) => (ctx.ledger[n] ?? 0) > 0).length * -10 +
    (Object.keys(v.needs).some((n) => COMMON.test(n)) ? -1 : 0)
  return [...ctx.variantCache[name]].sort((a, b) => score(a) - score(b)).slice(0, MAX_VARIANTS)
}

function fork (ctx) {
  return { ...ctx, ledger: { ...ctx.ledger }, steps: [], gaps: [] }
}

function cost (ctx) {
  return ctx.steps.length + ctx.gaps.length * 100
}

function adopt (ctx, trial) {
  ctx.ledger = trial.ledger
  ctx.steps.push(...trial.steps)
  ctx.gaps.push(...trial.gaps)
  ctx.tableNearby = trial.tableNearby
  ctx.furnaceNearby = trial.furnaceNearby
}

// The cheapest of several ways (each a function of a forked context), applied to ctx.
function cheapest (ctx, ways) {
  let best = null
  for (const way of ways) {
    const trial = fork(ctx)
    way(trial)
    if (!best || cost(trial) < cost(best)) best = trial
  }
  if (best) adopt(ctx, best)
}

function getItem (ctx, name, count, depth, stack) {
  const have = ctx.ledger[name] ?? 0
  if (have >= count) {
    ctx.ledger[name] = have - count
    return
  }
  const need = count - have
  ctx.ledger[name] = 0
  if (depth > MAX_DEPTH || stack.includes(name)) {
    ctx.gaps.push({ item: name, count: need, why: 'no way to make it found' })
    return
  }
  const path = [...stack, name]
  if (SMELT[name]) return smelt(ctx, name, need, depth, path)
  const recipes = variants(ctx, name)
  if (recipes.length) {
    return cheapest(ctx, recipes.map((recipe) => (trial) => craft(trial, name, need, recipe, depth, path)))
  }
  if (MOBS[name]) {
    ctx.steps.push({ action: 'hunt', item: name, count: need, from: MOBS[name] })
    return
  }
  const block = sourceBlock(ctx, name)
  // A block that drops itself but is crafted (bamboo_planks, from a bamboo block) isn't found
  // lying around: only natural sources (coal_ore for coal, oak_log for itself) are mined.
  if (block && !(block === name && ctx.crafted?.has(name))) return mineBlock(ctx, block, need, depth, path, name)
  ctx.gaps.push({ item: name, count: need, why: 'it can\'t be made, mined or hunted (found, traded or given)' })
}

function craft (ctx, name, need, recipe, depth, path) {
  const crafts = Math.ceil(need / recipe.makes)
  for (const [ingredient, n] of Object.entries(recipe.needs)) getItem(ctx, ingredient, crafts * n, depth + 1, path)
  if (recipe.table && !ctx.tableNearby) {
    getItem(ctx, 'crafting_table', 1, depth + 1, path)
    ctx.steps.push({ action: 'place', item: 'crafting_table', count: 1 })
    ctx.tableNearby = true
  }
  ctx.steps.push({ action: 'craft', item: name, count: crafts * recipe.makes, table: recipe.table })
  ctx.ledger[name] = (ctx.ledger[name] ?? 0) + crafts * recipe.makes - need
}

function smelt (ctx, name, need, depth, path) {
  getItem(ctx, SMELT[name], need, depth + 1, path)
  if (!ctx.furnaceNearby) {
    getItem(ctx, 'furnace', 1, depth + 1, path)
    ctx.steps.push({ action: 'place', item: 'furnace', count: 1 })
    ctx.furnaceNearby = true
  }
  const fuel = Object.keys(ctx.ledger).find((n) => FUEL.test(n) && ctx.ledger[n] > 0)
  if (!fuel) getItem(ctx, 'oak_log', Math.ceil(need / SMELTS_PER_LOG), depth + 1, path)
  ctx.steps.push({ action: 'smelt', item: name, count: need, from: SMELT[name] })
}

function mineBlock (ctx, blockName, count, depth, path, forItem = null) {
  const block = ctx.registry.blocksByName[blockName]
  const tools = Object.keys(block?.harvestTools ?? {}).map((id) => ctx.registry.items[id]?.name).filter(Boolean)
  let tool = tools.find((t) => (ctx.ledger[t] ?? 0) > 0) ?? null
  let toolNeeded = false
  if (tools.length && !tool) {
    toolNeeded = true
    cheapest(ctx, tools.map((t) => (trial) => {
      getItem(trial, t, 1, depth + 1, path)
      trial.ledger[t] = 1 // a tool isn't used up
    }))
    tool = tools.find((t) => (ctx.ledger[t] ?? 0) > 0) ?? tools[0]
  }
  const gives = forItem ?? dropNames(ctx.registry, block)[0] ?? blockName
  ctx.steps.push({ action: 'mine', item: gives, count, from: blockName, tool, tool_needed: toolNeeded })
}

// The steps in order, each kind of thing once (logs mined for planks and for fuel: one step).
function merged (steps) {
  const out = []
  const index = new Map()
  for (const step of steps) {
    const key = [step.action, step.item, step.from ?? '', step.tool ?? ''].join('|')
    if (index.has(key)) {
      if (step.action !== 'place') out[index.get(key)].count += step.count
      continue
    }
    index.set(key, out.length)
    out.push({ ...step })
  }
  return out
}

function isMineTarget (registry, name) {
  const block = registry.blocksByName[name]
  if (!block) return false
  const drops = dropNames(registry, block)
  return drops.length > 0 && !drops.includes(name)
}

// needsFor against her own inventory and whether a crafting table is near (as GET /recipes counts it).
export function needsForBot (bot, name, count = 1) {
  bot.__recipe ??= require('prismarine-recipe')(bot.registry).Recipe
  const table = bot.registry.blocksByName.crafting_table
  const tableNearby = Boolean(table && bot.findBlock({ matching: table.id, maxDistance: 32 }))
  return needsFor(bot.registry, bot.__recipe, name, { count, have: carried(bot), tableNearby })
}

// { target, known, kind, count, ready, steps, gaps }: what to do, in order, to get `count` of an
// item ("iron_ingot", "stone_pickaxe") or to mine `count` of a block ("iron_ore", "stone").
// `have`: her inventory by name. `tableNearby`: a crafting table is placed within reach.
export function needsFor (registry, Recipe, name, { count = 1, have = {}, tableNearby = false } = {}) {
  const wanted = String(name ?? '').toLowerCase().trim()
  if (!registry.itemsByName[wanted] && !registry.blocksByName[wanted]) {
    const names = [...new Set([...Object.keys(registry.itemsByName), ...Object.keys(registry.blocksByName)])]
    return { target: wanted, known: false, did_you_mean: suggestNames(names, wanted) }
  }
  const ctx = { registry, Recipe, ledger: { ...have }, steps: [], gaps: [], tableNearby, furnaceNearby: false }
  const kind = isMineTarget(registry, wanted) ? 'block' : 'item'
  if (kind === 'block') mineBlock(ctx, wanted, count, 0, [wanted])
  else getItem(ctx, wanted, count, 0, [])
  const steps = merged(ctx.steps)
  // Mining a block she can already harvest is the goal itself, not a prerequisite.
  const ready = ctx.gaps.length === 0 && steps.every((s) => s.action === 'mine' && !s.tool_needed && s.from === wanted)
  return { target: wanted, known: true, kind, count, ready, steps, gaps: ctx.gaps }
}
