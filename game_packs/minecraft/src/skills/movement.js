// go_to and follow_player.
import { createRequire } from 'node:module'

import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import {
  blockIds, distanceTo, findEntity, findPlayer, goals, hostilesAround, movements,
  requireBlock, requireEntityType, requireOtherPlayer, walkTo
} from './common.js'
import { lastSeen } from '../sightings.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

// Targets: "x y z" or "x z" (commas fine), "player:<name>", "mob:<type>", "block:<type>".
export function parseTarget (text) {
  const numbers = text.split(/[\s,]+/).filter(Boolean)
  if (numbers.length && numbers.every((n) => /^-?\d+(\.\d+)?$/.test(n))) {
    if (numbers.length === 3) return { kind: 'place', x: +numbers[0], y: +numbers[1], z: +numbers[2] }
    if (numbers.length === 2) return { kind: 'place', x: +numbers[0], z: +numbers[1] }
    throw new ParamError('a place is "x y z" or "x z"')
  }
  const match = /^(player|mob|block):([A-Za-z0-9_]{1,40})$/.exec(text)
  if (!match) throw new ParamError('target must be "x y z", "x z", "player:<name>", "mob:<type>" or "block:<type>"')
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
  description: 'Walk to a place, a player, a mob, or the nearest block of a type. A player out of sight: first to where she last saw them.',
  doneWhen: 'she is within range of the target (for a player: also at their level, within 1 block up or down)',
  stopsWhen: 'no path, target not found or gone, attacked, time limit',
  params: {
    target: { type: 'string', required: true, description: '"x y z", "x z", "player:<name>", "mob:<type>" or "block:<type>"' },
    range: { type: 'number', min: 1, max: 32, default: 2, description: 'how close counts as arrived, in blocks' },
    dig: {
      type: 'enum',
      values: ['yes', 'no'],
      default: 'no',
      description: 'yes: she may break blocks to get there, e.g. dig down into a hole or a tunnel to a player'
    },
    time_limit: timeLimitParam(60, 300)
  },
  validate (bot, params) {
    const target = parseTarget(params.target)
    if (target.kind === 'mob') requireEntityType(bot, target.name)
    if (target.kind === 'block') requireBlock(bot, target.name)
    if (target.kind === 'player') requireOtherPlayer(bot, target.name)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const target = parseTarget(params.target)
    const dig = params.dig === 'yes'
    const attack = watchForAttack(ctx)
    try {
      if (target.kind === 'place') return await goToPlace(ctx, target, params.range, attack)
      if (target.kind === 'block') {
        const blockPos = bot.findBlock({ matching: blockIds(bot, target.name), maxDistance: 64 })?.position
        if (!blockPos) return failed(`no ${target.name} within 64 blocks`)
        return await goToPlace(ctx, blockPos, params.range, attack)
      }
      if (target.kind === 'player') {
        const entity = findPlayer(bot, target.name) ?? await toLastSeen(ctx, target.name, attack)
        return await goToEntity(ctx, entity, params.range, attack, { level: true, dig })
      }
      const entity = findEntity(bot, target.name)
      if (!entity) return failed(`can't see a ${target.name} nearby`)
      return await goToEntity(ctx, entity, params.range, attack, { dig })
    } finally {
      attack.off()
    }
  }
}

async function goToPlace (ctx, place, range, attack) {
  const { bot } = ctx
  const goal = place.y === undefined
    ? new goals.GoalNearXZ(place.x, place.z, range)
    : new goals.GoalNear(place.x, place.y, place.z, range)
  // Re-plan up to a few times: long walks leave loaded terrain and paths get interrupted.
  for (let attempt = 0; attempt < 4; attempt++) {
    const arrived = await walkTo(ctx, goal)
    attack.check()
    if (arrived || goal.isEnd(bot.entity.position.floored())) {
      return done(position(bot))
    }
    ctx.progress({ remaining: Math.round(distanceToPlace(bot, place)) })
    await ctx.sleep(200)
  }
  return failed('no path', { remaining: Math.round(distanceToPlace(bot, place)) })
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
