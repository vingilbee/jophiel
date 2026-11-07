// look_around: turn and look, so her view (src/perception.js) covers what's around her or one thing,
// and the landmarks in her state are fresh; then check whether a route reaches the nearest ones. The
// view only shows what's in front of her eyes: what's behind her, or past a bend, is unknown until she
// looks (or walks) there.
import { createRequire } from 'node:module'

import { ParamError, done, failed, timeLimitParam } from './contract.js'
import { walkMovements } from './common.js'
import { approachSpots, checkRoute, spotGoal } from '../navigation.js'
import { botWorld } from '../view.js'
import { dimensionOf } from '../perception.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const LEVEL = -7 * Math.PI / 180 // a little down from level: the land ahead, not half sky
const SIDES = { ahead: 0, left: Math.PI / 2, behind: Math.PI, right: -Math.PI / 2 }
const COMPASS = {
  north: 0, 'north-west': Math.PI / 4, west: Math.PI / 2, 'south-west': 3 * Math.PI / 4,
  south: Math.PI, 'south-east': 5 * Math.PI / 4, east: 3 * Math.PI / 2, 'north-east': 7 * Math.PI / 4
}

export function parseToward (text) {
  if (text === undefined || text === null || text === '' || text === 'around') return { kind: 'around' }
  const t = String(text).trim().toLowerCase()
  const lm = /^(?:landmark:)?(l\d{1,6})$/.exec(t)
  if (lm) return { kind: 'landmark', id: lm[1].toUpperCase() }
  if (t in SIDES) return { kind: 'side', turn: SIDES[t] }
  if (t in COMPASS) return { kind: 'compass', yaw: COMPASS[t] }
  const n = t.split(/[\s,]+/).filter(Boolean)
  if (n.length === 3 && n.every((v) => /^-?\d+(\.\d+)?$/.test(v))) return { kind: 'place', x: +n[0], y: +n[1], z: +n[2] }
  throw new ParamError('toward must be a landmark ("L3"), ahead/left/right/behind, a compass direction ("north-east") or "x y z"')
}

function aimAt (bot, p) {
  const eye = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const dx = p.x - eye.x
  const dy = p.y - eye.y
  const dz = p.z - eye.z
  return { yaw: Math.atan2(-dx, -dz), pitch: Math.max(-1.2, Math.min(1.2, Math.atan2(dy, Math.hypot(dx, dz)))) }
}

// Whether a route reaches a landmark from here, under go_to's walking rules: kept with the landmark.
export async function landmarkRoute (ctx, lm, { budgetMs = 1500 } = {}) {
  const { bot } = ctx
  const world = botWorld(bot)
  const from = bot.entity.position.floored()
  const spots = approachSpots(world, lm, from, { max: 2 })
  if (!spots.length) return null
  const moves = walkMovements(bot, false)
  let best = null
  for (const spot of spots) {
    const route = await checkRoute(bot, spotGoal(spot, 1), moves, { budgetMs, check: ctx.check })
    const remaining = Math.round(Math.hypot(route.closest.x - spot.x, route.closest.y - spot.y, route.closest.z - spot.z))
    const note = { status: route.status, at: new Date().toISOString(), from: route.from, dig: false, ms: route.ms,
      searched: route.searched, closest: route.closest, remaining, spot }
    if (route.status === 'success') return note
    if (!best || remaining < best.remaining) best = note
  }
  return best
}

export const lookAround = {
  name: 'look_around',
  description: 'Look to see what is there: turns to look all round (4 views), or toward one landmark, side, ' +
    'direction or place, refreshing "In view" and the landmarks in your state; then checks whether a walking route ' +
    'reaches the nearest landmarks seen. Use it when what you need isn\'t in view, after getting somewhere new, or ' +
    'when go_to found no route (to look for a way round).',
  doneWhen: 'it has looked and checked the routes',
  stopsWhen: 'stopped, time limit',
  params: {
    toward: {
      type: 'string',
      description: 'empty or "around": all round; a landmark ("L3"); ahead, left, right or behind; ' +
        'a compass direction ("north-east"); or "x y z"'
    },
    routes: { type: 'enum', values: ['yes', 'no'], default: 'yes', description: 'yes: check routes to the nearest landmarks seen' },
    time_limit: timeLimitParam(30, 60)
  },
  validate (bot, params) {
    const t = parseToward(params.toward)
    if (t.kind === 'landmark' && !bot.perception?.landmarks?.()?.get(t.id)) throw new ParamError(`no landmark ${t.id}`)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const perception = bot.perception
    if (!perception) return failed('no view to look with')
    const marks = perception.landmarks()
    const t = parseToward(params.toward)
    const yaw0 = bot.entity.yaw
    let views = []
    if (t.kind === 'around') views = [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2].map((d) => ({ yaw: yaw0 + d, pitch: LEVEL }))
    else if (t.kind === 'side') views = [{ yaw: yaw0 + t.turn, pitch: LEVEL }]
    else if (t.kind === 'compass') views = [{ yaw: t.yaw, pitch: LEVEL }]
    else if (t.kind === 'place') views = [aimAt(bot, new Vec3(t.x + 0.5, t.y + 0.5, t.z + 0.5))]
    else {
      const lm = marks.get(t.id)
      if (lm.dimension !== dimensionOf(bot)) return failed(`${t.id} isn't in this dimension`)
      views = [aimAt(bot, new Vec3(lm.x + 0.5, lm.y + 1, lm.z + 0.5))]
    }
    const seen = new Set()
    const added = []
    for (const v of views) {
      // A non-zero pitch: the motion layer takes it as a look, not as a walking heading.
      await ctx.race(bot.look(v.yaw, v.pitch === 0 ? -0.001 : v.pitch, false))
      await ctx.sleep(150)
      const now = perception.look()
      for (const id of now.marks.visible) seen.add(id)
      added.push(...now.marks.added)
    }
    if (t.kind === 'around') {
      await ctx.race(bot.look(yaw0, LEVEL, false))
      perception.look()
    }
    const routes = {}
    if (params.routes !== 'no') {
      const p = bot.entity.position
      const targets = [...seen].map((id) => marks.get(id))
        .filter((l) => l && l.kind !== 'lava')
        .sort((a, b) => Math.hypot(a.x - p.x, a.z - p.z) - Math.hypot(b.x - p.x, b.z - p.z))
      if (t.kind === 'landmark' && !targets.some((l) => l.id === t.id)) targets.unshift(marks.get(t.id))
      for (const lm of targets.slice(0, 3)) {
        const r = await landmarkRoute(ctx, lm)
        if (!r) {
          routes[lm.id] = 'no place to stand found at it'
          continue
        }
        marks.noteRoute(lm.id, r)
        routes[lm.id] = r.status === 'success' ? 'a walking route reaches it'
          : r.status === 'no_route' ? `no walking route; closest reachable (${r.closest.x} ${r.closest.y} ${r.closest.z}), ${r.remaining} m from it`
            : `search ran out of time; got within ${r.remaining} m`
      }
    }
    const list = (ids) => ids.map((id) => {
      const lm = marks.get(id)
      return lm ? `${id} ${lm.kind}` : id
    })
    if (t.kind === 'landmark' && !seen.has(t.id)) {
      return done({ views: views.length, seen: list([...seen]), new: list(added), routes },
        `looked toward ${t.id} and didn't see it from here (in the way, or changed); it's kept as remembered`)
    }
    return done({ views: views.length, seen: list([...seen]), new: list(added), routes })
  }
}
