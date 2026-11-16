// go_to and follow_player.
import { createRequire } from 'node:module'

import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import {
  blockIds, distanceTo, findEntity, findPlayer, goals, hostilesAround, movements,
  requireBlock, requireEntityType, requireOtherPlayer, walkMovements, walkTo
} from './common.js'
import { seenBlock } from '../sight.js'
import { lastSeen } from '../sightings.js'
import { lookedAtBlock } from '../watching.js'
import { peakSpot, surfaceSpot } from '../terrain.js'
import { describeEnd, walk } from '../surroundings.js'
import { approachSpots, checkRoute, groundSpot, loaded, nearestStandable, spotGoal, spotProblem } from '../navigation.js'
import { botWorld } from '../view.js'
import { dimensionOf } from '../perception.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

// Targets: "x y z" or "x z" (commas fine), "player:<name>", "mob:<type>", "block:<type>",
// "looking:<player>" (the block that player is looking at), "peak" (the highest ground near,
// src/terrain.js), "surface" (underground: the way out to the sky) and a landmark of her view
// ("L3" or "landmark:L3", src/landmarks.js).
const TARGETS = '"x y z", "x z", "player:<name>", "mob:<type>", "block:<type>", "looking:<player>", "peak", "surface" or a landmark ("L3")'
export function parseTarget (text) {
  if (text === 'peak' || text === 'surface') return { kind: text }
  const landmark = /^(?:landmark:)?(L\d{1,6})$/i.exec(text)
  if (landmark) return { kind: 'landmark', id: landmark[1].toUpperCase() }
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
  description: 'Walk to a place, a landmark you see or remember (L3: an opening, a passage, a structure, water), ' +
    'a player, a mob, the nearest block of a type, or the spot a player is looking at. ' +
    'A landmark: it picks a spot to stand at it that a route reaches; when none does, it goes as close as walking allows ' +
    'and says what is in the way. Coordinates: it checks the spot is somewhere to stand (moving to the nearest one ' +
    'within 3 blocks if not) and that a route reaches it before walking. ' +
    'A player out of sight: first to where she last saw them. "Come here" / "next to me": player:<name> with range 1. ' +
    '"Go there" / "stand here" while they point their view at a spot: looking:<name> with range 0. ' +
    '"Climb the mountain" / "get to the top": peak (the highest ground near, see your state). ' +
    '"Get out" / "go up" underground: surface (the way out to the sky; with dig=yes it digs up when there is none).',
  doneWhen: 'she is within range of the target (for a player: also at their level, within 1 block up or down; ' +
    'range 0: standing on that exact block)',
  stopsWhen: 'no route under its rules (it says the closest spot reached and what is in the way), no place to stand ' +
    'there, target not found or gone, attacked, time limit',
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
    if (target.kind === 'landmark') requireLandmark(bot, target.id)
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
      if (target.kind === 'place') return await goToCoordinates(ctx, target, params.range, attack, { dig })
      if (target.kind === 'landmark') return await goToLandmark(ctx, target.id, params.range, attack, { dig })
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

// ---- grounded movement: coordinates she gave, and landmarks ------------------------------

function requireLandmark (bot, id) {
  const marks = bot.perception?.landmarks?.()
  const lm = marks?.get(id)
  if (!lm) {
    const known = marks?.list({ dimension: dimensionOf(bot), near: bot.entity?.position, max: 8 }).map((l) => l.id) ?? []
    throw new ParamError(`no landmark ${id}` + (known.length ? `; landmarks you know here: ${known.join(', ')}` : '; none known yet'))
  }
}

const at3 = (p) => `(${p.x} ${p.y} ${p.z})`
const dist = (a, b) => Math.hypot(a.x - b.x, (a.y ?? b.y ?? 0) - (b.y ?? a.y ?? 0), a.z - b.z)
const here = (bot) => {
  const p = bot.entity.position
  return { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) }
}

// What's straight toward a place from where she stands, walking the ground (src/surroundings.js).
function straightToward (bot, place) {
  const p = bot.entity.position
  const dx = place.x + 0.5 - p.x
  const dz = place.z + 0.5 - p.z
  const n = Math.hypot(dx, dz)
  if (n < 2) return null
  const at = (x, y, z) => bot.blockAt(new Vec3(x, y, z), false) ?? null
  try {
    const w = walk(at, p, dx / n, dz / n, Math.min(24, Math.ceil(n)))
    const end = describeEnd(w.end)
    if (!end) return null
    return w.walked ? `straight toward it from here the ground goes ${w.walked} m, then ${end}` : `straight toward it from here: ${end} right away`
  } catch {
    return null
  }
}

