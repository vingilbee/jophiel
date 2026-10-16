// Helpers shared by skills. Movement goes through pathfinder; the motion style layer (phase 2)
// will sit between these helpers and the bot.
import { createRequire } from 'node:module'

import { ParamError, failed } from './contract.js'

import { doorAware } from './doors.js'
import { didYouMean } from '../recipes.js'

const require = createRequire(import.meta.url)
const { Movements: BaseMovements, goals } = require('mineflayer-pathfinder')
const Movements = doorAware(BaseMovements)

export { goals }

// Stop every kind of action in progress. Safe to call at any time, any number of times.
export function stopEverything (bot) {
  try { bot.pathfinder?.setGoal(null) } catch {}
  try { if (bot.targetDigBlock) bot.stopDigging() } catch {}
  try { bot.clearControlStates() } catch {}
  try { if (bot.usingHeldItem) bot.deactivateItem() } catch {}
  try { if (bot.isSleeping) bot.wake().catch(() => {}) } catch {}
}

// Every block except leaves: what walking may not break.
const notLeaves = new WeakMap()
function allButLeaves (registry) {
  if (!notLeaves.has(registry)) {
    notLeaves.set(registry, new Set(registry.blocksArray.filter((b) => !b.name.endsWith('_leaves')).map((b) => b.id)))
  }
  return notLeaves.get(registry)
}

// Walking never breaks blocks (no tunnelling through a villager's wall), except leaves: they're
// natural, quick by hand, and she can otherwise end up trapped in a treetop after chopping.
// Only mining digs anything else.
export function movements (bot, { canDig = false } = {}) {
  const moves = new Movements(bot)
  moves.canDig = true
  if (!canDig) moves.blocksCantBreak = allButLeaves(bot.registry)
  moves.allowSprinting = true
  moves.allowParkour = false // gap jumps need sprint-jumping, which the motion layer keeps for real obstacles
  // Never spend her blocks on steps or bridges: she'd quietly use up what she just mined.
  moves.allow1by1towers = false
  moves.scafoldingBlocks = [] // (sic: pathfinder's spelling)
  return moves
}

// pathfinder's goto() resolves even when it found no path, and can keep re-planning a "partial"
// path forever, so every walk has its own time limit and every caller checks arrival itself.
//
// Stuck in place (canDig only): the pathfinder can keep "moving" against a step it can't jump,
// e.g. a cave staircase with a ceiling just over the jump (seen in tests: 100 s without a block of
// progress). After STUCK_MS without moving, the walk ends and she clears the block over her head,
// so the next try (callers retry) has room to jump.
const STUCK_MS = 4000
export async function walkTo (ctx, goal, { canDig = false, timeoutMs = 20000 } = {}) {
  const { bot } = ctx
  bot.pathfinder.setMovements(movements(bot, { canDig }))
  let timer
  let stuck = false
  let still = { at: Date.now(), position: bot.entity.position.clone() }
  const watch = canDig
    ? setInterval(() => {
      const p = bot.entity.position
      if (p.distanceTo(still.position) > 0.3 || bot.pathfinder.isMining?.()) {
        still = { at: Date.now(), position: p.clone() }
      } else if (Date.now() - still.at > STUCK_MS && bot.pathfinder.isMoving()) {
        stuck = true
        bot.pathfinder.setGoal(null)
      }
    }, 250)
    : null
  const gaveUp = new Promise((resolve) => { timer = setTimeout(() => resolve('timeout'), timeoutMs) })
  try {
    const outcome = await ctx.race(Promise.race([bot.pathfinder.goto(goal).then(() => 'ok'), gaveUp]))
    if (outcome === 'timeout') bot.pathfinder.setGoal(null)
  } catch (err) {
    if (err?.status) throw err // a SkillEnd from stop/timeout/hurt
    ctx.check()
    if (stuck) await clearHeadroom(ctx)
    return false // NoPath, Timeout, GoalChanged, PathStopped
  } finally {
    clearTimeout(timer)
    if (watch) clearInterval(watch)
  }
  return goal.isEnd(bot.entity.position.floored())
}

// The block just over her head, broken if it's solid and breakable: room to jump.
async function clearHeadroom (ctx) {
  const { bot } = ctx
  const above = bot.blockAt(bot.entity.position.floored().offset(0, 2, 0))
  if (!above || above.boundingBox !== 'block' || !bot.canDigBlock(above)) return
  try {
    await bot.tool?.equipForBlock?.(above)
    await ctx.race(bot.dig(above))
  } catch (err) {
    if (err?.status) throw err
  }
}

// ---- names and lookups -------------------------------------------------------------------

export function blockIds (bot, name) {
  const registry = bot.registry
  // "log" matches every *_log; otherwise an exact block name.
  if (name === 'log') return registry.blocksArray.filter((b) => b.name.endsWith('_log')).map((b) => b.id)
  const block = registry.blocksByName[name]
  return block ? [block.id] : []
}

export function requireBlock (bot, name) {
  if (!blockIds(bot, name).length) {
    throw new ParamError(`no block is called ${name}${didYouMean(Object.keys(bot.registry.blocksByName), name)}`)
  }
}

export function requireItem (bot, name) {
  if (!bot.registry.itemsByName[name]) {
    throw new ParamError(`no item is called ${name}${didYouMean(Object.keys(bot.registry.itemsByName), name)}`)
  }
}

