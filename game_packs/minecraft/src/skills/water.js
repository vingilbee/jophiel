// surface: swim up for air. Also what her breathing reflex runs (runner.js), so she never drowns:
// bots sink in water unless they hold jump, and live she drowned down to one heart following the
// user through the sea, and once ran out of air with no skill able to help.
import { done, failed, timeLimitParam } from './contract.js'

// Air is 0-20 (mineflayer's oxygenLevel; drowning damage starts below 0). At 10 she has about
// 7 s left: enough to come up from ~15 blocks down.
export const LOW_AIR = 10
const BREATHED = 18 // at the surface until her air is back to this, or BREATHE_MS
const BREATHE_MS = 3000
const UP_SCAN = 24 // blocks above her head checked for open water
const AIR_RADIUS = 12 // how far she looks for an air pocket when something is overhead
const STUCK_MS = 3000 // swimming towards the pocket with no headway for this long: give up
const WATERY = new Set(['water', 'bubble_column', 'kelp', 'kelp_plant', 'seagrass', 'tall_seagrass'])

function watery (block) {
  return Boolean(block && (WATERY.has(block.name) || block.getProperties?.().waterlogged === true))
}

export function headInWater (bot) {
  const self = bot.entity
  if (!self?.position) return false
  return watery(bot.blockAt(self.position.offset(0, self.eyeHeight ?? 1.62, 0)))
}

// Running out of air: head under water and the air bar low. (Air readings above 20 are noise
// seen live, and ignored.)
export function needsAir (bot) {
  const air = bot.oxygenLevel
  return typeof air === 'number' && air <= LOW_AIR && air >= -20 && headInWater(bot)
}

// Open water straight above her head up to the surface (nothing solid in the way)?
function openAbove (bot) {
  const head = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0).floored()
  for (let dy = 1; dy <= UP_SCAN; dy++) {
    const block = bot.blockAt(head.offset(0, dy, 0))
    if (!block) return false
    if (watery(block)) continue
    return block.boundingBox === 'empty' // air (or a plant) at the top; anything solid is a ceiling
  }
  return false
}

// The nearest air she can swim to: outward through connected water from her head, the first
// water block with air (or a plant) right above it. Plain "nearest air block" picked air on the
// far side of a glass wall (seen on a real server).
const SIDES = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
function nearestAirPocket (bot) {
  const start = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0).floored()
  const seen = new Set([start.toString()])
  const queue = [start]
  while (queue.length) {
    const p = queue.shift()
    const up = bot.blockAt(p.offset(0, 1, 0))
    if (up && !watery(up) && up.boundingBox === 'empty') return p.offset(0, 1, 0)
    for (const [dx, dy, dz] of SIDES) {
      const next = p.offset(dx, dy, dz)
      const key = next.toString()
      if (seen.has(key) || next.distanceTo(start) > AIR_RADIUS) continue
      seen.add(key)
      if (watery(bot.blockAt(next))) queue.push(next)
    }
  }
  return null
}

// Swim towards a spot herself: the pathfinder plans no routes through water (seen on a real
// server: it gives up at once). Face it, swim forward, and up when it's above her.
async function swimTo (ctx, spot) {
  const { bot } = ctx
  const target = spot.offset(0.5, 0.5, 0.5)
  let best = Infinity
  let since = Date.now()
  while (headInWater(bot)) {
    const d = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0).distanceTo(target)
    if (d < best - 0.3) {
      best = d
      since = Date.now()
    } else if (Date.now() - since > STUCK_MS) {
      return false
    }
    await bot.lookAt(target, true)
    bot.setControlState('forward', d > 0.8)
    bot.setControlState('jump', target.y > bot.entity.position.y + 0.5)
    await ctx.sleep(100)
  }
  bot.setControlState('forward', false)
  return true
}

// At the surface: keep swimming up (it keeps her head out) until she has her breath back.
async function catchBreath (ctx) {
  const { bot } = ctx
  const until = Date.now() + BREATHE_MS
  while (Date.now() < until && (bot.oxygenLevel ?? 20) < BREATHED) {
    bot.setControlState('jump', true)
    await ctx.sleep(100)
  }
  bot.setControlState('jump', false)
}

export const surface = {
  name: 'surface',
  description: 'Swim up for air: straight up through open water, or to the nearest air pocket when something is overhead. ' +
    'Her breathing reflex does this by itself whenever her air runs low under water.',
  doneWhen: 'her head is out of the water and she has her breath back',
  stopsWhen: 'no air within reach, time limit',
  params: {
    time_limit: timeLimitParam(20, 60)
  },
  async run (ctx) {
    const { bot } = ctx
    if (!headInWater(bot)) return done({ air: bot.oxygenLevel, note: 'head already above water' })
    const startY = bot.entity.position.y
    if (openAbove(bot)) {
      await bot.look(bot.entity.yaw, Math.PI / 2, true) // eyes up, the way a swimmer goes
      while (headInWater(bot)) {
        bot.setControlState('forward', false)
        bot.setControlState('jump', true)
        await ctx.sleep(100)
      }
    } else {
      const pocket = nearestAirPocket(bot)
      if (!pocket) return failed(`something is overhead and there's no air within ${AIR_RADIUS} blocks`, { air: bot.oxygenLevel })
      if (!await swimTo(ctx, pocket)) return failed('stuck swimming to the air pocket', { air: bot.oxygenLevel })
    }
    await catchBreath(ctx)
    return done({ air: bot.oxygenLevel, rose: Math.round(bot.entity.position.y - startY) })
  }
}