function routeNote (route, place, dig) {
  return {
    status: route.status, at: new Date().toISOString(), from: route.from, dig, ms: route.ms, searched: route.searched,
    closest: route.closest, remaining: Math.round(dist(route.closest, place))
  }
}

// The pathfinder's own limits (mineflayer-pathfinder Movements: maxDropDown 4, any depth into water).
const RULES = (dig) => dig
  ? 'digging allowed, no block placing, drops of at most 4 (deeper only into water)'
  : 'walking only: no digging, no block placing, drops of at most 4 (deeper only into water)'

// The reason for a walk that didn't arrive, from the evidence: the route search's result, where she
// got to, and what's straight ahead. Only what was measured is said.
function noArrival (bot, place, label, route, { dig, adjusted, walkedTo = null }) {
  const now = here(bot)
  const remaining = Math.round(dist(now, place))
  const parts = []
  if (route?.status === 'no_route') {
    parts.push(`no route to ${label} under go_to's rules (${RULES(dig)}) within loaded terrain: the search ran out of ` +
      `places to try (${route.searched} spots)`)
  } else if (route?.status === 'incomplete') {
    parts.push(`didn't get to ${label}: the route search used its time before finding a way (not proof there's none)`)
  } else {
    parts.push(`didn't get to ${label}: the walk stopped getting closer`)
  }
  if (route?.closest && route.status !== 'success') {
    const c = route.closest
    parts.push(`the closest spot the search reached is ${at3(c)}, ${Math.round(dist(c, place))} m from it`)
  }
  parts.push(walkedTo ? `walked to ${at3(now)} (a nearer spot to look from), ${remaining} m from it` : `you're at ${at3(now)}, ${remaining} m from it`)
  const ahead = straightToward(bot, place)
  if (ahead) parts.push(ahead)
  parts.push(dig ? 'Try a different approach: another side or landmark, or look_around from here'
    : 'Don\'t repeat it unchanged: try another spot or landmark on this side, look_around for a way round, or dig="yes"')
  return failed(parts.join('; ') + '.', {
    nav: {
      problem: route?.status === 'no_route' ? 'no_route' : route?.status === 'incomplete' ? 'search_incomplete' : 'no_progress',
      target: place, reached: now, remaining,
      ...(route ? { route: { status: route.status, ms: route.ms, searched: route.searched, closest: route.closest } } : {}),
      ...(adjusted ? { adjusted } : {})
    }
  })
}

// Walk to a goal after a route check; legs while she keeps getting closer (terrain loads as she goes).
// No route under the rules: walking it again won't change that; she goes to the closest spot the search
// reached if that's a real step nearer (a new place to look from), and says what's in the way.
async function travel (ctx, goal, place, label, attack, { dig = false, adjusted = null, onRoute = () => {} } = {}) {
  const { bot } = ctx
  if (goal.isEnd(bot.entity.position.floored())) return done({ ...position(bot), ...(adjusted ? { adjusted } : {}) })
  const moves = walkMovements(bot, dig)
  let route = await checkRoute(bot, goal, moves, { budgetMs: dig ? 4000 : 2500, check: ctx.check })
  attack.check()
  onRoute(route)
  ctx.progress({ route: route.status })
  if (route.status === 'no_route') {
    const start = here(bot)
    const c = route.closest
    if (dist(c, place) < dist(start, place) - 3 && dist(c, start) >= 2) {
      await walkTo(ctx, new goals.GoalBlock(c.x, c.y, c.z), { canDig: dig })
      attack.check()
      const moved = dist(here(bot), start) >= 2
      return noArrival(bot, place, label, route, { dig, adjusted, walkedTo: moved ? c : null })
    }
    return noArrival(bot, place, label, route, { dig, adjusted })
  }
  let stalls = 0
  for (let leg = 0; leg < 8; leg++) {
    const before = distanceToPlace(bot, place)
    const arrived = await walkTo(ctx, goal, { canDig: dig })
    attack.check()
    if (arrived || goal.isEnd(bot.entity.position.floored())) {
      return done({ ...position(bot), ...(adjusted ? { adjusted } : {}) })
    }
    const after = distanceToPlace(bot, place)
    ctx.progress({ remaining: Math.round(after) })
    if (after < before - 2) {
      stalls = 0
      continue
    }
    if (++stalls >= 2) break
    // Not closer: check again from here before trying again.
    const again = await checkRoute(bot, goal, moves, { budgetMs: 1500, check: ctx.check })
    attack.check()
    onRoute(again)
    route = again
    if (again.status === 'no_route') break
    await ctx.sleep(200)
  }
  return noArrival(bot, place, label, route, { dig, adjusted })
}

