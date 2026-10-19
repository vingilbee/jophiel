// go_to and follow_player.
import { createRequire } from 'node:module'

import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import {
  blockIds, distanceTo, findEntity, findPlayer, goals, hostilesAround, movements,
  requireBlock, requireEntityType, requireOtherPlayer, walkTo
} from './common.js'
import { seenBlock } from '../sight.js'
import { lastSeen } from '../sightings.js'
import { lookedAtBlock } from '../watching.js'
import { peakSpot, surfaceSpot } from '../terrain.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

// Targets: "x y z" or "x z" (commas fine), "player:<name>", "mob:<type>", "block:<type>",
// "looking:<player>" (the block that player is looking at), "peak" (the highest ground near,
// src/terrain.js) and "surface" (underground: the way out to the sky).
const TARGETS = '"x y z", "x z", "player:<name>", "mob:<type>", "block:<type>", "looking:<player>", "peak" or "surface"'
export function parseTarget (text) {
  if (text === 'peak' || text === 'surface') return { kind: text }
  const numbers = text.split(/[\s,]+/).filter(Boolean)
  if (numbers.length && numbers.every((n) => /^-?\d+(\.\d+)?$/.test(n))) {
    if (numbers.length === 3) return { kind: 'place', x: +numbers[0], y: +numbers[1], z: +numbers[2] }
    if (numbers.length === 2) return { kind: 'place', x: +numbers[0], z: +numbers[1] }
    throw new ParamError('a place is "x y z" or "x z"')
  }
  const match = /^(player|mob|block|looking):([A-Za-z0-9_]{1,40})$/.exec(text)
  if (!match) {
    throw new ParamError(`target must be ${TARGETS}`)
  }
  return { kind: match[1], name: match[2] }
}

// Fails or stops when she's attacked: something hostile hurt her (fall damage doesn't count).
function watchForAttack (ctx) {
  const { bot } = ctx
  let attacker = null
  const onHurt = (entity) => {
    if (entity !== bot.entity) return
    const near = hostilesAround(bot, 6)
    if (near.length) attacker = near[0].name
  }
  bot.on('entityHurt', onHurt)
  return {
    check () {
      // With guard on, the guard fights it and this skill carries on afterwards (src/modes).
      if (attacker && !ctx.guarded?.()) throw new SkillEnd('hurt', `attacked by ${attacker}`)
      attacker = null
    },
    off () { bot.off('entityHurt', onHurt) }
  }
}

export const goTo = {
  name: 'go_to',
  description: 'Walk to a place, a player, a mob, the nearest block of a type, or the spot a player is looking at. ' +
    'A player out of sight: first to where she last saw them. "Come here" / "next to me": player:<name> with range 1. ' +
    '"Go there" / "stand here" while they point their view at a spot: looking:<name> with range 0. ' +
    '"Climb the mountain" / "get to the top": peak (the highest ground near, see your state). ' +
    '"Get out" / "go up" underground: surface (the way out to the sky; with dig=yes it digs up when there is none).',
  doneWhen: 'she is within range of the target (for a player: also at their level, within 1 block up or down; ' +
    'range 0: standing on that exact block)',
  stopsWhen: 'no path, target not found or gone, attacked, time limit',
  params: {
    target: {
      type: 'string',
      required: true,
      description: `${TARGETS} (looking: where they look)`
    },
    range: {
      type: 'number',
      min: 0,
      max: 32,
      default: 2,
      description: 'how close counts as arrived, in blocks (0: on that exact block; for a player at least 1)'
    },
    dig: {
      type: 'enum',
      values: ['yes', 'no'],
      default: 'no',
      description: 'yes: she may break blocks to get there: dig down into a hole, a tunnel to a player, steps up a slope too steep to walk'
    },
    time_limit: timeLimitParam(60, 300)
  },
  validate (bot, params) {
    const target = parseTarget(params.target)
    if (target.kind === 'mob') requireEntityType(bot, target.name)
    if (target.kind === 'block') requireBlock(bot, target.name)
    if (target.kind === 'player' || target.kind === 'looking') requireOtherPlayer(bot, target.name)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const target = parseTarget(params.target)
    const dig = params.dig === 'yes'
    const attack = watchForAttack(ctx)
    try {
      if (target.kind === 'place') return await goToPlace(ctx, target, params.range, attack, { dig })
      if (target.kind === 'block') {
        const blockPos = seenBlock(bot, { matching: blockIds(bot, target.name), maxDistance: 64 })?.position
        if (!blockPos) return failed(`no ${target.name} in sight within 64 blocks`)
        return await goToPlace(ctx, blockPos, params.range, attack, { dig })
      }
      if (target.kind === 'peak') return await goToPeak(ctx, params.range, attack, dig)
      if (target.kind === 'surface') return await goToSurface(ctx, params.range, attack, dig)
      if (target.kind === 'looking') return await goToLookedAt(ctx, target.name, params.range, attack)
      if (target.kind === 'player') {
        if (bot.players[target.name]?.gamemode === 3) {
          return failed(`${target.name} is in spectator mode: invisible and passing through walls, so you can't reach them`)
        }
        const entity = findPlayer(bot, target.name) ?? await toLastSeen(ctx, target.name, attack)
        return await goToEntity(ctx, entity, Math.max(1, params.range), attack, { level: true, dig })
      }
      const entity = findEntity(bot, target.name)
      if (!entity) return failed(`can't see a ${target.name} nearby`)
      return await goToEntity(ctx, entity, Math.max(1, params.range), attack, { dig })
    } finally {
      attack.off()
    }
  }
}

