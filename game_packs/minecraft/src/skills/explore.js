// explore(goal, radius): walk to unexplored ground around where she started, watching for a goal.
// Remembers explored chunks per world (WorldMemory), so the next explore goes somewhere new.
// Places she passes (villages, caves, water, lava) are noted by the noticing layer, which runs all
// the time; explore reports the ones noted during its run. Lava and drops are avoided by pathfinder.
import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import {
  blockIds, findEntity, goals, hostilesAround, isNight, movements, requireBlock, requireEntityType
} from './common.js'

const LEG_SECONDS = 25
const STUCK_SECONDS = 5
const DANGER_RADIUS = 8
const SEEN_CHUNKS = 1 // chunks around her own that count as seen (a 3x3 block of chunks)

export function parseGoal (text) {
  if (!text) return null
  const match = /^(block|mob):([a-z0-9_]{1,40})$/.exec(text)
  if (!match) throw new ParamError('goal must be "block:<type>" or "mob:<type>"')
  return { kind: match[1], name: match[2] }
}

// Next unexplored chunk within radius of the start, nearest to where she is now.
export function nextFrontier (visited, start, here, radius) {
  const r = Math.ceil(radius / 16)
  const sx = Math.floor(start.x / 16)
  const sz = Math.floor(start.z / 16)
  let best = null
  let bestScore = Infinity
  for (let dx = -r; dx <= r; dx++) {
    for (let dz = -r; dz <= r; dz++) {
      if (dx * dx + dz * dz > r * r) continue
      const cx = sx + dx
      const cz = sz + dz
      if (visited.has(`${cx},${cz}`)) continue
      const x = cx * 16 + 8
      const z = cz * 16 + 8
      const score = Math.hypot(x - here.x, z - here.z)
      if (score < bestScore) {
        best = { x, z, key: `${cx},${cz}` }
        bestScore = score
      }
    }
  }
  return best
}

export function createExplore (memory) {
  return {
    name: 'explore',
    description: 'Walk to unexplored ground nearby, watching for a goal; reports places noticed on the way (villages, caves, water, lava).',
    doneWhen: 'the goal is found (reports where), or the area is covered',
    stopsWhen: 'night, danger nearby, time limit',
    params: {
      goal: { type: 'string', description: 'optional: "block:<type>" or "mob:<type>" to look for' },
      radius: { type: 'integer', min: 16, max: 256, default: 96, description: 'how far from where she starts, in blocks' },
      time_limit: timeLimitParam(180, 600)
    },
    validate (bot, params) {
      const goal = parseGoal(params.goal)
      if (goal?.kind === 'block') requireBlock(bot, goal.name)
      if (goal?.kind === 'mob') requireEntityType(bot, goal.name)
    },
    async run (ctx, params) {
      const { bot } = ctx
      const goal = parseGoal(params.goal)
      const start = bot.entity.position.clone()
      const startedAt = new Date().toISOString()
      const result = (extra = {}) => ({ explored_chunks: memory.visited.size, noted: memory.placesSince(startedAt), ...extra })

      const look = () => {
        // She sees well past her own chunk: count the ring around it as explored too.
        const cx = Math.floor(bot.entity.position.x / 16)
        const cz = Math.floor(bot.entity.position.z / 16)
        for (let dx = -SEEN_CHUNKS; dx <= SEEN_CHUNKS; dx++) {
          for (let dz = -SEEN_CHUNKS; dz <= SEEN_CHUNKS; dz++) memory.visited.add(`${cx + dx},${cz + dz}`)
        }
        if (isNight(bot)) throw new SkillEnd('failed', 'night is coming', result())
        const danger = hostilesAround(bot, DANGER_RADIUS)[0]
        if (danger) {
          throw new SkillEnd('failed', `danger: ${danger.name} ${Math.round(danger.position.distanceTo(bot.entity.position))} m away`, result())
        }
        if (goal) {
          const found = goal.kind === 'block'
            ? bot.findBlock({ matching: blockIds(bot, goal.name), maxDistance: 32 })?.position
            : findEntity(bot, goal.name, 32)?.position
          if (found) {
            const where = { x: Math.round(found.x), y: Math.round(found.y), z: Math.round(found.z) }
            throw new SkillEnd('done', `found ${goal.name}`, result({ found: { name: goal.name, ...where } }))
          }
        }
      }

      try {
        look()
        bot.pathfinder.setMovements(movements(bot, { canDig: false }))
        while (true) {
          const frontier = nextFrontier(memory.visited, start, bot.entity.position, params.radius)
          if (!frontier) return done(result(), 'area covered')
          memory.visited.add(frontier.key) // reached or not, don't try it again
          const leg = new goals.GoalNearXZ(frontier.x, frontier.z, 6)
          bot.pathfinder.setGoal(leg)
          const legEnd = Date.now() + LEG_SECONDS * 1000
          let lastMove = Date.now()
          let lastPos = bot.entity.position.clone()
          while (Date.now() < legEnd) {
            await ctx.sleep(250)
            look()
            if (leg.isEnd(bot.entity.position.floored())) break
            if (bot.entity.position.distanceTo(lastPos) > 1) {
              lastPos = bot.entity.position.clone()
              lastMove = Date.now()
            } else if (Date.now() - lastMove > STUCK_SECONDS * 1000) {
              break // no way there; the frontier is already marked
            }
          }
          bot.pathfinder.setGoal(null)
          ctx.progress(result())
        }
      } finally {
        memory.saveSoon()
      }
    }
  }
}

export { failed }