// Coordinates she gave: a spot to stand on (the nearest one within 3 blocks when it isn't), loaded,
// and reached by a route. Terrain that isn't loaded is unknown, never empty: she walks toward it while
// she keeps getting closer and says where the known world ended.
// ``approached``: set once she walked toward it while it was unloaded ({ start }: how far it was then); the
// destination is resolved again here and, if it still isn't loaded, that's the outcome (no second approach).
async function goToCoordinates (ctx, target, range, attack, { dig, approached = null }) {
  const { bot } = ctx
  const world = botWorld(bot)
  const asked = target.y === undefined ? { x: target.x, z: target.z } : { x: target.x, y: target.y, z: target.z }
  const fx = Math.floor(target.x)
  const fz = Math.floor(target.z)
  const resume = (start) => goToCoordinates(ctx, target, range, attack, { dig, approached: { start } })
  if (target.y === undefined) {
    const ground = groundSpot(world, fx, fz, Math.floor(bot.entity.position.y), { up: 96, down: 96 })
    if (ground === null) {
      const place = { x: fx, z: fz }
      return approached ? beyondLoaded(bot, place, approached.start) : toUnloaded(ctx, place, range, attack, dig, resume)
    }
    const place = ground || { x: fx, y: Math.floor(bot.entity.position.y), z: fz }
    return travel(ctx, new goals.GoalNearXZ(target.x, target.z, range), place, `(${fx} ${fz})`, attack, { dig })
  }
  const fy = Math.floor(target.y)
  if (!loaded(world, fx, fy, fz)) {
    const place = { x: fx, y: fy, z: fz }
    return approached ? beyondLoaded(bot, place, approached.start) : toUnloaded(ctx, place, range, attack, dig, resume)
  }
  let spot = { x: fx, y: fy, z: fz }
  let adjusted = null
  const problem = spotProblem(world, fx, fy, fz)
  if (problem) {
    const near = nearestStandable(world, fx, fy, fz, { radius: Math.max(3, Math.ceil(range)), column: 8 })
    if (!near) {
      return failed(`${at3(spot)} is no place to stand (${problem}) and there's no spot to stand within 3 blocks of it, ` +
        'so go_to didn\'t walk. Pick a spot from your state (a Ways end spot, a landmark like L3), or give "x z" for the ground there.',
      { nav: { problem: 'invalid_spot', target: asked, why: problem } })
    }
    adjusted = { from: asked, to: near, why: problem }
    spot = near
  }
  const goal = range <= 0 ? new goals.GoalBlock(spot.x, spot.y, spot.z) : new goals.GoalNear(spot.x, spot.y, spot.z, range)
  const label = adjusted ? `${at3(spot)} (the nearest place to stand to ${at3(asked)}, which is ${problem})` : at3(spot)
  return travel(ctx, goal, spot, label, attack, { dig, adjusted })
}

// Toward a place beyond the loaded world: legs while she gets closer, then an honest outcome. With
// ``resume`` the walk is only an approach: once the place is loaded (or she reached its column), ``resume``
// resolves the original destination and range, so a height or an exact range is checked, never assumed.
async function toUnloaded (ctx, place, range, attack, dig, resume = null) {
  const { bot } = ctx
  const goal = new goals.GoalNearXZ(place.x, place.z, Math.max(range, 1))
  const start = distanceToPlace(bot, place)
  let stalls = 0
  for (let leg = 0; leg < 12; leg++) {
    const before = distanceToPlace(bot, place)
    const arrived = await walkTo(ctx, goal, { canDig: dig })
    attack.check()
    if (resume && placeLoaded(bot, place)) return resume(start)
    if (arrived || goal.isEnd(bot.entity.position.floored())) return resume ? resume(start) : done(position(bot))
    const after = distanceToPlace(bot, place)
    ctx.progress({ remaining: Math.round(after) })
    if (after < before - 2) { stalls = 0; continue }
    if (++stalls >= 2) break
    await ctx.sleep(300)
  }
  return beyondLoaded(bot, place, start)
}

function placeLoaded (bot, place) {
  const world = botWorld(bot)
  if (place.y !== undefined) return loaded(world, place.x, place.y, place.z)
  return groundSpot(world, place.x, place.z, Math.floor(bot.entity.position.y), { up: 96, down: 96 }) !== null
}