// dig: she may break blocks on the way (it used to be dropped here, so "x y z" with dig=yes never
// dug: live, a 3-block dirt step on a mountain was "no path" every time).
async function goToPlace (ctx, place, range, attack, { dig = false, attempts = 4 } = {}) {
  const { bot } = ctx
  const goal = place.y === undefined
    ? new goals.GoalNearXZ(place.x, place.z, range)
    : new goals.GoalNear(place.x, place.y, place.z, range)
  // Re-plan up to a few times: long walks leave loaded terrain and paths get interrupted.
  for (let attempt = 0; attempt < attempts; attempt++) {
    const arrived = await walkTo(ctx, goal, { canDig: dig })
    attack.check()
    if (arrived || goal.isEnd(bot.entity.position.floored())) {
      return done(position(bot))
    }
    ctx.progress({ remaining: Math.round(distanceToPlace(bot, place)) })
    await ctx.sleep(200)
  }
  return failed('no path', { remaining: Math.round(distanceToPlace(bot, place)) })
}

// The highest ground near (src/terrain.js), worked out now. Already on it: done, nothing to do.
async function goToPeak (ctx, range, attack, dig) {
  const peak = peakSpot(ctx.bot)
  if (!peak) return done({ ...position(ctx.bot), note: 'already on the highest ground near' })
  const leg = await walkStops(ctx, peak.waypoints, attack, dig)
  if (leg) {
    if (!dig) leg.reason += ' (steps too steep to walk: dig=yes digs steps)'
    leg.data = { ...leg.data, peak: { x: peak.x, y: peak.y, z: peak.z } }
    return leg
  }
  const end = await goToPlace(ctx, { x: peak.x, y: peak.y, z: peak.z }, Math.max(1, range), attack, { dig })
  if (end.status === 'failed' && !dig) end.reason += ' (steps too steep to walk: dig=yes digs steps)'
  end.data = { ...end.data, peak: { x: peak.x, y: peak.y, z: peak.z } }
  return end
}

// A long way, stop by stop: each a short search (one long search through diggable ground gave up,
// seen in tests). Walk first, dig only where walking can't: allowed to dig everywhere, the
// pathfinder sometimes tunnelled its own way beside the open one and got stuck in it.
// null when every stop was reached, else the failed leg's outcome.
async function walkStops (ctx, stops, attack, dig) {
  for (const stop of stops ?? []) {
    let leg = await goToPlace(ctx, stop, 1, attack, { dig: false, attempts: dig ? 1 : 4 })
    if (leg.status !== 'done' && dig) leg = await goToPlace(ctx, stop, 1, attack, { dig: true })
    if (leg.status !== 'done') {
      leg.data = { ...leg.data, stuck_at: stop }
      return leg
    }
  }
  return null
}

