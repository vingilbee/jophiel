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
//
// A dig is never thrown away half done. Through diggable ground (a terracotta mesa) the route search
// often runs out of time; the pathfinder then walks the best part-route it has, digging, and starts
// a new search when that runs out. Ending the walk there, or setting the same movements again (which
// resets the path), stopped the dig in progress: by hand terracotta takes 6.25 s, the search gave up
// after 5, so the same block was started over and over and never broke (live, 2026-10-16).
const STUCK_MS = 4000
// Not even "moving": the pathfinder re-plans every few seconds and gets nowhere (seen: wedged on a
// block's edge in a dug staircase, hovering, 30 s). Longer than a search may take.
const STUCK_IDLE_MS = 12000
const WALK_THINK_MS = 5000 // pathfinder's own default
const DIG_THINK_MS = 10000 // more ways to weigh when she may dig
const walkMoves = new WeakMap() // bot -> { walk, dig }: the same Movements each time, so no needless reset

function movesFor (bot, canDig) {
  if (!walkMoves.has(bot)) walkMoves.set(bot, {})
  const cached = walkMoves.get(bot)
  const key = canDig ? 'dig' : 'walk'
  cached[key] ??= movements(bot, { canDig })
  return cached[key]
}

// The Movements go_to walks with (the same object walkTo uses): a route check under the same rules.
export function walkMovements (bot, canDig = false) {
  return movesFor(bot, canDig)
}

// Resolves when the goal is reached. Walking: pathfinder's goto(). A walk that may dig waits for
// the real arrival itself: through diggable ground the search often runs out of time, and goto()
// then gives up although the pathfinder carries on with its part-route; and goto() takes the empty
// draft route a search starts with as "arrived" (seen on a terracotta mesa: three "arrivals" a
// second, at the foot of the cliff).
function arrive (bot, goal, canDig) {
  if (!canDig) return bot.pathfinder.goto(goal)
  return new Promise((resolve, reject) => {
    const end = (err) => {
      clearInterval(poll)
      bot.removeListener('goal_reached', onReached)
      bot.removeListener('path_update', onUpdate)
      bot.removeListener('goal_updated', onGoal)
      bot.removeListener('path_stop', onStop)
      setTimeout(() => (err ? reject(err) : resolve()), 0) // as goto(): let pathfinder settle first
    }
    const error = (name, message) => Object.assign(new Error(message), { name })
    const onReached = () => end()
    const onUpdate = (r) => { if (r.status === 'noPath' && r.path.length === 0) end(error('NoPath', 'No path to the goal!')) }
    const onGoal = (g) => { if (g !== goal) end(error('GoalChanged', 'The goal was changed')) }
    const onStop = () => end(error('PathStopped', 'Path was stopped'))
    const poll = setInterval(() => { if (goal.isEnd(bot.entity.position.floored())) end() }, 250)
    bot.on('goal_reached', onReached)
    bot.on('path_update', onUpdate)
    bot.on('goal_updated', onGoal)
    bot.on('path_stop', onStop)
    bot.pathfinder.setGoal(goal)
  })
}

// A block she's breaking is finished before the walk ends (stopping the walk would stop the dig).
async function finishDig (ctx) {
  const { bot } = ctx
  const block = bot.targetDigBlock
  if (!block || !bot.pathfinder.isMining?.()) return
  const until = Date.now() + Math.min(bot.digTime(block) + 1000, 15000)
  while (bot.targetDigBlock && Date.now() < until) await ctx.sleep(100)
}

// How far from the goal, by the goal's own estimate (Infinity if it has none).
function closeness (goal, bot) {
  const p = bot.entity.position.floored()
  try { return goal.heuristic?.({ x: p.x, y: p.y, z: p.z }) ?? Infinity } catch { return Infinity }
}

export async function walkTo (ctx, goal, { canDig = false, timeoutMs = 20000 } = {}) {
  const { bot } = ctx
  const moves = movesFor(bot, canDig)
  if (bot.pathfinder.movements !== moves) bot.pathfinder.setMovements(moves)
  bot.pathfinder.thinkTimeout = canDig ? DIG_THINK_MS : WALK_THINK_MS
  let timer
  let stuck = false
  let still = { at: Date.now(), position: bot.entity.position.clone() }
  const watch = canDig
    ? setInterval(() => {
      const p = bot.entity.position
      if (p.distanceTo(still.position) > 0.3 || bot.pathfinder.isMining?.()) {
        still = { at: Date.now(), position: p.clone() }
      } else if (Date.now() - still.at > (bot.pathfinder.isMoving() ? STUCK_MS : STUCK_IDLE_MS)) {
        stuck = true
        bot.pathfinder.setGoal(null)
      }
    }, 250)
    : null
  // Walking: timeoutMs in all. Digging: timeoutMs without progress (a block broken, or a block
  // closer to the goal): by hand a terracotta step is 7 s a block, and a climb is many blocks.
  let deadline = Date.now() + timeoutMs
  let best = closeness(goal, bot)
  // (Not 'diggingCompleted': pathfinder removes every listener of it after its own digs.)
  const onDug = (before, after) => {
    if (before?.boundingBox === 'block' && after?.boundingBox === 'empty' &&
      before.position.distanceTo(bot.entity.position) < 6) deadline = Date.now() + timeoutMs
  }
  if (canDig) bot.on('blockUpdate', onDug)
  const gaveUp = new Promise((resolve) => {
    timer = setInterval(() => {
      const now = closeness(goal, bot)
      if (canDig && now < best - 1) { best = now; deadline = Date.now() + timeoutMs }
      if (Date.now() > deadline) resolve('timeout')
    }, 250)
  })
  try {
    const outcome = await ctx.race(Promise.race([arrive(bot, goal, canDig).then(() => 'ok'), gaveUp]))
    if (outcome === 'timeout') {
      await finishDig(ctx)
      bot.pathfinder.setGoal(null)
    }
  } catch (err) {
    if (err?.status) throw err // a SkillEnd from stop/timeout/hurt
    ctx.check()
    await finishDig(ctx)
    if (bot.pathfinder.goal === goal) bot.pathfinder.setGoal(null) // a failed search leaves it walking
    if (stuck) {
      await recenter(ctx)
      await clearHeadroom(ctx)
    }
    return false // NoPath, Timeout, GoalChanged, PathStopped
  } finally {
    clearInterval(timer)
    if (watch) clearInterval(watch)
    bot.removeListener('blockUpdate', onDug)
    bot.pathfinder.thinkTimeout = WALK_THINK_MS
  }
  return goal.isEnd(bot.entity.position.floored())
}

// A step back to the middle of her block: off an edge or out of a corner she's wedged in.
async function recenter (ctx) {
  const { bot } = ctx
  const p = bot.entity.position
  const middle = p.floored().offset(0.5, 0, 0.5)
  if (Math.hypot(middle.x - p.x, middle.z - p.z) < 0.2) return
  try {
    await bot.lookAt(middle.offset(0, bot.entity.height * 0.9, 0), true)
    bot.setControlState('forward', true)
    const until = Date.now() + 600
    while (Date.now() < until && Math.hypot(middle.x - bot.entity.position.x, middle.z - bot.entity.position.z) > 0.2) await ctx.sleep(50)
  } finally {
    bot.setControlState('forward', false)
  }
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