// A player target that's her own name: refuse, and say who she is.
export function requireOtherPlayer (bot, name) {
  if (name && bot.username && name.toLowerCase() === bot.username.toLowerCase()) {
    const others = Object.keys(bot.players).filter((n) => n !== bot.username)
    throw new ParamError(`${name} is you (your own player)` + (others.length ? `; other players: ${others.join(', ')}` : ''))
  }
}

export function requireEntityType (bot, name) {
  if (!bot.registry.entitiesByName[name]) throw new ParamError(`no mob is called ${name}`)
}

export function count (bot, names) {
  const wanted = new Set(names)
  return bot.inventory.items().filter((i) => wanted.has(i.name)).reduce((n, i) => n + i.count, 0)
}

export function distanceTo (bot, position) {
  return bot.entity.position.distanceTo(position)
}

// Neutral until provoked: not danger, and never picked as "nearest hostile" (hitting one starts a fight).
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin'])

export function isHostile (entity) {
  if (NEUTRAL.has(entity?.name)) return false
  return entity?.type === 'hostile' || ['slime', 'magma_cube', 'phantom', 'ghast', 'shulker'].includes(entity?.name)
}

// Fight order: creeper, then skeletons, then zombies, then the rest; nearest first within a rank.
const RANK = { creeper: 0, skeleton: 1, stray: 1, bogged: 1, zombie: 2, husk: 2, drowned: 2, zombie_villager: 2 }
export const hostileRank = (entity) => RANK[entity.name] ?? 3

export function hostilesAround (bot, radius) {
  const here = bot.entity.position
  return Object.values(bot.entities)
    .filter((e) => e !== bot.entity && isHostile(e) && e.position.distanceTo(here) <= radius)
}

export function nearestHostile (bot, radius) {
  return closest(bot, hostilesAround(bot, radius))
}

export function closest (bot, entities) {
  const here = bot.entity.position
  let best = null
  let bestDistance = Infinity
  for (const e of entities) {
    const d = e.position.distanceTo(here)
    if (d < bestDistance) {
      best = e
      bestDistance = d
    }
  }
  return best
}

export function findEntity (bot, name, radius = 64) {
  return closest(bot, Object.values(bot.entities).filter((e) =>
    e !== bot.entity && e.position.distanceTo(bot.entity.position) <= radius &&
    (e.name === name || (e.type === 'player' && e.username === name))))
}

export function findPlayer (bot, username) {
  return bot.players[username]?.entity ?? null
}

// ---- equipment ---------------------------------------------------------------------------

// The inventory item that breaks this block fastest (or the hand). null when nothing can harvest it.
export function bestToolFor (bot, block) {
  const candidates = [null, ...bot.inventory.items()]
  let best = null
  let bestTime = Infinity
  for (const item of candidates) {
    if (block.harvestTools && !(item && block.harvestTools[item.type])) continue
    const time = block.digTime(item?.type ?? null, false, false, false, item?.enchants ?? [], bot.entity.effects)
    if (time < bestTime) {
      best = item
      bestTime = time
    }
  }
  return { item: best, harvestable: bestTime !== Infinity }
}

const WEAPONS = [
  'netherite_sword', 'diamond_sword', 'iron_sword', 'copper_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe', 'copper_axe', 'stone_axe', 'golden_axe', 'wooden_axe'
]

export async function equipBestWeapon (bot) {
  for (const name of WEAPONS) {
    const item = bot.inventory.items().find((i) => i.name === name)
    if (item) {
      if (bot.heldItem?.name !== name) await bot.equip(item, 'hand')
      return name
    }
  }
  return null
}

// Ticks between full-strength hits for what she's holding (attack speed 1.6/s sword, ~1/s axe, 4/s hand).
export function attackCooldownMs (heldName) {
  if (!heldName) return 250
  if (heldName.endsWith('_sword')) return 625
  if (heldName.endsWith('_axe')) return 1000
  return 250
}

export async function equipShield (bot) {
  const shield = bot.inventory.items().find((i) => i.name === 'shield')
  if (!shield) return false
  const offHand = bot.inventory.slots[45]
  if (offHand?.name !== 'shield') await bot.equip(shield, 'off-hand')
  return true
}

const BAD_FOOD = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'suspicious_stew', 'chorus_fruit'])

export function bestFood (bot, name = null) {
  const foods = bot.registry.foodsByName
  return bot.inventory.items()
    .filter((i) => foods[i.name] && (name ? i.name === name : !BAD_FOOD.has(i.name)))
    .sort((a, b) => foods[b.name].foodPoints - foods[a.name].foodPoints)[0] ?? null
}

// Eats the best food she has. Returns the food's name, or null when there was nothing to eat.
export async function eatBest (bot, name = null) {
  const food = bestFood(bot, name)
  if (!food) return null
  await bot.equip(food, 'hand')
  await bot.consume()
  return food.name
}

// Walk directly away from an entity for up to ms, or until stopped() says so.
export async function retreatFrom (bot, entity, ms, stopped = () => false) {
  const here = bot.entity.position
  const away = here.minus(entity.position)
  await bot.lookAt(here.plus(away), true)
  bot.setControlState('forward', true)
  bot.setControlState('sprint', true)
  const until = Date.now() + Math.max(0, ms)
  while (Date.now() < until && !stopped()) await new Promise((resolve) => setTimeout(resolve, 50))
  bot.clearControlStates()
}

export function isNight (bot) {
  const t = bot.time.timeOfDay
  return t >= 12542 && t <= 23459
}

export function describeBlockPos (position) {
  return { x: position.x, y: position.y, z: position.z }
}

export { failed }