// Underground, to the sky: the way out found through open space; with none, straight up by digging.
async function goToSurface (ctx, range, attack, dig) {
  const { bot } = ctx
  const { way, above } = surfaceSpot(bot)
  if (!above) return done({ ...position(bot), note: 'already under open sky' })
  if (way) {
    const leg = await walkStops(ctx, way.waypoints, attack, dig)
    if (leg) {
      if (way.climb && !dig) leg.reason += ` (a ${way.climb.height} m climb on the way: dig=yes digs steps)`
      leg.data = { ...leg.data, exit: way.exit }
      return leg
    }
    const end = await goToPlace(ctx, way.exit, Math.max(1, range), attack, { dig })
    if (end.status === 'failed' && way.climb && !dig) end.reason += ` (a ${way.climb.height} m climb on the way: dig=yes digs steps)`
    end.data = { ...end.data, exit: way.exit }
    return end
  }
  if (!dig) return failed('no way out to the sky through open space nearby; dig=yes digs up to it')
  const p = bot.entity.position.floored()
  const end = await goToPlace(ctx, { x: p.x, y: above.y, z: p.z }, Math.max(1, range), attack, { dig: true })
  end.data = { ...end.data, dug_up_to: above.y }
  return end
}

const LOOK_REACH = 32 // how far along a player's view she looks for the spot they mean

// The spot a player is looking at, taken once at the start (they may look away after). A floor
// block with room above: stand on it (range 0: exactly there). Anything else (a wall, a bed, a
// table): stand next to it.
async function goToLookedAt (ctx, name, range, attack) {
  const { bot } = ctx
  const player = findPlayer(bot, name)
  if (!player) return failed(`can't see ${name}, so can't tell where they're looking`)
  const block = lookedAtBlock(bot, player, LOOK_REACH)
  if (!block) return failed(`${name} isn't looking at a block within ${LOOK_REACH} blocks`)
  const above = bot.blockAt(block.position.offset(0, 1, 0))
  const head = bot.blockAt(block.position.offset(0, 2, 0))
  const floor = block.boundingBox === 'block' && above?.boundingBox === 'empty' && head?.boundingBox === 'empty'
  if (!floor) {
    const arrived = await walkTo(ctx, new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z))
    attack.check()
    return arrived ? done({ ...position(bot), next_to: block.name }) : failed(`no path to the ${block.name}`)
  }
  const spot = block.position.offset(0, 1, 0)
  const end = await goToPlace(ctx, { x: spot.x, y: spot.y, z: spot.z }, range, attack)
  if (end.status === 'done') end.data = { ...end.data, on: block.name }
  return end
}

// A player out of sight (online, beyond entity range): walk to where she last saw them and
// return their entity once they're in sight again. Throws an outcome when that doesn't work.
async function toLastSeen (ctx, name, attack) {
  const { bot } = ctx
  if (!bot.players[name]) throw failed(`${name} isn't in the game`)
  const seen = lastSeen(bot, name)
  if (!seen) throw failed(`can't see ${name} and haven't seen them since joining; ask where they are`)
  const goal = new goals.GoalNearXZ(seen.x, seen.z, 8)
  bot.pathfinder.setMovements(movements(bot))
  bot.pathfinder.setGoal(goal)
  let restarts = 0
  let stillSince = null
  try {
    while (!goal.isEnd(bot.entity.position.floored())) {
      await ctx.sleep(250)
      attack.check()
      const found = findPlayer(bot, name)
      if (found) return found
      ctx.progress({ heading_to: 'last_seen', remaining: Math.round(distanceToPlace(bot, seen)) })
      if (bot.pathfinder.isMoving()) {
        stillSince = null
        continue
      }
      // Long walks leave loaded terrain and the path ends early: re-aim a few times.
      stillSince ??= Date.now()
      if (Date.now() - stillSince < 2000) continue
      if (++restarts > 4) throw failed(`no path to where ${name} was last seen`, position(bot))
      stillSince = null
      bot.pathfinder.setGoal(goal)
    }
  } finally {
    bot.pathfinder.setGoal(null)
  }
  const found = findPlayer(bot, name)
  if (found) return found
  throw new SkillEnd('lost', `went to where ${name} was last seen; they've moved on and are still out of sight`,
    position(bot))
}

function distanceToPlace (bot, place) {
  const p = bot.entity.position
  return place.y === undefined ? Math.hypot(p.x - place.x, p.z - place.z) : p.distanceTo(new Vec3(place.x, place.y, place.z))
}

// Within this many blocks up or down counts as "at their level" (a step or a slab).
const LEVEL_DY = 1.1

