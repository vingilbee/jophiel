// fight(target): fight hostile mobs until the target is dead or none are left.
// Low health is the runner's job (shared safety): it ends this skill as `hurt`, retreats and eats.
import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import {
  attackCooldownMs, equipBestWeapon, equipShield, goals, hostileRank, hostilesAround,
  isHostile, movements, retreatFrom
} from './common.js'

const SEARCH_RADIUS = 16
const REACH = 3
const OUTNUMBERED = 4 // this many hostiles within 8 blocks: flee instead
const TICK_MS = 50

// Highest priority first (creeper, skeletons, zombies, rest), nearest first within a rank.
export function pickTarget (bot, candidates) {
  const here = bot.entity.position
  return [...candidates].sort((a, b) =>
    hostileRank(a) - hostileRank(b) || a.position.distanceTo(here) - b.position.distanceTo(here))[0] ?? null
}

export const fight = {
  name: 'fight',
  description: 'Fight hostile mobs: the nearest hostile (creepers first, then skeletons, then zombies), or one type.',
  doneWhen: 'the target type is dead, or no hostiles are left nearby',
  stopsWhen: 'health low (retreats, then eats), outnumbered (flees), none found, time limit',
  params: {
    target: { type: 'string', default: 'nearest_hostile', description: '"nearest_hostile" or a hostile mob type, e.g. zombie' },
    radius: { type: 'integer', min: 4, max: SEARCH_RADIUS, default: SEARCH_RADIUS, description: 'how far from her to look for them, in blocks' },
    time_limit: timeLimitParam(90, 300)
  },
  validate (bot, params) {
    if (params.target === 'nearest_hostile') return
    const type = bot.registry.entitiesByName[params.target]
    if (!type) throw new ParamError(`no mob is called ${params.target}`)
    if (!isHostile({ type: type.type, name: type.name })) throw new ParamError(`${params.target} isn't hostile; she only fights hostile mobs`)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const stopped = () => ctx.signal.aborted
    const only = params.target === 'nearest_hostile' ? null : params.target
    const candidates = () => hostilesAround(bot, params.radius).filter((e) => !only || e.name === only)

    if (!candidates().length) return failed(`no ${only ?? 'hostile mob'} within ${params.radius} blocks`)
    const weapon = await ctx.race(equipBestWeapon(bot))
    const hasShield = await ctx.race(equipShield(bot))
    const cooldown = attackCooldownMs(weapon)

    let killed = 0
    let hits = 0
    let misses = 0
    let lastHit = 0
    let strafeLeft = true
    let lastStrafeSwap = 0
    let target = null
    const tally = (entity) => {
      if (entity === target) killed++
    }
    bot.on('entityDead', tally)
    bot.pathfinder.setMovements(movements(bot, { canDig: false }))
    try {
      while (true) {
        ctx.progress({ killed, hits, misses })
        const around = candidates()
        const close = hostilesAround(bot, 8)
        if (close.length >= OUTNUMBERED) {
          bot.pathfinder.setGoal(null)
          await retreatFrom(bot, pickTarget(bot, close), 3000, stopped)
          ctx.check()
          return failed(`outnumbered (${close.length} hostiles close), fled`, { killed, hits, misses })
        }
        if (!around.length) return done({ killed, hits, misses }, only ? `no ${only} left` : 'no hostiles left')

        const next = pickTarget(bot, around)
        if (next !== target) {
          target = next
          ctx.motion?.track(target)
          bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
        }
        const distance = bot.entity.position.distanceTo(target.position)
        const ready = Date.now() - lastHit >= cooldown

        if (distance <= REACH && ready) {
          if (bot.usingHeldItem) bot.deactivateItem() // lower the shield to swing
          if (ctx.motion) {
            // Swing once she's turned onto it; a hit lands only if her crosshair is really on it.
            await ctx.motion.ready(400)
            ctx.check()
            if (ctx.motion.onTarget(target) && !ctx.motion.fumble()) {
              bot.attack(target)
              hits++
            } else {
              bot.swingArm()
              misses++
            }
          } else {
            await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
            ctx.check()
            bot.attack(target)
            hits++
          }
          lastHit = Date.now()
          if (target.name === 'creeper') {
            // Never stay next to a creeper: one hit, then back off until it calms down.
            bot.pathfinder.setGoal(null)
            await retreatFrom(bot, target, 1500, stopped)
            ctx.check()
            bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
          } else {
            await stepBack(ctx)
          }
        } else if (hasShield && distance <= 5 && !ready && !bot.usingHeldItem) {
          bot.activateItem(true) // raise the shield while waiting for the swing to recharge
        }

        if (isRanged(target) && distance <= 10 && Date.now() - lastStrafeSwap > 800) {
          strafeLeft = !strafeLeft
          lastStrafeSwap = Date.now()
          bot.setControlState('left', strafeLeft)
          bot.setControlState('right', !strafeLeft)
        } else if (!isRanged(target)) {
          bot.setControlState('left', false)
          bot.setControlState('right', false)
        }
        await ctx.sleep(TICK_MS)
      }
    } catch (err) {
      if (err instanceof SkillEnd) err.data = { killed, hits, misses, ...err.data }
      throw err
    } finally {
      bot.off('entityDead', tally)
    }
  }
}

const isRanged = (entity) => ['skeleton', 'stray', 'bogged', 'pillager', 'blaze'].includes(entity.name)

async function stepBack (ctx) {
  const { bot } = ctx
  bot.setControlState('back', true)
  await ctx.sleep(250)
  bot.setControlState('back', false)
}