// The outcome when a place was beyond the loaded world: how much closer she got, and where she is.
function beyondLoaded (bot, place, start) {
  const now = here(bot)
  const walked = Math.round(start - distanceToPlace(bot, place))
  const label = place.y === undefined ? `(${place.x} ${place.z})` : at3(place)
  const ahead = straightToward(bot, place)
  return failed(`${label} was beyond the loaded world when asked (unknown terrain, not known to be open)` +
    `; got ${Math.max(0, walked)} m closer, to ${at3(now)}, ${Math.round(distanceToPlace(bot, place))} m from it` +
    (ahead ? `; ${ahead}` : '') + '. What\'s around you now is newly loaded: look at your state before going on.',
  { nav: { problem: 'unloaded', target: place, reached: now, remaining: Math.round(distanceToPlace(bot, place)) } })
}

// A landmark (src/landmarks.js): spots to stand at it, nearest first; the first one a route reaches is
// where she goes. When no spot is reached by a route, she goes to the closest spot any search reached (a
// nearer viewpoint) and says so; each try is kept with the landmark.
async function goToLandmark (ctx, id, range, attack, { dig }) {
  const { bot } = ctx
  const marks = bot.perception?.landmarks?.()
  const lm = marks?.get(id)
  if (!lm) return failed(`no landmark ${id}`)
  if (lm.dimension !== dimensionOf(bot)) return failed(`${id} is in the ${String(lm.dimension).replace('minecraft:', '')}, not here`)
  if (lm.status === 'gone') return failed(`${id} was gone when last checked (${lm.gone_at}): its place has changed`)
  const world = botWorld(bot)
  const from = here(bot)
  const place = { x: lm.x, y: lm.y, z: lm.z }
  const label = `${id} (${lm.kind} at ${at3(place)})`
  const record = (outcome, extra = {}) => marks.noteApproach(id, {
    at: new Date().toISOString(), from, dig, outcome, reached: here(bot), remaining: Math.round(dist(here(bot), place)), ...extra
  })
  if (!loaded(world, lm.x, lm.y, lm.z)) {
    const end = await toUnloaded(ctx, place, Math.max(range, 2), attack, dig)
    record(end.status === 'done' ? 'arrived' : 'beyond loaded terrain')
    return end
  }
  const spots = approachSpots(world, lm, from)
  if (!spots.length) {
    record('no place to stand found at it')
    return failed(`found no spot to stand at ${label}: the ground there isn't somewhere to stand (water, a drop, or blocks). ` +
      'look_around toward it from closer, or go_to a spot next to it.', { nav: { problem: 'invalid_spot', target: place } })
  }
  const moves = walkMovements(bot, dig)
  const budgetAt = Date.now() + (dig ? 6000 : 4500)
  let best = null // the attempt whose search got closest
  for (const spot of spots.slice(0, 4)) {
    if (Date.now() > budgetAt) break
    const goal = range >= 2 ? spotGoal(spot, Math.min(range, 3)) : spotGoal(spot, 0)
    if (goal.isEnd(bot.entity.position.floored())) {
      record('arrived')
      return done({ ...position(bot), landmark: id, stand: spot })
    }
    const route = await checkRoute(bot, goal, moves, { budgetMs: Math.max(800, Math.min(2500, budgetAt - Date.now())), check: ctx.check })
    attack.check()
    if (route.status === 'success') {
      marks.noteRoute(id, { ...routeNote(route, spot, dig), spot })
      const end = await travel(ctx, goal, spot, `${label}, standing at ${at3(spot)}`, attack, { dig })
      record(end.status === 'done' ? 'arrived' : end.reason?.slice(0, 160) ?? end.status, { spot })
      if (end.status === 'done') end.data = { ...end.data, landmark: id, stand: spot }
      return end
    }
    const gap = dist(route.closest, spot)
    if (!best || gap < best.gap) best = { route, spot, gap }
  }
  // No spot reached by a route.
  const { route, spot } = best
  marks.noteRoute(id, { ...routeNote(route, spot, dig), spot })
  const c = route.closest
  let walkedTo = null
  if (dist(c, place) < dist(from, place) - 3 && dist(c, from) >= 2) {
    await walkTo(ctx, new goals.GoalBlock(c.x, c.y, c.z), { canDig: dig })
    attack.check()
    if (dist(here(bot), from) >= 2) walkedTo = c
  }
  const end = noArrival(bot, place, label, route, { dig, walkedTo })
  record(walkedTo ? 'went to the closest reachable spot' : route.status === 'no_route' ? 'no route' : 'search incomplete',
    { spot, route: route.status })
  return end
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