// Next to a spot and at its level (one block up or down allowed): the rim of a hole two
// blocks above a player is not "with them", which a plain distance goal accepted.
function levelGoal (p, range) {
  return new goals.GoalCompositeAll([
    new goals.GoalNearXZ(p.x, p.z, Math.max(1, range)),
    new goals.GoalCompositeAny([new goals.GoalY(p.y - 1), new goals.GoalY(p.y), new goals.GoalY(p.y + 1)])
  ])
}

// `level`: arriving also needs her at the target's level (players: "come to me" means
// down in the hole with them). `dig`: she may break blocks on the way.
async function goToEntity (ctx, entity, range, attack, { level = false, dig = false } = {}) {
  const { bot } = ctx
  bot.pathfinder.setMovements(movements(bot, { canDig: dig }))
  let aimedAt = null
  const aim = () => {
    if (!level) return bot.pathfinder.setGoal(new goals.GoalFollow(entity, range), true)
    const p = entity.position.floored()
    if (aimedAt && aimedAt.distanceTo(p) < 1) return
    aimedAt = p
    bot.pathfinder.setGoal(levelGoal(p, range))
  }
  aim()
  // Digging is slow (a dirt block takes most of a second by hand): give it longer to show progress.
  const patienceMs = dig ? 15000 : 8000
  let noProgressSince = Date.now()
  let best = distanceTo(bot, entity.position)
  while (true) {
    await ctx.sleep(100)
    attack.check()
    if (!entity.isValid) return new SkillEnd('lost', 'the target is gone')
    const d = distanceTo(bot, entity.position)
    const dy = entity.position.y - bot.entity.position.y
    if (d <= range + 0.5 && (!level || Math.abs(dy) <= LEVEL_DY)) {
      return done({ ...position(bot), distance: round1(d) })
    }
    if (level) aim() // they moved: follow
    if (d < best - 0.5) {
      best = d
      noProgressSince = Date.now()
    } else if (Date.now() - noProgressSince > patienceMs) {
      if (level && Math.abs(dy) > LEVEL_DY && !dig) {
        const where = dy < 0 ? 'below' : 'above'
        return failed(`can't get down to their level: they're ${Math.round(Math.abs(dy))} blocks ${where} her ` +
          '(in a hole or on a ledge); go_to with dig=yes digs to them', { distance: round1(d), dy: round1(dy) })
      }
      return failed('no path', { distance: round1(d) })
    }
    ctx.progress({ distance: round1(d) })
  }
}

export const followPlayer = {
  name: 'follow_player',
  description: 'Stay close to a player for a while. Out of sight: first walks to where she last saw them.',
  doneWhen: 'the time asked for is up',
  stopsWhen: 'the player leaves or gets out of sight, attacked, stopped',
  params: {
    player: { type: 'string', required: true, description: 'their in-game name' },
    distance: { type: 'number', min: 1, max: 10, default: 3, description: 'how close to stay, in blocks' },
    seconds: { type: 'number', min: 1, max: 600, default: 60, description: 'how long to follow' },
    time_limit: timeLimitParam(605, 605)
  },
  validate (bot, params) {
    requireOtherPlayer(bot, params.player)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const attack = watchForAttack(ctx)
    try {
      if (!findPlayer(bot, params.player)) await toLastSeen(ctx, params.player, attack)
      bot.pathfinder.setMovements(movements(bot, { canDig: false }))
      // Re-aim from this loop whenever they've moved on from where she's heading, rather than
      // relying only on pathfinder noticing (it sometimes stops where they used to be).
      let aimedAt = null
      const until = Date.now() + params.seconds * 1000
      while (Date.now() < until) {
        attack.check()
        const current = bot.players[params.player]?.entity
        if (!current?.isValid) return new SkillEnd('lost', `${params.player} is out of sight`)
        const d = distanceTo(bot, current.position)
        if (d > 48) return new SkillEnd('lost', `${params.player} got too far ahead`)
        if (d > params.distance + 1 && (!aimedAt || current.position.distanceTo(aimedAt) > 1.5 || !bot.pathfinder.isMoving())) {
          aimedAt = current.position.clone()
          bot.pathfinder.setGoal(new goals.GoalFollow(current, params.distance), true)
        }
        await ctx.sleep(200)
      }
      return done({ followed_seconds: params.seconds })
    } finally {
      attack.off()
    }
  }
}

const round1 = (n) => Math.round(n * 10) / 10
const position = (bot) => {
  const p = bot.entity.position
  return { x: Math.round(p.x), y: Math.round(p.y), z: Math.round(p.z) }
}
